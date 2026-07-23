"""
Разбор чатов по корзинам, закрытие и архив.

Зачем: в очереди накопились сотни чатов, где последним писал покупатель.
Разбор по ключевым словам показывает, что там лежит: реальная проблема,
прямой вопрос, требование денег или просто «спасибо». Закрывать скопом нельзя —
закрываем только заведомо безопасное, остальное раскладываем по корзинам.

Закрытые чаты уезжают в cx_dismissed_items (mp='wb', item_type='chat') — это же
хранилище использует кнопка «в архив» в переписке.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import get_db
from .auth import get_current_user

router = APIRouter(prefix="/api/v1/chat-triage", tags=["chat-triage"])

# Корзины. Порядок веток важен: деньги важнее вопроса, вопрос важнее проблемы.
CATEGORY_SQL = """
    CASE
      WHEN COALESCE(TRIM(c.last_message),'') = '' THEN 'empty'
      WHEN TRIM(c.last_message) ~* '(верн(и|у)те|деньги|компенсац|возмест|неустойк|суд|юрист|роспотреб|обман|мошенн)' THEN 'red'
      WHEN TRIM(c.last_message) LIKE '%?%' THEN 'question'
      WHEN TRIM(c.last_message) ~* '(брак|слома|не хвата|недоста|некомплект|дефект|возврат|заявк|жду|требу|претензи|не работает|трещин|вмятин|разбит|повреж)' THEN 'problem'
      WHEN length(TRIM(c.last_message)) <= 60
           AND TRIM(c.last_message) ~* '^(спасибо|благодар|ок|хорошо|понятно|принято|извините|поняла|понял)' THEN 'thanks'
      ELSE 'other'
    END
"""

CATEGORY_LABELS = {
    "red":      "Деньги и жалобы",
    "question": "Прямой вопрос",
    "problem":  "Проблема с товаром",
    "other":    "Требует разбора",
    "thanks":   "Благодарность",
    "empty":    "Нет данных",
}

# Что безопасно закрывать без разбора.
SAFE_TO_CLOSE = ("thanks", "empty")

# Чаты в работе: те же условия, что и в очереди на странице «Чаты».
BASE_WHERE = """
    NOT COALESCE(c.is_closing, false)
    AND c.last_ts > EXTRACT(epoch FROM now() - INTERVAL '30 days') * 1000
    AND c.chat_id NOT IN (
        SELECT item_id FROM cx_dismissed_items WHERE mp='wb' AND item_type='chat'
    )
"""


@router.get("/summary")
def get_summary(stale_days: int = Query(0, ge=0, le=90),
                db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Сколько чатов в каждой корзине. stale_days>0 — только те, что молчат дольше N дней."""
    stale = ""
    if stale_days:
        stale = f"AND c.last_ts < EXTRACT(epoch FROM now() - INTERVAL '{int(stale_days)} days') * 1000"
    rows = db.execute(text(f"""
        SELECT {CATEGORY_SQL} AS category, count(*) AS cnt
        FROM wb_chats c
        WHERE {BASE_WHERE} AND c.needs_answer {stale}
        GROUP BY 1
    """)).mappings().all()

    by_cat = {r["category"]: r["cnt"] for r in rows}
    items = [{"category": k, "label": CATEGORY_LABELS.get(k, k),
              "count": by_cat.get(k, 0), "safe_to_close": k in SAFE_TO_CLOSE}
             for k in ("red", "question", "problem", "other", "thanks", "empty")]
    return {
        "items": items,
        "total": sum(by_cat.values()),
        "safe_to_close_total": sum(by_cat.get(k, 0) for k in SAFE_TO_CLOSE),
    }


