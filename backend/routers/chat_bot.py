"""
API бота автоответов: статус, журнал теневых ответов, ручной прогон.
"""
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import get_db
from .auth import get_current_user
from .. import chat_bot
from .. import ym_product_card
from . import chat_playbook

router = APIRouter(prefix="/api/v1/chat-bot", tags=["chat-bot"])
bot_api_router = APIRouter(prefix="/api/v1/bot", tags=["chat-bot"])


@router.get("/product-card")
def product_card(
    article: str = Query("", max_length=255, description="Артикул продавца из чата"),
    nm_id: Optional[int] = Query(None, description="WB nm_id, если артикул в чате недоступен"),
    mp: str = Query("wb", description="wb | ozon | ym"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Точечная карточка товара. Не подбирает похожие товары."""
    mp = (mp or "wb").strip().lower()
    article = (article or "").strip()
    if not article and mp == "wb" and nm_id is not None:
        row = db.execute(text("""
            SELECT supplier_article FROM wb_logistics
            WHERE nm_id = :nm AND nullif(trim(supplier_article), '') IS NOT NULL
            ORDER BY last_change_date DESC NULLS LAST, dt DESC NULLS LAST
            LIMIT 1
        """), {"nm": int(nm_id)}).fetchone()
        if not row:
            row = db.execute(text("""
                SELECT supplier_article FROM wb_claims
                WHERE nm_id = :nm AND nullif(trim(supplier_article), '') IS NOT NULL
                ORDER BY claim_date DESC NULLS LAST, created_dt DESC NULLS LAST
                LIMIT 1
            """), {"nm": int(nm_id)}).fetchone()
        if row and row[0]:
            article = str(row[0]).strip()
    if not article:
        raise HTTPException(status_code=400, detail="article_or_nm_id_required")
    return ym_product_card.get_product_card(db, article)


@router.get("/status")
def bot_status(
    mp: str = Query("wb", description="wb | ozon | ym"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    mp = (mp or "wb").lower()
    if mp not in ("wb", "ozon", "ym"):
        mp = "wb"
    enabled = chat_playbook.get_setting_mp(db, "bot_enabled", mp, "false")
    shadow = chat_playbook.get_setting(db, "bot_shadow", "true")
    only_days = chat_playbook.get_setting_mp(db, "bot_only_days", mp, "2")
    only_no_mgr = chat_playbook.get_setting_mp(db, "bot_only_no_manager", mp, "true")
    bot_model = chat_playbook.get_setting(db, "bot_model", "yandexgpt-lite")
    ai_draft = chat_playbook.get_setting_mp(db, "bot_ai_draft", mp, "true")
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
          count(*) FILTER (WHERE created_at::date = CURRENT_DATE AND shadow AND NOT sent) AS today_shadow,
          count(*) FILTER (WHERE created_at::date = CURRENT_DATE AND COALESCE(mp,'wb') = :mp) AS today_mp
        FROM cx_chat_bot_log
    """), {"mp": mp}).mappings().first()

    if mp == "ozon":
        pending = db.execute(text("""
            SELECT count(*) FROM ozon_chats c
            LEFT JOIN cx_dismissed_items d
                   ON d.item_id = c.chat_id AND d.mp = 'ozon' AND d.item_type = 'chat'
            LEFT JOIN cx_chat_bot_state s ON s.chat_id = c.chat_id
            WHERE c.needs_answer
              AND lower(COALESCE(c.last_sender,'')) IN ('customer','buyer','client')
              AND d.item_id IS NULL
              AND COALESCE(
                    (SELECT MAX(m.created_at) FROM ozon_chat_messages m WHERE m.chat_id = c.chat_id),
                    c.waiting_since, c.updated_at, c.created_at, now()
                  ) > now() - make_interval(days => :days)
              AND (s.last_bot_at IS NULL
                   OR s.last_bot_at < COALESCE(
                        (SELECT MAX(m.created_at) FROM ozon_chat_messages m
                         WHERE m.chat_id = c.chat_id
                           AND m.sender_type IN ('customer','buyer','client')),
                        c.waiting_since, c.updated_at, c.created_at))
        """), {"days": max(days, 1)}).scalar()
    elif mp == "ym":
        pending = db.execute(text("""
            SELECT count(*) FROM ym_chats c
            LEFT JOIN cx_dismissed_items d
                   ON d.item_id = c.chat_id AND d.mp = 'ym' AND d.item_type = 'chat'
            LEFT JOIN cx_chat_bot_state s ON s.chat_id = c.chat_id
            WHERE c.needs_answer
              AND lower(COALESCE(c.last_sender,'')) IN ('customer','buyer','client','user')
              AND d.item_id IS NULL
              AND COALESCE(
                    (SELECT MAX(m.created_at) FROM ym_chat_messages m WHERE m.chat_id = c.chat_id),
                    c.waiting_since, c.updated_at, c.created_at, now()
                  ) > now() - make_interval(days => :days)
              AND (s.last_bot_at IS NULL
                   OR s.last_bot_at < COALESCE(
                        (SELECT MAX(m.created_at) FROM ym_chat_messages m
                         WHERE m.chat_id = c.chat_id
                           AND lower(COALESCE(m.sender_type,'')) IN ('customer','buyer','client','user')),
                        c.waiting_since, c.updated_at, c.created_at))
        """), {"days": max(days, 1)}).scalar()
    else:
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
        "mp": mp,
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
    mp: str = Query("", description="фильтр wb|ozon|ym или пусто = все"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    where = []
    params: dict = {"lim": limit}
    if only_review:
        where.append("(need_human = true OR zone = 'draft')")
    elif only_human:
        where.append("need_human = true")
    if mp in ("wb", "ozon", "ym"):
        where.append("COALESCE(mp,'wb') = :mp")
        params["mp"] = mp
    wh = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.execute(text(f"""
        SELECT id, chat_id, COALESCE(mp,'wb') AS mp, client_message, theme, confidence, zone,
               branch_key, reply_text, action, shadow, sent, need_human, reason, created_at
        FROM cx_chat_bot_log
        {wh}
        ORDER BY id DESC
        LIMIT :lim
    """), params).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.get("/review-queue")
def bot_review_queue(
    limit: int = Query(80, ge=1, le=200),
    mp: str = Query("all", description="wb | ozon | ym | all"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Очередь для менеджера: последний вердикт бота по чату —
    черновик или передача человеку. Без просмотра всей переписки.
    """
    mp = (mp or "all").lower()
    if mp not in ("wb", "ozon", "ym", "all"):
        mp = "all"
    mp_sql = "" if mp == "all" else "AND COALESCE(l.mp,'wb') = :mp"
    params: dict = {}
    if mp != "all":
        params["mp"] = mp

    rows = db.execute(text(f"""
        SELECT DISTINCT ON (l.chat_id)
               l.id, l.chat_id, COALESCE(l.mp,'wb') AS mp, l.client_message, l.theme,
               l.confidence, l.zone, l.reply_text, l.reason, l.need_human, l.shadow,
               l.sent, l.created_at,
               COALESCE(w.client_name, o.context_article, y.customer_name) AS client_name,
               COALESCE(w.good_name, o.context_article) AS good_name,
               w.nm_id,
               COALESCE(w.needs_answer, o.needs_answer, y.needs_answer) AS needs_answer,
               COALESCE(
                 w.last_ts,
                 EXTRACT(EPOCH FROM COALESCE(o.waiting_since, o.updated_at))::bigint * 1000,
                 EXTRACT(EPOCH FROM COALESCE(y.waiting_since, y.updated_at))::bigint * 1000
               ) AS last_ts
        FROM cx_chat_bot_log l
        LEFT JOIN wb_chats w ON w.chat_id = l.chat_id AND COALESCE(l.mp,'wb') = 'wb'
        LEFT JOIN ozon_chats o ON o.chat_id = l.chat_id AND l.mp = 'ozon'
        LEFT JOIN ym_chats y ON y.chat_id = l.chat_id AND l.mp = 'ym'
        WHERE (l.need_human = true OR l.zone = 'draft')
          AND l.created_at > now() - interval '14 days'
          {mp_sql}
        ORDER BY l.chat_id, l.id DESC
    """), params).mappings().all()
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
    return chat_bot.suggest_for_chat(db, chat_id, force_ai=False, use_cache=True, mp=mp)


@router.post("/suggest/{chat_id:path}/generate")
def bot_suggest_generate(
    chat_id: str,
    mp: str = Query("wb", description="wb | ozon | ym"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Принудительно сгенерировать ИИ-ответ (кнопка, когда шаблон не подобран)."""
    mp = (mp or "wb").lower()
    if mp not in ("wb", "ozon", "ym"):
        mp = "wb"
    return chat_bot.suggest_for_chat(db, chat_id, force_ai=True, use_cache=False, mp=mp)


class QuestionSuggestIn(BaseModel):
    text: str
    mp: str = "wb"
    article: str = ""
    nm_id: Optional[int] = None
    force_ai: bool = False


@router.post("/suggest-question")
def bot_suggest_question(
    payload: QuestionSuggestIn,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Автоподбор шаблона ответа на вопрос (плейбук чатов + классификатор)."""
    return chat_bot.suggest_for_question(
        db,
        payload.text,
        mp=payload.mp,
        article=payload.article or "",
        nm_id=payload.nm_id,
        force_ai=bool(payload.force_ai),
    )


@router.get("/question-templates")
def question_chat_templates(
    mp: str = Query("wb"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Шаблоны из плейбука вопросов (копия чатового, может расходиться)."""
    from . import question_playbook
    return {"templates": question_playbook.list_chip_templates(db, mp=mp)}


@router.get("/chat-templates")
@bot_api_router.get("/chat-templates")
def chat_templates(
    mp: str = Query("wb", description="wb | ozon | ym"),
    theme: str = Query("", description="тема текущей подсказки для приоритета"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Активные чатовые шаблоны для чипов в поле ответа."""
    mp = (mp or "wb").strip().lower()
    if mp not in ("wb", "ozon", "ym"):
        mp = "wb"
    theme = (theme or "").strip()

    rows = db.execute(text("""
        SELECT v.id, v.theme_key, t.name AS theme_name,
               v.branch_key, v.branch_label, v.body,
               COALESCE(v.mp, '') AS mp
        FROM cx_chat_variants v
        JOIN cx_chat_themes t ON t.theme_key = v.theme_key
        WHERE v.active AND t.active
          AND (
            COALESCE(v.mp, '') = ''
            OR COALESCE(v.mp, '') = :mp
          )
          AND (
            :mp = 'wb'
            OR v.theme_key NOT IN (
              'nekomplekt', 'soglasie', 'ssylka', 'status_zayavki',
              'otkaz_dootpravki', 'nevozvratny'
            )
          )
          AND v.body !~ :placeholder_pattern
        ORDER BY
          CASE WHEN :theme != '' AND v.theme_key = :theme THEN 0 ELSE 1 END,
          t.sort_order, v.theme_key, v.sort_order, v.id
    """), {
        "mp": mp,
        "theme": theme,
        "placeholder_pattern": r"\{[^{}]*\}",
    }).mappings().all()

    templates = []
    for row in rows:
        body = (row["body"] or "").strip()
        # Защита на случай неожиданной смены SQL: чип не должен
        # подставлять менеджеру незаполненные {placeholders}.
        if not body or re.search(r"\{[^{}]*\}", body):
            continue
        title = row["theme_name"] or row["theme_key"]
        if row["branch_label"]:
            title = f"{title}: {row['branch_label']}"
        templates.append({
            "id": f"chat-{row['id']}",
            "title": title,
            "body": body,
            "theme_key": row["theme_key"],
            "theme_name": row["theme_name"] or row["theme_key"],
            "branch_key": row["branch_key"] or "",
            "mp": row["mp"] or "all",
        })

    return {
        "templates": templates,
        "greeting_text": chat_playbook.get_setting(db, "greeting_text", "Здравствуйте!"),
        "signature": chat_playbook.get_setting(db, "signature", ""),
    }


@router.post("/run-once")
def bot_run_once(
    limit: int = Query(15, ge=1, le=50),
    mp: str = Query("all", description="wb | ozon | ym | all"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Ручной прогон пачки. Без bot_enabled_* — только журнал (shadow forced)."""
    mp = (mp or "all").lower()
    if mp == "all":
        return chat_bot.process_batch_all(db, limit=limit, force=True)
    if mp not in ("wb", "ozon", "ym"):
        mp = "wb"
    return chat_bot.process_batch(db, limit=limit, force=True, mp=mp)


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
