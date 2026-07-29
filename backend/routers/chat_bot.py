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
    # Статус панели — по WB (единственная площадка с живым автоботом).
    enabled = chat_playbook.get_setting_mp(db, "bot_enabled", "wb", "false")
    shadow = chat_playbook.get_setting(db, "bot_shadow", "true")
    only_days = chat_playbook.get_setting_mp(db, "bot_only_days", "wb", "2")
    only_no_mgr = chat_playbook.get_setting_mp(db, "bot_only_no_manager", "wb", "true")
    bot_model = chat_playbook.get_setting(db, "bot_model", "yandexgpt-lite")
    ai_draft = chat_playbook.get_setting_mp(db, "bot_ai_draft", "wb", "true")
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
          count(*) FILTER (WHERE created_at::date = CURRENT_DATE AND zone = 'auto') AS today_auto,
          count(*) FILTER (WHERE created_at::date = CURRENT_DATE AND shadow AND NOT sent) AS today_shadow
        FROM cx_chat_bot_log
    """)).mappings().first()
    pending = db.execute(text("""
        SELECT count(*) FROM wb_chats c
        LEFT JOIN cx_dismissed_items d
               ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
        LEFT JOIN cx_chat_bot_state s ON s.chat_id = c.chat_id
        WHERE c.needs_answer AND COALESCE(c.is_closing,false) = false
          AND c.last_sender = 'client' AND d.item_id IS NULL
          AND c.last_ts > EXTRACT(EPOCH FROM now() - make_interval(days => :days)) * 1000
          AND (s.last_bot_at IS NULL
               OR (EXTRACT(EPOCH FROM s.last_bot_at) * 1000) < c.last_ts)
          AND (
                CAST(:only_no_mgr AS boolean) = false
                OR s.last_bot_at IS NOT NULL
                OR NOT EXISTS (
                    SELECT 1 FROM wb_chat_messages m
                    WHERE m.chat_id = c.chat_id AND m.sender = 'seller'
                      AND m.text IS NOT NULL AND m.text != ''
                )
              )
    """), {
        "days": max(days, 1),
        "only_no_mgr": only_no_mgr.lower() == "true",
    }).scalar()
    return {
        "enabled": enabled.lower() == "true",
        "shadow": shadow.lower() == "true",
        "only_days": days,
        "only_no_manager": only_no_mgr.lower() == "true",
        "model": bot_model,
        "ai_draft": ai_draft.lower() == "true",
        "pending_chats": pending or 0,
        "batch_size": 15,
        "today": dict(today) if today else {},
    }


@router.get("/log")
def bot_log(
    limit: int = Query(50, ge=1, le=200),
    only_human: bool = False,
    only_review: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    where = ""
    if only_review:
        where = "WHERE (need_human = true OR zone = 'draft')"
    elif only_human:
        where = "WHERE need_human = true"
    rows = db.execute(text(f"""
        SELECT id, chat_id, client_message, theme, confidence, zone, branch_key,
               reply_text, action, shadow, sent, need_human, reason, created_at
        FROM cx_chat_bot_log
        {where}
        ORDER BY id DESC
        LIMIT :lim
    """), {"lim": limit}).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.get("/review-queue")
def bot_review_queue(
    limit: int = Query(80, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Очередь для менеджера: последний вердикт бота по чату —
    черновик или передача человеку. Без просмотра всей переписки.
    """
    rows = db.execute(text("""
        SELECT DISTINCT ON (l.chat_id)
               l.id, l.chat_id, l.client_message, l.theme, l.confidence, l.zone,
               l.reply_text, l.reason, l.need_human, l.shadow, l.sent, l.created_at,
               c.client_name, c.good_name, c.nm_id, c.needs_answer, c.last_ts
        FROM cx_chat_bot_log l
        LEFT JOIN wb_chats c ON c.chat_id = l.chat_id
        WHERE (l.need_human = true OR l.zone = 'draft')
          AND l.created_at > now() - interval '14 days'
        ORDER BY l.chat_id, l.id DESC
    """)).mappings().all()
    # Свежие сверху
    items = sorted(rows, key=lambda r: r.get("created_at") or 0, reverse=True)[:limit]
    return {
        "count": len(items),
        "items": [dict(r) for r in items],
    }


@router.get("/suggest/{chat_id:path}")
def bot_suggest(
    chat_id: str,
    mp: str = Query("wb", description="wb | ozon | ym"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Шаблонный ответ при открытии чата. Ничего не отправляет клиенту. С кэшем."""
    mp = (mp or "wb").lower()
    if mp not in ("wb", "ozon", "ym"):
        mp = "wb"
    enabled = chat_playbook.get_setting_mp(db, "bot_suggest_on_open", mp, "true")
    if enabled.lower() != "true":
        return {"ok": True, "skip": True, "reply_text": "", "reason": "подсказки выключены",
                "can_generate": False}
    # Подбор шаблонов пока только по WB-чатам; флаг Ozon/ЯМ уже учитывается выше.
    if mp != "wb":
        return {"ok": True, "skip": True, "reply_text": "",
                "reason": "автоподсказки для этой площадки пока в разработке",
                "can_generate": False}
    return chat_bot.suggest_for_chat(db, chat_id, force_ai=False, use_cache=True)


@router.post("/suggest/{chat_id:path}/generate")
def bot_suggest_generate(chat_id: str, db: Session = Depends(get_db),
                         current_user=Depends(get_current_user)):
    """Принудительно сгенерировать ИИ-ответ (кнопка, когда шаблон не подобран)."""
    return chat_bot.suggest_for_chat(db, chat_id, force_ai=True, use_cache=False)


@router.post("/run-once")
def bot_run_once(
    limit: int = Query(15, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Ручной прогон пачки (админ). Пока автозапуск выключен — только в журнал, клиенту не пишет."""
    return chat_bot.process_batch(db, limit=limit, force=True)


@router.post("/log/clear")
def bot_log_clear(
    reset_state: bool = Query(True, description="Сбросить last_bot_at, чтобы можно было прогнать те же чаты снова"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Очистить журнал тестов. По умолчанию также сбрасывает метку «уже обработан»."""
    deleted = db.execute(text("DELETE FROM cx_chat_bot_log RETURNING id")).rowcount or 0
    reset = 0
    if reset_state:
        reset = db.execute(text(
            "UPDATE cx_chat_bot_state SET last_bot_at = NULL WHERE last_bot_at IS NOT NULL"
        )).rowcount or 0
    db.commit()
    return {"ok": True, "deleted_log": deleted, "reset_state": reset}
