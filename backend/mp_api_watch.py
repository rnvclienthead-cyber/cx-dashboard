"""Следит за официальными каналами API WB / Ozon / Яндекс Маркет.

Бот не может подписаться как человек. Читаем публичную ленту t.me/s/…
Новые посты про методы — в Telegram, без правок кода.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import text

from . import telegram_proxy
from .database import engine

logger = logging.getLogger("mp_api_watch")

MSK = ZoneInfo("Europe/Moscow")
NOTIFY_HOUR = int(os.getenv("MP_API_WATCH_HOUR", "15"))
NOTIFY_MINUTE = int(os.getenv("MP_API_WATCH_MINUTE", "0"))
MAX_DIGEST_POSTS = 12
BACKEND_DIR = Path(__file__).resolve().parent

CHANNELS = (
    {"slug": "OzonSellerAPI", "mp": "Ozon", "label": "Ozon Seller API"},
    {"slug": "wb_api_notifications", "mp": "WB", "label": "WB API"},
    {"slug": "yandex_market_api", "mp": "Яндекс Маркет", "label": "API Яндекс Маркета"},
)

_POST_RE = re.compile(
    r'data-post="(?P<slug>[^"/]+)/(?P<id>\d+)"[\s\S]{0,8000}?'
    r'class="tgme_widget_message_text[^"]*"[^>]*>(?P<body>[\s\S]*?)</div>',
    re.I,
)
_TAG_RE = re.compile(r"<[^>]+>")
_SKIP_RE = re.compile(
    r"пройти опрос|магазин приложений|набор в команду|оставьте заявку на регистрацию",
    re.I,
)
_NEED_RE = re.compile(
    r"\bapi\b|/v\d+/|/api/|метод|webhook|устарел|отключ|ключ seller|"
    r"чат с покупател|отзыв|вопрос о товар|возврат|претенз|seller api|wb api",
    re.I,
)
_PATH_RE = re.compile(
    r"(?:GET|POST|PUT|PATCH|DELETE)\s+(/[A-Za-z0-9_{}/.-]{6,})"
    r"|(/v\d+/[A-Za-z0-9_{}/.-]{4,})"
    r"|(/api/[A-Za-z0-9_{}/.-]{6,})",
    re.I,
)

_code_index: tuple[float, dict[str, tuple[str, ...]]] = (0.0, {})


def init_tables(eng=None) -> None:
    db = eng or engine
    with db.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mp_api_watch_seen (
                channel    TEXT NOT NULL,
                post_id    BIGINT NOT NULL,
                sent       BOOLEAN NOT NULL DEFAULT false,
                title      TEXT,
                seen_at    TIMESTAMP NOT NULL DEFAULT NOW(),
                PRIMARY KEY (channel, post_id)
            )
        """))
        conn.commit()


def _plain(raw: str) -> str:
    text_body = (raw or "").replace("<br/>", "\n").replace("<br>", "\n")
    text_body = _TAG_RE.sub("", text_body)
    return html_lib.unescape(text_body).strip()


def parse_channel_html(page: str, slug: str) -> list[dict]:
    posts = []
    for m in _POST_RE.finditer(page or ""):
        if (m.group("slug") or "").lower() != slug.lower():
            continue
        body = _plain(m.group("body") or "")
        if not body:
            continue
        posts.append({
            "slug": slug,
            "post_id": int(m.group("id")),
            "text": body,
            "url": f"https://t.me/{slug}/{m.group('id')}",
        })
    posts.sort(key=lambda p: p["post_id"])
    return posts


def is_relevant(text_body: str) -> bool:
    raw = text_body or ""
    if _NEED_RE.search(raw) or _PATH_RE.search(raw):
        if _SKIP_RE.search(raw) and not _PATH_RE.search(raw):
            return False
        return True
    return not _SKIP_RE.search(raw)


def extract_paths(text_body: str) -> list[str]:
    found = []
    for m in _PATH_RE.finditer(text_body or ""):
        path = next((g for g in m.groups() if g), "")
        path = re.sub(r"\{[^}]+\}", "", path).rstrip("/")
        if path and path not in found:
            found.append(path)
    return found[:12]


