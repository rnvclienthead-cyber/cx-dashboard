"""
Возвраты Ozon через кабинет (composer page JSON).

Seller API не отдаёт комментарий/фото покупателя.
Берём карточку: /api/composer-api.bx/page/json/v2?url=/app/returns/details/common/{id}
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
VPS = os.getenv("OZON_BOT_VPS", "root@72.56.240.22")
OUT = ROOT / "ozon_returns_audit.json"
ENRICH_FILE = ROOT / "ozon_returns_enrich.json"
ENRICH_LIMIT = int(os.getenv("OZON_BOT_RETURNS_ENRICH_LIMIT", "25"))

JS_CARD = """
async (returnId) => {
  const path = '/app/returns/details/common/' + returnId + '?companyMode=seller';
  const url = '/api/composer-api.bx/page/json/v2?url=' + encodeURIComponent(path);
  try {
    const r = await fetch(url, { credentials: 'include' });
    const text = await r.text();
    return { status: r.status, text };
  } catch (e) {
    return { status: 0, text: String(e) };
  }
}
"""


def _clean_photo_urls(urls: list[str]) -> list[str]:
    best: dict[str, str] = {}
    for raw in urls:
        u = html.unescape(str(raw)).strip()
        u = u.replace("&amp;", "&").split()[0].rstrip(".,;)")
        if "rms-api-images" not in u:
            continue
        m = re.search(r"/([0-9a-f-]{36})\.jpg", u, re.I)
        key = m.group(1) if m else u.split("?")[0]
        if "/wc300/" in u:
            u = u.replace("/wc300/", "/wc600/")
        # берём самый длинный URL (с полной подписью)
        prev = best.get(key)
        if prev is None or len(u) > len(prev):
            best[key] = u
    return list(best.values())


def parse_card_payload(raw_text: str) -> dict:
    """Достаёт комментарий и фото из composer JSON."""
    out = {"buyer_comment": None, "photos": [], "return_reason": None}
    try:
        data = json.loads(raw_text)
    except Exception:
        return out
    states = data.get("widgetStates") or {}
    html_blob = ""
    for v in states.values():
        obj = v
        if isinstance(v, str):
            try:
                obj = json.loads(v)
            except Exception:
                html_blob += v
                continue
        if isinstance(obj, dict) and obj.get("html"):
            html_blob += obj["html"]
    html_blob = (
        html.unescape(html_blob)
        .replace("\\u002F", "/")
        .replace("\\/", "/")
        .replace("\\u0026", "&")
        .replace("&amp;", "&")
    )

    imgs = re.findall(
        r'https://cdn1\.ozonusercontent\.com/s3/rms-api-images[^"\'\s<>]+',
        html_blob,
        re.I,
    )
    # иногда подпись режется HTML-атрибутом — добираем по uuid
    for uuid in re.findall(r"/([0-9a-f-]{36})\.jpg", html_blob, re.I):
        ms = re.findall(
            rf'https://cdn1\.ozonusercontent\.com/s3/rms-api-images[^"\'\s<>]*{uuid}\.jpg[^"\'\s<>]*',
            html_blob,
            re.I,
        )
        imgs.extend(ms)
    out["photos"] = _clean_photo_urls(imgs)

    # тексты из HTML
    texts = [
        html.unescape(t).strip()
        for t in re.findall(r">([^<]{3,500})<", html_blob)
    ]
    texts = [t for t in texts if t]

    def after_label(label: str) -> str | None:
        for i, t in enumerate(texts):
            if t.strip().lower() == label.lower() and i + 1 < len(texts):
                nxt = texts[i + 1].strip()
                if nxt.lower() in (
                    "информация о возврате",
                    "помощь",
                    "история заявки",
                    "причина возврата",
                    "комментарий покупателя",
                ):
                    return None
                return nxt
        return None

    out["return_reason"] = after_label("Причина возврата")
    out["buyer_comment"] = after_label("Комментарий покупателя")

    # fallback: regex по плоскому тексту
    if not out["buyer_comment"]:
        m = re.search(
            r"Комментарий покупателя\s*(.+?)\s*(?:Информация о возврате|Помощь|История|$)",
            "\n".join(texts),
            re.S | re.I,
        )
        if m:
            out["buyer_comment"] = re.sub(r"\s+", " ", m.group(1)).strip()[:2000] or None
    return out


def fetch_ids_to_enrich(limit: int = 25) -> list[str]:
    remote = f"""
