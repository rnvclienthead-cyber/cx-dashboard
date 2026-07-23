"""
API бота автоответов: статус, журнал теневых ответов, ручной прогон.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import get_db
from .auth import get_current_user
from .. import chat_bot
from . import chat_playbook

router = APIRouter(prefix="/api/v1/chat-bot", tags=["chat-bot"])


@router.get("/status")
def bot_status(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    enabled = chat_playbook.get_setting(db, "bot_enabled", "false")
    shadow = chat_playbook.get_setting(db, "bot_shadow", "true")
    only_days = chat_playbook.get_setting(db, "bot_only_days", "2")
    try:
        days = int(float(only_days or "2"))
    except ValueError:
        days = 2
    today = db.execute(text("""
        SELECT
          count(*) FILTER (WHERE created_at::date = CURRENT_DATE) AS today_total,
          count(*) FILTER (WHERE created_at::date = CURRENT_DATE AND sent) AS today_sent,
          count(*) FILTER (WHERE created_at::date = CURRENT_DATE AND need_human) AS today_human,
          count(*) FILTER (WHERE created_at::date = CURRENT_DATE AND zone = 'draft') AS today_draft,
          count(*) FILTER (WHERE created_at::date = CURRENT_DATE AND shadow AND NOT sent) AS today_shadow
        FROM cx_chat_bot_log
    """)).mappings().first()
    pending = db.execute(text("""
        SELECT count(*) FROM wb_chats c
        LEFT JOIN cx_dismissed_items d
               ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
        WHERE c.needs_answer AND COALESCE(c.is_closing,false) = false
          AND c.last_sender = 'client' AND d.item_id IS NULL
          AND c.last_ts > EXTRACT(EPOCH FROM now() - make_interval(days => :days)) * 1000
    """), {"days": max(days, 1)}).scalar()
    return {
        "enabled": enabled.lower() == "true",
        "shadow": shadow.lower() == "true",
        "only_days": days,
        "pending_chats": pending or 0,
        "today": dict(today) if today else {},
    }


@router.get("/log")
def bot_log(
    limit: int = Query(50, ge=1, le=200),
    only_human: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    where = "WHERE need_human = true" if only_human else ""
    rows = db.execute(text(f"""
        SELECT id, chat_id, client_message, theme, confidence, zone, branch_key,
               reply_text, action, shadow, sent, need_human, reason, created_at
        FROM cx_chat_bot_log
        {where}
        ORDER BY id DESC
        LIMIT :lim
    """), {"lim": limit}).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.post("/run-once")
def bot_run_once(
    limit: int = Query(15, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Ручной прогон пачки. Работает даже при выключенном автозапуске. Учитывает bot_shadow."""
    return chat_bot.process_batch(db, limit=limit, force=True)
