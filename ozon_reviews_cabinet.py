"""
Синхронизация отзывов Ozon через веб-кабинет (Playwright + сохранённая сессия).

Seller Review API без подписки даёт 403 — поэтому читаем кабинет как сотрудник.

Запуск:
  PYTHONPATH=/root/cx-dashboard /root/cx-dashboard/venv/bin/python3 \\
    /root/cx-dashboard/ozon_reviews_cabinet.py sync

Требует заранее: ozon_setup_session.py → ozon_session.json
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv(ROOT / ".env")

from backend.database import engine

log = logging.getLogger("ozon_reviews_cabinet")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SESSION_FILE = str(ROOT / "ozon_session.json")
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
REVIEWS_URL = "https://seller.ozon.ru/app/reviews"


def _log_db(job: str, status: str, message: str = "") -> None:
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO system_logs (action, status, details) VALUES (:a,:s,:d)"),
                {"a": job, "s": status, "d": (message or "")[:2000]},
            )
    except Exception:
        pass


def _parse_dt(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        # секунды или мс
        ts = float(val)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.utcfromtimestamp(ts)
        except Exception:
            return None
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%d.%m.%Y %H:%M",
        ):
            try:
                return datetime.strptime(s.replace("+00:00", "Z"), fmt.replace("%z", "Z") if s.endswith("Z") else fmt)
            except Exception:
                continue
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None
    return None


def _dig_reviews(obj: Any, out: list[dict]) -> None:
    """Рекурсивно ищем массивы отзывов в JSON ответах кабинета."""
    if isinstance(obj, dict):
        # эвристика: объект похож на отзыв
        keys = set(obj.keys())
        id_like = keys & {"uuid", "review_id", "id", "reviewUuid", "interactionId"}
        text_like = keys & {"text", "comment", "content", "review_text", "positive", "negative", "pros", "cons"}
        rating_like = keys & {"rating", "score", "stars", "mark"}
        if id_like and (text_like or rating_like):
            out.append(obj)
        for v in obj.values():
            _dig_reviews(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _dig_reviews(item, out)


def _normalize(raw: dict) -> dict | None:
    rid = (
        raw.get("uuid")
        or raw.get("review_id")
        or raw.get("reviewUuid")
        or raw.get("interactionId")
        or raw.get("id")
    )
    if rid is None:
        return None
    rid = str(rid)

    comment = raw.get("comment") or raw.get("content") or {}
    if isinstance(comment, dict):
        text_body = (
            comment.get("overall")
            or comment.get("text")
            or comment.get("body")
            or ""
        )
        pros = comment.get("positive") or comment.get("pros") or raw.get("pros") or ""
        cons = comment.get("negative") or comment.get("cons") or raw.get("cons") or ""
    else:
        text_body = str(comment or raw.get("text") or raw.get("review_text") or "")
        pros = raw.get("pros") or raw.get("positive") or ""
        cons = raw.get("cons") or raw.get("negative") or ""

    rating = raw.get("rating") or raw.get("score") or raw.get("stars") or raw.get("mark")
    try:
        rating = int(rating) if rating is not None else None
    except Exception:
        rating = None

    sku = raw.get("sku") or raw.get("item_id") or raw.get("product_id")
    if isinstance(sku, dict):
        sku = sku.get("sku") or sku.get("id")
    try:
        sku = int(sku) if sku is not None else None
    except Exception:
        sku = None

    product = raw.get("product") or raw.get("item") or {}
    if isinstance(product, dict):
        product_name = product.get("name") or product.get("title") or raw.get("product_name")
        art = (
            product.get("offer_id")
            or product.get("article")
            or product.get("supplier_article")
            or raw.get("supplier_article")
        )
        if sku is None:
            try:
                sku = int(product.get("sku") or product.get("id") or 0) or None
            except Exception:
                pass
    else:
        product_name = raw.get("product_name")
        art = raw.get("supplier_article") or raw.get("offer_id")

    created = _parse_dt(
        raw.get("created_at")
        or raw.get("published_at")
        or raw.get("createdAt")
        or raw.get("date")
        or raw.get("publishedAt")
    )

    answer = raw.get("answer") or raw.get("company_comment") or raw.get("reply") or {}
    answered_at = None
    answer_text = None
    if isinstance(answer, dict) and answer:
        answer_text = answer.get("text") or answer.get("comment") or answer.get("body")
        answered_at = _parse_dt(answer.get("created_at") or answer.get("published_at") or answer.get("createdAt"))
    elif isinstance(answer, str) and answer.strip():
        answer_text = answer

    return {
        "review_id": rid,
        "sku": sku,
        "supplier_article": (str(art)[:120] if art else None),
        "product_name": (str(product_name)[:500] if product_name else None),
        "rating": rating,
        "review_text": (text_body[:2000] if text_body else None),
        "pros": (str(pros)[:500] if pros else None),
        "cons": (str(cons)[:500] if cons else None),
        "created_at": created,
        "answered_at": answered_at,
        "answer_text": (str(answer_text)[:2000] if answer_text else None),
    }


def _upsert(rows: list[dict]) -> int:
    if not rows:
        return 0
    n = 0
    with engine.begin() as conn:
        for r in rows:
            conn.execute(
                text(
                    """
                    INSERT INTO ozon_reviews (
                        review_id, sku, supplier_article, product_name,
                        rating, review_text, pros, cons,
                        answered_at, answer_text, created_at, synced_at
                    ) VALUES (
                        :review_id, :sku, :supplier_article, :product_name,
                        :rating, :review_text, :pros, :cons,
                        :answered_at, :answer_text, :created_at, NOW()
                    )
                    ON CONFLICT (review_id) DO UPDATE SET
                        sku = COALESCE(EXCLUDED.sku, ozon_reviews.sku),
                        supplier_article = COALESCE(EXCLUDED.supplier_article, ozon_reviews.supplier_article),
                        product_name = COALESCE(EXCLUDED.product_name, ozon_reviews.product_name),
                        rating = COALESCE(EXCLUDED.rating, ozon_reviews.rating),
                        review_text = COALESCE(EXCLUDED.review_text, ozon_reviews.review_text),
                        pros = COALESCE(EXCLUDED.pros, ozon_reviews.pros),
                        cons = COALESCE(EXCLUDED.cons, ozon_reviews.cons),
                        answered_at = COALESCE(EXCLUDED.answered_at, ozon_reviews.answered_at),
                        answer_text = COALESCE(EXCLUDED.answer_text, ozon_reviews.answer_text),
                        created_at = COALESCE(EXCLUDED.created_at, ozon_reviews.created_at),
                        synced_at = NOW()
                    """
                ),
                r,
            )
            n += 1
    return n


def sync_reviews_via_cabinet() -> int:
    if not os.path.exists(SESSION_FILE):
        raise RuntimeError(
            f"Нет сессии {SESSION_FILE}. Сначала: python3 ozon_setup_session.py"
        )

    from playwright.sync_api import sync_playwright

    captured: list[Any] = []

    def on_response(resp) -> None:
        try:
            url = resp.url
            ct = (resp.headers.get("content-type") or "").lower()
            if "json" not in ct and "javascript" not in ct:
                return
            # интересуют ответы, похожие на отзывы / interactions
            ul = url.lower()
            if not any(
                x in ul
                for x in (
                    "review",
                    "feedback",
                    "rating",
                    "interaction",
                    "comment",
                    "seller",
                    "composite",
                    "rpc",
                    "graphql",
                )
            ):
                # всё равно пробуем крупные JSON — кабинет часто ходит на generic gateway
                if "ozon" not in ul:
                    return
            if resp.status != 200:
                return
            data = resp.json()
            captured.append({"url": url, "data": data})
        except Exception:
            return

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            storage_state=SESSION_FILE,
            user_agent=UA,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page.on("response", on_response)

        log.info("Открываем отзывы кабинета...")
        page.goto(REVIEWS_URL, wait_until="domcontentloaded", timeout=90000)
        time.sleep(8)

        title = page.title()
        url = page.url
        log.info("title=%r url=%s", title, url)

        if "Antibot" in title or "login" in url.lower() or "id.ozon" in url.lower():
            page.screenshot(path=str(ROOT / "ozon_reviews_need_login.png"), full_page=True)
            browser.close()
            raise RuntimeError(
                "Сессия протухла или антибот. Перезапустите ozon_setup_session.py"
            )

        # прокрутка — подтянуть ленту / дозапросы
        for _ in range(6):
            page.mouse.wheel(0, 2400)
            time.sleep(1.5)

        # обновим storage (продление cookie)
        context.storage_state(path=SESSION_FILE)
        browser.close()

    raw_items: list[dict] = []
    interesting_urls: list[str] = []
    for pack in captured:
        before = len(raw_items)
        _dig_reviews(pack["data"], raw_items)
        if len(raw_items) > before:
            interesting_urls.append(pack["url"])

    # дедуп по id
    seen = set()
    rows: list[dict] = []
    for item in raw_items:
        norm = _normalize(item)
        if not norm:
            continue
        if norm["review_id"] in seen:
            continue
        seen.add(norm["review_id"])
        rows.append(norm)

    # диагностика, если пусто
    if not rows:
        dump_path = ROOT / "ozon_reviews_capture.json"
        slim = [
            {"url": p["url"], "keys": list(p["data"].keys())[:30] if isinstance(p["data"], dict) else type(p["data"]).__name__}
            for p in captured[:80]
        ]
        dump_path.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
        log.warning("Отзывы не извлечены. URLs с JSON: %s. Дамп: %s", len(captured), dump_path)
        _log_db("ozon_sync_reviews_cabinet", "error", f"0 reviews; json_packets={len(captured)}")
        return 0

    n = _upsert(rows)
    log.info("Сохранено отзывов: %s (источники: %s)", n, len(interesting_urls))
    for u in interesting_urls[:8]:
        log.info("  src: %s", u)
    _log_db("ozon_sync_reviews_cabinet", "success", f"{n} отзывов")
    return n


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    if cmd == "sync":
        try:
            n = sync_reviews_via_cabinet()
            print(f"OK: {n}")
        except Exception as e:
            log.error("%s", e)
            _log_db("ozon_sync_reviews_cabinet", "error", str(e))
            sys.exit(1)
    else:
        print("Usage: ozon_reviews_cabinet.py sync")
        sys.exit(1)
