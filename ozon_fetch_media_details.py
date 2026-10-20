"""Досбор фото по ozon_media_list.json пакетами (resume). CDP :9222."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

COMPANY_ID = "1060377"
LIST_FILE = Path("ozon_media_list.json")
OUT = Path("ozon_reviews_photos_only.json")
BATCH = 8

JS_BATCH = """
async ({ uuids, companyId }) => {
  const out = [];
  for (const uuid of uuids) {
    try {
      const r = await fetch('/api/v2/review/detail', {
        method: 'POST', credentials: 'include',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify({ review_uuid: uuid, company_id: String(companyId), company_type: 'seller' }),
      });
      const text = await r.text();
      out.push({ uuid, status: r.status, text });
    } catch (e) {
      out.push({ uuid, status: 0, text: String(e) });
    }
  }
  return out;
}
"""


def pdt(s):
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def apply_detail(row, detail):
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
        if (text.get("comment") or "").strip():
            row["review_text"] = text["comment"].strip()[:2000]
        if (text.get("positive") or "").strip():
            row["pros"] = text["positive"].strip()[:500]
        if (text.get("negative") or "").strip():
            row["cons"] = text["negative"].strip()[:500]
    if urls:
        row["photos"] = " ".join(urls)
    if detail.get("rating") is not None:
        row["rating"] = detail.get("rating")
    if detail.get("published_at"):
        row["created_at"] = pdt(detail.get("published_at"))
    return bool(urls)


def upload(path: Path):
    remote = "/tmp/ozon_reviews_photos_only.json"
    subprocess.check_call(["scp", str(path), f"root@72.56.240.22:{remote}"])
    script = r"""
import json
from datetime import datetime
from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv
load_dotenv('/root/cx-dashboard/.env')
url=os.getenv('DATABASE_URL_LOCAL') or 'postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard'
engine=create_engine(url, pool_pre_ping=True)
rows=json.load(open('/tmp/ozon_reviews_photos_only.json'))
with engine.begin() as conn:
    conn.execute(text('ALTER TABLE ozon_reviews ADD COLUMN IF NOT EXISTS photos TEXT'))
    n=0
    for r in rows:
        ca=r.get('created_at')
        if isinstance(ca,str) and ca:
            try: ca=datetime.fromisoformat(ca.replace('Z','+00:00')).replace(tzinfo=None)
            except Exception: ca=None
        conn.execute(text('''
            INSERT INTO ozon_reviews (review_id, sku, supplier_article, product_name, rating, review_text, pros, cons, photos, created_at, synced_at)
            VALUES (:review_id,:sku,:supplier_article,:product_name,:rating,:review_text,:pros,:cons,:photos,:created_at,NOW())
            ON CONFLICT (review_id) DO UPDATE SET
              sku=COALESCE(EXCLUDED.sku, ozon_reviews.sku),
              supplier_article=COALESCE(EXCLUDED.supplier_article, ozon_reviews.supplier_article),
              product_name=COALESCE(EXCLUDED.product_name, ozon_reviews.product_name),
              rating=COALESCE(EXCLUDED.rating, ozon_reviews.rating),
              review_text=COALESCE(EXCLUDED.review_text, ozon_reviews.review_text),
              pros=COALESCE(EXCLUDED.pros, ozon_reviews.pros),
              cons=COALESCE(EXCLUDED.cons, ozon_reviews.cons),
              photos=COALESCE(EXCLUDED.photos, ozon_reviews.photos),
              created_at=COALESCE(EXCLUDED.created_at, ozon_reviews.created_at),
              synced_at=NOW()
        '''), {**{k:r.get(k) for k in ['review_id','sku','supplier_article','product_name','rating','review_text','pros','cons','photos']}, 'created_at': ca})
        n+=1
print('upserted', n)
with engine.connect() as c:
    print('total', c.execute(text('select count(*) from ozon_reviews')).scalar())
    print('with_photos', c.execute(text('select count(*) from ozon_reviews where photos is not null and length(photos)>0')).scalar())
"""
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
    do_upload = "--upload-vps" in sys.argv
    rows = json.loads(LIST_FILE.read_text())
    # merge previous progress
    if OUT.exists():
        for old in json.loads(OUT.read_text()):
            if old.get("photos"):
                for r in rows:
                    if r["review_id"] == old["review_id"]:
                        r["photos"] = old["photos"]
                        r["review_text"] = old.get("review_text") or r.get("review_text")
                        r["pros"] = old.get("pros")
                        r["cons"] = old.get("cons")
                        break

    need = [r for r in rows if not r.get("photos")]
    print(f"total={len(rows)} need_detail={len(need)} already={len(rows)-len(need)}")

    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next(pg for pg in b.contexts[0].pages if "seller.ozon.ru" in (pg.url or ""))
        if "reviews" not in page.url:
            page.goto("https://seller.ozon.ru/app/reviews", wait_until="domcontentloaded")
            time.sleep(2)

        ok = fail = empty = 0
        for i in range(0, len(need), BATCH):
            chunk = need[i : i + BATCH]
            uuids = [r["review_id"] for r in chunk]
            for attempt in range(1, 5):
                try:
                    results = page.evaluate(
                        JS_BATCH, {"uuids": uuids, "companyId": COMPANY_ID}
                    )
                    break
                except Exception as e:
                    print(f"batch eval err attempt {attempt}: {e}")
                    time.sleep(2 * attempt)
                    try:
                        page.goto(
                            "https://seller.ozon.ru/app/reviews",
                            wait_until="domcontentloaded",
                        )
                        time.sleep(3)
                    except Exception:
                        pass
                    results = []
            by_id = {r["review_id"]: r for r in chunk}
            for item in results or []:
                row = by_id.get(item["uuid"])
                if not row:
                    continue
                if item["status"] != 200:
                    fail += 1
                    print(f"  fail {item['uuid']}: {item['status']}")
                    continue
                try:
                    detail = json.loads(item["text"])
                except Exception:
                    fail += 1
                    continue
                if apply_detail(row, detail):
                    ok += 1
                else:
                    empty += 1
            done = i + len(chunk)
            if done % 80 == 0 or done >= len(need):
                # write full rows state
                OUT.write_text(
                    json.dumps(rows, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                have = sum(1 for r in rows if r.get("photos"))
                print(
                    f"progress {done}/{len(need)} batch_ok~{ok} empty={empty} fail={fail} have_photos={have}"
                )
            time.sleep(0.35)

    OUT.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    have = sum(1 for r in rows if r.get("photos"))
    print(f"DONE have_photos={have}/{len(rows)}")
    if do_upload:
        upload(OUT)


if __name__ == "__main__":
    main()
