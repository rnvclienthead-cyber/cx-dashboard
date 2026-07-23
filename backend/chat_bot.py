"""
ИИ-бот автоответов в WB-чатах.

Архитектура (согласована с Рустамом 2026-07-22/23):
  1. Диспетчер (YandexGPT-lite + жёсткие правила) → тема / уверенность / нужен человек
  2. Плейбук → текст + действие (форма, статус, передача менеджеру)
  3. Светофор темы (auto / draft / human)
  4. Сборка: [приветствие?] + суть + [подпись?]  (приветствие — раз в календарный день МСК)
  5. Shadow-режим: пишем «в стол», клиенту не отправляем

Воркер крутится рядом с синком чатов. Включается настройками:
  bot_enabled = true/false
  bot_shadow  = true/false   (по умолчанию true — безопасно)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import cx_alert
from .routers import chat_playbook, reshipment_skus
from . import wb_chat

logger = logging.getLogger("chat_bot")

MSK = ZoneInfo("Europe/Moscow")
BOT_INTERVAL_SEC = 90
BOT_BATCH_LIMIT = 15
MIN_CONFIDENCE = 0.55

FORM_BASE = "https://box.vidovito.com/reshipment/form"

# Жёсткая красная зона — без ИИ
_RED_RE = re.compile(
    r"(деньг|компенсац|возврат\s*денег|роспотреб|суд\b|юрист|адвокат|"
    r"обман|мошен|угроз|травм|здоровь|отравил|аллерги|"
    r"прокуратур|полици|жалоб\w*\s+в\s+|напишу\s+в\s+поддержк|"
    r"отклонили\s+мо[юе]|вы\s+отклонили)",
    re.IGNORECASE,
)

_AGREE_RE = re.compile(
    r"^(да|даа+|ок|окей|хорошо|отправляйте|отправляй|отправьте|"
    r"буду\s+благодар|актуально|согласен|согласна|нужно|"
    r"давайте|давай|конечно|ждём|ждем)[\s!.]*$",
    re.IGNORECASE,
)

_DECLINE_RE = re.compile(
    r"(не\s+актуальн|не\s+надо|не\s+нужно|отказ|не\s+хочу|"
    r"уже\s+перезаказ|хочу\s+вернуть|верну|возврат)",
    re.IGNORECASE,
)

_GREETING_RE = re.compile(
    r"^\s*(здравствуйте|добрый\s+(день|вечер|утро)|привет)[!.,]?\s*",
    re.IGNORECASE,
)

_SHORT_NO_SIGN_RE = re.compile(
    r"^(спасибо|благодар|ок|окей|хорошо|пожалуйста|рад\s+помочь|"
    r"пожалуйста[!.,]?|понял|принято)[\s!.❤🙏👍]*$",
    re.IGNORECASE,
)


# ── Инициализация ────────────────────────────────────────────────────────────

EXTRA_SETTINGS = [
    ("bot_enabled", "false",
     "Автозапуск бота (true/false). false = работает только по кнопке «Прогнать сейчас»."),
    ("bot_shadow", "true",
     "Теневой режим: бот пишет ответы в журнал, но НЕ отправляет клиенту (true/false)."),
    ("greeting_text", "Здравствуйте!",
     "Приветствие. Ставится раз в календарный день (по Москве)."),
    ("bot_confidence_min", "0.55",
     "Минимальная уверенность диспетчера. Ниже — передаём менеджеру."),
    ("bot_only_days", "2",
     "Брать только чаты за последние N дней (0 = без ограничения). Экономит токены на старом бэклоге."),
]


def init_bot_tables(engine):
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_chat_bot_state (
                chat_id            TEXT PRIMARY KEY,
                stage              TEXT DEFAULT 'new',
                last_theme         TEXT,
                last_greeted_date  DATE,
                last_bot_at        TIMESTAMP,
                closing_asked_at   TIMESTAMP,
                updated_at         TIMESTAMP DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_chat_bot_log (
                id              BIGSERIAL PRIMARY KEY,
                chat_id         TEXT NOT NULL,
                client_message  TEXT,
                theme           TEXT,
                confidence      REAL,
                zone            TEXT,
                branch_key      TEXT DEFAULT '',
                reply_text      TEXT,
                action          TEXT DEFAULT '',
                shadow          BOOLEAN DEFAULT TRUE,
                sent            BOOLEAN DEFAULT FALSE,
                need_human      BOOLEAN DEFAULT FALSE,
                reason          TEXT DEFAULT '',
                created_at      TIMESTAMP DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_chat_bot_log_created
            ON cx_chat_bot_log (created_at DESC)
        """))
        for k, v, d in EXTRA_SETTINGS:
            conn.execute(text("""
                INSERT INTO cx_chat_settings (key, value, descr, updated_by)
                VALUES (:k, :v, :d, 'system')
                ON CONFLICT (key) DO UPDATE SET descr = EXCLUDED.descr
            """), {"k": k, "v": v, "d": d})
        conn.commit()


