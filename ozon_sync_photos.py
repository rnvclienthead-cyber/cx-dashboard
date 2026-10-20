"""
Сбор фото отзывов Ozon: фильтр «С медиа» + detail API.
CDP Chrome :9222 должен быть открыт на seller.ozon.ru.

  ./venv/bin/python ozon_sync_photos.py --upload-vps
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
COMPANY_ID = "1060377"
CDP = "http://127.0.0.1:9222"
OUT = ROOT / "ozon_reviews_photos_only.json"

JS_LIST = """
async ({ lastReview, companyId, filter }) => {
  const body = {
    company_id: String(companyId),
    company_type: 'seller',
    last_review: lastReview || null,
    filter: filter,
    sort: { sort_by: 'PUBLISHED_AT', sort_direction: 'DESC' },
  };
  const r = await fetch('/api/v4/review/list', {
    method: 'POST', credentials: 'include',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify(body),
  });
  return { status: r.status, text: await r.text() };
}
"""

JS_DETAIL = """
async ({ uuid, companyId }) => {
  const body = { review_uuid: uuid, company_id: String(companyId), company_type: 'seller' };
  const r = await fetch('/api/v2/review/detail', {
    method: 'POST', credentials: 'include',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify(body),
  });
  return { status: r.status, text: await r.text() };
}
"""

# Рабочий фильтр кабинета: Фильтры → Контент → С медиа
MEDIA_FILTER = {
    "published_at": {},
    "interaction_status": ["ALL"],
    "by_content_2": ["BY_CONTENT_2_FLAG_WITH_MEDIA"],
    "awaiting_reply": False,
}


def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def capture_media_filter(page) -> dict:
    """Всегда используем проверенный фильтр «С медиа»."""
    return dict(MEDIA_FILTER)


def collect(page, media_filter: dict, max_pages: int = 5000):
    rows = []
    seen = set()
    last = None
    list_media = 0
    for page_i in range(1, max_pages + 1):
        for attempt in range(1, 4):
            try:
                raw = page.evaluate(
                    JS_LIST,
                    {"lastReview": last, "companyId": COMPANY_ID, "filter": media_filter},
                )
                break
            except Exception as e:
                print(f"list evaluate fail attempt {attempt}: {e}")
                time.sleep(2 * attempt)
                page.goto("https://seller.ozon.ru/app/reviews", wait_until="domcontentloaded")
                time.sleep(3)
                raw = {"status": 0, "text": ""}
        if raw.get("status") != 200:
            print("LIST FAIL", raw.get("status"), str(raw.get("text"))[:200])
            break
        data = json.loads(raw["text"])
        items = data.get("result") or []
        if not items:
            break
        for it in items:
            uid = it.get("uuid")
            if not uid or uid in seen:
                continue
            seen.add(uid)
            pc = int(it.get("photos_count") or 0)
            vc = int(it.get("videos_count") or 0)
            if pc > 0 or vc > 0:
                list_media += 1
            prod = it.get("product") or {}
            rows.append(
                {
                    "review_id": uid,
                    "sku": int(prod["sku"]) if str(prod.get("sku") or "").isdigit() else None,
                    "supplier_article": prod.get("offer_id"),
                    "product_name": prod.get("title"),
                    "rating": it.get("rating"),
                    "review_text": (it.get("text") or "").strip() or None,
                    "pros": None,
                    "cons": None,
                    "photos": None,
                    "created_at": parse_dt(it.get("published_at")),
                    "_pc": pc,
                    "_vc": vc,
                }
            )
        print(
            f"media-list page {page_i}: +{len(items)} total={len(rows)} "
            f"flag_media={list_media} hasNext={data.get('hasNext')}"
        )
        if not data.get("hasNext"):
            break
        last = data.get("last_review")
        if not last:
            break
        time.sleep(0.25)

    # детали для всех из media-list (они и должны быть с медиа)
    ok = fail = empty = 0
    for i, row in enumerate(rows, 1):
        success = False
        for attempt in range(1, 6):
            try:
                raw = page.evaluate(
                    JS_DETAIL, {"uuid": row["review_id"], "companyId": COMPANY_ID}
                )
            except Exception as e:
                print(f"  detail eval err {row['review_id']}: {e}")
                time.sleep(2 * attempt)
                continue
            if raw["status"] == 200:
                detail = json.loads(raw["text"])
                urls = []
                for ph in detail.get("photos") or []:
                    if isinstance(ph, dict) and ph.get("url"):
                        urls.append(ph["url"])
                for v in detail.get("videos") or []:
                    if isinstance(v, dict):
                        u = v.get("url") or v.get("preview_url") or v.get("cover")
                        if u:
                            urls.append(u)
                text = detail.get("text") or {}
                if isinstance(text, dict):
                    c = (text.get("comment") or "").strip()
                    if c:
                        row["review_text"] = c[:2000]
                    p = (text.get("positive") or "").strip()
                    n = (text.get("negative") or "").strip()
                    if p:
                        row["pros"] = p[:500]
                    if n:
                        row["cons"] = n[:500]
                if urls:
                    row["photos"] = " ".join(urls)
                    ok += 1
                else:
                    empty += 1
                success = True
                break
            print(
                f"  detail {raw['status']} {row['review_id']} attempt {attempt}"
            )
            time.sleep(2.5 * attempt)
            if attempt in (2, 4):
                try:
                    page.goto(
                        "https://seller.ozon.ru/app/reviews",
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                    time.sleep(3)
                except Exception:
                    pass
        if not success:
            fail += 1
        if i % 20 == 0 or i == len(rows):
            print(f"detail {i}/{len(rows)} ok_photos={ok} empty={empty} fail={fail}")
        time.sleep(0.5)

    for r in rows:
        r.pop("_pc", None)
        r.pop("_vc", None)
    return rows, {"list": len(rows), "ok_photos": ok, "empty": empty, "fail": fail}


def upload_vps(path: Path):
    remote = "/tmp/ozon_reviews_photos_only.json"
    subprocess.check_call(["scp", str(path), f"root@72.56.240.22:{remote}"])
    script = r'''
import json
from datetime import datetime
from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv
load_dotenv("/root/cx-dashboard/.env")
url = os.getenv("DATABASE_URL_LOCAL") or "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"
engine = create_engine(url, pool_pre_ping=True)
with engine.begin() as conn:
    conn.execute(text("ALTER TABLE ozon_reviews ADD COLUMN IF NOT EXISTS photos TEXT"))
rows = json.load(open("/tmp/ozon_reviews_photos_only.json"))
n = 0
with engine.begin() as conn:
    for r in rows:
        ca = r.get("created_at")
        if isinstance(ca, str) and ca:
            try:
                ca = datetime.fromisoformat(ca.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                ca = None
        conn.execute(text("""
            INSERT INTO ozon_reviews (
                review_id, sku, supplier_article, product_name,
                rating, review_text, pros, cons, photos,
                created_at, synced_at
            ) VALUES (
                :review_id, :sku, :supplier_article, :product_name,
                :rating, :review_text, :pros, :cons, :photos,
                :created_at, NOW()
            )
            ON CONFLICT (review_id) DO UPDATE SET
                sku = COALESCE(EXCLUDED.sku, ozon_reviews.sku),
                supplier_article = COALESCE(EXCLUDED.supplier_article, ozon_reviews.supplier_article),
                product_name = COALESCE(EXCLUDED.product_name, ozon_reviews.product_name),
                rating = COALESCE(EXCLUDED.rating, ozon_reviews.rating),
                review_text = COALESCE(EXCLUDED.review_text, ozon_reviews.review_text),
                pros = COALESCE(EXCLUDED.pros, ozon_reviews.pros),
                cons = COALESCE(EXCLUDED.cons, ozon_reviews.cons),
                photos = COALESCE(EXCLUDED.photos, ozon_reviews.photos),
                created_at = COALESCE(EXCLUDED.created_at, ozon_reviews.created_at),
                synced_at = NOW()
        """), {
            "review_id": r["review_id"],
            "sku": r.get("sku"),
            "supplier_article": r.get("supplier_article"),
            "product_name": r.get("product_name"),
            "rating": r.get("rating"),
            "review_text": r.get("review_text"),
            "pros": r.get("pros"),
            "cons": r.get("cons"),
            "photos": r.get("photos"),
            "created_at": ca,
        })
        n += 1
print("upserted", n)
with engine.connect() as c:
    print("total", c.execute(text("select count(*) from ozon_reviews")).scalar())
    print("with_photos", c.execute(text("select count(*) from ozon_reviews where photos is not null and length(photos)>0")).scalar())
'''
    subprocess.check_call(
        [
            "ssh",
            "root@72.56.240.22",
            "PYTHONPATH=/root/cx-dashboard /root/cx-dashboard/venv/bin/python - <<'PY'\n"
            + script
            + "\nPY",
        ]
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload-vps", action="store_true")
    args = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        page = next(pg for pg in browser.contexts[0].pages if "seller.ozon.ru" in (pg.url or ""))
        media_filter = capture_media_filter(page)
        print("using filter", media_filter)
        # убедимся что страница в кабинете
        if "seller.ozon.ru" not in page.url:
            page.goto("https://seller.ozon.ru/app/reviews", wait_until="domcontentloaded")
            time.sleep(2)
        rows, stats = collect(page, media_filter)

    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    with_photos = sum(1 for r in rows if r.get("photos"))
    print(f"DONE stats={stats} with_photo_urls={with_photos} → {OUT}")
    if args.upload_vps:
        upload_vps(OUT)


if __name__ == "__main__":
    main()
