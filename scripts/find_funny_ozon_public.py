#!/usr/bin/env python3
"""Сбор публичных отзывов Ozon по SKU (Seller Review API недоступен без подписки)."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import requests
from sqlalchemy import text
from backend.database import engine

MSK = timezone(timedelta(hours=3))
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

FUN_HINTS = [
    "кот", "кошк", "пёс", "собак", "муж", "жена", "тёща", "теща",
    "ребён", "ребен", "малыш", "внук", "внуч", "бабуш", "дедуш",
    "ору", "смех", "ржа", "угар", "ахаха", "😂", "🤣", "😅",
    "собрал", "собрала", "инструкц", "головолом", "экскурси",
    "зачем", "восток", "чудо", "подруг", "сосед", "мама", "сын", "доч",
    "кайф", "обожаю", "мечт", "лучший", "шикарн",
]
EXCLUDE = [
    "брак", "сломал", "сломан", "вернул", "возврат", "обман",
    "ужас", "кошмар", "не советую", "не покупайте", "отвратитель", "мошен",
]


def score(txt: str) -> int:
    t = (txt or "").lower()
    if len(t) < 40:
        return -1
    if any(x in t for x in EXCLUDE):
        return -1
    s = 0
    for h in FUN_HINTS:
        if h in t:
            s += 2
    if "!" in (txt or ""):
        s += 1
    if any(c in (txt or "") for c in "😂🤣😅😆🙈🔥💞"):
        s += 3
    if len(t) > 120:
        s += 1
    if len(t) > 250:
        s += 1
    for w in ("ахах", "ору", "ржу", "прикол", "смешно", "забавн"):
        if w in t:
            s += 3
    return s


def fmt(dt: datetime | None) -> str:
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK).strftime("%d.%m.%Y %H:%M МСК")


def top_skus(limit: int = 40) -> list[tuple[int, str | None]]:
    with engine.connect() as c:
        rows = c.execute(
            text(
                """
            SELECT ozon_sku::bigint AS sku,
                   max(supplier_article) AS art,
                   count(*) AS n
            FROM ozon_returns
            WHERE ozon_sku IS NOT NULL AND ozon_sku::text ~ '^[0-9]+$'
            GROUP BY 1
            ORDER BY n DESC
            LIMIT :lim
            """
            ),
            {"lim": limit},
        ).mappings().all()
        chat = c.execute(
            text(
                """
            SELECT context_sku::bigint AS sku,
                   max(context_article) AS art,
                   count(*) AS n
            FROM ozon_chats
            WHERE context_sku IS NOT NULL AND context_sku::text ~ '^[0-9]+$'
            GROUP BY 1
            ORDER BY n DESC
            LIMIT 30
            """
            )
        ).mappings().all()
    by: dict[int, tuple[int, str | None]] = {}
    for r in list(rows) + list(chat):
        sku = int(r["sku"])
        prev = by.get(sku)
        n = int(r["n"] or 0)
        art = r["art"]
        if not prev or n > prev[0]:
            by[sku] = (n, art)
    ranked = sorted(by.items(), key=lambda x: -x[1][0])[:limit]
    return [(sku, art) for sku, (_, art) in ranked]


def fetch_reviews_composer(sku: int, pages: int = 3) -> list[dict[str, Any]]:
    """Публичная страница отзывов через composer-api."""
    out: list[dict[str, Any]] = []
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": UA,
            "Accept": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
    )
    for page in range(1, pages + 1):
        url = (
            "https://www.ozon.ru/api/composer-api.bx/page/json/v2"
            f"?url=/product/{sku}/reviews/?page={page}&sort=created_at_desc"
        )
        try:
            r = session.get(url, timeout=25)
        except Exception as e:
            print(f"  fetch err sku={sku} page={page}: {e}")
            break
        if r.status_code != 200:
            print(f"  http {r.status_code} sku={sku} page={page}")
            break
        try:
            data = r.json()
        except Exception:
            print(f"  bad json sku={sku}")
            break
        found = _extract_from_composer(data, sku)
        if not found:
            # fallback: dump keys once
            if page == 1 and not out:
                print(f"  no reviews parsed sku={sku}; top keys={list(data.keys())[:12]}")
            break
        out.extend(found)
        time.sleep(0.35)
    return out


def _extract_from_composer(data: dict, sku: int) -> list[dict[str, Any]]:
    widget_states = data.get("widgetStates") or {}
    reviews: list[dict[str, Any]] = []
    for key, raw in widget_states.items():
        if "review" not in key.lower() and "comment" not in key.lower():
            continue
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        reviews.extend(_walk_reviews(payload, sku))
    # sometimes reviews live in seo/layout too — walk whole tree lightly
    if not reviews:
        reviews.extend(_walk_reviews(data, sku))
    # dedupe by id/text
    seen = set()
    uniq = []
    for r in reviews:
        key = r.get("id") or (r.get("text") or "")[:80]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def _walk_reviews(obj: Any, sku: int, depth: int = 0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if depth > 12:
        return out
    if isinstance(obj, dict):
        # heuristic: review-like node
        text_body = (
            obj.get("text")
            or obj.get("content")
            or obj.get("comment")
            or obj.get("reviewText")
            or ""
        )
        if isinstance(text_body, dict):
            text_body = (
                text_body.get("text")
                or text_body.get("overall")
                or text_body.get("content")
                or ""
            )
        rating = obj.get("rating") or obj.get("score") or obj.get("mark")
        rid = obj.get("uuid") or obj.get("id") or obj.get("reviewId") or obj.get("review_id")
        created = (
            obj.get("createdAt")
            or obj.get("created_at")
            or obj.get("publishedAt")
            or obj.get("date")
            or obj.get("published_at")
        )
        author = ""
        if isinstance(obj.get("author"), dict):
            author = obj["author"].get("firstName") or obj["author"].get("name") or ""
        elif isinstance(obj.get("authorName"), str):
            author = obj["authorName"]

        looks_like = (
            isinstance(text_body, str)
            and len(text_body) >= 20
            and rating is not None
            and (
                "review" in str(obj.get("cellTrackingInfo") or "").lower()
                or rid is not None
                or obj.get("isReview") is True
                or ("pros" in obj or "cons" in obj or "advantages" in obj)
            )
        )
        # looser: long text + rating 1-5
        if not looks_like and isinstance(text_body, str) and len(text_body) >= 40:
            try:
                rt = int(rating)
                looks_like = 1 <= rt <= 5 and (
                    "created" in json.dumps({k: 1 for k in obj.keys()}).lower()
                    or "author" in obj
                    or "photos" in obj
                    or "likes" in obj
                )
            except Exception:
                looks_like = False

        if looks_like:
            pros = obj.get("pros") or obj.get("advantages") or ""
            cons = obj.get("cons") or obj.get("disadvantages") or ""
            if isinstance(pros, list):
                pros = "; ".join(map(str, pros))
            if isinstance(cons, list):
                cons = "; ".join(map(str, cons))
            body = str(text_body).strip()
            if pros:
                body += f"\n+ {pros}"
            if cons:
                body += f"\n- {cons}"
            out.append(
                {
                    "id": str(rid) if rid else None,
                    "sku": sku,
                    "rating": int(rating) if str(rating).isdigit() else rating,
                    "text": body,
                    "created": created,
                    "author": author,
                }
            )
        for v in obj.values():
            out.extend(_walk_reviews(v, sku, depth + 1))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_walk_reviews(v, sku, depth + 1))
    return out


def parse_created(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        # seconds or ms
        ts = float(val)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(val, str):
        s = val.strip()
        for fmt_s in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%d.%m.%Y",
        ):
            try:
                dt = datetime.strptime(s.replace("+00:00", "Z").replace("Z", ""), fmt_s.replace("Z", "").replace("%z", ""))
                return dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
        # ISO fallback
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", s)
            if m:
                d, mo, y = map(int, m.groups())
                return datetime(y, mo, d, tzinfo=MSK)
    return None


def main():
    skus = top_skus(35)
    print(f"top skus: {len(skus)}")
    all_revs: list[dict[str, Any]] = []
    art_by_sku = {sku: art for sku, art in skus}

    for i, (sku, art) in enumerate(skus, 1):
        print(f"[{i}/{len(skus)}] sku={sku} art={art}")
        revs = fetch_reviews_composer(sku, pages=2)
        print(f"  got {len(revs)}")
        for r in revs:
            r["article"] = art
            all_revs.append(r)
        time.sleep(0.25)

    print(f"\ntotal reviews pulled: {len(all_revs)}")
    scored = []
    for r in all_revs:
        sc = score(r.get("text") or "")
        if sc >= 3 and (r.get("rating") or 0) >= 4:
            scored.append((sc, r))
    scored.sort(key=lambda x: (-x[0], -len(x[1].get("text") or "")))

    seen = set()
    out = []
    for sc, r in scored:
        key = (r.get("text") or "")[:90].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((sc, r))
        if len(out) >= 25:
            break

    print(f"funny candidates: {len(out)}\n")
    for i, (sc, r) in enumerate(out, 1):
        body = (r.get("text") or "").strip()
        if len(body) > 450:
            body = body[:450] + "…"
        dt = parse_created(r.get("created"))
        sku = r["sku"]
        print(f"——— {i}. ★{r.get('rating')}  {r.get('article') or '—'}  score={sc}")
        print(f"date: {fmt(dt)}  raw={r.get('created')!r}")
        print(f"sku: {sku}  id: {r.get('id')}")
        print(f"url: https://www.ozon.ru/product/{sku}/reviews/")
        print(body)
        print()


if __name__ == "__main__":
    main()