# ── Время ────────────────────────────────────────────────────────────────────

def _msk_now() -> datetime:
    return datetime.now(MSK)


def _msk_date(dt: Optional[datetime] = None):
    return (dt or _msk_now()).astimezone(MSK).date()


def _ts_to_msk_date(ts_ms: Optional[int]):
    if not ts_ms:
        return None
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(MSK).date()


# ── Контекст чата ────────────────────────────────────────────────────────────

def _get_article(db: Session, nm_id) -> str:
    if not nm_id:
        return ""
    try:
        n = int(nm_id)
    except (TypeError, ValueError):
        return ""
    for sql in (
        """SELECT supplier_article FROM wb_orders
           WHERE nm_id = :n AND supplier_article IS NOT NULL AND supplier_article != ''
           ORDER BY dt DESC NULLS LAST LIMIT 1""",
        """SELECT supplier_article FROM wb_feedbacks
           WHERE nm_id = :n AND supplier_article IS NOT NULL AND supplier_article != ''
           ORDER BY created_date DESC NULLS LAST LIMIT 1""",
        """SELECT supplier_article FROM wb_claims
           WHERE nm_id = :n AND supplier_article IS NOT NULL AND supplier_article != ''
           LIMIT 1""",
    ):
        try:
            row = db.execute(text(sql), {"n": n}).fetchone()
            if row and row[0]:
                return row[0]
        except Exception:
            db.rollback()
    return ""


def _get_reshipment(db: Session, chat_id: str) -> Optional[dict]:
    try:
        row = db.execute(text("""
            SELECT id, status, cdek_number, track_number, delivery_method,
                   client_pvz_address, customer_name, wb_nm_id
            FROM reshipment_requests
            WHERE wb_chat_id = :cid
            ORDER BY id DESC LIMIT 1
        """), {"cid": chat_id}).mappings().fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.warning("[ChatBot] reshipment lookup: %s", e)
        try:
            db.rollback()
        except Exception:
            pass
        return None


def _history(db: Session, chat_id: str, limit: int = 8) -> List[dict]:
    rows = db.execute(text("""
        SELECT sender, text, add_ts FROM wb_chat_messages
        WHERE chat_id = :cid AND text IS NOT NULL AND text != ''
        ORDER BY add_ts DESC NULLS LAST LIMIT :lim
    """), {"cid": chat_id, "lim": limit}).mappings().all()
    return list(reversed([dict(r) for r in rows]))


def _bot_state(db: Session, chat_id: str) -> dict:
    row = db.execute(text(
        "SELECT * FROM cx_chat_bot_state WHERE chat_id = :cid"
    ), {"cid": chat_id}).mappings().fetchone()
    if row:
        return dict(row)
    db.execute(text("""
        INSERT INTO cx_chat_bot_state (chat_id, stage) VALUES (:cid, 'new')
        ON CONFLICT (chat_id) DO NOTHING
    """), {"cid": chat_id})
    db.commit()
    return {"chat_id": chat_id, "stage": "new", "last_theme": None,
            "last_greeted_date": None, "last_bot_at": None, "closing_asked_at": None}


