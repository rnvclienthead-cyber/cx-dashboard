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

_PHOTO_SYNC_INTERVAL = 86400   # раз в 24 часа


def _sync_product_photos(get_db_func) -> int:
    """Загружает фото товаров из WB Content API v2 и сохраняет в wb_product_photos."""
    # Приоритет: WB_CONTENT_KEY (отдельный токен с правом "Контент"),
    # иначе WB_API_KEY как запасной вариант
    key = wb_client.content_key()
    if not key:
        logger.warning("[PhotoSync] нет WB_CONTENT_KEY или WB_API_KEY")
        return 0

    saved = 0
    cursor: dict = {}

    while True:
        try:
            r = wb_client.list_cards_page_raw(cursor, 100, key=key, timeout=30)
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
from .. import wb_client, ym_client, ozon_client


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
    return wb_client.api_key()


def _ym_key() -> str:
    return ym_client.api_key()


def _ym_business_id() -> str:
    return ym_client.business_id()


def _ym_campaign_id() -> str:
    return ym_client.campaign_id()


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
    return ozon_client.client_id()


def _ozon_api_key() -> str:
    return ozon_client.api_key()


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


# ── Очередь ──────────────────────────────────────────────────────────────────
#
# cx_unified_queue — настоящий SQL VIEW (не Python-строка), объединяющий
# отзывы/вопросы/чаты/претензии всех площадок для /queue. Определение и
# миграция — в init_unified_queue_view() ниже, вызывается один раз при
# старте приложения (backend/main.py). Роутер только читает из готового
# VIEW по имени — единый источник правды для схемы очереди.
UNIFIED_QUEUE_VIEW = "cx_unified_queue"