def _code_paths() -> dict[str, tuple[str, ...]]:
    global _code_index
    stamp, cached = _code_index
    if cached and time.time() - stamp < 3600:
        return cached
    hits: dict[str, list[str]] = {}
    files = list(BACKEND_DIR.glob("*.py")) + list((BACKEND_DIR / "routers").glob("*.py"))
    for path in files:
        if path.name.startswith("test_"):
            continue
        try:
            src = path.read_text(encoding="utf-8")
        except Exception:
            continue
        name = path.name
        for token in re.findall(r"/[A-Za-z0-9_./-]{8,}", src):
            token = token.split("?")[0].rstrip(")'\",")
            hits.setdefault(token, []).append(name)
    compact = {k: tuple(sorted(set(v))) for k, v in hits.items()}
    _code_index = (time.time(), compact)
    return compact


def project_files_for(text_body: str) -> list[str]:
    index = _code_paths()
    names: set[str] = set()
    for path in extract_paths(text_body):
        for token, files in index.items():
            if path in token or token in path:
                names.update(files)
    return sorted(names)[:8]


def seconds_until_daily(now: datetime | None = None, hour: int = NOTIFY_HOUR, minute: int = NOTIFY_MINUTE) -> float:
    """Секунд до ближайших hour:minute по Москве. Если время уже прошло — завтра."""
    stamp = now or datetime.now(MSK)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=MSK)
    else:
        stamp = stamp.astimezone(MSK)
    target = stamp.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if stamp >= target:
        target += timedelta(days=1)
    return max(1.0, (target - stamp).total_seconds())


def format_alert(channel: dict, post: dict, used_in: Iterable[str]) -> str:
    body = re.sub(r"\n{3,}", "\n\n", post["text"]).strip()
    if len(body) > 700:
        body = body[:700].rstrip() + "…"
    used = list(used_in)
    if used:
        mark = "⚠️ у нас в коде: " + ", ".join(used)
    else:
        mark = "ℹ️ в нашем коде эти методы не нашёл — скорее всего не про CX"
    return (
        f"📡 {channel['mp']} · {channel['label']}\n\n"
        f"{body}\n\n"
        f"{mark}\n"
        f"{post['url']}"
    )


def fetch_html(slug: str) -> str:
    url = f"https://t.me/s/{slug}"
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 CX-dashboard api-watch"},
                timeout=25,
            )
            resp.raise_for_status()
            return resp.text or ""
        except Exception as e:
            last_err = e
            time.sleep(1.2 * (attempt + 1))
    raise last_err


def _seen_ids(conn, slug: str) -> set[int]:
    rows = conn.execute(
        text("SELECT post_id FROM mp_api_watch_seen WHERE channel = :c"),
        {"c": slug},
    ).fetchall()
    return {int(r[0]) for r in rows}


def _mark(conn, slug: str, post_id: int, sent: bool, title: str) -> None:
    conn.execute(text("""
        INSERT INTO mp_api_watch_seen (channel, post_id, sent, title)
        VALUES (:c, :id, :s, :t)
        ON CONFLICT (channel, post_id) DO NOTHING
    """), {"c": slug, "id": post_id, "s": sent, "t": (title or "")[:240]})


def _headline(text_body: str) -> str:
    line = (text_body or "").strip().split("\n", 1)[0].strip()
    line = re.sub(r"\s+", " ", line)
    if len(line) > 120:
        return line[:117].rstrip() + "…"
    return line or "Новый пост"


def collect_updates() -> dict:
    """Читает каналы. Первый раз только запоминает. Дальше копит новые посты."""
    stats = {
        "fetched": 0,
        "new": 0,
        "seeded": 0,
        "items": [],
        "errors": [],
        "checked": [],
    }
    init_tables()
    with engine.connect() as conn:
        for channel in CHANNELS:
            slug = channel["slug"]
            try:
                page = fetch_html(slug)
            except Exception as e:
                logger.warning("[api-watch] %s fetch: %s", slug, e)
                stats["errors"].append(f"{channel['mp']}: не открылся канал")
                continue
            posts = parse_channel_html(page, slug)
            stats["fetched"] += len(posts)
            stats["checked"].append(channel["mp"])
            known = _seen_ids(conn, slug)
            if not known:
                for post in posts:
                    _mark(conn, slug, post["post_id"], False, post["text"][:120])
                    stats["seeded"] += 1
                conn.commit()
                logger.info("[api-watch] %s seed %s posts, без рассылки", slug, len(posts))
                continue
            fresh = [p for p in posts if p["post_id"] not in known]
            stats["new"] += len(fresh)
            for post in fresh:
                title = post["text"][:120]
                relevant = is_relevant(post["text"])
                _mark(conn, slug, post["post_id"], relevant, title)
                if relevant:
                    stats["items"].append({
                        "channel": channel,
                        "post": post,
                        "used_in": project_files_for(post["text"]),
                    })
            conn.commit()
    stats["items"] = stats["items"][:MAX_DIGEST_POSTS]
    return stats