def _set_state(db: Session, chat_id: str, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    params = {"cid": chat_id, **fields}
    db.execute(text(f"""
        INSERT INTO cx_chat_bot_state (chat_id, stage)
        VALUES (:cid, 'new')
        ON CONFLICT (chat_id) DO NOTHING
    """), {"cid": chat_id})
    db.execute(text(f"""
        UPDATE cx_chat_bot_state SET {sets}, updated_at = now()
        WHERE chat_id = :cid
    """), params)
    db.commit()


# ── Классификация ────────────────────────────────────────────────────────────

def _rule_classify(msg: str, stage: str) -> Optional[Tuple[str, float, bool, str]]:
    """Жёсткие правила до ИИ. Возвращает (theme, conf, need_human, reason) или None."""
    s = (msg or "").strip()
    if not s:
        return ("blagodarnost", 0.4, True, "пустое сообщение")

    if _RED_RE.search(s):
        return ("otkaz_dootpravki", 0.95, True, "красная зона (правило)")

    # Короткие согласия/отказы зависят от стадии
    if stage in ("awaiting_client", "form_sent") and len(s) < 80:
        if _AGREE_RE.match(s) and not _DECLINE_RE.search(s):
            return ("soglasie", 0.92, False, "согласие по стадии")
        if _DECLINE_RE.search(s):
            return ("otkaz_dootpravki", 0.9, True, "отказ от доотправки")

    low = s.lower()
    if len(s) < 40 and re.search(r"(спасибо|благодар|всё\s+пришло|все\s+пришло|получил)", low):
        return ("blagodarnost", 0.85, False, "благодарность")

    if re.search(r"(ссылк\w+\s+не|не\s+открыва|перекидывает|что\s+за\s+сайт)", low):
        return ("ssylka", 0.88, False, "ссылка")

    if re.search(r"(не\s+хватает|нет\s+в\s+коробке|отсутств|не\s+положили|недокомплект|"
                 r"не\s+хватало|забыли\s+положить|без\s+(труб|винт|колес|крепеж|ножек))", low):
        return ("nekomplekt", 0.8, False, "некомплект")

    return None


def _themes_for_prompt(db: Session) -> List[dict]:
    rows = db.execute(text("""
        SELECT theme_key, name, when_text FROM cx_chat_themes
        WHERE active ORDER BY sort_order
    """)).mappings().all()
    return [dict(r) for r in rows]


def _yandex_classify(msg: str, history: List[dict], themes: List[dict],
                     article: str, stage: str) -> Optional[dict]:
    api_key = os.getenv("YANDEX_API_KEY", "").strip()
    folder_id = os.getenv("FOLDER_ID", "").strip()
    if not api_key or not folder_id:
        logger.warning("[ChatBot] YANDEX_API_KEY/FOLDER_ID не заданы")
        return None

    theme_lines = "\n".join(
        f"- {t['theme_key']}: {t['name']} — {t['when_text']}" for t in themes
    )
    hist_lines = "\n".join(
        f"{'Клиент' if h['sender']=='client' else 'Мы'}: {(h.get('text') or '')[:200]}"
        for h in history[-6:]
    ) or "(пусто)"

    system = (
        "Ты диспетчер чата продавца маркетплейса. "
        "Выбери ОДНУ тему из списка. Не пиши ответ клиенту. "
        "Если это деньги/суд/угрозы/травма — need_human=true. "
        "Ответь ТОЛЬКО JSON: "
        '{"theme":"theme_key","confidence":0.0,"need_human":false,"reason":"..."}'
    )
    user = (
        f"Стадия диалога: {stage}\n"
        f"Артикул: {article or 'неизвестен'}\n"
        f"Темы:\n{theme_lines}\n\n"
        f"История:\n{hist_lines}\n\n"
        f"Новое сообщение клиента:\n{msg}\n"
    )

    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {
        "Authorization": f"Api-Key {api_key}",
        "Content-Type": "application/json",
        "x-folder-id": folder_id,
    }
    payload = {
        "modelUri": f"gpt://{folder_id}/yandexgpt-lite/latest",
        "completionOptions": {"temperature": 0.1, "maxTokens": 300},
        "messages": [
            {"role": "system", "text": system},
            {"role": "user", "text": user},
        ],
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=40)
        if r.status_code != 200:
            logger.warning("[ChatBot] Yandex %s: %s", r.status_code, r.text[:200])
            return None
        raw = r.json()["result"]["alternatives"][0]["message"]["text"]
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group(0))
        return data
    except Exception as e:
        logger.warning("[ChatBot] Yandex classify error: %s", e)
        return None


