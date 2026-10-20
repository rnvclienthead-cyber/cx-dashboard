"""Список «С медиа» + детали с фото. Resume-friendly. CDP :9222."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

COMPANY_ID = "1060377"
FILT = {
    "published_at": {},
    "interaction_status": ["ALL"],
    "by_content_2": ["BY_CONTENT_2_FLAG_WITH_MEDIA"],
    "awaiting_reply": False,
}
LIST_FILE = Path("ozon_media_list.json")
OUT = Path("ozon_reviews_photos_only.json")

JS_LIST = """
async ({ lastReview, companyId, filter }) => {
  const body={company_id:String(companyId),company_type:'seller',last_review:lastReview||null,filter,sort:{sort_by:'PUBLISHED_AT',sort_direction:'DESC'}};
  const r=await fetch('/api/v4/review/list',{method:'POST',credentials:'include',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
  return {status:r.status,text:await r.text()};
}
"""
JS_DETAIL = """
async ({ uuid, companyId }) => {
  const body={review_uuid:uuid,company_id:String(companyId),company_type:'seller'};
  const r=await fetch('/api/v2/review/detail',{method:'POST',credentials:'include',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
  return {status:r.status,text:await r.text()};
}
"""


def pdt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


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
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next(pg for pg in b.contexts[0].pages if "seller.ozon.ru" in (pg.url or ""))
        page.goto("https://seller.ozon.ru/app/reviews", wait_until="domcontentloaded")
        time.sleep(2)

        rows = []
        seen = set()
        last = None
        for pi in range(1, 10000):
            raw = page.evaluate(
                JS_LIST, {"lastReview": last, "companyId": COMPANY_ID, "filter": FILT}
            )
            if raw["status"] != 200:
                print("LIST FAIL", raw["status"], raw["text"][:150])
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
                prod = it.get("product") or {}
                rows.append(
                    {
                        "review_id": uid,
                        "sku": int(prod["sku"])
                        if str(prod.get("sku") or "").isdigit()
                        else None,
                        "supplier_article": prod.get("offer_id"),
                        "product_name": prod.get("title"),
                        "rating": it.get("rating"),
                        "review_text": (it.get("text") or "").strip() or None,
                        "pros": None,
                        "cons": None,
                        "photos": None,
                        "created_at": pdt(it.get("published_at")),
                    }
                )
            if pi % 50 == 0 or not data.get("hasNext"):
                print(f"list page={pi} total={len(rows)} hasNext={data.get('hasNext')}")
                LIST_FILE.write_text(
                    json.dumps(rows, ensure_ascii=False, default=str), encoding="utf-8"
                )
            if not data.get("hasNext"):
                break
            last = data.get("last_review")
            if not last:
                break
            time.sleep(0.2)
        LIST_FILE.write_text(
            json.dumps(rows, ensure_ascii=False, default=str), encoding="utf-8"
        )
        print(f"LIST DONE {len(rows)}")

        prev = {}
        if OUT.exists():
            for r in json.loads(OUT.read_text()):
                if r.get("photos"):
                    prev[r["review_id"]] = r
        print(f"resume photos={len(prev)}")

        ok = fail = empty = 0
        for i, row in enumerate(rows, 1):
            if row["review_id"] in prev:
                old = prev[row["review_id"]]
                row["photos"] = old["photos"]
                row["review_text"] = old.get("review_text") or row.get("review_text")
                row["pros"] = old.get("pros")
                row["cons"] = old.get("cons")
                ok += 1
                continue
            success = False
            for attempt in range(1, 6):
                try:
                    raw = page.evaluate(
                        JS_DETAIL,
                        {"uuid": row["review_id"], "companyId": COMPANY_ID},
                    )
                except Exception as e:
                    print("eval err", e)
                    time.sleep(2 * attempt)
                    continue
                if raw["status"] == 200:
                    d = json.loads(raw["text"])
                    urls = [
                        ph["url"]
                        for ph in (d.get("photos") or [])
                        if isinstance(ph, dict) and ph.get("url")
                    ]
                    for v in d.get("videos") or []:
                        if isinstance(v, dict):
                            u = v.get("url") or v.get("preview_url") or v.get("cover")
                            if u:
                                urls.append(u)
                    t = d.get("text") or {}
                    if isinstance(t, dict):
                        if (t.get("comment") or "").strip():
                            row["review_text"] = t["comment"].strip()[:2000]
                        if (t.get("positive") or "").strip():
                            row["pros"] = t["positive"].strip()[:500]
                        if (t.get("negative") or "").strip():
                            row["cons"] = t["negative"].strip()[:500]
                    if urls:
                        row["photos"] = " ".join(urls)
                        ok += 1
                    else:
                        empty += 1
                    success = True
                    break
                print(f"detail {raw['status']} {row['review_id']} try {attempt}")
                time.sleep(2.5 * attempt)
                if attempt in (2, 4):
                    try:
                        page.goto(
                            "https://seller.ozon.ru/app/reviews",
                            wait_until="domcontentloaded",
                        )
                        time.sleep(3)
                    except Exception:
                        pass
            if not success:
                fail += 1
            if i % 25 == 0 or i == len(rows):
                print(f"detail {i}/{len(rows)} ok={ok} empty={empty} fail={fail}")
                OUT.write_text(
                    json.dumps(rows, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
            time.sleep(0.45)

    OUT.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(
        f"DONE list={len(rows)} ok={ok} empty={empty} fail={fail} "
        f"with_urls={sum(1 for r in rows if r.get('photos'))}"
    )
    if do_upload:
        upload(OUT)


if __name__ == "__main__":
    main()