def format_digest(stats: dict, now: datetime | None = None) -> str:
    stamp = now or datetime.now(MSK)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=MSK)
    else:
        stamp = stamp.astimezone(MSK)
    when = stamp.strftime("%d.%m.%Y") + ", 15:00 МСК"
    lines = [f"📡 Проверка API маркетплейсов · {when}"]
    items = stats.get("items") or []
    if not items:
        lines.append("")
        lines.append("За сутки новых постов про методы нет.")
    else:
        lines.append("")
        ours = sum(1 for it in items if it.get("used_in"))
        lines.append(f"Новых постов: {len(items)}" + (f", из них у нас в коде: {ours}" if ours else ""))
        by_mp: dict[str, list] = {}
        for it in items:
            by_mp.setdefault(it["channel"]["mp"], []).append(it)
        for mp, group in by_mp.items():
            lines.append("")
            lines.append(f"{mp}:")
            for it in group:
                flag = " ⚠️ есть у нас" if it.get("used_in") else ""
                lines.append(f"• {_headline(it['post']['text'])}{flag}")
                lines.append(f"  {it['post']['url']}")
        if ours:
            lines.append("")
            lines.append("Если затронуто наше — напиши «делай».")
    if stats.get("errors"):
        lines.append("")
        lines.append("Не прочиталось: " + "; ".join(stats["errors"]))
    return "\n".join(lines)


def tick(send_fn=None) -> dict:
    """Совместимость: сбор без рассылки (первый проход — seed)."""
    stats = collect_updates()
    stats["sent"] = 0
    return stats


def run_daily(send_fn=None) -> dict:
    """Суточная проверка: всегда одно сообщение в бот."""
    send = send_fn or _send_digest
    dest = (os.getenv("TG_ALERT_CHAT_ID") or "").strip()
    stats = collect_updates()
    text_body = format_digest(stats)
    stats["sent"] = 0
    if dest and send(dest, text_body):
        stats["sent"] = 1
    return stats


def _send_digest(chat_id: str, text_body: str) -> bool:
    return telegram_proxy.send_text(chat_id, text_body)


def enabled() -> bool:
    flag = (os.getenv("MP_API_WATCH") or "1").strip().lower()
    return flag not in {"0", "false", "off", "no"}


async def start_worker(get_db_func=None) -> None:
    if not enabled():
        logger.info("[api-watch] выключен (MP_API_WATCH=0)")
        return
    logger.info("[api-watch] старт, уведомление каждый день в %02d:%02d МСК", NOTIFY_HOUR, NOTIFY_MINUTE)
    while True:
        wait = seconds_until_daily()
        logger.info("[api-watch] следующая проверка через %.0f мин", wait / 60)
        try:
            await asyncio.sleep(wait)
        except asyncio.CancelledError:
            raise
        try:
            stats = await asyncio.to_thread(run_daily)
            logger.info("[api-watch] daily %s", {k: stats[k] for k in ("fetched", "new", "seeded", "sent") if k in stats})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[api-watch] daily: %s", e)
            dest = (os.getenv("TG_ALERT_CHAT_ID") or "").strip()
            if dest:
                try:
                    telegram_proxy.send_text(
                        dest,
                        "📡 Проверка API маркетплейсов в 15:00 не удалась. Повторим завтра.",
                    )
                except Exception:
                    pass
        # Чуть подождать, чтобы не дёрнуть ту же минуту второй раз.
        await asyncio.sleep(60)