def classify(db: Session, msg: str, stage: str, history: List[dict],
             article: str) -> Tuple[str, float, bool, str]:
    ruled = _rule_classify(msg, stage)
    if ruled:
        return ruled

    themes = _themes_for_prompt(db)
    ai = _yandex_classify(msg, history, themes, article, stage)
    if not ai:
        return ("brak", 0.3, True, "ИИ недоступен — менеджеру")

    keys = {t["theme_key"] for t in themes}
    theme = ai.get("theme") or ""
    if theme not in keys:
        return ("brak", 0.3, True, f"неизвестная тема ИИ: {theme}")

    conf = float(ai.get("confidence") or 0.5)
    need = bool(ai.get("need_human"))
    reason = str(ai.get("reason") or "ИИ")
    return theme, conf, need, reason


# ── Ветки и сборка ответа ────────────────────────────────────────────────────

def _make_form_url(db: Session, chat_id: str) -> str:
    token = secrets.token_hex(16)
    try:
        db.execute(text("""
            INSERT INTO reshipment_chat_tokens (token, chat_id, created_by, expires_at)
            VALUES (:token, :cid, 'chat_bot', NOW() + INTERVAL '7 days')
        """), {"token": token, "cid": chat_id})
        db.commit()
    except Exception as e:
        logger.warning("[ChatBot] token error: %s", e)
        db.rollback()
    return f"{FORM_BASE}?t={token}"


def _resolve_branch(db: Session, theme: str, article: str,
                    reship: Optional[dict], msg: str) -> str:
    if theme == "nekomplekt":
        return "sku_allowed" if reshipment_skus.is_allowed(db, article) else "sku_denied"
    if theme == "status_zayavki" and reship:
        st = reship.get("status") or "new"
        if st in ("new", "pending", "awaiting"):
            return "new"
        if st in ("approved", "warehouse", "ready_to_ship"):
            return "approved"
        if st in ("shipped", "in_transit"):
            return "shipped"
        if st == "ready_for_pickup":
            return "ready_for_pickup"
        if st == "delivered":
            return "delivered"
        return "new"
    if theme == "brak":
        # Пока без тонкой ветки «чинится деталью» — безопаснее вести на возврат,
        # кроме разрешённых SKU (можно предложить деталь).
        return "fixable_by_part" if reshipment_skus.is_allowed(db, article) else "needs_return"
    if theme == "blagodarnost":
        if re.search(r"(всё\s+пришло|все\s+пришло|получил|подошло|собрал)", msg or "", re.I):
            return "problem_solved"
        return "just_thanks"
    if theme == "sborka" and article and "стеллаж" in article.lower():
        return "стеллаж"
    return ""


def _fill_placeholders(body: str, *, form_url: str = "", reship: Optional[dict] = None) -> str:
    carrier = "СДЭК"
    track = ""
    if reship:
        if (reship.get("delivery_method") or "") == "yandex":
            carrier = "Яндекс Доставка"
        track = reship.get("cdek_number") or reship.get("track_number") or ""
    return (
        body.replace("{form_url}", form_url or FORM_BASE)
            .replace("{carrier}", carrier)
            .replace("{track}", track or "—")
            .replace("{storage_until}", "указанного срока")
    )


