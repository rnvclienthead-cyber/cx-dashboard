"""
Синк отзывов Ozon (+фото) через открытый Chrome CDP :9222.

Список:  POST /api/v4/review/list
Детали/фото: POST /api/v2/review/detail  (только если photos_count|videos_count > 0)

  ~/start-ozon-bot-chrome.sh   # войти, открыть Отзывы
  ./venv/bin/python ozon_sync_cdp.py --upload-vps
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

CDP = os.getenv("OZON_BOT_CDP", "http://127.0.0.1:9222")
COMPANY_ID = (os.getenv("OZON_CLIENT_ID") or "1060377").strip()
ROWS_FILE = ROOT / "ozon_reviews_rows.json"

JS_LIST = """
async ({ lastReview, companyId }) => {
  const body = {
    company_id: String(companyId),
    company_type: 'seller',
    last_review: lastReview || null,
    filter: { published_at: {}, interaction_status: ['ALL'] },
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


def parse_dt(s):
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def normalize_list_item(item: dict) -> dict | None:
    rid = item.get("uuid")
    if not rid:
        return None
    prod = item.get("product") or {}
    sku = prod.get("sku")
    try:
        sku = int(sku) if sku is not None else None
    except Exception:
        sku = None
    text = (item.get("text") or "").strip() or None
    return {
        "review_id": str(rid),
        "sku": sku,
        "supplier_article": prod.get("offer_id") or None,
        "product_name": prod.get("title") or None,
        "rating": item.get("rating"),
        "review_text": text[:2000] if text else None,
        "pros": None,
        "cons": None,
        "photos": None,
        "created_at": parse_dt(item.get("published_at")),
        "answered_at": None,
        "answer_text": None,
        "_photos_count": int(item.get("photos_count") or 0),
        "_videos_count": int(item.get("videos_count") or 0),
    }


def apply_detail(row: dict, detail: dict) -> dict:
    text = detail.get("text") or {}
    if isinstance(text, dict):
        comment = (text.get("comment") or "").strip() or None
        pros = (text.get("positive") or "").strip() or None
        cons = (text.get("negative") or "").strip() or None
        if comment:
            row["review_text"] = comment[:2000]
        if pros:
            row["pros"] = pros[:500]
        if cons:
            row["cons"] = cons[:500]
    photos = detail.get("photos") or []
    urls = []
    for p in photos:
        if isinstance(p, dict) and p.get("url"):
            urls.append(p["url"])
        elif isinstance(p, str) and p.startswith("http"):
            urls.append(p)
    for v in detail.get("videos") or []:
        if isinstance(v, dict):
            u = v.get("url") or v.get("preview_url") or v.get("cover")
            if u:
                urls.append(u)
    if urls:
        row["photos"] = " ".join(urls)
    if detail.get("rating") is not None:
        row["rating"] = detail.get("rating")
    if detail.get("published_at"):
        row["created_at"] = parse_dt(detail.get("published_at"))
    return row


def collect(max_pages: int = 500, detail_sleep: float = 0.25) -> list[dict]:
    from playwright.sync_api import sync_playwright

    rows: list[dict] = []
    seen: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        context = browser.contexts[0]
        page = next(
            (pg for pg in context.pages if "seller.ozon.ru" in (pg.url or "")),
            None,
        )
        if page is None:
            raise RuntimeError("Нет вкладки seller.ozon.ru — откройте кабинет в Chrome")

        if "/app/reviews" not in page.url:
            page.goto(
                "https://seller.ozon.ru/app/reviews",
                wait_until="domcontentloaded",
                timeout=90000,
            )
            time.sleep(3)

        last = None
        for page_i in range(1, max_pages + 1):
            raw = page.evaluate(JS_LIST, {"lastReview": last, "companyId": COMPANY_ID})
            if raw["status"] != 200:
                raise RuntimeError(f"list HTTP {raw['status']}: {raw['text'][:400]}")
            data = json.loads(raw["text"])
            items = data.get("result") or []
            if not items:
                break
            for it in items:
                n = normalize_list_item(it)
                if not n or n["review_id"] in seen:
                    continue
                seen.add(n["review_id"])
                rows.append(n)
            print(
                f"list page {page_i}: +{len(items)} total={len(rows)} hasNext={data.get('hasNext')}"
            )
            if not data.get("hasNext"):
                break
            last = data.get("last_review")
            if not last:
                break
            time.sleep(0.3)

        need_detail = [
            r for r in rows if (r.get("_photos_count") or 0) > 0 or (r.get("_videos_count") or 0) > 0
        ]
        print(f"детализация для медиа: {len(need_detail)}")
        ok = 0
        fail = 0
        for i, row in enumerate(need_detail, 1):
            raw = page.evaluate(
                JS_DETAIL, {"uuid": row["review_id"], "companyId": COMPANY_ID}
            )
            if raw["status"] != 200:
                fail += 1
                print(f"  detail fail {row['review_id']}: {raw['status']} {raw['text'][:120]}")
                continue
            detail = json.loads(raw["text"])
            apply_detail(row, detail)
            ok += 1
            if i % 20 == 0 or i == len(need_detail):
                print(f"  detail {i}/{len(need_detail)} ok={ok} fail={fail}")
            time.sleep(detail_sleep)

        context.storage_state(path=str(ROOT / "ozon_session.json"))

    for r in rows:
        r.pop("_photos_count", None)
        r.pop("_videos_count", None)
    return rows


def upload_vps(rows_path: Path) -> None:
    remote = "/tmp/ozon_reviews_rows.json"
    subprocess.check_call(["scp", str(rows_path), f"root@72.56.240.22:{remote}"])
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
rows = json.load(open("/tmp/ozon_reviews_rows.json"))
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
                answered_at, answer_text, created_at, synced_at
            ) VALUES (
                :review_id, :sku, :supplier_article, :product_name,
                :rating, :review_text, :pros, :cons, :photos,
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
            "answered_at": None,
            "answer_text": None,
            "created_at": ca,
        })
        n += 1
print("upserted", n)
with engine.connect() as c:
    print("total", c.execute(text("select count(*) from ozon_reviews")).scalar())
    print("with_photos", c.execute(text("select count(*) from ozon_reviews where photos is not null and photos<>''")).scalar())
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload-vps", action="store_true")
    ap.add_argument("--max-pages", type=int, default=500)
    args = ap.parse_args()

    print(f"CDP={CDP} companyId={COMPANY_ID}")
    rows = collect(max_pages=args.max_pages)
    ROWS_FILE.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    with_text = sum(1 for r in rows if r.get("review_text"))
    with_photos = sum(1 for r in rows if r.get("photos"))
    print(f"OK: {len(rows)} отзывов, текст={with_text}, фото={with_photos} → {ROWS_FILE}")
    if args.upload_vps:
        upload_vps(ROWS_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