PYTHONPATH=/root/cx-dashboard /root/cx-dashboard/venv/bin/python - <<'PY'
import json, os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
load_dotenv('/root/cx-dashboard/.env')
url=os.getenv('DATABASE_URL_LOCAL') or 'postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard'
e=create_engine(url, pool_pre_ping=True)
lim={int(limit)}
with e.begin() as c:
    c.execute(text('ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS buyer_comment TEXT'))
    c.execute(text('ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS photos TEXT'))
    c.execute(text('ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS cabinet_synced_at TIMESTAMPTZ'))
    rows=c.execute(text('''
      SELECT return_id::text FROM ozon_returns
      WHERE (
          cabinet_synced_at IS NULL
          OR photos ILIKE '%ozonusercontent%'
        )
        AND (
          status_sys = 'OnSellerApproval'
          OR is_opened IS TRUE
          OR photos ILIKE '%ozonusercontent%'
        )
      ORDER BY
        CASE WHEN photos ILIKE '%ozonusercontent%' THEN 0
             WHEN status_sys = 'OnSellerApproval' THEN 1 ELSE 2 END,
        return_date DESC NULLS LAST
      LIMIT :lim
    '''), {{'lim': lim}}).fetchall()
print(json.dumps([r[0] for r in rows]))
PY
"""
    out = subprocess.check_output(["ssh", VPS, remote], text=True)
    line = out.strip().splitlines()[-1] if out.strip() else "[]"
    return json.loads(line)


def enrich_returns(page, ids: list[str] | None = None, limit: int | None = None) -> list[dict]:
    lim = ENRICH_LIMIT if limit is None else limit
    if ids is None:
        try:
            ids = fetch_ids_to_enrich(lim)
        except Exception as e:
            print(f"returns enrich ids ERROR: {e}", flush=True)
            return []
    if not ids:
        print("returns enrich: nothing", flush=True)
        return []
    rows: list[dict] = []
    print(f"returns enrich start n={len(ids)}", flush=True)
    for i, rid in enumerate(ids, 1):
        row = {
            "return_id": str(rid),
            "buyer_comment": None,
            "photos": None,
            "return_reason": None,
            "ok": False,
        }
        try:
            raw = page.evaluate(JS_CARD, str(rid))
            if raw.get("status") == 200:
                parsed = parse_card_payload(raw.get("text") or "")
                row["buyer_comment"] = parsed.get("buyer_comment")
                row["return_reason"] = parsed.get("return_reason")
                photos = parsed.get("photos") or []
                if photos:
                    row["photos"] = " ".join(photos)
                row["ok"] = True
            else:
                print(f"  fail {rid}: {raw.get('status')}", flush=True)
        except Exception as e:
            print(f"  err {rid}: {e}", flush=True)
        rows.append(row)
        if i % 5 == 0 or i == len(ids):
            print(f"  enrich {i}/{len(ids)}", flush=True)
        time.sleep(0.45)
    return rows


def upload_enrich(rows: list[dict]) -> None:
    if not rows:
        return
    ENRICH_FILE.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    remote = "/tmp/ozon_returns_enrich.json"
    subprocess.check_call(["scp", str(ENRICH_FILE), f"{VPS}:{remote}"])
    # Ссылки Ozon CDN живут ~30 мин (X-Amz-Expires=1800) — сразу качаем в /static/ozon_returns/
    script = r'''
import json, os, re, urllib.request
from pathlib import Path
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
load_dotenv("/root/cx-dashboard/.env")
url = os.getenv("DATABASE_URL_LOCAL") or "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"
engine = create_engine(url, pool_pre_ping=True)
rows = json.load(open("/tmp/ozon_returns_enrich.json"))
STATIC = Path("/root/cx-dashboard/static/ozon_returns")
STATIC.mkdir(parents=True, exist_ok=True)
os.chmod("/root/cx-dashboard/static", 0o711)
os.chmod(str(STATIC), 0o755)

def materialize(return_id: str, photos_str: str | None) -> str | None:
    if not photos_str:
        return None
    parts = str(photos_str).split()
    if parts and all(p.startswith("/static/") for p in parts):
        return " ".join(parts)
    out = []
    for i, u in enumerate(parts):
        if u.startswith("/static/"):
            out.append(u)
            continue
        if "ozonusercontent.com" not in u and "rms-api-images" not in u:
            continue
        m = re.search(r"/([0-9a-f-]{36})\.jpg", u, re.I)
        name = f"{return_id}_{m.group(1) if m else i}.jpg"
        dest = STATIC / name
        if not dest.exists() or dest.stat().st_size < 200:
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
                data = urllib.request.urlopen(req, timeout=40).read()
                if len(data) >= 200:
                    dest.write_bytes(data)
                    os.chmod(dest, 0o644)
                else:
                    print(f"dl tiny {return_id} {name} size={len(data)}")
                    continue
            except Exception as e:
                print(f"dl fail {return_id} {name}: {e}")
                continue
        if dest.exists() and dest.stat().st_size >= 200:
            out.append(f"/static/ozon_returns/{name}")
    return " ".join(out) if out else None

with_comment = with_photos = 0
with engine.begin() as conn:
    conn.execute(text("ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS buyer_comment TEXT"))
    conn.execute(text("ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS photos TEXT"))
    conn.execute(text("ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS cabinet_synced_at TIMESTAMPTZ"))
    for r in rows:
        local_photos = materialize(str(r["return_id"]), r.get("photos"))
        conn.execute(text("""
            UPDATE ozon_returns SET
              buyer_comment = COALESCE(NULLIF(trim(:buyer_comment), ''), buyer_comment),
              photos = COALESCE(NULLIF(:photos, ''), photos),
              return_reason = CASE
                WHEN :return_reason IS NOT NULL AND length(trim(:return_reason))>0
                  THEN COALESCE(NULLIF(trim(return_reason), ''), :return_reason)
                ELSE return_reason END,
              cabinet_synced_at = NOW(),
              synced_at = COALESCE(synced_at, NOW())
            WHERE return_id::text = :return_id
        """), {
            "return_id": str(r["return_id"]),
            "buyer_comment": r.get("buyer_comment"),
            "photos": local_photos,
            "return_reason": r.get("return_reason"),
        })
        if r.get("buyer_comment"):
            with_comment += 1
        if local_photos:
            with_photos += 1
print("returns_enrich", len(rows), "with_comment", with_comment, "with_photos", with_photos)
'''
    subprocess.check_call(
        [
            "ssh",
            VPS,
            "PYTHONPATH=/root/cx-dashboard /root/cx-dashboard/venv/bin/python - <<'PY'\n"
            + script
            + "\nPY",
        ]
    )


def audit_returns() -> dict:
    """Свежесть синка + дыры комментариев/фото."""
    remote = r"""