def _needs_greeting(state: dict, last_seller_ts: Optional[int]) -> bool:
    today = _msk_date()
    last_g = state.get("last_greeted_date")
    if last_g:
        if hasattr(last_g, "isoformat"):
            if last_g == today:
                return False
        elif str(last_g)[:10] == str(today):
            return False
    # Если сегодня уже писали мы (по last_ts продавца) — тоже без приветствия
    if last_seller_ts:
        if _ts_to_msk_date(last_seller_ts) == today:
            return False
    return True


def _strip_greeting(body: str) -> str:
    return _GREETING_RE.sub("", body or "").lstrip()


def assemble_reply(db: Session, theme: str, branch: str, chat_id: str,
                   state: dict, reship: Optional[dict],
                   last_seller_ts: Optional[int]) -> Tuple[str, str]:
    """Возвращает (текст, action)."""
    action_row = db.execute(text(
        "SELECT action FROM cx_chat_themes WHERE theme_key = :k"
    ), {"k": theme}).fetchone()
    action = (action_row[0] if action_row else "") or ""

    body = chat_playbook.pick_variant(db, theme, branch)
    if not body and branch:
        body = chat_playbook.pick_variant(db, theme, "")
    if not body:
        return "", action

    form_url = ""
    if action in ("offer_reshipment", "send_form_link", "resend_form_link") or "{form_url}" in body:
        form_url = _make_form_url(db, chat_id)

    body = _fill_placeholders(_strip_greeting(body), form_url=form_url, reship=reship)

    parts = []
    if _needs_greeting(state, last_seller_ts):
        greet = chat_playbook.get_setting(db, "greeting_text", "Здравствуйте!").strip()
        if greet:
            parts.append(greet)

    parts.append(body)
    text_out = "\n\n".join(p for p in parts if p).strip()

    # Подпись — только под содержательным ответом
    core = _strip_greeting(body).strip()
    if core and not _SHORT_NO_SIGN_RE.match(core) and len(core) > 40:
        sig = chat_playbook.get_setting(db, "signature", "").strip()
        if sig:
            text_out = f"{text_out}{sig if sig.startswith(chr(10)) else chr(10)+chr(10)+sig}"

    return text_out[:1000], action


def _next_stage(theme: str, action: str, current: str) -> str:
    if theme == "soglasie" or action == "send_form_link":
        return "form_sent"
    if theme == "nekomplekt" and action == "offer_reshipment":
        return "awaiting_client"
    if theme == "ssylka":
        return "form_sent"
    if theme == "blagodarnost":
        return "closing"
    if theme == "status_zayavki":
        return current or "in_work"
    if theme == "otkaz_dootpravki":
        return "handoff"
    return current or "new"


# ── Отправка / лог ───────────────────────────────────────────────────────────

def _log(db: Session, **kw):
    db.execute(text("""
        INSERT INTO cx_chat_bot_log
          (chat_id, client_message, theme, confidence, zone, branch_key,
           reply_text, action, shadow, sent, need_human, reason)
        VALUES
          (:chat_id, :client_message, :theme, :confidence, :zone, :branch_key,
           :reply_text, :action, :shadow, :sent, :need_human, :reason)
    """), kw)
    db.commit()


def _send_to_client(db: Session, chat_id: str, reply_sign: str, text_msg: str) -> bool:
    try:
        wb_chat.send_message(reply_sign, text_msg)
    except Exception as e:
        logger.error("[ChatBot] send fail %s: %s", chat_id, e)
        return False
    now_ms = int(time.time() * 1000)
    db.execute(text("""
        UPDATE wb_chats SET needs_answer = false, last_sender = 'seller',
            last_message = :ans, last_ts = :ts, updated_at = now()
        WHERE chat_id = :cid
    """), {"cid": chat_id, "ans": text_msg[:1000], "ts": now_ms})
    db.execute(text("""
        INSERT INTO wb_chat_messages (chat_id, sender, text, add_ts)
        VALUES (:cid, 'seller', :txt, :ts)
        ON CONFLICT (chat_id, add_ts, sender) DO NOTHING
    """), {"cid": chat_id, "txt": text_msg, "ts": now_ms})
    db.execute(text("""
        INSERT INTO cx_response_log (item_type, item_id, mp, answered_by, answer_text)
        VALUES ('chat', :iid, 'wb', 'chat_bot', :ans)
    """), {"iid": chat_id, "ans": text_msg})
    db.commit()
    return True


