"""
CX-Control Telegram alerts.

Nastroyka:
  Dobavte v /root/cx-dashboard/.env:
    TG_ALERT_BOT_TOKEN=123456:ABC...   (token ot @BotFather)
    TG_ALERT_CHAT_ID=-1001234567890    (chat_id administratora ili gruppy)

Poluchit' chat_id:
  1. Napishite svoemu botu lyubuyu soobschenie
  2. Otkroyte: https://api.telegram.org/bot<TOKEN>/getUpdates
  3. Naydit' "chat": {"id": ...}

Alerty (proverka kazhdyy chas):
  - RED_QUEUE:   ocheredi s prosrochennymi > 5 i starshimi > 3x SLA
  - TAGGING_LAG: % razmetki za poslednie 24ch < 40%
  - RATING_DROP: sredniy reyting WB za segodnya < 4.3

Antipam: kazhdyy alert tipа ne chashche 1 raza v 6 chasov.
"""
import os
import logging
import asyncio
import requests
from datetime import datetime, timezone, timedelta
from sqlalchemy import text

logger = logging.getLogger("cx_alert")

TG_BOT_TOKEN = os.getenv("TG_ALERT_BOT_TOKEN", "")
TG_CHAT_ID   = os.getenv("TG_ALERT_CHAT_ID", "")

# Антиспам: один алерт одного типа не чаще раза в сутки
COOLDOWN_HOURS = 24


CF_WORKER_URL = os.getenv("CF_WORKER_URL", "https://lingering-recipe-b1b3.mursalimov.workers.dev")