_UNIFIED_QUEUE_VIEW_SQL = """
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
        -- sku = наш артикул (для product_photos); не подставляем номер заказа
        COALESCE(
            NULLIF(TRIM(c.context_article), ''),
            (
              SELECT o.supplier_article FROM ozon_orders o
              WHERE NULLIF(TRIM(c.order_number), '') IS NOT NULL
                AND (
                  o.posting_number = c.order_number
                  OR o.order_number = c.order_number
                  OR o.posting_number LIKE c.order_number || '-%'
                )
                AND NULLIF(TRIM(o.supplier_article), '') IS NOT NULL
              ORDER BY o.created_at DESC NULLS LAST
              LIMIT 1
            )
        ),
        c.context_sku::text,
        c.last_message, NULL::int, NULL::text, NULL::text,
        NULL::text, c.order_number::text,
        CASE
          WHEN NULLIF(TRIM(c.context_article), '') IS NOT NULL
            THEN TRIM(c.context_article)
          WHEN NULLIF(TRIM(c.order_number), '') IS NOT NULL
            THEN 'Заказ ' || TRIM(c.order_number)
          WHEN c.context_sku IS NOT NULL
            THEN 'Ozon · SKU ' || c.context_sku::text
          ELSE 'Покупатель'
        END,
        COALESCE(
            (SELECT MAX(m.created_at) FROM ozon_chat_messages m
             WHERE m.chat_id = c.chat_id
               AND m.sender_type IN ('customer', 'buyer', 'client')),
            c.waiting_since, c.created_at, c.updated_at, now()
        ),
        EXTRACT(epoch FROM now() - COALESCE(
            (SELECT MAX(m.created_at) FROM ozon_chat_messages m
             WHERE m.chat_id = c.chat_id
               AND m.sender_type IN ('customer', 'buyer', 'client')),
            c.waiting_since, c.created_at, c.updated_at, now()
        ))::int,
        -- last_ts для UI: время последнего сообщения (не touch синка!)
        EXTRACT(epoch FROM COALESCE(
            (SELECT MAX(m.created_at) FROM ozon_chat_messages m WHERE m.chat_id = c.chat_id),
            c.waiting_since, c.created_at, c.updated_at, now()
        ))::bigint * 1000,
        c.needs_answer,
        c.last_sender
    FROM ozon_chats c
    WHERE c.chat_status ILIKE 'OPENED'
      AND (c.chat_type IS NULL OR upper(c.chat_type) LIKE '%BUYER%')
      -- как WB: только активность за 30 дней (по сообщениям, не по sync touch)
      AND COALESCE(
            (SELECT MAX(m.created_at) FROM ozon_chat_messages m WHERE m.chat_id = c.chat_id),
            c.waiting_since, c.created_at, c.updated_at, now()
          ) > now() - INTERVAL '30 days'
      -- мы ответили, клиент молчит >3 дн — не держим в очереди (уходят в архив)
      AND NOT (
            lower(COALESCE(c.last_sender, '')) IN ('seller', 'courier')
            AND COALESCE(
              (SELECT MAX(m.created_at) FROM ozon_chat_messages m WHERE m.chat_id = c.chat_id),
              c.waiting_since, c.created_at, c.updated_at, now()
            ) < now() - INTERVAL '3 days'
          )
      AND c.chat_id NOT IN
          (SELECT item_id FROM cx_dismissed_items WHERE mp='ozon' AND item_type='chat')

    UNION ALL

    SELECT
        c.chat_id, 'ym', 'chat',
        COALESCE(
            (
              SELECT o.supplier_article FROM ym_orders o
              WHERE c.order_id IS NOT NULL
                AND o.order_id::text = c.order_id
                AND NULLIF(TRIM(o.supplier_article), '') IS NOT NULL
              LIMIT 1
            ),
            (
              SELECT r.supplier_article FROM ym_returns r
              WHERE (
                (c.return_id IS NOT NULL AND r.return_id::text = c.return_id)
                OR (c.order_id IS NOT NULL AND r.order_id::text = c.order_id)
              )
              AND NULLIF(TRIM(r.supplier_article), '') IS NOT NULL
              ORDER BY r.created_at DESC NULLS LAST
              LIMIT 1
            )
        ),
        NULL::text,
        c.last_message, NULL::int, NULL::text, NULL::text,
        NULL::text, c.order_id,
        COALESCE(NULLIF(TRIM(c.customer_name), ''), 'Покупатель'),
        COALESCE(
            (SELECT MAX(m.created_at) FROM ym_chat_messages m
             WHERE m.chat_id = c.chat_id
               AND lower(COALESCE(m.sender_type, '')) IN ('customer', 'buyer', 'client', 'user')),
            c.waiting_since, c.updated_at, c.created_at, now()
        ),
        EXTRACT(epoch FROM now()-COALESCE(
            (SELECT MAX(m.created_at) FROM ym_chat_messages m
             WHERE m.chat_id = c.chat_id
               AND lower(COALESCE(m.sender_type, '')) IN ('customer', 'buyer', 'client', 'user')),
            c.waiting_since, c.updated_at, c.created_at, now()
        ))::int,
        EXTRACT(epoch FROM COALESCE(
            (SELECT MAX(m.created_at) FROM ym_chat_messages m WHERE m.chat_id = c.chat_id),
            c.waiting_since, c.updated_at, c.created_at, now()
        ))::bigint * 1000,
        c.needs_answer,
        c.last_sender
    FROM ym_chats c
    WHERE COALESCE(
            (SELECT MAX(m.created_at) FROM ym_chat_messages m WHERE m.chat_id = c.chat_id),
            c.waiting_since, c.updated_at, c.created_at, now()
          ) > now() - INTERVAL '30 days'
      AND c.chat_id NOT IN
          (SELECT item_id FROM cx_dismissed_items WHERE mp='ym' AND item_type='chat')

    UNION ALL

    -- WB претензии/возвраты: заявки, ожидающие решения продавца (Фаза 3 —
    -- возвраты как полноценная 4-я ветка общей очереди, наравне с
    -- отзывами/вопросами/чатами). id: claim_id, а если он ещё не пришёл от
    -- WB — падаем на srid (как и фронт ReturnsView.vue уже делает:
    -- item.claim_id || item.srid), чтобы строка не терялась без id.
    SELECT
        COALESCE(c.claim_id::text, c.srid), 'wb', 'claim',
        c.supplier_article, c.nm_id::text,
        COALESCE(NULLIF(TRIM(c.user_comment),''), '—'),
        NULL::int, NULL::text, NULL::text,
        c.photos, c.srid, NULL::text,
        COALESCE(c.claim_date, c.order_date),
        EXTRACT(epoch FROM now()-COALESCE(c.claim_date, c.order_date))::int,
        NULL::bigint,
        NULL::boolean,
        NULL::text
    FROM wb_claims c
    WHERE c.status IN ('На рассмотрении', '0', '0.0', 'Открыта')
      AND NOT COALESCE(c.is_archive, false)
      AND COALESCE(c.claim_id::text, c.srid) NOT IN
          (SELECT item_id FROM cx_dismissed_items WHERE mp='wb' AND item_type='claim')
"""


