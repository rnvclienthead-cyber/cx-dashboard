"""
Раздел «Ответы КС» — очередь обращений и отправка ответов из системы.

Доступ: admin или пользователь с правом cx_responses в role_permissions.
Фаза 1: WB отзывы, вопросы, чаты.
Фаза 2: ЯМ отзывы, вопросы. ИИ-черновики (Haiku).
"""
import os
import time
import asyncio
import logging
import re
import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import text, bindparam
from pydantic import BaseModel

logger = logging.getLogger("cx_responses")

_IMG_MD_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_IMG_MD_URL_RE = re.compile(r"!\[[^\]]*\]\((https?://[^)]+)\)")

WB_CONTENT_BASE = "https://content-api.wildberries.ru"
_PHOTO_SYNC_INTERVAL = 86400   # раз в 24 часа


def _sync_product_photos(get_db_func) -> int:
    """Загружает фото товаров из WB Content API v2 и сохраняет в wb_product_photos."""
    # Приоритет: WB_CONTENT_KEY (отдельный токен с правом "Контент"),
    # иначе WB_API_KEY как запасной вариант
    key = (os.getenv("WB_CONTENT_KEY", "")
           or os.getenv("WB_API_KEY", "")
           or os.getenv("WB_FEEDBACKS_TOKEN", ""))
    if not key:
        logger.warning("[PhotoSync] нет WB_CONTENT_KEY или WB_API_KEY")
        return 0

    headers = {"Authorization": key, "Content-Type": "application/json"}
    saved = 0
    cursor: dict = {}

    while True:
        body = {"settings": {"cursor": {**{"limit": 100}, **cursor}, "filter": {"withPhoto": 1}}}
        try:
            r = requests.post(f"{WB_CONTENT_BASE}/content/v2/get/cards/list",
                              json=body, headers=headers, timeout=30)
            if r.status_code == 401:
                logger.error("[PhotoSync] 401 — токен не имеет права 'Контент'. "
                             "Добавьте WB_CONTENT_KEY в .env")
                return 0
            r.raise_for_status()
            data = r.json().get("data", {})
        except Exception as e:
            logger.error("[PhotoSync] ошибка запроса: %s", e)
            break

        cards = data.get("cards", [])
        if not cards:
            break

        rows = []
        for card in cards:
            nm_id = card.get("nmID")
            photos = card.get("photos") or []
            if nm_id and photos:
                url = (photos[0].get("tm") or photos[0].get("small")
                       or photos[0].get("big") or "")
                if url:
                    rows.append({"nm": nm_id, "url": url})

        if rows:
            try:
                db = next(get_db_func())
                try:
                    with db.bind.connect() as conn:
                        for row in rows:
                            conn.execute(text("""
                                INSERT INTO wb_product_photos (nm_id, photo_url, updated_at)
                                VALUES (:nm, :url, now())
                                ON CONFLICT (nm_id) DO UPDATE
                                SET photo_url = EXCLUDED.photo_url, updated_at = now()
                            """), row)
                        conn.commit()
                        saved += len(rows)
                finally:
                    db.close()
            except Exception as e:
                logger.error("[PhotoSync] ошибка записи в БД: %s", e)

        cur = data.get("cursor", {})
        if (cur.get("total") or 0) < 100:
            break
        cursor = {"updatedAt": cur.get("updatedAt", ""), "nmID": cur.get("nmID", 0)}
        time.sleep(0.3)

    logger.info("[PhotoSync] сохранено %d фото товаров", saved)
    return saved


async def start_photo_sync_worker(get_db_func):
    """Фоновый воркер: синк фото при старте + раз в 24 часа."""
    await asyncio.sleep(60)   # ждём пока поднимется приложение
    while True:
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: _sync_product_photos(get_db_func)
            )
        except Exception as e:
            logger.error("[PhotoSync] воркер: %s", e)
        await asyncio.sleep(_PHOTO_SYNC_INTERVAL)

from ..database import get_db
from .auth import get_current_user

WB_FEEDBACKS_BASE = "https://feedbacks-api.wildberries.ru"
WB_CHAT_BASE      = "https://buyer-chat-api.wildberries.ru"
YM_BASE           = "https://api.partner.market.yandex.ru"