PYTHONPATH=/root/cx-dashboard /root/cx-dashboard/venv/bin/python - <<'PY'
import json, os
from datetime import datetime, timezone
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
load_dotenv('/root/cx-dashboard/.env')
url=os.getenv('DATABASE_URL_LOCAL') or 'postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard'
e=create_engine(url, pool_pre_ping=True)
report={"ts": datetime.now(timezone.utc).isoformat(), "gaps": []}
with e.connect() as c:
    for q in (
        "ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS buyer_comment TEXT",
        "ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS photos TEXT",
        "ALTER TABLE ozon_returns ADD COLUMN IF NOT EXISTS cabinet_synced_at TIMESTAMPTZ",
    ):
        c.execute(text(q))
    c.commit()
    report["total"]=c.execute(text("SELECT count(*) FROM ozon_returns")).scalar()
    report["opened"]=c.execute(text("SELECT count(*) FROM ozon_returns WHERE is_opened IS TRUE")).scalar()
    report["last_sync"]=str(c.execute(text("SELECT max(synced_at) FROM ozon_returns")).scalar())
    report["with_buyer_comment"]=c.execute(text(
        "SELECT count(*) FROM ozon_returns WHERE buyer_comment IS NOT NULL AND length(trim(buyer_comment))>0"
    )).scalar()
    report["with_photos"]=c.execute(text(
        "SELECT count(*) FROM ozon_returns WHERE photos IS NOT NULL AND length(photos)>0"
    )).scalar()
    report["cabinet_synced"]=c.execute(text(
        "SELECT count(*) FROM ozon_returns WHERE cabinet_synced_at IS NOT NULL"
    )).scalar()
    report["approval_pending_enrich"]=c.execute(text('''
        SELECT count(*) FROM ozon_returns
        WHERE status_sys='OnSellerApproval' AND cabinet_synced_at IS NULL
    ''')).scalar()
    try:
        row=c.execute(text('''
            SELECT status, details, created_at FROM system_logs
            WHERE action='ozon_sync_returns'
            ORDER BY created_at DESC LIMIT 1
        ''')).mappings().first()
        if row:
            report["last_job"]={"status":row["status"],"details":(row["details"] or "")[:300],
                                "created_at":str(row["created_at"])}
    except Exception as ex:
        report["last_job_error"]=str(ex)
print(json.dumps(report, ensure_ascii=False, default=str))
PY
"""
    out = subprocess.check_output(["ssh", VPS, remote], text=True)
    line = out.strip().splitlines()[-1] if out.strip() else "{}"
    report = json.loads(line)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"returns audit total={report.get('total')} comments={report.get('with_buyer_comment')} "
        f"photos={report.get('with_photos')} pending_approval_enrich={report.get('approval_pending_enrich')}",
        flush=True,
    )
    return report


def enrich_and_audit(page) -> int:
    rows = enrich_returns(page)
    upload_enrich(rows)
    audit_returns()
    return len(rows)


if __name__ == "__main__":
    import sys
    from playwright.sync_api import sync_playwright

    cdp = os.getenv("OZON_BOT_CDP", "http://127.0.0.1:9222")
    ids = [a for a in sys.argv[1:] if a.isdigit()]
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(cdp)
        page = next(pg for pg in b.contexts[0].pages if "seller.ozon.ru" in (pg.url or ""))
        if ids:
            rows = enrich_returns(page, ids=ids)
            upload_enrich(rows)
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            enrich_and_audit(page)