def send_tg(message: str) -> bool:
    if not TG_CHAT_ID:
        logger.debug("[CXAlert] TG_ALERT_CHAT_ID ne nastroeny — propusk")
        return False
    try:
        resp = requests.post(
            CF_WORKER_URL,
            json={"chat_id": TG_CHAT_ID, "text": message},
            timeout=15,
        )
        if resp.ok:
            logger.info("[CXAlert] TG sent via CF Worker: %s", message[:60])
            return True
        logger.warning("[CXAlert] CF Worker error %s: %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        logger.error("[CXAlert] CF Worker exception: %s", e)
        return False


def _ensure_table(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS cx_alert_sent (
            alert_key  VARCHAR(120) PRIMARY KEY,
            last_sent  TIMESTAMP NOT NULL,
            last_msg   TEXT
        )
    """))
    conn.commit()


def _cooldown_ok(conn, key: str) -> bool:
    row = conn.execute(
        text("SELECT last_sent FROM cx_alert_sent WHERE alert_key = :k"), {"k": key}
    ).fetchone()
    if not row:
        return True
    last = row[0]
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last > timedelta(hours=COOLDOWN_HOURS)


def _mark_sent(conn, key: str, msg: str):
    conn.execute(text("""
        INSERT INTO cx_alert_sent (alert_key, last_sent, last_msg)
        VALUES (:k, now(), :m)
        ON CONFLICT (alert_key) DO UPDATE SET last_sent = now(), last_msg = :m
    """), {"k": key, "m": msg[:500]})
    conn.commit()


def _fmtage(minutes: int) -> str:
    if minutes < 60: return f"{minutes} мин"
    h = minutes // 60
    if h < 24: return f"{h} ч"
    return f"{h//24} дн {h%24} ч"


def check_alerts(db) -> list[dict]:
    """
    Checks current alert conditions WITHOUT sending.
    Returns list of active alerts.
    """
    alerts = []
    try:
        with db.bind.connect() as conn:
            # ── Alert 1: Prosrochennye ocheredi ──────────────────────────────
            QUEUES = [
                ("wb_rev_neg", "WB отзывы негатив", "wb_feedbacks",
                 "state='active' AND COALESCE(valuation,0)<=3", 240),
                ("wb_rev_pos", "WB отзывы позитив", "wb_feedbacks",
                 "state='active' AND COALESCE(valuation,0)>=4", 1440),
                ("wb_q",  "WB вопросы",  "wb_questions", "state='active'", 240),
                ("ym_q",  "ЯМ вопросы",  "ym_questions", "state='UNANSWERED'", 240),
            ]
            for qid, qlabel, tbl, cond, sla_min in QUEUES:
                r = conn.execute(text(f"""
                    SELECT
                        count(*) FILTER (WHERE now()-created_date > make_interval(mins=>{sla_min})) red,
                        COALESCE(ROUND(EXTRACT(epoch FROM now()-min(created_date))/60)::int,0) oldest_min
                    FROM {tbl} WHERE {cond}
                """)).fetchone()
                red, oldest = int(r[0] or 0), int(r[1] or 0)
                if red > 5 and oldest > sla_min * 3:
                    alerts.append({
                        "key": f"red_queue_{qid}",
                        "type": "red_queue",
                        "label": qlabel,
                        "message": f"{qlabel}: {red} просрочено, старейшее {_fmtage(oldest)}",
                        "severity": "high",
                    })

            # ── Alert 2: Otstavanie razmetki ─────────────────────────────────
            r2 = conn.execute(text("""
                SELECT count(*) total,
                       count(*) FILTER (WHERE ai_tags IS NOT NULL AND ai_tags ? 'processed') tagged
                FROM wb_feedbacks WHERE created_date > now()-interval '24 hours'
            """)).fetchone()
            total_24h, tagged_24h = int(r2[0] or 0), int(r2[1] or 0)
            pct_tag = round(tagged_24h / total_24h * 100, 1) if total_24h > 20 else None
            if pct_tag is not None and pct_tag < 40:
                alerts.append({
                    "key": "tagging_lag",
                    "type": "tagging_lag",
                    "label": "ИИ-разметка отстаёт",
                    "message": f"Разметка WB за 24ч: {pct_tag}% ({tagged_24h}/{total_24h}) — ниже 40%",
                    "severity": "medium",
                })

            # ── Alert 3: Padenie reytinga ─────────────────────────────────────
            r3 = conn.execute(text("""
                SELECT round(avg(valuation::numeric),2) FROM wb_feedbacks
                WHERE created_date::date = current_date AND valuation IS NOT NULL
            """)).scalar()
            if r3 is not None and float(r3) < 4.3:
                alerts.append({
                    "key": "rating_drop",
                    "type": "rating_drop",
                    "label": "Падение рейтинга WB",
                    "message": f"Средний рейтинг WB сегодня: {r3} — ниже 4.3",
                    "severity": "medium",
                })

    except Exception as e:
        logger.error("[CXAlert] check_alerts error: %s", e)

    return alerts


def send_pending_alerts(db):
    """Checks conditions, sends TG for new alerts (respecting cooldown)."""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return

    alerts = check_alerts(db)
    if not alerts:
        return

    try:
        with db.bind.connect() as conn:
            _ensure_table(conn)
            for a in alerts:
                key = a["key"]
                if _cooldown_ok(conn, key):
                    emoji = {"high": "🔴", "medium": "🟡"}.get(a["severity"], "⚪")
                    msg = f"{emoji} <b>CX-Control Alert</b>\n{a['message']}\n\n<i>cxvo.ru/cx-control</i>"
                    if send_tg(msg):
                        _mark_sent(conn, key, a["message"])
    except Exception as e:
        logger.error("[CXAlert] send_pending_alerts error: %s", e)


async def start_alert_worker(get_db_func) -> None:
    """Fonovaya zadacha FastAPI: proverka alertov kazhdyy chas."""
    logger.info("[CXAlert] Alert worker started (every 60 min)")
    while True:
        await asyncio.sleep(3600)
        try:
            db = next(get_db_func())
            try:
                send_pending_alerts(db)
            finally:
                db.close()
        except Exception as e:
            logger.error("[CXAlert] worker cycle error: %s", e)