def init_unified_queue_view(engine) -> None:
    """Создаёт/обновляет cx_unified_queue — общий VIEW очереди обращений.

    Идемпотентно (CREATE OR REPLACE VIEW), вызывается при каждом старте
    приложения из backend/main.py, как и другие init_*_tables() модулей.
    Требует уже существующих таблиц cx_dismissed_items, wb_feedbacks,
    ym_feedbacks, wb_questions, ym_questions, wb_chats, ozon_reviews,
    ozon_chats, ozon_chat_messages, ym_chats, ym_chat_messages, wb_claims.
    """
    with engine.connect() as conn:
        conn.execute(text(f"CREATE OR REPLACE VIEW {UNIFIED_QUEUE_VIEW} AS {_UNIFIED_QUEUE_VIEW_SQL}"))
        conn.commit()


@router.get("/queue")
def get_queue(
    db: Session = Depends(get_db),
    mp: str = Query("all"),
):
    """Сводная очередь обращений — все площадки, сортировка по давности."""
    # Значение mp всегда через параметр (:mp) — в SQL-текст попадает только
    # наличие/отсутствие условия, никогда сама строка mp (защита от SQL-инъекции).
    mp_filter = "" if mp == "all" else "AND mp = :mp"
    mp_params = {} if mp == "all" else {"mp": mp}

    with db.bind.connect() as conn:
        counts = conn.execute(text(f"""
            SELECT
                COUNT(*)                                    AS total,
                COUNT(*) FILTER (WHERE item_type='review')  AS reviews,
                COUNT(*) FILTER (WHERE item_type='question') AS questions,
                COUNT(*) FILTER (WHERE item_type='chat')    AS chats,
                COUNT(*) FILTER (WHERE item_type='claim')   AS claims,
                COUNT(*) FILTER (WHERE mp='wb')             AS wb,
                COUNT(*) FILTER (WHERE mp='ym')             AS ym,
                COUNT(*) FILTER (WHERE mp='ozon')           AS ozon
            FROM {UNIFIED_QUEUE_VIEW} q WHERE 1=1 {mp_filter}
        """), mp_params).mappings().first()

        rows = conn.execute(text(f"""
            SELECT q.*,
                COALESCE(
                    pp.photo_url,
                    (SELECT pp2.photo_url
                     FROM wb_feedbacks wf
                     JOIN product_photos pp2 ON pp2.supplier_article = wf.supplier_article
                     WHERE wf.nm_id::text = q.nm_id LIMIT 1)
                ) AS product_photo
            FROM {UNIFIED_QUEUE_VIEW} q
            LEFT JOIN product_photos pp ON pp.supplier_article = q.sku
            WHERE 1=1 {mp_filter}
            ORDER BY
                CASE WHEN item_type='review' AND rating IS NOT NULL AND rating<=3 THEN 0 ELSE 1 END,
                age_sec DESC NULLS LAST
        """), mp_params).mappings().all()

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
            # Не подставлять sku (артикул) — иначе тип чата ломается
            ctx = meta.get("context_type")
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
    # Значения mp/item_type всегда через параметры — в SQL-текст попадает
    # только наличие/отсутствие условия, никогда сами строки (защита от SQL-инъекции).
    mp_cond   = "" if mp == "all"   else "AND a.mp = :mp"
    type_cond = "" if item_type == "all" else "AND a.item_type = :item_type"
    offset = (page - 1) * per_page

    # Доп. фильтры (параметризованные)
    conds = ""
    params = {"lim": per_page, "off": offset}
    if mp != "all":
        params["mp"] = mp
    if item_type != "all":
        params["item_type"] = item_type
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


def _normalize_claim_photos(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(u) for u in raw if u]
    s = str(raw).strip()
    if not s:
        return []
    # В БД фото через пробел; URL могут быть без схемы
    out = []
    for u in s.split():
        u = u.strip()
        if not u:
            continue
        if u.startswith("//"):
            u = "https:" + u
        out.append(u)
    return out