@router.get("/chats")
def list_chats(category: Optional[str] = Query(None),
               stale_days: int = Query(0, ge=0, le=90),
               limit: int = Query(200, ge=1, le=1000),
               db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Чаты одной корзины — чтобы посмотреть, что там на самом деле."""
    stale = ""
    if stale_days:
        stale = f"AND c.last_ts < EXTRACT(epoch FROM now() - INTERVAL '{int(stale_days)} days') * 1000"
    cat_filter = ""
    params = {"lim": limit}
    if category:
        cat_filter = f"AND {CATEGORY_SQL} = :cat"
        params["cat"] = category

    rows = db.execute(text(f"""
        SELECT c.chat_id, c.client_name, c.good_name, c.nm_id::text AS nm_id,
               c.last_message, c.last_ts, c.needs_answer,
               {CATEGORY_SQL} AS category,
               FLOOR((EXTRACT(epoch FROM now()) - c.last_ts/1000) / 86400)::int AS days_silent
        FROM wb_chats c
        WHERE {BASE_WHERE} AND c.needs_answer {stale} {cat_filter}
        ORDER BY c.last_ts DESC
        LIMIT :lim
    """), params).mappings().all()

    return {"items": [{**dict(r), "category_label": CATEGORY_LABELS.get(r["category"], r["category"])}
                      for r in rows]}


class CloseRequest(BaseModel):
    categories: list[str] = list(SAFE_TO_CLOSE)
    stale_days: int = 5
    dry_run: bool = True


@router.post("/close")
def close_chats(payload: CloseRequest, db: Session = Depends(get_db),
                current_user=Depends(get_current_user)):
    """
    Закрыть чаты выбранных корзин (уходят в архив).
    По умолчанию dry_run — сначала показываем, сколько закроется.
    """
    cats = [c for c in payload.categories if c in CATEGORY_LABELS]
    if not cats:
        return {"status": "noop", "closed": 0}

    stale = ""
    if payload.stale_days:
        stale = f"AND c.last_ts < EXTRACT(epoch FROM now() - INTERVAL '{int(payload.stale_days)} days') * 1000"

    rows = db.execute(text(f"""
        SELECT c.chat_id, {CATEGORY_SQL} AS category
        FROM wb_chats c
        WHERE {BASE_WHERE} AND c.needs_answer {stale}
    """)).mappings().all()
    targets = [r["chat_id"] for r in rows if r["category"] in cats]

    if payload.dry_run:
        return {"status": "dry_run", "would_close": len(targets), "categories": cats}

    for chat_id in targets:
        db.execute(text("""
            INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
            VALUES ('chat', :id, 'wb', :u, :n)
        """), {"id": chat_id, "u": current_user["username"],
               "n": "Массовое закрытие: разбор по корзинам"})
    db.commit()
    return {"status": "ok", "closed": len(targets), "categories": cats}


class CloseOneRequest(BaseModel):
    chat_id: str
    note: Optional[str] = None


@router.post("/close-chat")
def close_one_chat(payload: CloseOneRequest, db: Session = Depends(get_db),
                   current_user=Depends(get_current_user)):
    """Закрыть один чат вручную — после того, как посмотрели переписку."""
    exists = db.execute(text("""
        SELECT 1 FROM cx_dismissed_items
        WHERE mp='wb' AND item_type='chat' AND item_id = :id
    """), {"id": payload.chat_id}).fetchone()
    if exists:
        return {"status": "already_closed", "chat_id": payload.chat_id}

    db.execute(text("""
        INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
        VALUES ('chat', :id, 'wb', :u, :n)
    """), {"id": payload.chat_id, "u": current_user["username"],
           "n": payload.note or "Закрыт вручную из разбора"})
    db.commit()
    return {"status": "ok", "chat_id": payload.chat_id}


@router.get("/archive")
def get_archive(limit: int = Query(200, ge=1, le=1000),
                db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Архив: чаты, закрытые вручную или массовым разбором."""
    rows = db.execute(text("""
        SELECT d.item_id AS chat_id, d.dismissed_by, d.dismissed_at, d.note,
               c.client_name, c.good_name, c.nm_id::text AS nm_id,
               c.last_message, c.last_ts
        FROM cx_dismissed_items d
        LEFT JOIN wb_chats c ON c.chat_id = d.item_id
        WHERE d.mp = 'wb' AND d.item_type = 'chat'
        ORDER BY d.dismissed_at DESC
        LIMIT :lim
    """), {"lim": limit}).mappings().all()
    total = db.execute(text(
        "SELECT count(*) FROM cx_dismissed_items WHERE mp='wb' AND item_type='chat'"
    )).scalar()
    return {"items": [dict(r) for r in rows], "total": total}


class RestoreRequest(BaseModel):
    chat_id: str


@router.post("/restore")
def restore_chat(payload: RestoreRequest, db: Session = Depends(get_db),
                 current_user=Depends(get_current_user)):
    """Вернуть чат из архива в работу."""
    db.execute(text("""
        DELETE FROM cx_dismissed_items
        WHERE mp='wb' AND item_type='chat' AND item_id = :id
    """), {"id": payload.chat_id})
    db.commit()
    return {"status": "ok"}