def _cs_access(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    role = current_user.get("role", "")
    if role == "admin":
        return current_user
    row = db.execute(
        text("SELECT 1 FROM role_permissions WHERE role_name = :r AND module = 'cx_responses'"),
        {"r": role},
    ).first()
    if not row:
        raise HTTPException(status_code=403, detail="Нет доступа к разделу Ответы КС")
    return current_user


router = APIRouter(
    prefix="/api/v1/cx-responses",
    tags=["CX Responses"],
    dependencies=[Depends(_cs_access)],
)

# Роутер без авторизации — только для отдачи медиафайлов WB в браузер
# (браузер не может отправить Bearer в <img src>)
public_router = APIRouter(
    prefix="/api/v1/cx-responses",
    tags=["CX Responses Public"],
)


def _wb_key() -> str:
    return os.getenv("WB_API_KEY", "")


def _wb_headers() -> dict:
    return {"Authorization": _wb_key(), "Content-Type": "application/json"}


def _ym_key() -> str:
    return os.getenv("YM_API_KEY", "")


def _ym_business_id() -> str:
    return os.getenv("YM_BUSINESS_ID", "")


def _ym_campaign_id() -> str:
    return os.getenv("YM_CAMPAIGN_ID", "")


def _ym_cabinet_chat_url(
    cabinet_chat_id: str | None = None,
    context_type: str | None = None,
) -> str:
    """Deep-link в кабинет ЯМ.

    В URL нужен внутренний chatId вида ``0/43/<uuid>`` (не числовой Partner API id).
    Если uuid ещё не известен — открываем нужную вкладку без chatId (без ошибки).
    """
    from urllib.parse import urlencode
    biz = (_ym_business_id() or "").strip()
    camp = (_ym_campaign_id() or "").strip()
    if not biz:
        return "https://partner.market.yandex.ru/"
    group = str(context_type or "DIRECT").strip().upper()
    if group not in ("DIRECT", "RETURN", "ORDER"):
        group = "DIRECT"
    q: dict[str, str] = {"chatGroup": group}
    if camp:
        q["campaignId"] = camp
    cab = (cabinet_chat_id or "").strip()
    # Только формат кабинета (0/43/uuid). Числовой API-id ломает страницу.
    if cab and "/" in cab:
        q["chatId"] = cab
    return f"https://partner.market.yandex.ru/business/{biz}/arbiter?{urlencode(q)}"


def _ozon_cabinet_chat_url(chat_id: str | None = None) -> str:
    """Deep-link в мессенджер Ozon на конкретный чат (uuid из Seller API)."""
    cid = (chat_id or "").strip()
    if cid:
        return f"https://seller.ozon.ru/app/messenger/?group=customers_v2&id={cid}"
    return "https://seller.ozon.ru/app/messenger/?group=customers_v2"


def _ozon_client_id() -> str:
    return os.getenv("OZON_CLIENT_ID", "")


def _ozon_api_key() -> str:
    return os.getenv("OZON_API_KEY", "")


def _ozon_proxy_url(file_url: str) -> str:
    """Браузер не шлёт Client-Id/Api-Key в <img> — отдаём через наш прокси."""
    from urllib.parse import quote
    u = (file_url or "").strip()
    if not u:
        return ""
    if u.startswith("/api/v1/cx-responses/chat-download/"):
        return u
    return f"/api/v1/cx-responses/chat-download/ozon?url={quote(u, safe='')}"


def _ym_proxy_or_direct(file_url: str) -> str:
    """YM payload.url: если с partner API — через прокси с Api-Key."""
    u = (file_url or "").strip()
    if not u:
        return ""
    if "api.partner.market.yandex.ru" in u:
        from urllib.parse import quote
        return f"/api/v1/cx-responses/chat-download/ym?url={quote(u, safe='')}"
    return u


def _ym_headers() -> dict:
    return {"Api-Key": _ym_key(), "Content-Type": "application/json"}


# ── Очередь ──────────────────────────────────────────────────────────────────

_QUEUE_UNION = """
    SELECT
        f.id::text                                         AS id,
        'wb'::text                                         AS mp,
        'review'::text                                     AS item_type,
        f.supplier_article                                 AS sku,
        f.nm_id::text                                      AS nm_id,
        COALESCE(NULLIF(TRIM(f.text),''),
                 NULLIF(TRIM(f.pros),''),
                 NULLIF(TRIM(f.cons),''),'—')              AS body,
        f.valuation                                        AS rating,
        NULLIF(TRIM(f.pros),'')                            AS pros,
        NULLIF(TRIM(f.cons),'')                            AS cons,
        f.photos                                           AS photos_raw,
        NULL::text                                         AS order_number,
        NULL::text                                         AS contact,
        f.created_date                                     AS created_at,
        EXTRACT(epoch FROM now()-f.created_date)::int      AS age_sec,
        NULL::bigint                                       AS last_ts,
        NULL::boolean                                      AS needs_answer,
        NULL::text                                         AS last_sender
    FROM wb_feedbacks f
    WHERE f.state='active'
      AND f.id::text NOT IN
          (SELECT item_id FROM cx_dismissed_items WHERE mp='wb' AND item_type='review')

    UNION ALL

    SELECT
        y.feedback_id::text,
        'ym', 'review',
        y.supplier_article, NULL::text,
        COALESCE(NULLIF(TRIM(y.comment),''),
                 NULLIF(TRIM(y.pro_text),''),
                 NULLIF(TRIM(y.contra_text),''),'—'),
        y.valuation,
        NULLIF(TRIM(y.pro_text),''), NULLIF(TRIM(y.contra_text),''),
        NULL::text, NULL::text, NULL::text,
        y.created_date,
        EXTRACT(epoch FROM now()-y.created_date)::int,
        NULL::bigint,
        NULL::boolean,
        NULL::text
    FROM ym_feedbacks y
    WHERE y.answered_at IS NULL
      AND y.feedback_id NOT IN
          (SELECT item_id FROM cx_dismissed_items WHERE mp='ym' AND item_type='review')

    UNION ALL

    SELECT
        q.id, 'wb', 'question',
        q.supplier_article, NULL::text,
        q.text, NULL::int, NULL::text, NULL::text,
        NULL::text, NULL::text, NULL::text,
        q.created_date,
        EXTRACT(epoch FROM now()-q.created_date)::int,
        NULL::bigint,
        NULL::boolean,
        NULL::text
    FROM wb_questions q
    WHERE q.state='active'
      AND q.id NOT IN
          (SELECT item_id FROM cx_dismissed_items WHERE mp='wb' AND item_type='question')

    UNION ALL

    SELECT
        q.question_id::text, 'ym', 'question',
        q.supplier_article, NULL::text,
        q.text, NULL::int, NULL::text, NULL::text,
        NULL::text, NULL::text, NULL::text,
        q.created_date,
        EXTRACT(epoch FROM now()-q.created_date)::int,
        NULL::bigint,
        NULL::boolean,
        NULL::text
    FROM ym_questions q
    WHERE q.state='UNANSWERED'
      AND q.question_id::text NOT IN
          (SELECT item_id FROM cx_dismissed_items WHERE mp='ym' AND item_type='question')

    UNION ALL

    -- WB чаты: все за последние 30 дней (не только ждущие ответа),
    -- кроме закрывающих («спасибо» и т.п.)
    SELECT
        c.chat_id, 'wb', 'chat',
        c.good_name, c.nm_id::text,
        c.last_message, NULL::int, NULL::text, NULL::text,
        NULL::text, NULL::text, c.client_name,
        c.created_at,
        EXTRACT(epoch FROM now()-c.created_at)::int,
        c.last_ts,
        c.needs_answer,
        c.last_sender
    FROM wb_chats c
    WHERE NOT COALESCE(c.is_closing,false)
      AND c.last_ts > EXTRACT(epoch FROM now()-INTERVAL '30 days')*1000
      AND c.chat_id NOT IN
          (SELECT item_id FROM cx_dismissed_items WHERE mp='wb' AND item_type='chat')

    UNION ALL

    SELECT
        r.review_id, 'ozon', 'review',
        r.supplier_article, NULL::text,
        COALESCE(NULLIF(TRIM(r.review_text),''),
                 NULLIF(TRIM(r.pros),''),
                 NULLIF(TRIM(r.cons),''),'—'),
        r.rating,
        NULLIF(TRIM(r.pros),''), NULLIF(TRIM(r.cons),''),
        NULL::text, NULL::text, NULL::text,
        r.created_at,
        EXTRACT(epoch FROM now()-r.created_at)::int,
        NULL::bigint,
        NULL::boolean,
        NULL::text
    FROM ozon_reviews r
    WHERE r.answered_at IS NULL
      AND r.review_id NOT IN
          (SELECT item_id FROM cx_dismissed_items WHERE mp='ozon' AND item_type='review')

    UNION ALL

    SELECT
        c.chat_id, 'ozon', 'chat',
        COALESCE(c.context_article, c.order_number, c.context_sku::text),
        NULL::text,
        c.last_message, NULL::int, NULL::text, NULL::text,
        NULL::text, c.order_number::text,
        COALESCE(c.order_number, 'Покупатель'),
        COALESCE(c.waiting_since, c.updated_at, now()),
        EXTRACT(epoch FROM now()-COALESCE(c.waiting_since, c.updated_at, now()))::int,
        EXTRACT(epoch FROM COALESCE(c.updated_at, now()))::bigint * 1000,
        c.needs_answer,
        c.last_sender
    FROM ozon_chats c
    WHERE c.chat_status ILIKE 'OPENED'
      AND (c.chat_type IS NULL OR upper(c.chat_type) LIKE '%BUYER%')
      AND (
            c.needs_answer = true
            OR COALESCE(c.updated_at, now()) > now() - INTERVAL '7 days'
          )
      AND c.chat_id NOT IN
          (SELECT item_id FROM cx_dismissed_items WHERE mp='ozon' AND item_type='chat')

    UNION ALL

    SELECT
        c.chat_id, 'ym', 'chat',
        COALESCE(c.context_type, c.order_id, 'чат'),
        NULL::text,
        c.last_message, NULL::int, NULL::text, NULL::text,
        NULL::text, c.order_id,
        COALESCE(NULLIF(TRIM(c.customer_name), ''), 'Покупатель'),
        COALESCE(c.waiting_since, c.updated_at, c.created_at, now()),
        EXTRACT(epoch FROM now()-COALESCE(c.waiting_since, c.updated_at, c.created_at, now()))::int,
        EXTRACT(epoch FROM COALESCE(c.updated_at, c.created_at, now()))::bigint * 1000,
        c.needs_answer,
        c.last_sender
    FROM ym_chats c
    WHERE COALESCE(c.updated_at, c.created_at, now()) > now() - INTERVAL '30 days'
      AND c.chat_id NOT IN
          (SELECT item_id FROM cx_dismissed_items WHERE mp='ym' AND item_type='chat')
"""


@router.get("/queue")
def get_queue(
    db: Session = Depends(get_db),
    mp: str = Query("all"),
):
    """Сводная очередь обращений — все площадки, сортировка по давности."""
    mp_filter = "" if mp == "all" else f"AND mp = '{mp}'"

    with db.bind.connect() as conn:
        counts = conn.execute(text(f"""
            SELECT
                COUNT(*)                                    AS total,
                COUNT(*) FILTER (WHERE item_type='review')  AS reviews,
                COUNT(*) FILTER (WHERE item_type='question') AS questions,
                COUNT(*) FILTER (WHERE item_type='chat')    AS chats,
                COUNT(*) FILTER (WHERE mp='wb')             AS wb,
                COUNT(*) FILTER (WHERE mp='ym')             AS ym,
                COUNT(*) FILTER (WHERE mp='ozon')           AS ozon
            FROM ({_QUEUE_UNION}) q WHERE 1=1 {mp_filter}
        """)).mappings().first()

        rows = conn.execute(text(f"""
            SELECT q.*,
                COALESCE(
                    pp.photo_url,
                    (SELECT pp2.photo_url
                     FROM wb_feedbacks wf
                     JOIN product_photos pp2 ON pp2.supplier_article = wf.supplier_article
                     WHERE wf.nm_id::text = q.nm_id LIMIT 1)
                ) AS product_photo
            FROM ({_QUEUE_UNION}) q
            LEFT JOIN product_photos pp ON pp.supplier_article = q.sku
            WHERE 1=1 {mp_filter}
            ORDER BY
                CASE WHEN item_type='review' AND rating IS NOT NULL AND rating<=3 THEN 0 ELSE 1 END,
                age_sec DESC NULLS LAST
        """)).mappings().all()

    def _row(r):
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat() if hasattr(d["created_at"], "isoformat") else str(d["created_at"])
        # Разбиваем фото на список
        raw = d.pop("photos_raw", None) or ""
        d["photos"] = [u for u in raw.split() if u.startswith("http")]
        return d

    items = [_row(r) for r in rows]

    # Deep-link в кабинеты: Ozon — uuid чата; YM — внутренний 0/43/uuid
    ym_ids = [
        str(i["id"]) for i in items
        if i.get("mp") == "ym" and i.get("item_type") == "chat" and i.get("id")
    ]
    ym_cab: dict[str, dict] = {}
    if ym_ids:
        with db.bind.connect() as conn:
            # колонка может появиться после миграции/скрипта
            try:
                rows_cab = conn.execute(
                    text("""
                        SELECT chat_id, cabinet_chat_id, context_type
                        FROM ym_chats WHERE chat_id IN :ids
                    """).bindparams(bindparam("ids", expanding=True)),
                    {"ids": ym_ids},
                ).mappings().all()
                ym_cab = {str(r["chat_id"]): dict(r) for r in rows_cab}
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass

    for item in items:
        if item.get("item_type") != "chat":
            continue
        if item.get("mp") == "ozon":
            item["cabinet_url"] = _ozon_cabinet_chat_url(str(item.get("id") or ""))
        elif item.get("mp") == "ym":
            meta = ym_cab.get(str(item.get("id") or ""), {})
            ctx = meta.get("context_type") or item.get("sku")
            item["context_type"] = ctx
            item["cabinet_chat_id"] = meta.get("cabinet_chat_id")
            item["cabinet_url"] = _ym_cabinet_chat_url(
                meta.get("cabinet_chat_id"), ctx
            )

    # Наложить черновики на модерации
    if items:
        with db.bind.connect() as conn:
            pending = conn.execute(text(
                "SELECT id, mp, item_type, item_id, draft_text, template_title "
                "FROM cx_pending_moderation"
            )).mappings().all()
        pm_map = {(p["mp"], p["item_type"], p["item_id"]): dict(p) for p in pending}
        for item in items:
            pm = pm_map.get((item["mp"], item["item_type"], str(item["id"])))
            if pm:
                item["pending_mod_id"]   = pm["id"]
                item["pending_draft"]    = pm["draft_text"]
                item["pending_template"] = pm.get("template_title") or ""

    counts_d = dict(counts) if counts else {}
    return {
        "items":  items,
        "counts": counts_d,
    }


# ── Архив ────────────────────────────────────────────────────────────────────

@router.get("/archive")
def get_archive(
    db: Session = Depends(get_db),
    mp: str = Query("all"),
    item_type: str = Query("all"),
    sku: str = Query(""),
    has_text: str = Query(""),      # '' | 'with' | 'without'
    status: str = Query(""),        # '' | 'answered' | 'dismissed'
    date_from: str = Query(""),
    date_to: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=5, le=200),
):
    """Отвеченные и вручную закрытые обращения."""
    mp_cond   = "" if mp == "all"   else f"AND a.mp = '{mp}'"
    type_cond = "" if item_type == "all" else f"AND a.item_type = '{item_type}'"
    offset = (page - 1) * per_page

    # Доп. фильтры (параметризованные)
    conds = ""
    params = {"lim": per_page, "off": offset}
    if sku.strip():
        conds += " AND a.sku ILIKE :sku"
        params["sku"] = f"%{sku.strip()}%"
    if has_text == "with":
        conds += " AND COALESCE(NULLIF(TRIM(a.body),''),'—') <> '—'"
    elif has_text == "without":
        conds += " AND COALESCE(NULLIF(TRIM(a.body),''),'—') = '—'"
    if status in ("answered", "dismissed"):
        conds += " AND a.archive_reason = :status"
        params["status"] = status
    if date_from.strip():
        conds += " AND a.created_at >= :dfrom"
        params["dfrom"] = date_from.strip()
    if date_to.strip():
        conds += " AND a.created_at < (:dto)::date + 1"
        params["dto"] = date_to.strip()

    archive_union = """
        SELECT
            f.id::text AS id, 'wb' AS mp, 'review' AS item_type,
            f.supplier_article AS sku, f.nm_id::text AS nm_id,
            COALESCE(NULLIF(TRIM(f.text),''), '—') AS body,
            f.valuation AS rating,
            f.answer_text AS answer_text,
            f.created_date AS created_at,
            NULL::text AS dismissed_by, NULL::text AS note,
            'answered' AS archive_reason
        FROM wb_feedbacks f WHERE f.state='answered'
        UNION ALL
        SELECT
            y.feedback_id::text, 'ym', 'review',
            y.supplier_article, NULL::text,
            COALESCE(NULLIF(TRIM(y.comment),''), '—'),
            y.valuation, y.answer_text, y.created_date,
            NULL::text, NULL::text, 'answered'
        FROM ym_feedbacks y WHERE y.answered_at IS NOT NULL
        UNION ALL
        SELECT
            q.id, 'wb', 'question',
            q.supplier_article, NULL::text,
            q.text, NULL::int, q.answer_text, q.created_date,
            NULL::text, NULL::text, 'answered'
        FROM wb_questions q WHERE q.state='answered'
        UNION ALL
        SELECT
            r.review_id, 'ozon', 'review',
            r.supplier_article, NULL::text,
            COALESCE(NULLIF(TRIM(r.review_text),''), '—'),
            r.rating, r.answer_text, r.created_at,
            NULL::text, NULL::text, 'answered'
        FROM ozon_reviews r WHERE r.answered_at IS NOT NULL
        UNION ALL
        SELECT
            d.item_id, d.mp, d.item_type,
            NULL::text, NULL::text, NULL::text, NULL::int, NULL::text,
            d.dismissed_at,
            d.dismissed_by, d.note, 'dismissed'
        FROM cx_dismissed_items d
    """

    with db.bind.connect() as conn:
        total = conn.execute(text(f"""
            SELECT COUNT(*) FROM ({archive_union}) a WHERE 1=1 {mp_cond} {type_cond} {conds}
        """), params).scalar() or 0

        rows = conn.execute(text(f"""
            SELECT a.*, l.answered_by, l.sent_at AS answered_at,
                COALESCE(
                    pp.photo_url,
                    (SELECT pp2.photo_url
                     FROM wb_feedbacks wf
                     JOIN product_photos pp2 ON pp2.supplier_article = wf.supplier_article
                     WHERE wf.nm_id::text = a.nm_id LIMIT 1)
                ) AS product_photo
            FROM ({archive_union}) a
            LEFT JOIN LATERAL (
                SELECT answered_by, sent_at FROM cx_response_log
                WHERE item_id=a.id AND mp=a.mp AND item_type=a.item_type
                ORDER BY sent_at DESC LIMIT 1
            ) l ON true
            LEFT JOIN product_photos pp ON pp.supplier_article = a.sku
            WHERE 1=1 {mp_cond} {type_cond} {conds}
            ORDER BY a.created_at DESC NULLS LAST
            LIMIT :lim OFFSET :off
        """), params).mappings().all()

    def _afmt(r):
        d = dict(r)
        for k in ("created_at", "answered_at"):
            if d.get(k) and hasattr(d[k], "isoformat"):
                d[k] = d[k].isoformat()
        return d

    import math
    return {
        "items": [_afmt(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, math.ceil(total / per_page)),
    }


# ── Закрыть обращение (без ответа) ────────────────────────────────────────────

class DismissPayload(BaseModel):
    mp: str
    item_type: str
    item_id: str
    note: str = ""


@router.post("/dismiss")
def dismiss_item(
    payload: DismissPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    with db.bind.connect() as conn:
        conn.execute(text("""
            INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
            VALUES (:it, :iid, :mp, :user, :note)
            ON CONFLICT (item_type, item_id, mp) DO NOTHING
        """), {"it": payload.item_type, "iid": payload.item_id, "mp": payload.mp,
               "user": current_user["username"], "note": payload.note})
        conn.commit()
    return {"status": "ok"}


@router.delete("/dismiss/{mp}/{item_type}/{item_id}")
def undismiss_item(mp: str, item_type: str, item_id: str, db: Session = Depends(get_db)):
    with db.bind.connect() as conn:
        conn.execute(text(
            "DELETE FROM cx_dismissed_items WHERE mp=:mp AND item_type=:it AND item_id=:iid"
        ), {"mp": mp, "it": item_type, "iid": item_id})
        conn.commit()
    return {"status": "ok"}


# ── История чата WB ──────────────────────────────────────────────────────────

def _extract_image_ids(attachments) -> list:
    """Достаёт downloadID вложений-картинок из разных возможных форм ответа WB."""
    if not attachments:
        return []
    out = []
    for key in ("images", "files", "photos", "attachments"):
        arr = attachments.get(key) if isinstance(attachments, dict) else None
        if not isinstance(arr, list):
            continue
        for a in arr:
            if isinstance(a, str):
                out.append(a)
            elif isinstance(a, dict):
                # WB buyer-chat API возвращает downloadID как ключ для скачивания
                fid = (a.get("downloadID") or a.get("id") or
                       a.get("fileID") or a.get("fileId"))
                # url пустой у WB, игнорируем
                if fid:
                    out.append(str(fid))
    return out


@router.get("/chat-messages/wb/{chat_id}")
def get_wb_chat_messages(chat_id: str, db: Session = Depends(get_db)):
    """Полная лента переписки WB.

    Копится в wb_chat_messages из потока событий (WB не отдаёт историю задним
    числом). Если лента пустая (старый чат до запуска накопления) — показываем
    последнее сообщение из wb_chats + наши логи ответов как fallback.
    """
    with db.bind.connect() as conn:
        rows = conn.execute(text(
            "SELECT sender, text, attachments, add_ts "
            "FROM wb_chat_messages WHERE chat_id = :cid ORDER BY add_ts, id"
        ), {"cid": chat_id}).mappings().all()

    if rows:
        msgs = []
        for r in rows:
            imgs = _extract_image_ids(r.get("attachments"))
            msgs.append({
                "text": r["text"] or "",
                "senderType": "seller" if r["sender"] == "seller" else ("wb" if r["sender"] == "wb" else "client"),
                "createdAt": r["add_ts"],
                "images": [f"/api/v1/cx-responses/chat-download/wb/{i}" for i in imgs] if imgs else [],
            })
        return {"messages": msgs, "partial": False}

    # ── Fallback: старый чат без накопленной ленты ──
    msgs = []
    with db.bind.connect() as conn:
        chat = conn.execute(text(
            "SELECT last_message, last_sender, last_ts, waiting_since "
            "FROM wb_chats WHERE chat_id = :cid"
        ), {"cid": chat_id}).mappings().first()
        replies = conn.execute(text(
            "SELECT answer_text, sent_at FROM cx_response_log "
            "WHERE mp='wb' AND item_type='chat' AND item_id = :cid ORDER BY sent_at"
        ), {"cid": chat_id}).mappings().all()

    if chat and chat.get("last_message"):
        ts = chat.get("last_ts")
        if not ts:
            ws = chat.get("waiting_since")
            ts = int(ws.timestamp() * 1000) if ws else None
        msgs.append({
            "text": chat["last_message"],
            "senderType": "seller" if chat.get("last_sender") == "seller" else "client",
            "createdAt": ts, "images": [],
        })
    for r in replies:
        msgs.append({
            "text": r["answer_text"], "senderType": "seller",
            "createdAt": int(r["sent_at"].timestamp() * 1000) if r.get("sent_at") else None,
            "images": [],
        })
    msgs.sort(key=lambda m: (m["createdAt"] is None, m["createdAt"] or 0))
    return {"messages": msgs, "partial": True}


def _iso_to_ms(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    try:
        if hasattr(v, "timestamp"):
            return int(v.timestamp() * 1000)
        s = str(v).replace("Z", "+00:00")
        from datetime import datetime
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except Exception:
        return None


@router.get("/chat-messages/ozon/{chat_id}")
def get_ozon_chat_messages(chat_id: str, db: Session = Depends(get_db)):
    """Лента чата Ozon из ozon_chat_messages (+ fallback на превью)."""
    # Если ленты ещё нет — один раз дотянуть history из API
    with db.bind.connect() as conn:
        n = conn.execute(text(
            "SELECT count(*) FROM ozon_chat_messages WHERE chat_id = :cid"
        ), {"cid": chat_id}).scalar() or 0
    if n == 0:
        try:
            from .. import ozon_chat_sync
            with db.bind.connect() as conn:
                ozon_chat_sync._sync_history(conn, chat_id)
                conn.commit()
        except Exception as e:
            logger.warning("[ozon messages] live history %s: %s", chat_id, e)

    with db.bind.connect() as conn:
        rows = conn.execute(text("""
            SELECT sender_type, text, attachments, created_at, is_image
            FROM ozon_chat_messages
            WHERE chat_id = :cid
            ORDER BY created_at NULLS LAST, message_id
        """), {"cid": chat_id}).mappings().all()

    if rows:
        msgs = []
        for r in rows:
            st = str(r.get("sender_type") or "").lower()
            if st == "seller":
                sender = "seller"
            elif st in ("system", "chatbot"):
                sender = "system"
            else:
                sender = "client"
            imgs = []
            raw = r.get("attachments") or ""
            if raw:
                imgs = [u for u in str(raw).split(",") if u.startswith("http")]
            # из markdown в тексте, если attachments пусты
            if not imgs and r.get("text") and "![" in str(r.get("text")):
                imgs = _IMG_MD_URL_RE.findall(str(r.get("text")))
            body = r.get("text") or ""
            # убрать markdown-картинки из текста если уже в images
            if imgs and "![" in body:
                body = _IMG_MD_RE.sub("", body).strip()
            msgs.append({
                "text": body,
                "senderType": sender,
                "createdAt": _iso_to_ms(r.get("created_at")),
                "images": [_ozon_proxy_url(u) for u in imgs if u],
            })
        return {"messages": msgs, "partial": False}

    with db.bind.connect() as conn:
        chat = conn.execute(text("""
            SELECT last_message, last_sender, updated_at, waiting_since
            FROM ozon_chats WHERE chat_id = :cid
        """), {"cid": chat_id}).mappings().first()
    msgs = []
    if chat and chat.get("last_message"):
        st = str(chat.get("last_sender") or "").lower()
        msgs.append({
            "text": chat["last_message"],
            "senderType": "seller" if st == "seller" else "client",
            "createdAt": _iso_to_ms(chat.get("updated_at") or chat.get("waiting_since")),
            "images": [],
        })
    return {"messages": msgs, "partial": True}


@router.get("/chat-messages/ym/{chat_id}")
def get_ym_chat_messages(chat_id: str, db: Session = Depends(get_db)):
    """Лента чата Яндекс Маркета."""
    with db.bind.connect() as conn:
        n = conn.execute(text(
            "SELECT count(*) FROM ym_chat_messages WHERE chat_id = :cid"
        ), {"cid": chat_id}).scalar() or 0
    if n == 0:
        try:
            from .. import ym_chat_sync
            with db.bind.connect() as conn:
                ym_chat_sync._sync_history(conn, chat_id)
                conn.commit()
        except Exception as e:
            logger.warning("[ym messages] live history %s: %s", chat_id, e)

    with db.bind.connect() as conn:
        try:
            rows = conn.execute(text("""
                SELECT sender_type, text, created_at, attachments
                FROM ym_chat_messages
                WHERE chat_id = :cid
                ORDER BY created_at NULLS LAST, message_id
            """), {"cid": chat_id}).mappings().all()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            rows = conn.execute(text("""
                SELECT sender_type, text, created_at
                FROM ym_chat_messages
                WHERE chat_id = :cid
                ORDER BY created_at NULLS LAST, message_id
            """), {"cid": chat_id}).mappings().all()

    if rows:
        msgs = []
        for r in rows:
            st = str(r.get("sender_type") or "").lower()
            if st == "seller":
                sender = "seller"
            elif st == "system":
                sender = "system"
            else:
                sender = "client"
            imgs = []
            raw = r.get("attachments") or ""
            if raw:
                imgs = [u for u in str(raw).split("\n") if u.startswith("http")]
            msgs.append({
                "text": r.get("text") or "",
                "senderType": sender,
                "createdAt": _iso_to_ms(r.get("created_at")),
                "images": [_ym_proxy_or_direct(u) for u in imgs if u],
            })
        return {"messages": msgs, "partial": False}

    with db.bind.connect() as conn:
        chat = conn.execute(text("""
            SELECT last_message, last_sender, updated_at, waiting_since
            FROM ym_chats WHERE chat_id = :cid
        """), {"cid": chat_id}).mappings().first()
    msgs = []
    if chat and chat.get("last_message"):
        st = str(chat.get("last_sender") or "").lower()
        msgs.append({
            "text": chat["last_message"],
            "senderType": "seller" if st == "seller" else "client",
            "createdAt": _iso_to_ms(chat.get("updated_at") or chat.get("waiting_since")),
            "images": [],
        })
    return {"messages": msgs, "partial": True}


CLAIM_TYPE_MAP = {"1": "Брак", "2": "Не то, что заказывали", "3": "Недостача",
                   "4": "Другое", "5": "Недовложение"}
# Коды returns-api: 0=на рассмотрении, 1=отказ, 2=одобрено (не путать со старым map).
CLAIM_STATUS_MAP = {
    "0": "На рассмотрении",
    "1": "Отказ",
    "2": "Одобрено",
    "3": "Отказ",
    "Отклонено": "Отказ",
    "Закрыта": "Закрыта",
}
_RU_MONTHS = ["января","февраля","марта","апреля","мая","июня",
               "июля","августа","сентября","октября","ноября","декабря"]


def _ru_date(d) -> str:
    """Преобразует дату в строку вида '8 июля' (как в WB ЛК)."""
    if not d:
        return ""
    try:
        if hasattr(d, "day"):
            return f"{d.day} {_RU_MONTHS[d.month - 1]}"
        import datetime as _dt
        p = str(d)[:10]
        dt = _dt.date.fromisoformat(p)
        return f"{dt.day} {_RU_MONTHS[dt.month - 1]}"
    except Exception:
        return str(d)[:10]


def _decode_claim(row: dict) -> dict:
    ct = str(row.get("claim_type") or "")
    st = str(row.get("status") or "").strip()
    row["claim_type_label"] = CLAIM_TYPE_MAP.get(ct, ct) or ct
    label = CLAIM_STATUS_MAP.get(st, st) or st
    # Архивная заявка без финального статуса — не вводим в заблуждение «На рассмотрении»
    if label in ("На рассмотрении", "0", "0.0", "Открыта") and row.get("is_archive"):
        label = "Закрыта"
    row["status_label"] = label
    return row


@router.get("/review-card")
def review_card(
    mp: str = Query(...),
    item_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Полная карточка отзыва: сам отзыв + наш ответ + связанные чаты, возвраты, контакты/переотправки."""
    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)

    with db.bind.connect() as conn:
        # ── Сам отзыв ──
        if mp == "wb":
            r = conn.execute(text(
                "SELECT id::text AS id, supplier_article AS sku, nm_id::text AS nm_id, "
                "valuation AS rating, text AS body, pros, cons, photos AS photos_raw, "
                "answer_text, state, created_date AS created_at FROM wb_feedbacks WHERE id::text=:i"
            ), {"i": item_id}).mappings().first()
        elif mp == "ym":
            r = conn.execute(text(
                "SELECT feedback_id::text AS id, supplier_article AS sku, NULL::text AS nm_id, "
                "valuation AS rating, comment AS body, pro_text AS pros, contra_text AS cons, "
                "NULL::text AS photos_raw, answer_text, "
                "CASE WHEN answered_at IS NULL THEN 'active' ELSE 'answered' END AS state, "
                "created_date AS created_at FROM ym_feedbacks WHERE feedback_id::text=:i"
            ), {"i": item_id}).mappings().first()
        else:  # ozon
            r = conn.execute(text(
                "SELECT review_id AS id, supplier_article AS sku, NULL::text AS nm_id, "
                "rating, review_text AS body, pros, cons, NULL::text AS photos_raw, answer_text, "
                "CASE WHEN answered_at IS NULL THEN 'active' ELSE 'answered' END AS state, "
                "created_at FROM ozon_reviews WHERE review_id=:i"
            ), {"i": item_id}).mappings().first()

        if not r:
            raise HTTPException(status_code=404, detail="Отзыв не найден")
        review = dict(r)
        review["created_at"] = _iso(review.get("created_at"))
        raw = review.pop("photos_raw", None) or ""
        review["photos"] = [u for u in str(raw).split() if u.startswith("http")]
        sku, nm = review.get("sku"), review.get("nm_id")

        # ── Фото карточки ──
        review["product_photo"] = conn.execute(text(
            "SELECT photo_url FROM product_photos WHERE supplier_article=:a LIMIT 1"
        ), {"a": sku}).scalar()

        # ── Кто и когда ответил ──
        log = conn.execute(text(
            "SELECT answered_by, sent_at, answer_text FROM cx_response_log "
            "WHERE mp=:mp AND item_type='review' AND item_id=:i ORDER BY sent_at DESC LIMIT 1"
        ), {"mp": mp, "i": item_id}).mappings().first()
        if log:
            review["answered_by"] = log["answered_by"]
            review["answered_at"] = _iso(log["sent_at"])
            if not review.get("answer_text"):
                review["answer_text"] = log["answer_text"]

        chats, returns, reshipments = [], [], []
        if mp == "wb" and nm:
            chats = [dict(x) for x in conn.execute(text(
                "SELECT chat_id, client_name, good_name, last_message, last_ts, needs_answer "
                "FROM wb_chats WHERE nm_id::text=:nm ORDER BY last_ts DESC NULLS LAST LIMIT 10"
            ), {"nm": nm}).mappings().all()]

        if mp == "wb" and (sku or nm):
            rows = conn.execute(text(
                "SELECT srid, claim_type, status, claim_date, user_comment, is_archive "
                "FROM wb_claims WHERE supplier_article=:a OR nm_id::text=:nm "
                "ORDER BY claim_date DESC NULLS LAST LIMIT 10"
            ), {"a": sku, "nm": nm}).mappings().all()
            for x in rows:
                d = _decode_claim(dict(x))
                d["claim_date"] = _iso(d.get("claim_date"))
                returns.append(d)

        if mp == "wb" and nm:
            rows = conn.execute(text(
                "SELECT id, customer_name, customer_phone, customer_email, order_number, status, "
                "salebot_messenger, salebot_client_id, wb_chat_id, created_at "
                "FROM reshipment_requests WHERE wb_nm_id::text=:nm ORDER BY created_at DESC LIMIT 10"
            ), {"nm": nm}).mappings().all()
            for x in rows:
                d = dict(x)
                d["created_at"] = _iso(d.get("created_at"))
                reshipments.append(d)

    return {"review": review, "chats": chats, "returns": returns, "reshipments": reshipments}


@router.get("/claims")
def list_claims(
    active: bool = True,
    status: str = "",
    claim_type: str = Query("", alias="type"),
    page: int = 1,
    per_page: int = 25,
    db: Session = Depends(get_db),
):
    """Список wb_claims с фильтрами: active, status, type."""
    offset = (page - 1) * per_page
    filters, params = ["1=1"], {}
    filters.append("is_archive = :arch")
    params["arch"] = not active
    if status:
        filters.append("status = :status")
        params["status"] = status
    if claim_type:
        filters.append("claim_type = :ctype")
        params["ctype"] = claim_type
    where = " AND ".join(filters)
    with db.bind.connect() as conn:
        total = conn.execute(
            text(f"SELECT COUNT(*) FROM wb_claims WHERE {where}"), params
        ).scalar()
        rows = conn.execute(text(f"""
            SELECT c.claim_id::text, c.srid, c.supplier_article, c.nm_id,
                   c.claim_type, c.status, c.user_comment,
                   c.is_archive, c.claim_date, c.order_date, c.income_id,
                   c.photos, c.correction, c.audit_status, c.audit_comment,
                   COALESCE(pp.photo_url, wpp.photo_url) AS product_photo
            FROM wb_claims c
            LEFT JOIN product_photos pp  ON pp.supplier_article = c.supplier_article
            LEFT JOIN wb_product_photos wpp ON wpp.nm_id = c.nm_id
            WHERE {where}
            ORDER BY c.claim_date DESC
            LIMIT :lim OFFSET :off
        """), {**params, "lim": per_page, "off": offset}).mappings().all()
    items = [_decode_claim(dict(r)) for r in rows]
    return {"items": items, "total": total, "page": page,
            "total_pages": max(1, -(-total // per_page))}


@router.get("/chat-context/wb/{chat_id}")
def get_wb_chat_context(chat_id: str, db: Session = Depends(get_db)):
    """Конкретный отзыв/претензия клиента (по rid заказа) + все обращения по товару."""
    with db.bind.connect() as conn:
        chat = conn.execute(text(
            "SELECT nm_id FROM wb_chats WHERE chat_id = :cid"
        ), {"cid": chat_id}).mappings().first()
        if not chat or not chat["nm_id"]:
            return {"specific": {}, "reviews": [], "questions": [], "claims": []}
        nm_str = str(int(chat["nm_id"]))

        # ── Rid заказа из первого сообщения с goodCard ───────────────────────
        rid_row = conn.execute(text("""
            SELECT attachments->'goodCard'->>'rid' AS rid
            FROM wb_chat_messages
            WHERE chat_id = :cid
              AND (attachments->'goodCard'->>'rid') IS NOT NULL
              AND (attachments->'goodCard'->>'rid') <> ''
            LIMIT 1
        """), {"cid": chat_id}).mappings().first()
        rid = rid_row["rid"] if rid_row else None

        # Также пробуем wb_chat_pending (для чатов через модуль переотправки)
        if not rid:
            p = conn.execute(text(
                "SELECT rid FROM wb_chat_pending WHERE chat_id = :cid AND rid IS NOT NULL LIMIT 1"
            ), {"cid": chat_id}).mappings().first()
            rid = p["rid"] if p else None

        # ── Конкретная претензия и отзыв этого клиента по его заказу ─────────
        specific_claim  = None
        specific_review = None
        if rid:
            # Архивные «На рассмотрении» старше окна WB — сразу «Закрыта» в БД
            # (без live-запроса к Returns API: он тормозит воркеры и валит сайт).
            conn.execute(text("""
                UPDATE wb_claims
                SET status = 'Закрыта',
                    status_ex = CASE
                      WHEN coalesce(nullif(trim(status_ex), ''), '') = ''
                      THEN 'Статус не получен из WB API (заявка вне окна выдачи)'
                      ELSE status_ex
                    END,
                    last_sync = NOW()
                WHERE srid = :rid
                  AND status IN ('На рассмотрении', '0', '0.0', 'Открыта')
                  AND is_archive = true
                  AND coalesce(claim_date, created_dt, last_sync) < NOW() - INTERVAL '14 days'
            """), {"rid": rid})
            conn.commit()

            cr = conn.execute(text("""
                SELECT claim_id::text, claim_type, user_comment AS body,
                       status, is_archive, claim_date
                FROM wb_claims WHERE srid = :rid LIMIT 1
            """), {"rid": rid}).mappings().first()
            if cr:
                c = _decode_claim(dict(cr))
                c["claim_date"] = _ru_date(c.get("claim_date"))
                specific_claim = c

            rv = conn.execute(text("""
                SELECT f.id::text, f.valuation AS rating, f.state,
                       f.text, f.pros, f.cons, f.answer_text AS answer, f.created_date
                FROM wb_feedbacks f
                JOIN wb_orders o ON o.shk_id = f.last_order_shk_id
                WHERE o.srid = :rid LIMIT 1
            """), {"rid": rid}).mappings().first()
            if rv:
                r = dict(rv)
                r["created_date"] = _ru_date(r.get("created_date"))
                specific_review = r

        # ── Все отзывы по товару (последние 5) ────────────────────────────────
        reviews = conn.execute(text("""
            SELECT id::text, valuation AS rating, state,
                   text, pros,
                   to_char(created_date, 'DD.MM.YY') AS created_date
            FROM wb_feedbacks WHERE nm_id = :nm
            ORDER BY created_date DESC LIMIT 5
        """), {"nm": nm_str}).mappings().all()

        questions = conn.execute(text("""
            SELECT q.id::text, q.text AS body, q.state,
                   to_char(q.created_date, 'DD.MM.YY') AS created_date
            FROM wb_questions q
            WHERE q.supplier_article IN (
                SELECT DISTINCT f.supplier_article FROM wb_feedbacks f
                WHERE f.nm_id = :nm AND f.supplier_article IS NOT NULL
            ) ORDER BY q.created_date DESC LIMIT 3
        """), {"nm": nm_str}).mappings().all()

        claims = conn.execute(text("""
            SELECT claim_id::text, claim_type, user_comment AS body,
                   status, is_archive,
                   to_char(claim_date, 'DD.MM.YY') AS claim_date
            FROM wb_claims WHERE nm_id = :nm
            ORDER BY claim_date DESC LIMIT 5
        """), {"nm": nm_str}).mappings().all()

    return {
        "specific": {"claim": specific_claim, "review": specific_review},
        "reviews":  [dict(r) for r in reviews],
        "questions":[dict(r) for r in questions],
        "claims":   [_decode_claim(dict(c)) for c in claims],
    }


@router.get("/wb-photo/{nm_id}")
def wb_product_photo(nm_id: str, db: Session = Depends(get_db)):
    """Фото товара WB — редирект на CDN URL из кэша Content API."""
    try:
        nm = int(nm_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid nm_id")
    with db.bind.connect() as conn:
        row = conn.execute(
            text("SELECT photo_url FROM wb_product_photos WHERE nm_id = :nm"),
            {"nm": nm}
        ).first()
    if row and row[0]:
        return RedirectResponse(row[0], status_code=302)
    raise HTTPException(status_code=404, detail="photo not cached yet")


@router.post("/wb-photos/sync")
def trigger_photo_sync(db: Session = Depends(get_db)):
    """Ручной запуск синхронизации фото товаров (для первого наполнения)."""
    from ..database import get_db as _get_db
    count = _sync_product_photos(_get_db)
    return {"synced": count}


@public_router.get("/chat-download/wb/{file_id}")
def download_wb_chat_file(file_id: str):
    """Прокси скачивания вложения WB (фото в чате)."""
    if not _wb_key():
        raise HTTPException(status_code=400, detail="WB ключ не настроен")
    if file_id.startswith("http"):
        return RedirectResponse(file_id)
    try:
        r = requests.get(
            f"{WB_CHAT_BASE}/api/v1/seller/download/{file_id}",
            headers={"Authorization": _wb_key()},
            timeout=25,
        )
        r.raise_for_status()
        return Response(
            content=r.content,
            media_type=r.headers.get("Content-Type", "image/jpeg"),
        )
    except requests.HTTPError as e:
        logger.error("[WBChatFile] %s → %s: %s", file_id, e.response.status_code if e.response else "?", e)
        raise HTTPException(status_code=502, detail=str(e))


@public_router.get("/chat-download/ozon")
def download_ozon_chat_file(url: str = Query(..., min_length=8)):
    """Прокси вложений Ozon (api-seller требует Client-Id/Api-Key)."""
    if not url.startswith("https://api-seller.ozon.ru/v2/chat/file/"):
        raise HTTPException(status_code=400, detail="Недопустимый URL файла Ozon")
    cid, key = _ozon_client_id(), _ozon_api_key()
    if not cid or not key:
        raise HTTPException(status_code=400, detail="Ozon ключи не настроены")
    try:
        r = requests.get(
            url,
            headers={"Client-Id": cid, "Api-Key": key},
            timeout=30,
        )
        r.raise_for_status()
        return Response(
            content=r.content,
            media_type=r.headers.get("Content-Type", "image/jpeg"),
        )
    except requests.HTTPError as e:
        logger.error("[OzonChatFile] %s → %s", url[:120], e.response.status_code if e.response else "?")
        raise HTTPException(status_code=502, detail=str(e))


@public_router.get("/chat-download/ym")
def download_ym_chat_file(url: str = Query(..., min_length=8)):
    """Прокси вложений ЯМ (если URL с partner API)."""
    if "api.partner.market.yandex.ru" not in url and "yandex" not in url:
        raise HTTPException(status_code=400, detail="Недопустимый URL файла ЯМ")
    key = _ym_key()
    headers = {"Api-Key": key} if key else {}
    try:
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        return Response(
            content=r.content,
            media_type=r.headers.get("Content-Type", "application/octet-stream"),
        )
    except requests.HTTPError as e:
        logger.error("[YmChatFile] %s → %s", url[:120], e.response.status_code if e.response else "?")
        raise HTTPException(status_code=502, detail=str(e))


# ── ИИ черновик ответа ───────────────────────────────────────────────────────

@router.get("/draft")
def get_ai_draft(
    item_type: str = Query(..., pattern="^(review|question|chat)$"),
    mp: str        = Query("wb", pattern="^(wb|ym|ozon)$"),
    body: str      = Query(""),
    rating: int    = Query(0),
    pros: str      = Query(""),
    cons: str      = Query(""),
    sku: str       = Query(""),
    db: Session    = Depends(get_db),
):
    """Генерирует черновик ответа через Claude Haiku, опираясь на фирменную базу шаблонов Vidovito."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY не настроен")

    mp_label = {"wb": "Wildberries", "ym": "Яндекс Маркет", "ozon": "Ozon"}.get(mp, mp)
    type_label = {"review": "отзыв", "question": "вопрос", "chat": "сообщение в чате"}.get(item_type, item_type)

    parts = []
    if sku:
        parts.append(f"Артикул: {sku}")
    if rating:
        parts.append(f"Оценка: {rating}/5")
    if pros:
        parts.append(f"Плюсы: {pros}")
    if cons:
        parts.append(f"Минусы: {cons}")
    if body:
        parts.append(f"Текст: {body}")

    context = "\n".join(parts) if parts else "Текст не указан"

    # Grounding на фирменных шаблонах: подбираем эталонный ответ из базы знаний
    reference = ""
    if item_type == "review" and sku:
        try:
            from . import kb
            full = ". ".join(p for p in [body, pros, cons] if p)
            ans, scen, _manual, _meta = kb.build_answer(db, sku, full, rating)
            if ans:
                reference = f"\n\nФирменный образец ответа ({scen}) — ориентируйся на его стиль, структуру и позицию бренда, но перефразируй по-своему, не копируй дословно:\n«{ans}»"
        except Exception:
            reference = ""

    system = (
        "Ты менеджер клиентского сервиса бренда товаров для дома Vidovito на маркетплейсе. "
        "Пиши живо, тепло и по-человечески, без канцелярита и клише. "
        "Отвечай только на русском языке. "
        "Структура ответа: сначала эмпатия/благодарность, затем решение или полезный совет, в конце — тёплое пожелание. "
        "Для негативных отзывов (1-3★) — искренне сожалей и предлагай конкретное решение (возврат по браку, «Чат с продавцом»). "
        "Для позитивных (4-5★) — благодари и добавляй лёгкий совет по эксплуатации. "
        "Не используй эмодзи. "
        "Не подписывайся и не добавляй прощание в конце — подпись добавится автоматически. "
        "Ответ должен быть готов к отправке — без заголовков и пояснений."
    )

    user_msg = (
        f"Маркетплейс: {mp_label}\n"
        f"Тип обращения: {type_label}\n"
        f"{context}"
        f"{reference}\n\n"
        "Напиши ответ продавца."
    )

    try:
        import anthropic as _anthropic
        # Прокси через Cloudflare Worker (api.anthropic.com заблокирован для RU-IP)
        kwargs = {"api_key": api_key}
        base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
        if base_url:
            kwargs["base_url"] = base_url
            proxy_token = os.getenv("ANTHROPIC_PROXY_TOKEN", "").strip()
            if proxy_token:
                kwargs["default_headers"] = {"x-proxy-token": proxy_token}
        client = _anthropic.Anthropic(**kwargs)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        draft = msg.content[0].text.strip()
        draft += "\n\nАлександр, команда Видовито"
        return {"draft": draft}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ИИ недоступен: {str(e)}")


# ── Ответ на отзыв WB ────────────────────────────────────────────────────────

class AnswerPayload(BaseModel):
    text: str


class ChatMessagePayload(BaseModel):
    text: str


@router.post("/wb/feedbacks/{feedback_id}/answer")
def answer_wb_feedback(
    feedback_id: str,
    payload: AnswerPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    txt = payload.text.strip()
    if not txt:
        raise HTTPException(status_code=400, detail="Текст ответа не может быть пустым")
    if not _wb_key():
        raise HTTPException(status_code=503, detail="WB_API_KEY не настроен")

    try:
        r = requests.patch(
            f"{WB_FEEDBACKS_BASE}/api/v1/feedbacks",
            headers=_wb_headers(),
            json={"id": feedback_id, "text": txt},
            timeout=20,
        )
        r.raise_for_status()
    except requests.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"WB API {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    with db.bind.connect() as conn:
        conn.execute(text(
            "UPDATE wb_feedbacks SET state = 'answered', answer_text = :ans WHERE id = :fid"
        ), {"ans": txt, "fid": feedback_id})
        conn.execute(text(
            "INSERT INTO cx_response_log (item_type, item_id, mp, answered_by, answer_text) "
            "VALUES ('review', :iid, 'wb', :user, :ans)"
        ), {"iid": feedback_id, "user": current_user["username"], "ans": txt})
        conn.execute(text(
            "DELETE FROM cx_pending_moderation WHERE item_type='review' AND mp='wb' AND item_id=:fid"
        ), {"fid": feedback_id})
        conn.commit()

    return {"status": "ok"}


# ── Ответ на вопрос WB ───────────────────────────────────────────────────────

@router.post("/wb/questions/{question_id}/answer")
def answer_wb_question(
    question_id: str,
    payload: AnswerPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    txt = payload.text.strip()
    if not txt:
        raise HTTPException(status_code=400, detail="Текст ответа не может быть пустым")
    if not _wb_key():
        raise HTTPException(status_code=503, detail="WB_API_KEY не настроен")

    try:
        r = requests.patch(
            f"{WB_FEEDBACKS_BASE}/api/v1/questions",
            headers=_wb_headers(),
            json={"id": question_id, "text": txt},
            timeout=20,
        )
        r.raise_for_status()
    except requests.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"WB API {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    with db.bind.connect() as conn:
        conn.execute(text(
            "UPDATE wb_questions SET state = 'answered', answer_text = :ans WHERE id = :qid"
        ), {"ans": txt, "qid": question_id})
        conn.execute(text(
            "INSERT INTO cx_response_log (item_type, item_id, mp, answered_by, answer_text) "
            "VALUES ('question', :iid, 'wb', :user, :ans)"
        ), {"iid": question_id, "user": current_user["username"], "ans": txt})
        conn.commit()

    return {"status": "ok"}


# ── Ответ на отзыв ЯМ ────────────────────────────────────────────────────────

@router.post("/ym/feedbacks/{feedback_id}/answer")
def answer_ym_feedback(
    feedback_id: str,
    payload: AnswerPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    txt = payload.text.strip()
    if not txt:
        raise HTTPException(status_code=400, detail="Текст ответа не может быть пустым")
    if not _ym_key() or not _ym_business_id():
        raise HTTPException(status_code=503, detail="YM_API_KEY или YM_BUSINESS_ID не настроены")

    try:
        r = requests.post(
            f"{YM_BASE}/v2/businesses/{_ym_business_id()}/goods-feedback/comments",
            headers=_ym_headers(),
            json={"feedbackId": feedback_id, "text": txt},
            timeout=20,
        )
        r.raise_for_status()
    except requests.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"ЯМ API {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    with db.bind.connect() as conn:
        conn.execute(text(
            "UPDATE ym_feedbacks SET answered_at = now(), answer_text = :ans WHERE feedback_id = :fid"
        ), {"ans": txt, "fid": feedback_id})
        conn.execute(text(
            "INSERT INTO cx_response_log (item_type, item_id, mp, answered_by, answer_text) "
            "VALUES ('review', :iid, 'ym', :user, :ans)"
        ), {"iid": feedback_id, "user": current_user["username"], "ans": txt})
        conn.execute(text(
            "DELETE FROM cx_pending_moderation WHERE item_type='review' AND mp='ym' AND item_id=:fid"
        ), {"fid": feedback_id})
        conn.commit()

    return {"status": "ok"}


# ── Ответ на вопрос ЯМ ───────────────────────────────────────────────────────

@router.post("/ym/questions/{question_id}/answer")
def answer_ym_question(
    question_id: str,
    payload: AnswerPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    txt = payload.text.strip()
    if not txt:
        raise HTTPException(status_code=400, detail="Текст ответа не может быть пустым")
    if not _ym_key() or not _ym_business_id():
        raise HTTPException(status_code=503, detail="YM_API_KEY или YM_BUSINESS_ID не настроены")

    try:
        r = requests.post(
            f"{YM_BASE}/v1/businesses/{_ym_business_id()}/questions/{question_id}/answers",
            headers=_ym_headers(),
            json={"text": txt},
            timeout=20,
        )
        r.raise_for_status()
    except requests.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"ЯМ API {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    with db.bind.connect() as conn:
        conn.execute(text(
            "UPDATE ym_questions SET state = 'ANSWERED', answer_text = :ans WHERE question_id = :qid"
        ), {"ans": txt, "qid": question_id})
        conn.execute(text(
            "INSERT INTO cx_response_log (item_type, item_id, mp, answered_by, answer_text) "
            "VALUES ('question', :iid, 'ym', :user, :ans)"
        ), {"iid": question_id, "user": current_user["username"], "ans": txt})
        conn.commit()

    return {"status": "ok"}


# ── Ответ на отзыв Ozon ──────────────────────────────────────────────────────

OZON_BASE = "https://api-seller.ozon.ru"


def _oz_key() -> str:
    return os.getenv("OZON_API_KEY", "")


def _oz_cid() -> str:
    return os.getenv("OZON_CLIENT_ID", "")


def _oz_headers() -> dict:
    return {"Client-Id": _oz_cid(), "Api-Key": _oz_key(), "Content-Type": "application/json"}


@router.post("/ozon/reviews/{review_id}/answer")
def answer_ozon_review(
    review_id: str,
    payload: AnswerPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    txt = payload.text.strip()
    if not txt:
        raise HTTPException(status_code=400, detail="Текст ответа не может быть пустым")
    if not _oz_key() or not _oz_cid():
        raise HTTPException(status_code=503, detail="OZON_API_KEY / OZON_CLIENT_ID не настроены")

    try:
        r = requests.post(
            f"{OZON_BASE}/v1/review/comment/create",
            headers=_oz_headers(),
            json={"review_uuid": review_id, "text": txt},
            timeout=20,
        )
        r.raise_for_status()
    except requests.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Ozon API {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    with db.bind.connect() as conn:
        conn.execute(text(
            "UPDATE ozon_reviews SET answered_at = now(), answer_text = :ans WHERE review_id = :rid"
        ), {"ans": txt, "rid": review_id})
        conn.execute(text(
            "INSERT INTO cx_response_log (item_type, item_id, mp, answered_by, answer_text) "
            "VALUES ('review', :iid, 'ozon', :user, :ans)"
        ), {"iid": review_id, "user": current_user["username"], "ans": txt})
        conn.execute(text(
            "DELETE FROM cx_pending_moderation WHERE item_type='review' AND mp='ozon' AND item_id=:rid"
        ), {"rid": review_id})
        conn.commit()

    return {"status": "ok"}


# ── Ответ в чат Ozon ─────────────────────────────────────────────────────────

@router.post("/ozon/chats/{chat_id}/message")
def send_ozon_chat_message(
    chat_id: str,
    payload: ChatMessagePayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    txt = payload.text.strip()
    if not txt:
        raise HTTPException(status_code=400, detail="Текст сообщения не может быть пустым")
    if not _oz_key() or not _oz_cid():
        raise HTTPException(status_code=503, detail="OZON_API_KEY / OZON_CLIENT_ID не настроены")

    try:
        r = requests.post(
            f"{OZON_BASE}/v1/chat/send/message",
            headers=_oz_headers(),
            json={"chat_id": chat_id, "text": txt[:1000]},
            timeout=20,
        )
        r.raise_for_status()
    except requests.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Ozon Chat API {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    with db.bind.connect() as conn:
        conn.execute(text(
            "UPDATE ozon_chats SET needs_answer = false, last_sender = 'seller', "
            "last_message = :msg, unread_count = 0, waiting_since = NULL, updated_at = now() "
            "WHERE chat_id = :cid"
        ), {"cid": chat_id, "msg": txt[:500]})
        conn.execute(text(
            "INSERT INTO ozon_chat_messages (message_id, chat_id, sender_type, text, created_at) "
            "VALUES (:mid, :cid, 'seller', :txt, now()) "
            "ON CONFLICT (message_id) DO NOTHING"
        ), {"mid": f"local-{chat_id}-{int(time.time()*1000)}", "cid": chat_id, "txt": txt[:4000]})
        conn.execute(text(
            "INSERT INTO cx_response_log (item_type, item_id, mp, answered_by, answer_text) "
            "VALUES ('chat', :iid, 'ozon', :user, :ans)"
        ), {"iid": chat_id, "user": current_user["username"], "ans": txt})
        conn.commit()

    return {"status": "ok"}


@router.post("/ym/chats/{chat_id}/message")
def send_ym_chat_message(
    chat_id: str,
    payload: ChatMessagePayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    txt = payload.text.strip()
    if not txt:
        raise HTTPException(status_code=400, detail="Текст сообщения не может быть пустым")
    if not _ym_key() or not _ym_business_id():
        raise HTTPException(status_code=503, detail="YM_API_KEY / YM_BUSINESS_ID не настроены")

    try:
        from .. import ym_chat_sync
        ym_chat_sync.send_message(chat_id, txt)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except requests.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"YM Chat API {e.response.status_code}: {e.response.text[:200]}",
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    with db.bind.connect() as conn:
        conn.execute(text(
            "UPDATE ym_chats SET needs_answer = false, last_sender = 'seller', "
            "last_message = :msg, waiting_since = NULL, status = 'WAITING_FOR_CUSTOMER', "
            "updated_at = now() WHERE chat_id = :cid"
        ), {"cid": chat_id, "msg": txt[:500]})
        conn.execute(text(
            "INSERT INTO ym_chat_messages (message_id, chat_id, sender_type, text, created_at) "
            "VALUES (:mid, :cid, 'seller', :txt, now()) "
            "ON CONFLICT (message_id) DO NOTHING"
        ), {"mid": f"local-{chat_id}-{int(time.time()*1000)}", "cid": chat_id, "txt": txt[:4000]})
        conn.execute(text(
            "INSERT INTO cx_response_log (item_type, item_id, mp, answered_by, answer_text) "
            "VALUES ('chat', :iid, 'ym', :user, :ans)"
        ), {"iid": chat_id, "user": current_user["username"], "ans": txt})
        conn.commit()

    return {"status": "ok"}


# ── Ответ в чат WB ───────────────────────────────────────────────────────────

@router.post("/wb/chats/{chat_id}/message")
def send_wb_chat_message(
    chat_id: str,
    payload: ChatMessagePayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    txt = payload.text.strip()
    if not txt:
        raise HTTPException(status_code=400, detail="Текст сообщения не может быть пустым")
    if not _wb_key():
        raise HTTPException(status_code=503, detail="WB_API_KEY не настроен")

    # 1) Кэш в wb_chats  2) pending / заявка  3) свежий replySign из WB API
    reply_sign = ""
    with db.bind.connect() as conn:
        row = conn.execute(text(
            "SELECT reply_sign FROM wb_chats WHERE chat_id = :cid"
        ), {"cid": chat_id}).first()
        if row and row[0]:
            reply_sign = (row[0] or "").strip()
        if not reply_sign:
            row = conn.execute(text(
                "SELECT reply_sign FROM wb_chat_pending WHERE chat_id = :cid "
                "AND coalesce(reply_sign,'') <> '' LIMIT 1"
            ), {"cid": chat_id}).first()
            if row and row[0]:
                reply_sign = (row[0] or "").strip()
        if not reply_sign:
            row = conn.execute(text(
                "SELECT wb_reply_sign FROM reshipment_requests WHERE wb_chat_id = :cid "
                "AND coalesce(wb_reply_sign,'') <> '' "
                "ORDER BY id DESC LIMIT 1"
            ), {"cid": chat_id}).first()
            if row and row[0]:
                reply_sign = (row[0] or "").strip()

    # Кэш часто пустой: синк событий не пишет reply_sign. Берём из списка чатов WB.
    # Старые/короткие подписи WB отклоняет — предпочитаем свежую (формат version:uuid:sig).
    try:
        from .. import wb_chat as _wbc
        fresh = _wbc.resolve_reply_sign(chat_id)
        if fresh:
            reply_sign = fresh
    except Exception:
        pass

    if not reply_sign:
        raise HTTPException(
            status_code=404,
            detail="Не удалось получить ключ чата у Wildberries. Обновите список чатов и попробуйте снова.",
        )

    try:
        r = requests.post(
            f"{WB_CHAT_BASE}/api/v1/seller/message",
            files={"replySign": (None, reply_sign), "message": (None, txt[:1000])},
            headers={"Authorization": _wb_key()},
            timeout=20,
        )
        r.raise_for_status()
    except requests.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"WB Chat API {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Сохраняем свежий reply_sign для следующих ответов
    try:
        with db.bind.connect() as conn:
            conn.execute(text("""
                UPDATE wb_chats SET reply_sign = :rs, updated_at = now()
                WHERE chat_id = :cid
            """), {"rs": reply_sign, "cid": chat_id})
            conn.commit()
    except Exception:
        pass

    import time as _t
    now_ms = int(_t.time() * 1000)
    with db.bind.connect() as conn:
        conn.execute(text(
            "UPDATE wb_chats SET needs_answer = false, last_sender = 'seller', "
            "last_message = :ans, last_ts = :ts, updated_at = now() WHERE chat_id = :cid"
        ), {"cid": chat_id, "ans": txt[:1000], "ts": now_ms})
        conn.execute(text(
            "INSERT INTO cx_response_log (item_type, item_id, mp, answered_by, answer_text) "
            "VALUES ('chat', :iid, 'wb', :user, :ans)"
        ), {"iid": chat_id, "user": current_user["username"], "ans": txt})
        # В ленту — только если такого текста ещё нет (воркер WB чуть позже
        # принесёт то же сообщение с другим add_ts).
        exists = conn.execute(text("""
            SELECT id FROM wb_chat_messages
            WHERE chat_id = :cid AND sender = 'seller' AND text = :txt
              AND add_ts BETWEEN :t0 AND :t1
            LIMIT 1
        """), {"cid": chat_id, "txt": txt[:1000],
               "t0": now_ms - 180_000, "t1": now_ms + 180_000}).first()
        if not exists:
            conn.execute(text("""
                INSERT INTO wb_chat_messages (chat_id, sender, text, add_ts)
                VALUES (:cid, 'seller', :txt, :ts)
                ON CONFLICT (chat_id, add_ts, sender) DO NOTHING
            """), {"cid": chat_id, "txt": txt[:1000], "ts": now_ms})
        conn.commit()

    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# ШАБЛОНЫ ОТВЕТОВ
# ══════════════════════════════════════════════════════════════════════════════

class TemplatePayload(BaseModel):
    title: str
    body: str
    mp: str = "all"
    item_type: str = "all"
    tone: str = "all"
    auto_reply: bool = False
    min_rating: int | None = None
    max_rating: int | None = None
    require_no_text: bool = False
    stop_words: str = ""
    ignore_questions: bool = False
    requires_confirmation: bool = False


@router.get("/templates")
def list_templates(
    mp: str = Query("all"),
    item_type: str = Query("all"),
    db: Session = Depends(get_db),
):
    conds = ["1=1"]
    params: dict = {}
    if mp != "all":
        conds.append("(mp = :mp OR mp = 'all')")
        params["mp"] = mp
    if item_type != "all":
        conds.append("(item_type = :it OR item_type = 'all')")
        params["it"] = item_type

    with db.bind.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT id, title, body, mp, item_type, tone,
                   auto_reply, min_rating, max_rating, require_no_text,
                   COALESCE(stop_words,'') AS stop_words,
                   COALESCE(ignore_questions,false) AS ignore_questions,
                   COALESCE(requires_confirmation,false) AS requires_confirmation,
                   created_by, created_at
            FROM cx_response_templates
            WHERE {' AND '.join(conds)}
            ORDER BY item_type, tone, title
        """), params).mappings().all()

    def _fmt(r):
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        return d

    return {"templates": [_fmt(r) for r in rows]}


@router.post("/templates")
def create_template(
    payload: TemplatePayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if not payload.title.strip() or not payload.body.strip():
        raise HTTPException(status_code=400, detail="Заголовок и текст обязательны")

    with db.bind.connect() as conn:
        row = conn.execute(text("""
            INSERT INTO cx_response_templates
                (title, body, mp, item_type, tone, auto_reply,
                 min_rating, max_rating, require_no_text,
                 stop_words, ignore_questions, requires_confirmation, created_by)
            VALUES (:title,:body,:mp,:it,:tone,:auto,:minr,:maxr,:notxt,
                    :sw,:iq,:rc,:user)
            RETURNING id
        """), {
            "title": payload.title.strip(), "body": payload.body.strip(),
            "mp": payload.mp, "it": payload.item_type, "tone": payload.tone,
            "auto": payload.auto_reply, "minr": payload.min_rating,
            "maxr": payload.max_rating, "notxt": payload.require_no_text,
            "sw": payload.stop_words, "iq": payload.ignore_questions,
            "rc": payload.requires_confirmation,
            "user": current_user["username"],
        }).first()
        conn.commit()

    return {"status": "ok", "id": row[0]}


@router.put("/templates/{tpl_id}")
def update_template(tpl_id: int, payload: TemplatePayload, db: Session = Depends(get_db)):
    if not payload.title.strip() or not payload.body.strip():
        raise HTTPException(status_code=400, detail="Заголовок и текст обязательны")

    with db.bind.connect() as conn:
        res = conn.execute(text("""
            UPDATE cx_response_templates
            SET title=:title, body=:body, mp=:mp, item_type=:it, tone=:tone,
                auto_reply=:auto, min_rating=:minr, max_rating=:maxr,
                require_no_text=:notxt, stop_words=:sw,
                ignore_questions=:iq, requires_confirmation=:rc,
                updated_at=now()
            WHERE id=:id
        """), {
            "title": payload.title.strip(), "body": payload.body.strip(),
            "mp": payload.mp, "it": payload.item_type, "tone": payload.tone,
            "auto": payload.auto_reply, "minr": payload.min_rating,
            "maxr": payload.max_rating, "notxt": payload.require_no_text,
            "sw": payload.stop_words, "iq": payload.ignore_questions,
            "rc": payload.requires_confirmation, "id": tpl_id,
        })
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
        conn.commit()

    return {"status": "ok"}


@router.delete("/templates/{tpl_id}")
def delete_template(tpl_id: int, db: Session = Depends(get_db)):
    with db.bind.connect() as conn:
        res = conn.execute(text("DELETE FROM cx_response_templates WHERE id=:id"), {"id": tpl_id})
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
        conn.commit()
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# МОДЕРАЦИЯ ЧЕРНОВИКОВ
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/moderation/{mod_id}/confirm")
def confirm_moderation(
    mod_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Подтвердить и отправить черновик из очереди модерации."""
    with db.bind.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM cx_pending_moderation WHERE id=:id"
        ), {"id": mod_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Черновик не найден")

    mp, itype, item_id, txt = row["mp"], row["item_type"], row["item_id"], row["draft_text"]

    # Отправляем через соответствующий API
    try:
        if mp == "wb" and itype == "review":
            r = requests.patch(f"{WB_FEEDBACKS_BASE}/api/v1/feedbacks",
                headers=_wb_headers(), json={"id": item_id, "text": txt}, timeout=20)
            r.raise_for_status()
            with db.bind.connect() as conn:
                conn.execute(text("UPDATE wb_feedbacks SET state='answered',answer_text=:a WHERE id=:i"),
                             {"a": txt, "i": item_id})
                conn.commit()

        elif mp == "wb" and itype == "question":
            r = requests.patch(f"{WB_FEEDBACKS_BASE}/api/v1/questions",
                headers=_wb_headers(), json={"id": item_id, "text": txt}, timeout=20)
            r.raise_for_status()
            with db.bind.connect() as conn:
                conn.execute(text("UPDATE wb_questions SET state='answered',answer_text=:a WHERE id=:i"),
                             {"a": txt, "i": item_id})
                conn.commit()

        elif mp == "ym" and itype == "review":
            r = requests.post(
                f"{YM_BASE}/v2/businesses/{_ym_business_id()}/goods-feedback/comments",
                headers=_ym_headers(), json={"feedbackId": item_id, "text": txt}, timeout=20)
            r.raise_for_status()
            with db.bind.connect() as conn:
                conn.execute(text("UPDATE ym_feedbacks SET answered_at=now(),answer_text=:a WHERE feedback_id=:i"),
                             {"a": txt, "i": item_id})
                conn.commit()

        else:
            raise HTTPException(status_code=400, detail=f"Подтверждение не поддержано для {mp}/{itype}")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ошибка отправки: {e}")

    # Логируем и удаляем из модерации
    with db.bind.connect() as conn:
        conn.execute(text(
            "INSERT INTO cx_response_log(item_type,item_id,mp,answered_by,answer_text)"
            " VALUES(:it,:iid,:mp,:user,:txt)"
        ), {"it": itype, "iid": item_id, "mp": mp,
            "user": current_user["username"], "txt": txt})
        conn.execute(text("DELETE FROM cx_pending_moderation WHERE id=:id"), {"id": mod_id})
        conn.commit()

    return {"status": "ok"}


@router.delete("/moderation/{mod_id}")
def reject_moderation(mod_id: int, db: Session = Depends(get_db)):
    """Отклонить черновик — удалить из очереди модерации без отправки."""
    with db.bind.connect() as conn:
        res = conn.execute(text("DELETE FROM cx_pending_moderation WHERE id=:id"), {"id": mod_id})
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Черновик не найден")
        conn.commit()
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# СТАТИСТИКА МЕНЕДЖЕРОВ
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/stats")
def get_stats(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    with db.bind.connect() as conn:
        managers = conn.execute(text("""
            SELECT
                COALESCE(answered_by, '—')                             AS manager,
                COUNT(*)                                               AS total,
                COUNT(*) FILTER (WHERE item_type='review')             AS reviews,
                COUNT(*) FILTER (WHERE item_type='question')           AS questions,
                COUNT(*) FILTER (WHERE item_type='chat')               AS chats,
                COUNT(*) FILTER (WHERE mp='wb')                        AS wb,
                COUNT(*) FILTER (WHERE mp='ym')                        AS ym,
                COUNT(*) FILTER (WHERE mp='ozon')                      AS ozon,
                COUNT(*) FILTER (WHERE answered_by='auto_reply')       AS auto_sent,
                MAX(sent_at)                                           AS last_at
            FROM cx_response_log
            WHERE sent_at >= now() - make_interval(days => :days)
            GROUP BY answered_by
            ORDER BY total DESC
        """), {"days": days}).mappings().all()

        daily = conn.execute(text("""
            SELECT
                DATE(sent_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow') AS day,
                COUNT(*)                                                        AS total,
                COUNT(*) FILTER (WHERE answered_by != 'auto_reply')            AS manual
            FROM cx_response_log
            WHERE sent_at >= now() - make_interval(days => :days)
            GROUP BY day ORDER BY day
        """), {"days": days}).mappings().all()

        by_mp   = conn.execute(text(
            "SELECT mp, COUNT(*) AS cnt FROM cx_response_log "
            "WHERE sent_at >= now()-make_interval(days=>:days) GROUP BY mp"
        ), {"days": days}).mappings().all()

        by_type = conn.execute(text(
            "SELECT item_type, COUNT(*) AS cnt FROM cx_response_log "
            "WHERE sent_at >= now()-make_interval(days=>:days) GROUP BY item_type"
        ), {"days": days}).mappings().all()

    def _m(r):
        d = dict(r)
        if d.get("last_at"): d["last_at"] = d["last_at"].isoformat()
        return d

    return {
        "days":     days,
        "managers": [_m(r) for r in managers],
        "daily":    [{"day": str(r["day"]), "total": r["total"], "manual": r["manual"]} for r in daily],
        "by_mp":    {r["mp"]: r["cnt"] for r in by_mp},
        "by_type":  {r["item_type"]: r["cnt"] for r in by_type},
    }


# ══════════════════════════════════════════════════════════════════════════════
# АВТО-ОТВЕТЫ
# ══════════════════════════════════════════════════════════════════════════════

def _check_stop_words(text_content: str, stop_words_raw: str) -> bool:
    """True если нужно пропустить (найдено стоп-слово)."""
    if not stop_words_raw:
        return False
    words = [w.strip().lower() for w in stop_words_raw.split("\n") if w.strip()]
    haystack = text_content.lower()
    return any(sw in haystack for sw in words)


def _run_auto_replies(db) -> dict:
    sent = 0
    queued = 0  # положено в модерацию

    with db.bind.connect() as conn:
        templates = conn.execute(text("""
            SELECT id, title, body, mp, item_type, min_rating, max_rating,
                   require_no_text, COALESCE(stop_words,'') AS stop_words,
                   COALESCE(ignore_questions,false) AS ignore_questions,
                   COALESCE(requires_confirmation,false) AS requires_confirmation
            FROM cx_response_templates WHERE auto_reply = true ORDER BY id
        """)).mappings().all()

    if not templates:
        return {"sent": 0, "queued": 0}

    for tpl in templates:
        mp, itype, body_tpl = tpl["mp"], tpl["item_type"], tpl["body"]
        min_r, max_r, no_txt = tpl["min_rating"], tpl["max_rating"], tpl["require_no_text"]
        stop_words_raw = tpl["stop_words"] or ""
        ign_q  = tpl["ignore_questions"]
        req_confirm = tpl["requires_confirmation"]
        tpl_id    = tpl["id"]
        tpl_title = tpl["title"]

        if itype in ("review", "all") and mp in ("wb", "all") and _wb_key():
            conds = ["state = 'active'"]
            if min_r is not None: conds.append(f"valuation >= {int(min_r)}")
            if max_r is not None: conds.append(f"valuation <= {int(max_r)}")
            if no_txt: conds.append("TRIM(COALESCE(text,''))='' AND TRIM(COALESCE(pros,''))='' AND TRIM(COALESCE(cons,''))=''")
            with db.bind.connect() as conn:
                rows = conn.execute(text(
                    f"SELECT id, COALESCE(text,'') AS t, COALESCE(pros,'') AS p, COALESCE(cons,'') AS c"
                    f" FROM wb_feedbacks WHERE {' AND '.join(conds)}"
                    " AND id::text NOT IN (SELECT item_id FROM cx_response_log WHERE mp='wb' AND item_type='review')"
                    " AND id::text NOT IN (SELECT item_id FROM cx_pending_moderation WHERE mp='wb' AND item_type='review')"
                    " LIMIT 20"
                )).fetchall()

            for row in rows:
                full_text = f"{row[1]} {row[2]} {row[3]}"
                if _check_stop_words(full_text, stop_words_raw):
                    continue
                if ign_q and "?" in full_text:
                    continue
                iid = str(row[0])

                if req_confirm:
                    with db.bind.connect() as conn:
                        conn.execute(text(
                            "INSERT INTO cx_pending_moderation"
                            " (item_type,item_id,mp,draft_text,template_id,template_title)"
                            " VALUES('review',:iid,'wb',:txt,:tid,:ttl)"
                            " ON CONFLICT (item_type,item_id,mp) DO NOTHING"
                        ), {"iid": iid, "txt": body_tpl, "tid": tpl_id, "ttl": tpl_title})
                        conn.commit()
                    queued += 1
                else:
                    try:
                        r = requests.patch(f"{WB_FEEDBACKS_BASE}/api/v1/feedbacks",
                            headers=_wb_headers(), json={"id": iid, "text": body_tpl}, timeout=20)
                        r.raise_for_status()
                        with db.bind.connect() as conn:
                            conn.execute(text("UPDATE wb_feedbacks SET state='answered',answer_text=:a WHERE id=:i"),
                                         {"a": body_tpl, "i": iid})
                            conn.execute(text("INSERT INTO cx_response_log(item_type,item_id,mp,answered_by,answer_text)"
                                             " VALUES('review',:i,'wb','auto_reply',:a)"),
                                         {"i": iid, "a": body_tpl})
                            conn.commit()
                        sent += 1
                    except Exception:
                        pass

        if itype in ("review", "all") and mp in ("ym", "all") and _ym_key() and _ym_business_id():
            conds = ["answered_at IS NULL"]
            if min_r is not None: conds.append(f"valuation >= {int(min_r)}")
            if max_r is not None: conds.append(f"valuation <= {int(max_r)}")
            if no_txt: conds.append("TRIM(COALESCE(comment,''))='' AND TRIM(COALESCE(pro_text,''))='' AND TRIM(COALESCE(contra_text,''))=''")
            with db.bind.connect() as conn:
                rows = conn.execute(text(
                    f"SELECT feedback_id,"
                    f" COALESCE(comment,'') AS t, COALESCE(pro_text,'') AS p, COALESCE(contra_text,'') AS c"
                    f" FROM ym_feedbacks WHERE {' AND '.join(conds)}"
                    " AND feedback_id::text NOT IN (SELECT item_id FROM cx_response_log WHERE mp='ym' AND item_type='review')"
                    " AND feedback_id::text NOT IN (SELECT item_id FROM cx_pending_moderation WHERE mp='ym' AND item_type='review')"
                    " LIMIT 20"
                )).fetchall()

            for row in rows:
                full_text = f"{row[1]} {row[2]} {row[3]}"
                if _check_stop_words(full_text, stop_words_raw):
                    continue
                if ign_q and "?" in full_text:
                    continue
                iid = str(row[0])

                if req_confirm:
                    with db.bind.connect() as conn:
                        conn.execute(text(
                            "INSERT INTO cx_pending_moderation"
                            " (item_type,item_id,mp,draft_text,template_id,template_title)"
                            " VALUES('review',:iid,'ym',:txt,:tid,:ttl)"
                            " ON CONFLICT (item_type,item_id,mp) DO NOTHING"
                        ), {"iid": iid, "txt": body_tpl, "tid": tpl_id, "ttl": tpl_title})
                        conn.commit()
                    queued += 1
                else:
                    try:
                        r = requests.post(
                            f"{YM_BASE}/v2/businesses/{_ym_business_id()}/goods-feedback/comments",
                            headers=_ym_headers(), json={"feedbackId": row[0], "text": body_tpl}, timeout=20)
                        r.raise_for_status()
                        with db.bind.connect() as conn:
                            conn.execute(text("UPDATE ym_feedbacks SET answered_at=now(),answer_text=:a WHERE feedback_id=:i"),
                                         {"a": body_tpl, "i": row[0]})
                            conn.execute(text("INSERT INTO cx_response_log(item_type,item_id,mp,answered_by,answer_text)"
                                             " VALUES('review',:i,'ym','auto_reply',:a)"),
                                         {"i": str(row[0]), "a": body_tpl})
                            conn.commit()
                        sent += 1
                    except Exception:
                        pass

    return {"sent": sent, "queued": queued}


@router.post("/auto-reply/run")
def run_auto_replies(db: Session = Depends(get_db)):
    result = _run_auto_replies(db)
    return {"status": "ok", **result}


@router.get("/auto-reply/preview")
def preview_auto_replies(db: Session = Depends(get_db)):
    with db.bind.connect() as conn:
        templates = conn.execute(text(
            "SELECT id,title,body,mp,item_type,min_rating,max_rating,require_no_text"
            " FROM cx_response_templates WHERE auto_reply=true"
        )).mappings().all()
        wb_el = conn.execute(text(
            "SELECT COUNT(*) FROM wb_feedbacks WHERE state='active'"
            " AND id::text NOT IN (SELECT item_id FROM cx_response_log WHERE mp='wb' AND item_type='review')"
        )).scalar()
        ym_el = conn.execute(text(
            "SELECT COUNT(*) FROM ym_feedbacks WHERE answered_at IS NULL"
            " AND feedback_id NOT IN (SELECT item_id FROM cx_response_log WHERE mp='ym' AND item_type='review')"
        )).scalar()

    return {"templates_active": len(templates), "wb_eligible": wb_el or 0,
            "ym_eligible": ym_el or 0, "templates": [dict(t) for t in templates]}
