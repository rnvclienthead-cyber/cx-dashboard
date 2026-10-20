"""
Регулярный тик бота Ozon (Mac + Chrome CDP :9222).

Собирает только отзывы, ждущие ответа (awaiting_reply), подтягивает фото,
заливает на VPS. Опционально отправляет очередь ответов из outbox.

  ~/start-ozon-bot-chrome.sh   # один раз: войти, открыть Отзывы
  ./venv/bin/python ozon_bot_tick.py
"""
from __future__ import annotations

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
VPS = os.getenv("OZON_BOT_VPS", "root@72.56.240.22")
ROWS_FILE = ROOT / "ozon_bot_tick_rows.json"
ENRICH_FILE = ROOT / "ozon_bot_enrich_rows.json"
LOG_DIR = Path(os.getenv("OZON_BOT_LOG_DIR", str(Path.home() / "Library/Logs")))
LOG_FILE = LOG_DIR / "ozon-bot-tick.log"
# за тик: дообогатить N отзывов без текста/фото (detail_synced_at IS NULL)
ENRICH_LIMIT = int(os.getenv("OZON_BOT_ENRICH_LIMIT", "120"))

JS_LIST = """
async ({ lastReview, companyId }) => {
  const body = {
    company_id: String(companyId),
    company_type: 'seller',
    last_review: lastReview || null,
    filter: {
      published_at: {},
      interaction_status: ['ALL'],
      awaiting_reply: true,
    },
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

# Поймано из кабинета 2026-08-20: POST /api/review/comment/create
# body: company_id, company_type=seller, text, review_uuid → {"result":true}
JS_COMMENT = """
async ({ uuid, companyId, text }) => {
  const path = '/api/review/comment/create';
  const body = {
    company_id: String(companyId),
    company_type: 'seller',
    text: String(text || ''),
    review_uuid: uuid,
  };
  try {
    const r = await fetch(path, {
      method: 'POST', credentials: 'include',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify(body),
    });
    const t = await r.text();
    return { status: r.status, text: t, path };
  } catch (e) {
    return { status: 0, text: String(e), path };
  }
}
"""


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _is_auth_block(err: object) -> bool:
    s = str(err).lower()
    return any(
        x in s
        for x in ("403", "401", "unauthenticated", "unauthorized", "forbidden")
    )


PAUSE_FILE = Path(os.getenv("OZON_BOT_PAUSE_FILE", "/var/log/ozon-bot/pause_until"))


def _pause_until_morning() -> None:
    """Ставит паузу до следующего утра (06:00 UTC = 09:00 МСК)."""
    now = datetime.utcnow()
    # следующее утро 06:00 UTC
    resume = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now.hour >= 6:
        from datetime import timedelta

        resume = resume + timedelta(days=1)
    try:
        PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
        PAUSE_FILE.write_text(resume.isoformat(timespec="seconds") + "Z", encoding="utf-8")
        log(f"PAUSE until morning set resume_at={resume.isoformat()}Z")
    except Exception as e:
        log(f"PAUSE write ERROR: {e}")


def _check_morning_pause() -> bool:
    """True = тик нужно пропустить (пауза после 403)."""
    if os.getenv("OZON_BOT_FORCE") == "1":
        return False
    if not PAUSE_FILE.exists():
        return False
    try:
        raw = PAUSE_FILE.read_text(encoding="utf-8").strip().rstrip("Z")
        resume = datetime.fromisoformat(raw)
        now = datetime.utcnow()
        if now < resume:
            log(f"SKIP pause until morning (resume_at={resume.isoformat()}Z)")
            return True
        PAUSE_FILE.unlink(missing_ok=True)
        log("PAUSE cleared — morning resume")
    except Exception as e:
        log(f"PAUSE read ERROR: {e}")
        try:
            PAUSE_FILE.unlink(missing_ok=True)
        except Exception:
            pass
    return False


def _clear_pause() -> None:
    try:
        if PAUSE_FILE.exists():
            PAUSE_FILE.unlink()
            log("PAUSE cleared after successful collect")
    except Exception:
        pass


def alert_ozon_auth_fail(where: str, err: object) -> None:
    """Срочный TG-алерт: кабинетный бот Ozon получил 401/403 (нужен повторный вход)."""
    if not _is_auth_block(err):
        return
    # антиспам: не чаще раза в час
    stamp = Path(os.getenv("OZON_BOT_ALERT_STAMP", "/var/log/ozon-bot/last_auth_alert"))
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        if stamp.exists():
            age = time.time() - stamp.stat().st_mtime
            if age < 3600:
                log(f"auth alert skipped (cooldown {int(age)}s)")
                return
    except Exception:
        pass

    # подхватить .env ещё раз (на случай если модуль алерта импортирован рано)
    try:
        load_dotenv(ROOT / ".env", override=False)
    except Exception:
        pass

    msg = (
        "🚨 Ozon бот на VPS: ошибка доступа (403/401)\n"
        f"Где: {where}\n"
        f"Ошибка: {str(err)[:280]}\n"
        "Поставил паузу до утра (09:00 МСК), чтобы не долбить кабинет.\n"
        "Если утром снова 403 — войди через VNC:\n"
        "ssh -L 5901:127.0.0.1:5901 root@72.56.240.22 → open vnc://127.0.0.1:5901"
    )
    chat = (os.getenv("TG_ALERT_CHAT_ID") or "").strip()
    worker = os.getenv(
        "CF_WORKER_URL",
        "https://lingering-recipe-b1b3.mursalimov.workers.dev",
    )
    sent = False

    # 1) напрямую через worker + chat из env (надёжнее, чем кэш модуля)
    if chat:
        try:
            import requests

            r = requests.post(
                worker, json={"chat_id": chat, "text": msg}, timeout=15
            )
            sent = bool(r.ok)
            if not sent:
                log(f"auth alert worker HTTP {r.status_code}: {r.text[:160]}")
        except Exception as e:
            log(f"auth alert worker ERROR: {e}")

    # 2) запасной путь — cx_alert
    if not sent:
        try:
            from backend import cx_alert

            cx_alert.TG_CHAT_ID = chat or cx_alert.TG_CHAT_ID
            sent = bool(cx_alert.send_tg(msg))
        except Exception as e:
            log(f"auth alert cx_alert ERROR: {e}")

    if sent:
        try:
            stamp.write_text(datetime.now().isoformat(), encoding="utf-8")
        except Exception:
            pass
        log("auth alert SENT")
    else:
        log("auth alert FAILED to send")


def parse_dt(s):
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def normalize(item: dict) -> dict | None:
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
    status = item.get("interaction_status")
    comments = int(item.get("comments_count") or 0)
    answered = status == "PROCESSED" or comments > 0
    urls = []
    for p in item.get("photos") or []:
        if isinstance(p, dict) and p.get("url"):
            urls.append(p["url"])
        elif isinstance(p, str) and p.strip():
            urls.append(p.strip())
    photos = " ".join(urls) if urls else None
    return {
        "review_id": str(rid),
        "sku": sku,
        "supplier_article": prod.get("offer_id") or None,
        "product_name": prod.get("title") or None,
        "rating": item.get("rating"),
        "review_text": text[:2000] if text else None,
        "pros": None,
        "cons": None,
        "photos": photos,
        "created_at": parse_dt(item.get("published_at")),
        "answered_at": datetime.utcnow() if answered else None,
        "answer_text": None,
        "interaction_status": status,
        "comments_count": comments,
        "_photos_count": int(item.get("photos_count") or 0),
        "_videos_count": int(item.get("videos_count") or 0),
    }


def apply_detail(row: dict, detail: dict) -> None:
    text = detail.get("text") or {}
    if isinstance(text, dict):
        comment = (text.get("comment") or "").strip()
        pros = (text.get("positive") or "").strip()
        cons = (text.get("negative") or "").strip()
        if comment:
            row["review_text"] = comment[:2000]
        if pros:
            row["pros"] = pros[:500]
        if cons:
            row["cons"] = cons[:500]
    urls = []
    for p in detail.get("photos") or []:
        if isinstance(p, dict) and p.get("url"):
            urls.append(p["url"])
    for v in detail.get("videos") or []:
        if isinstance(v, dict):
            u = v.get("url") or v.get("preview_url") or v.get("cover")
            if u:
                urls.append(u)
    if urls:
        row["photos"] = " ".join(urls)
    status = detail.get("interaction_status") or row.get("interaction_status")
    if status == "PROCESSED":
        row["answered_at"] = row.get("answered_at") or datetime.utcnow()


def cdp_ready() -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(CDP + "/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def collect(max_pages: int = 100, page=None) -> list[dict]:
    from playwright.sync_api import sync_playwright

    rows: list[dict] = []
    seen: set[str] = set()

    def _run(page):
        nonlocal rows, seen
        if page is None:
            raise RuntimeError("Нет вкладки seller.ozon.ru")
        if "/app/reviews" not in (page.url or ""):
            page.goto(
                "https://seller.ozon.ru/app/reviews",
                wait_until="domcontentloaded",
                timeout=90000,
            )
            time.sleep(2)

        last = None
        for page_i in range(1, max_pages + 1):
            raw = page.evaluate(JS_LIST, {"lastReview": last, "companyId": COMPANY_ID})
            if raw["status"] != 200:
                raise RuntimeError(f"list HTTP {raw['status']}: {raw['text'][:300]}")
            data = json.loads(raw["text"])
            items = data.get("result") or []
            if not items:
                break
            for it in items:
                n = normalize(it)
                if not n or n["review_id"] in seen:
                    continue
                seen.add(n["review_id"])
                rows.append(n)
            log(f"list page {page_i}: +{len(items)} total={len(rows)} hasNext={data.get('hasNext')}")
            if not data.get("hasNext"):
                break
            last = data.get("last_review")
            if not last:
                break
            time.sleep(0.25)

        # Фото/полный текст — через Seller API на VPS, не через /review/detail:
        # кабинетный detail помечает отзыв просмотренным в ЛК.

    if page is not None:
        _run(page)
    else:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP)
            pg = next(
                (x for x in browser.contexts[0].pages if "seller.ozon.ru" in (x.url or "")),
                None,
            )
            if pg is None:
                raise RuntimeError("Нет вкладки seller.ozon.ru")
            _run(pg)

    for r in rows:
        r.pop("_photos_count", None)
        r.pop("_videos_count", None)
        r.pop("interaction_status", None)
        r.pop("comments_count", None)
    return rows


def upload_vps(rows: list[dict]) -> None:
    ROWS_FILE.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    remote = "/tmp/ozon_bot_tick_rows.json"
    subprocess.check_call(["scp", str(ROWS_FILE), f"{VPS}:{remote}"])
    script = r'''
import json
from datetime import datetime
from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv
load_dotenv("/root/cx-dashboard/.env")
url = os.getenv("DATABASE_URL_LOCAL") or "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"
engine = create_engine(url, pool_pre_ping=True)
rows = json.load(open("/tmp/ozon_bot_tick_rows.json"))
ids = [r["review_id"] for r in rows]
with engine.begin() as conn:
    conn.execute(text("ALTER TABLE ozon_reviews ADD COLUMN IF NOT EXISTS photos TEXT"))
    n = 0
    for r in rows:
        ca = r.get("created_at")
        if isinstance(ca, str) and ca:
            try:
                ca = datetime.fromisoformat(ca.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                ca = None
        aa = r.get("answered_at")
        if isinstance(aa, str) and aa:
            try:
                aa = datetime.fromisoformat(aa.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                aa = None
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
                answered_at = CASE
                    WHEN EXCLUDED.answered_at IS NOT NULL THEN EXCLUDED.answered_at
                    ELSE ozon_reviews.answered_at
                END,
                answer_text = COALESCE(EXCLUDED.answer_text, ozon_reviews.answer_text),
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
            "answered_at": aa,
            "answer_text": r.get("answer_text"),
            "created_at": ca,
        })
        n += 1
print("upserted", n, "awaiting", len(ids))
with engine.connect() as c:
    print("unanswered", c.execute(text("select count(*) from ozon_reviews where answered_at is null")).scalar())
    print("with_photos", c.execute(text("select count(*) from ozon_reviews where photos is not null and length(photos)>0")).scalar())
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


def fetch_sparse_review_ids(limit: int = 120) -> list[str]:
    """ID отзывов без текста/фото, ещё не прогнанных через detail."""
    remote = (
        "PYTHONPATH=/root/cx-dashboard /root/cx-dashboard/venv/bin/python - <<'PY'\n"
        "import json, os\n"
        "from sqlalchemy import create_engine, text\n"
        "from dotenv import load_dotenv\n"
        "load_dotenv('/root/cx-dashboard/.env')\n"
        "url=os.getenv('DATABASE_URL_LOCAL') or "
        "'postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard'\n"
        "e=create_engine(url, pool_pre_ping=True)\n"
        f"lim={int(limit)}\n"
        "with e.begin() as c:\n"
        "  c.execute(text('ALTER TABLE ozon_reviews ADD COLUMN IF NOT EXISTS detail_synced_at TIMESTAMPTZ'))\n"
        "  rows=c.execute(text('''\n"
        "    SELECT review_id FROM ozon_reviews\n"
        "    WHERE detail_synced_at IS NULL\n"
        "      AND (\n"
        "        review_text IS NULL OR length(trim(review_text))=0\n"
        "        OR photos IS NULL OR length(photos)=0\n"
        "      )\n"
        "    ORDER BY\n"
        "      CASE WHEN answered_at IS NULL THEN 0 ELSE 1 END,\n"
        "      created_at DESC NULLS LAST\n"
        "    LIMIT :lim\n"
        "  '''), {'lim': lim}).fetchall()\n"
        "print(json.dumps([r[0] for r in rows]))\n"
        "PY\n"
    )
    out = subprocess.check_output(["ssh", VPS, remote], text=True)
    line = out.strip().splitlines()[-1] if out.strip() else "[]"
    return json.loads(line)


def enrich_sparse(page=None, limit: int | None = None) -> list[dict]:
    """Дотягивает текст/фото через Seller API на VPS — без отметки «просмотрено» в ЛК."""
    lim = ENRICH_LIMIT if limit is None else limit
    try:
        ids = fetch_sparse_review_ids(lim)
    except Exception as e:
        log(f"enrich fetch ids ERROR: {e}")
        return []
    if not ids:
        log("enrich: nothing to do")
        return []
    log(f"enrich start n={len(ids)} via seller API")
    payload = json.dumps(ids, ensure_ascii=False)
    remote_ids = "/tmp/ozon_bot_enrich_ids.json"
    Path("/tmp/ozon_bot_enrich_ids.json").write_text(payload, encoding="utf-8")
    subprocess.check_call(["scp", "/tmp/ozon_bot_enrich_ids.json", f"{VPS}:{remote_ids}"])
    script = r'''
import json, os
from dotenv import load_dotenv
load_dotenv("/root/cx-dashboard/.env")
from backend.ozon_client import review_info

ids = json.load(open("/tmp/ozon_bot_enrich_ids.json"))
rows = []
for rid in ids:
    row = {
        "review_id": rid, "review_text": None, "pros": None, "cons": None,
        "photos": None, "rating": None, "detail_ok": False,
    }
    try:
        detail = review_info(rid)
        text = detail.get("text") or {}
        if isinstance(text, dict):
            comment = (text.get("comment") or text.get("text") or "").strip()
            if comment:
                row["review_text"] = comment[:2000]
            row["pros"] = (text.get("positive") or "").strip() or None
            row["cons"] = (text.get("negative") or "").strip() or None
        elif isinstance(text, str) and text.strip():
            row["review_text"] = text.strip()[:2000]
        urls = []
        for p in detail.get("photos") or []:
            if isinstance(p, dict) and p.get("url"):
                urls.append(p["url"])
            elif isinstance(p, str) and p.strip():
                urls.append(p.strip())
        if urls:
            row["photos"] = " ".join(urls)
        if detail.get("rating") is not None:
            row["rating"] = detail.get("rating")
        row["detail_ok"] = True
    except Exception as e:
        row["error"] = str(e)[:200]
    rows.append(row)
print(json.dumps(rows, ensure_ascii=False, default=str))
'''
    out = subprocess.check_output(
        [
            "ssh",
            VPS,
            "cd /root/cx-dashboard && PYTHONPATH=/root/cx-dashboard "
            "./venv/bin/python - <<'PY'\n" + script + "\nPY",
        ],
        text=True,
    )
    line = out.strip().splitlines()[-1] if out.strip() else "[]"
    try:
        rows = json.loads(line)
    except Exception:
        log(f"enrich parse ERROR: {out[-400:]}")
        return []
    ok = sum(1 for r in rows if r.get("detail_ok"))
    log(f"enrich api ok={ok}/{len(rows)}")
    return rows


def upload_enrich(rows: list[dict]) -> None:
    if not rows:
        return
    ENRICH_FILE.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    remote = "/tmp/ozon_bot_enrich_rows.json"
    subprocess.check_call(["scp", str(ENRICH_FILE), f"{VPS}:{remote}"])
    script = r'''
import json, os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
load_dotenv("/root/cx-dashboard/.env")
url = os.getenv("DATABASE_URL_LOCAL") or "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"
engine = create_engine(url, pool_pre_ping=True)
rows = json.load(open("/tmp/ozon_bot_enrich_rows.json"))
filled_text = filled_photos = 0
with engine.begin() as conn:
    conn.execute(text("ALTER TABLE ozon_reviews ADD COLUMN IF NOT EXISTS photos TEXT"))
    conn.execute(text("ALTER TABLE ozon_reviews ADD COLUMN IF NOT EXISTS detail_synced_at TIMESTAMPTZ"))
    for r in rows:
        conn.execute(text("""
            UPDATE ozon_reviews SET
              review_text = CASE
                WHEN :review_text IS NOT NULL AND length(trim(:review_text))>0
                  THEN COALESCE(NULLIF(trim(review_text), ''), :review_text)
                ELSE review_text END,
              pros = COALESCE(pros, :pros),
              cons = COALESCE(cons, :cons),
              photos = CASE
                WHEN :photos IS NOT NULL AND length(:photos)>0
                  THEN COALESCE(NULLIF(photos, ''), :photos)
                ELSE photos END,
              rating = COALESCE(rating, :rating),
              detail_synced_at = NOW(),
              synced_at = NOW()
            WHERE review_id = :review_id
        """), {
            "review_id": r["review_id"],
            "review_text": r.get("review_text"),
            "pros": r.get("pros"),
            "cons": r.get("cons"),
            "photos": r.get("photos"),
            "rating": r.get("rating"),
        })
        if r.get("review_text"):
            filled_text += 1
        if r.get("photos"):
            filled_photos += 1
print("enrich_upserted", len(rows), "with_text", filled_text, "with_photos", filled_photos)
with engine.connect() as c:
    print("no_text_left", c.execute(text(
        "select count(*) from ozon_reviews where detail_synced_at is null and (review_text is null or length(trim(review_text))=0)"
    )).scalar())
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


def fetch_outbox(limit: int | None = None) -> list[dict]:
    """Тянем pending-ответы с VPS."""
    if limit is None:
        limit = int(os.getenv("OZON_OUTBOX_LIMIT", "20"))
    remote = (
        "PYTHONPATH=/root/cx-dashboard /root/cx-dashboard/venv/bin/python - <<'PY'\n"
        "import json, os\n"
        "from sqlalchemy import create_engine, text\n"
        "from dotenv import load_dotenv\n"
        "load_dotenv('/root/cx-dashboard/.env')\n"
        "url=os.getenv('DATABASE_URL_LOCAL') or "
        "'postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard'\n"
        "e=create_engine(url, pool_pre_ping=True)\n"
        "with e.begin() as c:\n"
        "  c.execute(text('''\n"
        "    CREATE TABLE IF NOT EXISTS ozon_review_outbox (\n"
        "      id SERIAL PRIMARY KEY,\n"
        "      review_id TEXT NOT NULL,\n"
        "      answer_text TEXT NOT NULL,\n"
        "      requested_by TEXT,\n"
        "      status TEXT NOT NULL DEFAULT \\'pending\\',\n"
        "      error TEXT,\n"
        "      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n"
        "      sent_at TIMESTAMPTZ\n"
        "    )\n"
        "  '''))\n"
        "  rows=c.execute(text('''\n"
        "    SELECT id, review_id, answer_text FROM ozon_review_outbox\n"
        "    WHERE status = \\'pending\\'\n"
        "    ORDER BY id ASC LIMIT :lim\n"
        "  '''), {'lim': %d}).mappings().all()\n"
        "print(json.dumps([dict(r) for r in rows], ensure_ascii=False))\n"
        "PY\n" % int(limit)
    )
    out = subprocess.check_output(["ssh", VPS, remote], text=True)
    line = out.strip().splitlines()[-1] if out.strip() else "[]"
    return json.loads(line)


def mark_outbox(item_id: int, status: str, error: str | None = None) -> None:
    err = (error or "").replace("\\", "\\\\").replace("'", "\\'")[:500]
    remote = (
        "PYTHONPATH=/root/cx-dashboard /root/cx-dashboard/venv/bin/python - <<'PY'\n"
        "import os\n"
        "from sqlalchemy import create_engine, text\n"
        "from dotenv import load_dotenv\n"
        "load_dotenv('/root/cx-dashboard/.env')\n"
        "url=os.getenv('DATABASE_URL_LOCAL') or "
        "'postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard'\n"
        "e=create_engine(url, pool_pre_ping=True)\n"
        f"status={status!r}; item_id={int(item_id)}; err={err!r}\n"
        "with e.begin() as c:\n"
        "  if status == 'sent':\n"
        "    row=c.execute(text('SELECT review_id, answer_text FROM ozon_review_outbox WHERE id=:id'), {'id': item_id}).mappings().first()\n"
        "    c.execute(text('''\n"
        "      UPDATE ozon_review_outbox\n"
        "      SET status=\\'sent\\', sent_at=NOW(), error=NULL\n"
        "      WHERE id=:id\n"
        "    '''), {'id': item_id})\n"
        "    if row:\n"
        "      c.execute(text('''\n"
        "        UPDATE ozon_reviews\n"
        "        SET answered_at = COALESCE(answered_at, NOW()), answer_text = :a\n"
        "        WHERE review_id = :rid\n"
        "      '''), {'rid': row['review_id'], 'a': row['answer_text']})\n"
        "      c.execute(text('''\n"
        "        INSERT INTO cx_response_log (item_type, item_id, mp, answered_by, answer_text)\n"
        "        VALUES (\\'review\\', :rid, \\'ozon\\', \\'cabinet_bot\\', :a)\n"
        "      '''), {'rid': row['review_id'], 'a': row['answer_text']})\n"
        "  else:\n"
        "    c.execute(text('''\n"
        "      UPDATE ozon_review_outbox\n"
        "      SET status=:st, error=:err\n"
        "      WHERE id=:id\n"
        "    '''), {'id': item_id, 'st': status, 'err': err or None})\n"
        "print('marked', item_id, status)\n"
        "PY\n"
    )
    subprocess.check_call(["ssh", VPS, remote])


def flush_outbox(page) -> int:
    """Отправляет pending из outbox через кабинет. Возвращает число успешных."""
    try:
        items = fetch_outbox(20)
    except Exception as e:
        log(f"outbox fetch ERROR: {e}")
        return 0
    if not items:
        log("outbox empty")
        return 0
    ok_n = 0
    for it in items:
        rid = it["review_id"]
        txt = (it.get("answer_text") or "").strip()
        if not txt:
            mark_outbox(it["id"], "failed", "empty text")
            continue
        try:
            res = page.evaluate(
                JS_COMMENT,
                {"uuid": rid, "companyId": COMPANY_ID, "text": txt},
            )
        except Exception as e:
            log(f"outbox send ERROR {rid}: {e}")
            mark_outbox(it["id"], "failed", str(e))
            continue
        status = (res or {}).get("status")
        body = (res or {}).get("text") or ""
        if status in (200, 201) and ('"result":true' in body.lower().replace(" ", "") or body.strip().lower() == "true"):
            mark_outbox(it["id"], "sent")
            ok_n += 1
            log(f"outbox SENT id={it['id']} review={rid}")
        else:
            mark_outbox(it["id"], "failed", f"{status}: {body[:300]}")
            log(f"outbox FAIL id={it['id']} review={rid} {status} {body[:200]}")
        time.sleep(0.8)
    return ok_n


def main() -> int:
    log(f"tick start CDP={CDP}")
    if _check_morning_pause():
        return 0
    if not cdp_ready():
        log("SKIP: Chrome CDP недоступен (запустите ~/start-ozon-bot-chrome.sh и откройте Отзывы)")
        return 0
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        page = next(
            pg for pg in browser.contexts[0].pages if "seller.ozon.ru" in (pg.url or "")
        )
        if "reviews" not in (page.url or ""):
            page.goto("https://seller.ozon.ru/app/reviews", wait_until="domcontentloaded")
            time.sleep(2)

        # 1) сначала очередь ответов из CX
        try:
            n = flush_outbox(page)
            log(f"outbox sent={n}")
        except Exception as e:
            log(f"ERROR outbox: {e}")

        # 2) сбор ожидающих ответа (+ 1 повтор при 403, иначе пауза до утра)
        rows = None
        try:
            rows = collect(page=page)
        except Exception as e:
            log(f"ERROR collect: {e}")
            if _is_auth_block(e):
                log("collect retry after reload (403/401)")
                try:
                    page.goto(
                        "https://seller.ozon.ru/app/reviews",
                        wait_until="domcontentloaded",
                    )
                    time.sleep(45)
                    rows = collect(page=page)
                except Exception as e2:
                    log(f"ERROR collect retry: {e2}")
                    _pause_until_morning()
                    alert_ozon_auth_fail("сбор отзывов (повтор)", e2)
                    return 1
            else:
                return 1
        if rows is None:
            return 1
        _clear_pause()
        log(f"collected awaiting_reply={len(rows)}")
        try:
            upload_vps(rows)
            log("upload OK")
        except Exception as e:
            log(f"ERROR upload: {e}")
            return 1

        # 3) дообогащение старых отзывов без текста/фото — Seller API, не ЛК
        try:
            erows = enrich_sparse()
            upload_enrich(erows)
            log(f"enrich OK n={len(erows)}")
        except Exception as e:
            log(f"ERROR enrich: {e}")

        # Рейтинг карточек снимается отдельно, раз в ночь:
        # scripts/ozon-bot-ratings.timer (05:20 МСК).

        # 4) возвраты: комментарий/фото из кабинета + отчёт
        try:
            from ozon_bot_returns import enrich_and_audit

            rn = enrich_and_audit(page)
            log(f"returns enrich OK n={rn}")
        except Exception as e:
            log(f"ERROR returns: {e}")
            if _is_auth_block(e):
                _pause_until_morning()
                alert_ozon_auth_fail("возвраты", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