def _notify_manager(db: Session, reason: str, client: str, sku: str,
                    message: str, chat_id: str):
    if chat_playbook.get_setting(db, "red_zone_notify_tg", "true").lower() != "true":
        return
    tmpl = chat_playbook.get_setting(db, "red_zone_manager_text", "")
    link = f"https://cxvo.ru/chats?chat={chat_id}"
    text_msg = (tmpl or "🔴 Чат {client}: {reason}\n{message}\n{link}").format(
        reason=reason or "—",
        client=client or "—",
        sku=sku or "—",
        message=(message or "")[:300],
        link=link,
    )
    try:
        cx_alert.send_tg(text_msg)
    except Exception as e:
        logger.warning("[ChatBot] TG notify: %s", e)


# ── Обработка одного чата ────────────────────────────────────────────────────

def process_one(db: Session, chat: dict, *, shadow: bool) -> Optional[dict]:
    chat_id = chat["chat_id"]
    msg = (chat.get("last_message") or "").strip()
    if not msg:
        return None

    # Уже отвечали на этот же last_ts?
    state = _bot_state(db, chat_id)
    last_bot = state.get("last_bot_at")
    last_ts = chat.get("last_ts")
    if last_bot and last_ts:
        # last_bot_at храним как timestamp; если бот уже трогал чат после last_ts — пропуск
        try:
            bot_ms = int(last_bot.replace(tzinfo=timezone.utc).timestamp() * 1000) if hasattr(last_bot, "timestamp") else 0
            # грубо: если last_bot новее сообщения клиента — уже обработали
            if bot_ms and bot_ms >= int(last_ts):
                return None
        except Exception:
            pass

    article = _get_article(db, chat.get("nm_id"))
    reship = _get_reshipment(db, chat_id)
    if reship and reship.get("wb_nm_id") and not article:
        article = _get_article(db, reship.get("wb_nm_id"))

    history = _history(db, chat_id)
    stage = state.get("stage") or "new"

    theme, conf, need_human, reason = classify(db, msg, stage, history, article)

    min_conf = float(chat_playbook.get_setting(db, "bot_confidence_min", str(MIN_CONFIDENCE))
                     or MIN_CONFIDENCE)
    if conf < min_conf:
        need_human = True
        reason = f"низкая уверенность ({conf:.2f}): {reason}"

    zone = chat_playbook.get_zone(db, theme)
    if need_human:
        zone = "human"

    branch = _resolve_branch(db, theme, article, reship, msg)

    # Последнее наше сообщение — для правила приветствия
    last_seller = db.execute(text("""
        SELECT add_ts FROM wb_chat_messages
        WHERE chat_id = :cid AND sender = 'seller'
        ORDER BY add_ts DESC NULLS LAST LIMIT 1
    """), {"cid": chat_id}).fetchone()
    last_seller_ts = last_seller[0] if last_seller else None
    need_greet = _needs_greeting(state, last_seller_ts)

    if zone == "human":
        client_text = chat_playbook.get_setting(db, "red_zone_client_text", "")
        reply = client_text.strip()
        action = "handoff_to_manager"
        if reply and need_greet:
            greet = chat_playbook.get_setting(db, "greeting_text", "Здравствуйте!").strip()
            if greet and not _GREETING_RE.match(reply):
                reply = f"{greet}\n\n{reply}"
    else:
        reply, action = assemble_reply(
            db, theme, branch, chat_id, state, reship, last_seller_ts
        )

    sent = False
    if zone == "auto" and reply and not shadow:
        sign = chat.get("reply_sign")
        if sign:
            sent = _send_to_client(db, chat_id, sign, reply)
        else:
            reason = (reason + "; нет reply_sign").strip("; ")

    if zone == "human" or need_human:
        _notify_manager(
            db, reason=reason or theme,
            client=chat.get("client_name") or "",
            sku=article,
            message=msg,
            chat_id=chat_id,
        )

    new_stage = _next_stage(theme, action, stage)
    state_update = {
        "stage": new_stage,
        "last_theme": theme,
        "last_bot_at": datetime.now(timezone.utc).replace(tzinfo=None),
    }
    if reply and need_greet:
        state_update["last_greeted_date"] = _msk_date()

    _set_state(db, chat_id, **state_update)

    _log(
        db,
        chat_id=chat_id,
        client_message=msg[:2000],
        theme=theme,
        confidence=conf,
        zone=zone,
        branch_key=branch or "",
        reply_text=reply or "",
        action=action or "",
        shadow=shadow,
        sent=sent,
        need_human=need_human or zone == "human",
        reason=reason or "",
    )

    return {
        "chat_id": chat_id,
        "theme": theme,
        "zone": zone,
        "confidence": conf,
        "sent": sent,
        "shadow": shadow,
        "reply": (reply or "")[:200],
        "reason": reason,
    }