def _claim_row_to_item(row: dict) -> dict:
    d = _decode_claim(dict(row))
    d["photos"] = _normalize_claim_photos(d.get("photos"))
    for k in ("claim_date", "order_date"):
        v = d.get(k)
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d


@router.get("/claims")
def list_claims(
    active: bool = True,
    status: str = "",
    claim_type: str = Query("", alias="type"),
    q: str = Query("", description="Поиск: артикул / srid / claim_id / комментарий"),
    page: int = 1,
    per_page: int = 25,
    db: Session = Depends(get_db),
):
    """Список wb_claims с фильтрами: active, status, type, q."""
    offset = (page - 1) * per_page
    filters, params = ["1=1"], {}
    filters.append("c.is_archive = :arch")
    params["arch"] = not active
    if status:
        # Поддержка и кодов, и русских статусов из БД
        st = status.strip()
        status_aliases = {
            "0": ["На рассмотрении", "0", "0.0", "Открыта"],
            "1": ["Отказ", "Отклонено", "1", "1.0"],
            "2": ["Одобрено", "2", "2.0"],
            "3": ["Отказ", "Закрыта", "3"],
            "На рассмотрении": ["На рассмотрении", "0", "0.0", "Открыта"],
            "Отказ": ["Отказ", "Отклонено", "1", "1.0", "3"],
            "Одобрено": ["Одобрено", "2", "2.0"],
            "Закрыта": ["Закрыта"],
            "Отклонено": ["Отказ", "Отклонено", "1", "1.0"],
        }
        aliases = status_aliases.get(st, [st])
        placeholders = []
        for i, a in enumerate(aliases):
            key = f"st{i}"
            placeholders.append(f":{key}")
            params[key] = a
        filters.append(f"c.status IN ({', '.join(placeholders)})")
    if claim_type:
        filters.append("c.claim_type = :ctype")
        params["ctype"] = claim_type
    q = (q or "").strip()
    if q:
        params["q"] = f"%{q}%"
        params["q_exact"] = q
        filters.append("""(
            c.supplier_article ILIKE :q
            OR c.srid ILIKE :q
            OR c.claim_id::text ILIKE :q
            OR coalesce(c.user_comment,'') ILIKE :q
            OR c.nm_id::text = :q_exact
        )""")
    where = " AND ".join(filters)
    with db.bind.connect() as conn:
        total = conn.execute(
            text(f"SELECT COUNT(*) FROM wb_claims c WHERE {where}"), params
        ).scalar()
        rows = conn.execute(text(f"""
            SELECT c.claim_id::text, c.srid, c.supplier_article, c.nm_id,
                   c.claim_type, c.status, c.status_ex, c.user_comment,
                   c.is_archive, c.claim_date, c.order_date, c.income_id,
                   c.photos, c.correction, c.audit_status, c.audit_comment,
                   COALESCE(pp.photo_url, wpp.photo_url) AS product_photo
            FROM wb_claims c
            LEFT JOIN product_photos pp  ON pp.supplier_article = c.supplier_article
            LEFT JOIN wb_product_photos wpp ON wpp.nm_id::text = c.nm_id::text
            WHERE {where}
            ORDER BY c.claim_date DESC NULLS LAST
            LIMIT :lim OFFSET :off
        """), {**params, "lim": per_page, "off": offset}).mappings().all()
    items = [_claim_row_to_item(dict(r)) for r in rows]
    return {"items": items, "total": total, "page": page,
            "total_pages": max(1, -(-total // per_page))}


class ClaimDecisionPayload(BaseModel):
    action: str
    comment: str = ""


@router.get("/claims/{claim_id}")
def get_claim_detail(claim_id: str, db: Session = Depends(get_db)):
    """Карточка претензии + живые actions с WB + связанный чат."""
    from .. import wb_claims_api

    with db.bind.connect() as conn:
        row = conn.execute(text("""
            SELECT c.claim_id::text, c.srid, c.supplier_article, c.nm_id,
                   c.claim_type, c.status, c.status_ex, c.user_comment,
                   c.is_archive, c.claim_date, c.order_date, c.income_id,
                   c.photos, c.correction, c.audit_status, c.audit_comment,
                   COALESCE(pp.photo_url, wpp.photo_url) AS product_photo
            FROM wb_claims c
            LEFT JOIN product_photos pp  ON pp.supplier_article = c.supplier_article
            LEFT JOIN wb_product_photos wpp ON wpp.nm_id::text = c.nm_id::text
            WHERE c.claim_id::text = :cid
            LIMIT 1
        """), {"cid": claim_id}).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Претензия не найдена")
        item = _claim_row_to_item(dict(row))

        # Связанный чат по srid / order_rid
        chat = None
        srid = item.get("srid") or ""
        if srid:
            chat = conn.execute(text("""
                SELECT chat_id, client_name, needs_answer, last_ts
                FROM wb_chats
                WHERE order_rid = :r
                   OR EXISTS (
                     SELECT 1 FROM wb_chat_messages m
                     WHERE m.chat_id = wb_chats.chat_id
                       AND m.attachments->'goodCard'->>'rid' = :r
                   )
                ORDER BY last_ts DESC NULLS LAST
                LIMIT 1
            """), {"r": srid}).mappings().first()
            if chat:
                chat = dict(chat)
                if hasattr(chat.get("last_ts"), "isoformat"):
                    chat["last_ts"] = chat["last_ts"].isoformat()

    live = wb_claims_api.fetch_claim_from_wb(claim_id)
    raw_actions = list((live or {}).get("actions") or [])
    live_status = (live or {}).get("status") if live else item.get("status")
    item["actions"] = wb_claims_api.map_actions(raw_actions, status=live_status or item.get("status"))
    item["actions_raw"] = raw_actions
    if live:
        # Подтянуть свежий статус с WB, если есть
        from ..wb_mini_sync import _map_claim_status, _status_ex_label
        wb_comment = str(live.get("wb_comment") or live.get("wbComment") or "")
        st_ex_raw = live.get("status_description") or live.get("status_ex") or ""
        mapped = _map_claim_status(live.get("status", ""), str(st_ex_raw), wb_comment)
        item["status"] = mapped
        item["status_ex"] = _status_ex_label(st_ex_raw, wb_comment) or item.get("status_ex")
        item = _decode_claim(item)
        if wb_comment and not item.get("wb_comment"):
            item["wb_comment"] = wb_comment
        # Пересчитать кнопки уже по финальному статусу
        item["actions"] = wb_claims_api.map_actions(raw_actions, status=item.get("status"))

    item["chat"] = chat
    item["can_decide"] = bool(item.get("actions")) and any(a.get("primary") for a in item["actions"])
    item["can_change_decision"] = any(a.get("is_change") for a in (item.get("actions") or []))
    return item


@router.post("/claims/{claim_id}/decision")
def decide_claim(
    claim_id: str,
    payload: ClaimDecisionPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Одобрить / отказать по претензии WB (из чата или очереди возвратов)."""
    from .. import wb_claims_api

    action = (payload.action or "").strip()
    if not action:
        raise HTTPException(status_code=400, detail="Не указано действие")
    result = wb_claims_api.decide_claim(
        db, claim_id, action, payload.comment or ""
    )
    if not result.get("ok"):
        code = int(result.get("status_code") or 502)
        if code < 400 or code > 599:
            code = 502
        raise HTTPException(status_code=code, detail=result.get("error") or "Ошибка WB")

    logger.info(
        "[WB Claims] decision claim=%s action=%s by=%s",
        claim_id, action, current_user.get("username") or current_user.get("sub") or "?",
    )
    return result


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
                       status, is_archive, claim_date, srid
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


def _ozon_return_as_claim(row: dict) -> dict:
    """Привести строку ozon_returns к форме specific.claim для UI чата."""
    d = dict(row)
    reason = (d.get("return_reason") or "").strip() or "—"
    status = (d.get("status_ru") or d.get("status_sys") or "").strip() or "—"
    return {
        "claim_id": str(d.get("return_id") or d.get("posting_number") or d.get("id") or ""),
        "claim_type": "return",
        "claim_type_label": "Возврат Ozon",
        "body": reason,
        "status": status,
        "status_label": status,
        "claim_date": _ru_date(d.get("return_date")),
        "supplier_article": d.get("supplier_article"),
        "posting_number": d.get("posting_number"),
        "order_number": d.get("order_number"),
    }


def _ym_return_as_claim(row: dict) -> dict:
    """Привести строку ym_returns к форме specific.claim для UI чата."""
    d = dict(row)
    body = (d.get("return_comment") or d.get("reason_type") or "").strip() or "—"
    status = (d.get("status_ru") or d.get("status") or "").strip() or "—"
    return {
        "claim_id": str(d.get("return_id") or d.get("id") or ""),
        "claim_type": "return",
        "claim_type_label": "Возврат ЯМ",
        "body": body,
        "status": status,
        "status_label": status,
        "claim_date": _ru_date(d.get("created_at")),
        "supplier_article": d.get("supplier_article"),
        "order_id": d.get("order_id"),
    }


@router.get("/chat-context/ozon/{chat_id}")
def get_ozon_chat_context(chat_id: str, db: Session = Depends(get_db)):
    """Возврат по заказу/SKU чата Ozon + последние возвраты по тому же артикулу."""
    with db.bind.connect() as conn:
        chat = conn.execute(text("""
            SELECT order_number, context_sku, context_article
            FROM ozon_chats WHERE chat_id = :cid
        """), {"cid": chat_id}).mappings().first()
        if not chat:
            return {"specific": {}, "reviews": [], "questions": [], "claims": []}

        order_number = (chat.get("order_number") or "").strip() or None
        sku = chat.get("context_sku")
        article = (chat.get("context_article") or "").strip() or None

        specific_claim = None
        if order_number or sku is not None:
            cr = conn.execute(text("""
                SELECT return_id, posting_number, order_number, ozon_sku,
                       supplier_article, return_reason, status_ru, status_sys,
                       return_date, id
                FROM ozon_returns
                WHERE (
                    (:ord IS NOT NULL AND (
                        posting_number = :ord
                        OR order_number = :ord
                        OR posting_number LIKE :ord || '-%'
                    ))
                    OR (:sku IS NOT NULL AND ozon_sku = :sku)
                )
                ORDER BY
                    CASE
                      WHEN :ord IS NOT NULL AND (
                        posting_number = :ord OR order_number = :ord
                      ) THEN 0
                      WHEN :ord IS NOT NULL AND posting_number LIKE :ord || '-%' THEN 1
                      ELSE 2
                    END,
                    return_date DESC NULLS LAST
                LIMIT 1
            """), {"ord": order_number, "sku": sku}).mappings().first()
            if cr:
                specific_claim = _ozon_return_as_claim(dict(cr))

        claims = []
        if article or sku is not None:
            rows = conn.execute(text("""
                SELECT return_id, posting_number, order_number, ozon_sku,
                       supplier_article, return_reason, status_ru, status_sys,
                       return_date, id
                FROM ozon_returns
                WHERE (
                    (:art IS NOT NULL AND supplier_article = :art)
                    OR (:sku IS NOT NULL AND ozon_sku = :sku)
                )
                ORDER BY return_date DESC NULLS LAST
                LIMIT 5
            """), {"art": article, "sku": sku}).mappings().all()
            claims = [_ozon_return_as_claim(dict(r)) for r in rows]

    return {
        "specific": {"claim": specific_claim, "review": None},
        "reviews": [],
        "questions": [],
        "claims": claims,
    }


@router.get("/chat-context/ym/{chat_id}")
def get_ym_chat_context(chat_id: str, db: Session = Depends(get_db)):
    """Возврат по return_id/order_id чата ЯМ + последние по заказу/артикулу."""
    with db.bind.connect() as conn:
        chat = conn.execute(text("""
            SELECT return_id, order_id, context_type, customer_name
            FROM ym_chats WHERE chat_id = :cid
        """), {"cid": chat_id}).mappings().first()
        if not chat:
            return {"specific": {}, "reviews": [], "questions": [], "claims": []}

        return_id = (str(chat.get("return_id") or "").strip() or None)
        order_id = (str(chat.get("order_id") or "").strip() or None)

        article = None
        if order_id:
            ar = conn.execute(text("""
                SELECT supplier_article FROM ym_orders
                WHERE order_id::text = :oid
                  AND NULLIF(TRIM(supplier_article), '') IS NOT NULL
                LIMIT 1
            """), {"oid": order_id}).mappings().first()
            if ar:
                article = ar["supplier_article"]
        if not article and (return_id or order_id):
            ar = conn.execute(text("""
                SELECT supplier_article FROM ym_returns
                WHERE (
                    (:rid IS NOT NULL AND return_id::text = :rid)
                    OR (:oid IS NOT NULL AND order_id::text = :oid)
                )
                AND NULLIF(TRIM(supplier_article), '') IS NOT NULL
                ORDER BY created_at DESC NULLS LAST
                LIMIT 1
            """), {"rid": return_id, "oid": order_id}).mappings().first()
            if ar:
                article = ar["supplier_article"]

        specific_claim = None
        if return_id or order_id:
            cr = conn.execute(text("""
                SELECT return_id, order_id, supplier_article, return_comment,
                       reason_type, status_ru, status, created_at, id
                FROM ym_returns
                WHERE (
                    (:rid IS NOT NULL AND return_id::text = :rid)
                    OR (:oid IS NOT NULL AND order_id::text = :oid)
                )
                ORDER BY
                    CASE WHEN :rid IS NOT NULL AND return_id::text = :rid THEN 0 ELSE 1 END,
                    created_at DESC NULLS LAST
                LIMIT 1
            """), {"rid": return_id, "oid": order_id}).mappings().first()
            if cr:
                specific_claim = _ym_return_as_claim(dict(cr))

        claims = []
        if return_id or order_id or article:
            rows = conn.execute(text("""
                SELECT return_id, order_id, supplier_article, return_comment,
                       reason_type, status_ru, status, created_at, id
                FROM ym_returns
                WHERE (
                    (:rid IS NOT NULL AND return_id::text = :rid)
                    OR (:oid IS NOT NULL AND order_id::text = :oid)
                    OR (:art IS NOT NULL AND supplier_article = :art)
                )
                ORDER BY created_at DESC NULLS LAST
                LIMIT 5
            """), {"rid": return_id, "oid": order_id, "art": article}).mappings().all()
            claims = [_ym_return_as_claim(dict(r)) for r in rows]

    return {
        "specific": {"claim": specific_claim, "review": None},
        "reviews": [],
        "questions": [],
        "claims": claims,
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
        r = wb_client.download_chat_file(file_id, timeout=25)
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
        r = ozon_client.download_chat_file(url, timeout=30)
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
    try:
        r = ym_client.download_chat_file(url, timeout=30)
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
        wb_client.answer_feedback(feedback_id, txt, timeout=20)
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
        wb_client.answer_question(question_id, txt, timeout=20)
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
        ym_client.answer_feedback(feedback_id, txt, biz=_ym_business_id(), key=_ym_key(), timeout=20)
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
        ym_client.answer_question(question_id, txt, biz=_ym_business_id(), key=_ym_key(), timeout=20)
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

def _oz_key() -> str:
    return ozon_client.api_key()


def _oz_cid() -> str:
    return ozon_client.client_id()


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
        ozon_client.answer_review(review_id, txt, timeout=20)
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
        from .. import ozon_chat_sync
        ozon_chat_sync.send_message(chat_id, txt)
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
        wb_client.send_chat_message(reply_sign, txt, timeout=20)
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
            wb_client.answer_feedback(item_id, txt, timeout=20)
            with db.bind.connect() as conn:
                conn.execute(text("UPDATE wb_feedbacks SET state='answered',answer_text=:a WHERE id=:i"),
                             {"a": txt, "i": item_id})
                conn.commit()

        elif mp == "wb" and itype == "question":
            wb_client.answer_question(item_id, txt, timeout=20)
            with db.bind.connect() as conn:
                conn.execute(text("UPDATE wb_questions SET state='answered',answer_text=:a WHERE id=:i"),
                             {"a": txt, "i": item_id})
                conn.commit()

        elif mp == "ym" and itype == "review":
            ym_client.answer_feedback(item_id, txt, biz=_ym_business_id(), key=_ym_key(), timeout=20)
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
                        wb_client.answer_feedback(iid, body_tpl, timeout=20)
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
                        ym_client.answer_feedback(row[0], body_tpl, biz=_ym_business_id(), key=_ym_key(), timeout=20)
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