def process_batch(db: Session, limit: int = BOT_BATCH_LIMIT, *, force: bool = False) -> dict:
    """force=True — ручной прогон по кнопке, игнорирует bot_enabled (автозапуск)."""
    enabled = chat_playbook.get_setting(db, "bot_enabled", "false").lower() == "true"
    if not enabled and not force:
        return {"ok": False, "reason": "bot_enabled=false (автозапуск выключен)", "processed": 0}

    shadow = chat_playbook.get_setting(db, "bot_shadow", "true").lower() == "true"
    try:
        only_days = int(float(chat_playbook.get_setting(db, "bot_only_days", "2") or "2"))
    except ValueError:
        only_days = 2
    if only_days <= 0:
        only_days = 30  # предохранитель: не берём вечность

    # Чаты, где ждём ответа клиента и не в архиве — только свежие.
    # reply_sign нужен только для реальной отправки; в тени обрабатываем и без него.
    rows = db.execute(text("""
        SELECT c.chat_id, c.client_name, c.nm_id, c.good_name, c.last_message,
               c.last_sender, c.last_ts, c.reply_sign, c.needs_answer, c.is_closing
        FROM wb_chats c
        LEFT JOIN cx_dismissed_items d
               ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
        WHERE c.needs_answer = true
          AND COALESCE(c.is_closing, false) = false
          AND c.last_sender = 'client'
          AND d.item_id IS NULL
          AND (CAST(:shadow AS boolean) = true OR c.reply_sign IS NOT NULL)
          AND c.last_ts > EXTRACT(EPOCH FROM now() - make_interval(days => :days)) * 1000
        ORDER BY c.last_ts DESC
        LIMIT :lim
    """), {"lim": limit, "days": only_days, "shadow": shadow}).mappings().all()

    results = []
    for row in rows:
        try:
            r = process_one(db, dict(row), shadow=shadow)
            if r:
                results.append(r)
        except Exception as e:
            logger.exception("[ChatBot] process %s: %s", row.get("chat_id"), e)
            try:
                db.rollback()
            except Exception:
                pass

    return {
        "ok": True,
        "shadow": shadow,
        "processed": len(results),
        "items": results,
    }


# ── Воркер ───────────────────────────────────────────────────────────────────

async def start_chat_bot_worker(get_db_func):
    logger.info("[ChatBot] worker started, interval=%ss", BOT_INTERVAL_SEC)
    await asyncio.sleep(20)  # дать синку чатов прогреться
    while True:
        try:
            db = next(get_db_func())
            try:
                stats = await asyncio.to_thread(process_batch, db, BOT_BATCH_LIMIT)
                if stats.get("processed"):
                    logger.info("[ChatBot] batch: %s", stats)
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[ChatBot] worker error: %s", e)
        await asyncio.sleep(BOT_INTERVAL_SEC)
