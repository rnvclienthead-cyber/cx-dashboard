"""
Раздел «Ответы КС» — очередь обращений и отправка ответов из системы.

Доступ: admin или пользователь с правом cx_responses в role_permissions.
Фаза 1: WB отзывы, вопросы, чаты.
Фаза 2: ЯМ отзывы, вопросы. ИИ-черновики (Haiku).
"""
import os
import time
import asyncio
import base64
import logging
import re
import requests
from pathlib import PurePath
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response, RedirectResponse
from starlette.concurrency import run_in_threadpool
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


def _qa_questions_access(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Доступ к очереди QA определяется назначенным модульным правом."""
    role = current_user.get("role", "")
    # Как и в остальных защищённых разделах, admin имеет полный доступ без
    # отдельной записи в role_permissions.
    if role == "admin":
        return current_user
    row = db.execute(
        text("SELECT 1 FROM role_permissions WHERE role_name = :r AND module = 'quality_questions'"),
        {"r": role},
    ).first()
    if not row:
        raise HTTPException(status_code=403, detail="Очередь уточнений доступна только отделу качества")
    return current_user


quality_questions_router = APIRouter(
    prefix="/api/v1/quality-questions",
    tags=["Quality question clarifications"],
    dependencies=[Depends(_qa_questions_access)],
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
      -- мы ответили, клиент молчит >2 дн — не держим в очереди (уходят в архив)
      AND NOT (
            lower(COALESCE(c.last_sender, '')) IN ('seller', 'courier')
            AND COALESCE(
              (SELECT MAX(m.created_at) FROM ozon_chat_messages m WHERE m.chat_id = c.chat_id),
              c.waiting_since, c.created_at, c.updated_at, now()
            ) < now() - INTERVAL '2 days'
          )
      AND c.chat_id NOT IN
          (SELECT item_id FROM cx_dismissed_items WHERE mp='ozon' AND item_type='chat')

    UNION ALL

    SELECT
        c.chat_id, 'ym', 'chat',
        COALESCE(
            NULLIF(TRIM(c.context_article), ''),
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
    current_user: dict = Depends(get_current_user),
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

        # Менеджер видит только свои уточнения, администратор — все. Это не
        # черновик маркетплейса: текст QA можно лишь подставить в поле ответа.
        with db.bind.connect() as conn:
            clarifications = conn.execute(text("""
                SELECT DISTINCT ON (mp, question_id)
                    id, mp, question_id, note, status, proposed_text,
                    requested_by, requested_at, answered_at
                FROM cx_question_clarifications
                WHERE (:is_admin = TRUE OR requested_by = :user)
                ORDER BY mp, question_id, requested_at DESC
            """), {
                "user": current_user["username"],
                "is_admin": current_user.get("role") == "admin",
            }).mappings().all()
        clarification_map = {
            (row["mp"], str(row["question_id"])): dict(row)
            for row in clarifications
        }
        for item in items:
            if item.get("item_type") != "question":
                continue
            clarification = clarification_map.get((item["mp"], str(item["id"])))
            if not clarification:
                continue
            for field in ("requested_at", "answered_at"):
                value = clarification.get(field)
                if value and hasattr(value, "isoformat"):
                    clarification[field] = value.isoformat()
            item["clarification"] = clarification

    counts_d = dict(counts) if counts else {}
    return {
        "items":  items,
        "counts": counts_d,
    }


# ── Уточнения у отдела качества по вопросам ──────────────────────────────────

class ClarificationRequestPayload(BaseModel):
    note: str = ""


class ClarificationAnswerPayload(BaseModel):
    text: str


def _cancel_obsolete_question_clarifications(conn) -> None:
    """Закрывает pending-уточнения, когда исходный вопрос уже не открыт.

    Такое происходит, если на него ответили в кабинете площадки или другой
    сотрудник. cancelled снимает частичный уникальный ключ pending и оставляет
    след в истории, вместо того чтобы незаметно «зависать» в очереди QA.
    """
    conn.execute(text(f"""
        UPDATE cx_question_clarifications c
        SET status = 'cancelled'
        WHERE c.status = 'pending'
          AND NOT EXISTS (
              SELECT 1
              FROM {UNIFIED_QUEUE_VIEW} q
              WHERE q.mp = c.mp
                AND q.item_type = 'question'
                AND q.id = c.question_id
          )
    """))


@router.post("/questions/{mp}/{question_id}/clarification")
def request_question_clarification(
    mp: str,
    question_id: str,
    payload: ClarificationRequestPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Поставить открытый вопрос WB/ЯМ в общую очередь отдела качества."""
    if current_user.get("role") not in ("cs_manager", "admin"):
        raise HTTPException(status_code=403, detail="Уточнение может запросить менеджер КС или администратор")
    if mp not in ("wb", "ym"):
        raise HTTPException(status_code=400, detail="Поддерживаются только вопросы WB и ЯМ")
    note = (payload.note or "").strip()
    if len(note) > 2000:
        raise HTTPException(status_code=400, detail="Комментарий не должен быть длиннее 2000 символов")

    with db.bind.connect() as conn:
        exists = conn.execute(text(f"""
            SELECT 1 FROM {UNIFIED_QUEUE_VIEW}
            WHERE mp = :mp AND item_type = 'question' AND id = :id
            LIMIT 1
        """), {"mp": mp, "id": str(question_id)}).first()
        if not exists:
            raise HTTPException(status_code=404, detail="Открытый вопрос не найден")
        try:
            row = conn.execute(text("""
                INSERT INTO cx_question_clarifications
                    (mp, question_id, requested_by, note)
                VALUES (:mp, :question_id, :requested_by, :note)
                RETURNING id, requested_at
            """), {
                "mp": mp,
                "question_id": str(question_id),
                "requested_by": current_user["username"],
                "note": note,
            }).mappings().first()
            conn.commit()
        except Exception as exc:
            conn.rollback()
            # Частичный уникальный индекс не даёт продублировать живой запрос.
            if "uq_cx_question_clarifications_pending" in str(exc):
                raise HTTPException(status_code=409, detail="Этот вопрос уже ожидает ответ отдела качества")
            raise

    return {
        "status": "pending",
        "id": row["id"],
        "requested_at": row["requested_at"].isoformat() if row["requested_at"] else None,
    }


@quality_questions_router.get("/queue")
def get_quality_question_queue(
    db: Session = Depends(get_db),
    mp: str = Query("all"),
):
    """Общая очередь QA: только активные уточнения, без кнопок отправки в МП."""
    if mp not in ("all", "wb", "ym"):
        raise HTTPException(status_code=400, detail="mp: all, wb или ym")
    params = {} if mp == "all" else {"mp": mp}
    mp_filter = "" if mp == "all" else "AND c.mp = :mp"
    with db.bind.connect() as conn:
        _cancel_obsolete_question_clarifications(conn)
        rows = conn.execute(text(f"""
            SELECT
                q.*, c.id AS clarification_id, c.note AS clarification_note,
                c.requested_by AS clarification_requested_by,
                c.requested_at AS clarification_requested_at
            FROM cx_question_clarifications c
            JOIN {UNIFIED_QUEUE_VIEW} q
              ON q.mp = c.mp AND q.item_type = 'question' AND q.id = c.question_id
            WHERE c.status = 'pending' {mp_filter}
            ORDER BY c.requested_at ASC
        """), params).mappings().all()
        conn.commit()

    items = []
    for row in rows:
        item = dict(row)
        if item.get("created_at") and hasattr(item["created_at"], "isoformat"):
            item["created_at"] = item["created_at"].isoformat()
        if item.get("clarification_requested_at") and hasattr(item["clarification_requested_at"], "isoformat"):
            item["clarification_requested_at"] = item["clarification_requested_at"].isoformat()
        raw = item.pop("photos_raw", None) or ""
        item["photos"] = [url for url in raw.split() if url.startswith("http")]
        items.append(item)
    return {"items": items, "counts": {"questions": len(items)}}


@quality_questions_router.get("/count")
def get_quality_question_count(db: Session = Depends(get_db)):
    """Количество активных уточнений для счётчика в сайдбаре."""
    with db.bind.connect() as conn:
        count = conn.execute(text(f"""
            SELECT COUNT(*)
            FROM cx_question_clarifications c
            WHERE c.status = 'pending'
              AND EXISTS (
                  SELECT 1
                  FROM {UNIFIED_QUEUE_VIEW} q
                  WHERE q.mp = c.mp
                    AND q.item_type = 'question'
                    AND q.id = c.question_id
              )
        """)).scalar()
    return {"count": int(count or 0)}


@quality_questions_router.post("/{clarification_id}/answer")
def answer_question_clarification(
    clarification_id: int,
    payload: ClarificationAnswerPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Сохранить предложение QA. В API WB/ЯМ этот метод принципиально не ходит."""
    proposed_text = payload.text.strip()
    if not proposed_text:
        raise HTTPException(status_code=400, detail="Введите предлагаемый ответ")
    if len(proposed_text) > 5000:
        raise HTTPException(status_code=400, detail="Ответ не должен быть длиннее 5000 символов")

    with db.bind.connect() as conn:
        # Одним запросом проверяем, что вопрос всё ещё находится в живой
        # очереди. Если его уже закрыли в кабинете МП, отменяем уточнение,
        # чтобы QA не передал менеджеру устаревший текст.
        row = conn.execute(text(f"""
            WITH target AS (
                SELECT c.id, c.mp, c.question_id, c.requested_by,
                       EXISTS (
                           SELECT 1
                           FROM {UNIFIED_QUEUE_VIEW} q
                           WHERE q.mp = c.mp
                             AND q.item_type = 'question'
                             AND q.id = c.question_id
                       ) AS is_active
                FROM cx_question_clarifications c
                WHERE c.id = :id AND c.status = 'pending'
                FOR UPDATE
            ), answered AS (
                UPDATE cx_question_clarifications c
                SET status = 'answered', proposed_text = :text,
                    answered_by = :user, answered_at = now()
                FROM target t
                WHERE c.id = t.id AND t.is_active
                RETURNING c.mp, c.question_id, c.requested_by, c.answered_at
            ), cancelled AS (
                UPDATE cx_question_clarifications c
                SET status = 'cancelled'
                FROM target t
                WHERE c.id = t.id AND NOT t.is_active
                RETURNING c.id
            )
            SELECT 'answered' AS outcome, mp, question_id, requested_by, answered_at
            FROM answered
            UNION ALL
            SELECT 'cancelled' AS outcome, NULL::varchar, NULL::text, NULL::varchar, NULL::timestamp
            FROM cancelled
        """), {
            "id": clarification_id,
            "text": proposed_text,
            "user": current_user["username"],
        }).mappings().first()
        if not row:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Запрос не найден или уже обработан")
        if row["outcome"] == "cancelled":
            conn.commit()
            raise HTTPException(
                status_code=409,
                detail="Исходный вопрос уже закрыт; уточнение отменено",
            )
        conn.commit()

    return {
        "status": "answered",
        "mp": row["mp"],
        "question_id": row["question_id"],
        "requested_by": row["requested_by"],
        "answered_at": row["answered_at"].isoformat() if row["answered_at"] else None,
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

def _resolve_wb_chat_id(conn, chat_id: str) -> str | None:
    """Канонический chat_id в БД: `1:uuid`. В ЛК WB в URL приходит голый uuid."""
    cid = (chat_id or "").strip()
    if not cid:
        return None
    row = conn.execute(text(
        "SELECT chat_id FROM wb_chats WHERE chat_id = :c LIMIT 1"
    ), {"c": cid}).scalar()
    if row:
        return str(row)
    if ":" not in cid:
        row = conn.execute(text("""
            SELECT chat_id FROM wb_chats
            WHERE chat_id = :p1 OR chat_id LIKE :suf
            ORDER BY CASE WHEN chat_id = :p1 THEN 0 ELSE 1 END, chat_id
            LIMIT 1
        """), {"p1": f"1:{cid}", "suf": f"%:{cid}"}).scalar()
        if row:
            return str(row)
    return None


def _attachment_kind(name: str = "", url: str = "", mime_type: str = "", default: str = "file") -> str:
    probe = f"{name} {url} {mime_type}".lower().split("?", 1)[0]
    if mime_type.lower().startswith("image/") or probe.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return "image"
    return default


def _extract_wb_attachments(attachments) -> list[dict]:
    """Нормализовать WB images/files, не выдавая PDF за изображение."""
    if not attachments:
        return []
    out = []
    for key in ("images", "files", "photos", "attachments"):
        arr = attachments.get(key) if isinstance(attachments, dict) else None
        if not isinstance(arr, list):
            continue
        for a in arr:
            fid = ""
            url = ""
            name = ""
            mime_type = ""
            if isinstance(a, str):
                fid = a
            elif isinstance(a, dict):
                fid = str(a.get("downloadID") or a.get("id") or a.get("fileID") or a.get("fileId") or "")
                url = str(a.get("url") or "")
                name = str(a.get("name") or a.get("fileName") or a.get("filename") or "")
                mime_type = str(a.get("contentType") or a.get("mimeType") or a.get("type") or "")
            if not fid and not url:
                continue
            resolved_url = (
                f"/api/v1/cx-responses/chat-download/wb/{fid}"
                if fid else url
            )
            default_kind = "image" if key in ("images", "photos") else "file"
            out.append({
                "type": _attachment_kind(name, url, mime_type, default_kind),
                "url": resolved_url,
                "name": name or ("Изображение" if default_kind == "image" else "Документ"),
                "mimeType": mime_type or None,
            })
    return out


@router.get("/chat-card/{mp}/{chat_id}")
def get_chat_card(mp: str, chat_id: str, db: Session = Depends(get_db)):
    """Карточка чата по id (в т.ч. голый uuid из ссылки ЛК WB → `1:uuid`)."""
    m = _normalize_mp(mp)
    if m != "wb":
        raise HTTPException(status_code=404, detail="Пока резолв только для WB")
    with db.bind.connect() as conn:
        cid = _resolve_wb_chat_id(conn, chat_id)
        if not cid:
            raise HTTPException(status_code=404, detail="Чат не найден")
        row = conn.execute(text("""
            SELECT c.chat_id AS id, c.client_name AS contact, c.nm_id,
                   c.good_name, c.last_message AS body, c.last_ts,
                   c.needs_answer, c.order_rid,
                   wpp.photo_url AS product_photo,
                   CASE
                     WHEN c.last_ts IS NULL OR c.last_ts <= 0 THEN NULL
                     WHEN c.last_ts > 1000000000000
                       THEN GREATEST(0, ((EXTRACT(epoch FROM now()) * 1000 - c.last_ts) / 1000)::int)
                     ELSE GREATEST(0, (EXTRACT(epoch FROM now()) - c.last_ts)::int)
                   END AS age_sec
            FROM wb_chats c
            LEFT JOIN wb_product_photos wpp ON wpp.nm_id = c.nm_id
            WHERE c.chat_id = :cid
            LIMIT 1
        """), {"cid": cid}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Чат не найден")
    d = dict(row)
    d["mp"] = "wb"
    d["item_type"] = "chat"
    d["sku"] = d.get("good_name") or (f"Арт. {d['nm_id']}" if d.get("nm_id") else "")
    return d


@router.get("/chat-messages/wb/{chat_id}")
def get_wb_chat_messages(chat_id: str, db: Session = Depends(get_db)):
    """Полная лента переписки WB.

    Копится в wb_chat_messages из потока событий (WB не отдаёт историю задним
    числом). Если лента пустая (старый чат до запуска накопления) — показываем
    последнее сообщение из wb_chats + наши логи ответов как fallback.
    """
    with db.bind.connect() as conn:
        resolved = _resolve_wb_chat_id(conn, chat_id) or chat_id
        rows = conn.execute(text(
            "SELECT sender, text, attachments, add_ts "
            "FROM wb_chat_messages WHERE chat_id = :cid ORDER BY add_ts, id"
        ), {"cid": resolved}).mappings().all()

    if rows:
        msgs = []
        for r in rows:
            typed_attachments = _extract_wb_attachments(r.get("attachments"))
            msgs.append({
                "text": r["text"] or "",
                "senderType": "seller" if r["sender"] == "seller" else ("wb" if r["sender"] == "wb" else "client"),
                "createdAt": r["add_ts"],
                "attachments": typed_attachments,
                "images": [item["url"] for item in typed_attachments if item["type"] == "image"],
            })
        return {"messages": msgs, "partial": False, "chat_id": resolved}

    # ── Fallback: старый чат без накопленной ленты ──
    msgs = []
    with db.bind.connect() as conn:
        resolved = _resolve_wb_chat_id(conn, chat_id) or chat_id
        chat = conn.execute(text(
            "SELECT last_message, last_sender, last_ts, waiting_since "
            "FROM wb_chats WHERE chat_id = :cid"
        ), {"cid": resolved}).mappings().first()
        replies = conn.execute(text(
            "SELECT answer_text, sent_at FROM cx_response_log "
            "WHERE mp='wb' AND item_type='chat' AND item_id = :cid ORDER BY sent_at"
        ), {"cid": resolved}).mappings().all()

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


def _ozon_ui_sender_type(sender_type) -> str:
    """Единая роль отправителя для UI Ozon.

    courier — исходящая сторона продавца в API Ozon; бот учитывает её
    как ответ продавца в _last_seller_ts, UI должен делать так же.
    """
    value = str(sender_type or "").lower()
    if value in ("seller", "courier"):
        return "seller"
    if value in ("system", "chatbot"):
        return "system"
    return "client"


def _typed_ozon_attachments(raw: str, is_image: bool, markdown_images: list[str]) -> list[dict]:
    from urllib.parse import unquote, urlparse

    urls = [u for u in str(raw or "").split(",") if u.startswith("http")]
    if not urls:
        urls = list(markdown_images)
    result = []
    for url in urls:
        basename = unquote(urlparse(url).path.rsplit("/", 1)[-1])
        kind = "image" if (is_image or url in markdown_images) else _attachment_kind(basename, url)
        result.append({
            "type": kind,
            "url": _ozon_proxy_url(url),
            "name": basename if "." in basename else ("Изображение" if kind == "image" else "Документ"),
            "mimeType": None,
        })
    return result


def _ym_ui_sender_type(sender_type) -> str:
    """Единая роль отправителя для UI Яндекс Маркета."""
    value = str(sender_type or "").lower()
    if value in ("seller", "partner", "merchant"):
        return "seller"
    if value == "system":
        return "system"
    return "client"


@router.get("/chat-messages/ozon/{chat_id}")
def get_ozon_chat_messages(chat_id: str, db: Session = Depends(get_db)):
    """Лента чата Ozon из ozon_chat_messages (+ fallback на превью)."""
    # Всегда подтягиваем history при открытии (иначе ответы из ЛК не видны,
    # если воркер пропустил чат с unread=0). Не чаще раза в 20 сек на чат.
    try:
        from .. import ozon_chat_sync
        with db.bind.connect() as conn:
            stale = conn.execute(text("""
                SELECT 1 FROM ozon_chats
                WHERE chat_id = :cid
                  AND (
                    updated_at IS NULL
                    OR updated_at < now() - interval '20 seconds'
                    OR NOT EXISTS (
                      SELECT 1 FROM ozon_chat_messages m WHERE m.chat_id = :cid
                    )
                  )
                LIMIT 1
            """), {"cid": chat_id}).scalar()
            if stale:
                ozon_chat_sync._sync_history(conn, chat_id)
                conn.execute(text("""
                    UPDATE ozon_chats SET updated_at = now() WHERE chat_id = :cid
                """), {"cid": chat_id})
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
            sender = _ozon_ui_sender_type(r.get("sender_type"))
            raw = r.get("attachments") or ""
            # из markdown в тексте, если attachments пусты
            markdown_images = []
            if r.get("text") and "![" in str(r.get("text")):
                markdown_images = _IMG_MD_URL_RE.findall(str(r.get("text")))
            body = r.get("text") or ""
            typed_attachments = _typed_ozon_attachments(
                raw,
                bool(r.get("is_image")),
                markdown_images,
            )
            # убрать markdown-картинки из текста если уже в images
            if typed_attachments and "![" in body:
                body = _IMG_MD_RE.sub("", body).strip()
            msgs.append({
                "text": body,
                "senderType": sender,
                "createdAt": _iso_to_ms(r.get("created_at")),
                "attachments": typed_attachments,
                "images": [item["url"] for item in typed_attachments if item["type"] == "image"],
            })
        return {"messages": msgs, "partial": False}

    with db.bind.connect() as conn:
        chat = conn.execute(text("""
            SELECT last_message, last_sender, updated_at, waiting_since
            FROM ozon_chats WHERE chat_id = :cid
        """), {"cid": chat_id}).mappings().first()
    msgs = []
    if chat and chat.get("last_message"):
        msgs.append({
            "text": chat["last_message"],
            "senderType": _ozon_ui_sender_type(chat.get("last_sender")),
            "createdAt": _iso_to_ms(chat.get("updated_at") or chat.get("waiting_since")),
            "images": [],
        })
    return {"messages": msgs, "partial": True}


@router.get("/chat-messages/ym/{chat_id}")
def get_ym_chat_messages(chat_id: str, db: Session = Depends(get_db)):
    """Лента чата Яндекс Маркета."""
    # При открытии подтягиваем history (дедуп local-/bot-, артикул DIRECT).
    # Не чаще раза в 20 сек на чат.
    try:
        from .. import ym_chat_sync
        with db.bind.connect() as conn:
            stale = conn.execute(text("""
                SELECT 1 FROM ym_chats
                WHERE chat_id = :cid
                  AND (
                    updated_at IS NULL
                    OR updated_at < now() - interval '20 seconds'
                    OR NOT EXISTS (
                      SELECT 1 FROM ym_chat_messages m WHERE m.chat_id = :cid
                    )
                  )
                LIMIT 1
            """), {"cid": chat_id}).scalar()
            if stale:
                ym_chat_sync._sync_history(conn, chat_id)
                conn.execute(text("""
                    UPDATE ym_chats SET updated_at = now() WHERE chat_id = :cid
                """), {"cid": chat_id})
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
            sender = _ym_ui_sender_type(r.get("sender_type"))
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
        msgs.append({
            "text": chat["last_message"],
            "senderType": _ym_ui_sender_type(chat.get("last_sender")),
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
    "Закрыта": "Нет в API WB",
    "Отменена": "Отменена",
    "Отменено": "Отменена",
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
        label = "Нет в API WB"
    if st == "Закрыта":
        label = "Нет в API WB"
    row["status_label"] = label
    row["status_raw"] = st  # исходное значение в БД (Закрыта / Отменена / …)
    return row


@router.get("/review-card")
def review_card(
    mp: str = Query(...),
    item_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Карточка отзыва + связка с чатом/возвратом ЭТОГО клиента (по заказу), не по товару."""
    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)

    linked_chat = None
    linked_return = None

    with db.bind.connect() as conn:
        # ── Сам отзыв ──
        if mp == "wb":
            r = conn.execute(text(
                "SELECT id::text AS id, supplier_article AS sku, nm_id::text AS nm_id, "
                "valuation AS rating, text AS body, pros, cons, photos AS photos_raw, "
                "answer_text, state, created_date AS created_at, "
                "last_order_shk_id, last_order_created_at "
                "FROM wb_feedbacks WHERE id::text=:i"
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
        shk = review.pop("last_order_shk_id", None)
        order_dt = review.pop("last_order_created_at", None)
        review["created_at"] = _iso(review.get("created_at"))
        raw = review.pop("photos_raw", None) or ""
        review["photos"] = [u for u in str(raw).split() if u.startswith("http")]
        sku, nm = review.get("sku"), review.get("nm_id")

        # ── Фото карточки ──
        review["product_photo"] = conn.execute(text(
            "SELECT photo_url FROM product_photos WHERE supplier_article=:a LIMIT 1"
        ), {"a": sku}).scalar()
        if not review["product_photo"] and nm and mp == "wb":
            review["product_photo"] = conn.execute(text(
                "SELECT photo_url FROM wb_product_photos WHERE nm_id::text=:nm LIMIT 1"
            ), {"nm": nm}).scalar()

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

        # ── WB: чат и возврат именно этого заказа (не «по nm_id») ──
        if mp == "wb":
            rid = None
            if shk is not None or order_dt is not None:
                ord_row = conn.execute(text("""
                    SELECT srid FROM wb_orders
                    WHERE (:shk IS NOT NULL AND shk_id = :shk)
                       OR (
                         :odt IS NOT NULL
                         AND dt = :odt + INTERVAL '3 hours'
                         AND supplier_article = :sku
                       )
                    ORDER BY CASE WHEN :shk IS NOT NULL AND shk_id = :shk THEN 0 ELSE 1 END
                    LIMIT 1
                """), {"shk": shk, "odt": order_dt, "sku": sku}).mappings().first()
                rid = (ord_row or {}).get("srid") if ord_row else None

            if rid:
                chat = conn.execute(text("""
                    SELECT chat_id, client_name, needs_answer, last_message, last_ts
                    FROM wb_chats
                    WHERE order_rid = :r
                       OR EXISTS (
                         SELECT 1 FROM wb_chat_messages m
                         WHERE m.chat_id = wb_chats.chat_id
                           AND m.attachments->'goodCard'->>'rid' = :r
                       )
                    ORDER BY last_ts DESC NULLS LAST
                    LIMIT 1
                """), {"r": rid}).mappings().first()
                if chat:
                    linked_chat = dict(chat)
                    if hasattr(linked_chat.get("last_ts"), "isoformat"):
                        linked_chat["last_ts"] = linked_chat["last_ts"].isoformat()

                cr = conn.execute(text("""
                    SELECT claim_id::text, srid, claim_type, status, is_archive,
                           user_comment, claim_date
                    FROM wb_claims WHERE srid = :r LIMIT 1
                """), {"r": rid}).mappings().first()
                if cr:
                    linked_return = _decode_claim(dict(cr))
                    linked_return["claim_date"] = _iso(linked_return.get("claim_date"))
                    linked_return["claim_date_ru"] = _ru_date(cr.get("claim_date"))

    return {
        "review": review,
        "linked_chat": linked_chat,
        "linked_return": linked_return,
        "chats": [],
        "returns": [],
        "reshipments": [],
    }


def _normalize_claim_photos(raw) -> list:
    """Разбирает строку фото из БД в список URL для UI.

    Форматы:
      - https://cdn/...
      - //cdn/...
      - /static/wb_claims/x.jpg|/https://cdn/...  → берём превью (до |)
      - /static/ym_returns/x.jpg
    """
    if not raw:
        return []
    if isinstance(raw, list):
        items = [str(u) for u in raw if u]
    else:
        s = str(raw).strip()
        if not s:
            return []
        items = s.split()

    out = []
    for u in items:
        u = (u or "").strip()
        if not u:
            continue
        # превью|оригинал → для показа превью
        if "|" in u:
            u = u.split("|", 1)[0].strip()
        if not u:
            continue
        if u.startswith("//"):
            u = "https:" + u
        if u.startswith("http") or u.startswith("/static/") or u.startswith("/uploads/"):
            out.append(u)
        elif u.startswith("/"):
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


def _normalize_mp(mp: str) -> str:
    m = (mp or "wb").strip().lower()
    if m in ("wb", "ym", "ozon"):
        return m
    return "wb"


def _ym_row_to_item(row: dict) -> dict:
    from .. import ym_returns_api

    d = dict(row)
    status_code = str(d.get("status") or "")
    status_label = ym_returns_api.status_ru(status_code, d.get("status_ru"))
    reason = ym_returns_api.reason_label(d.get("reason_type"), d.get("subreason_type"))
    photos = _normalize_claim_photos(d.get("photos"))
    # Локальные фото ЯМ лежат как /static/...
    photos = [
        (u if u.startswith("http") or u.startswith("/") else f"/{u.lstrip('/')}")
        for u in photos
    ]
    claim_date = d.get("created_at")
    if hasattr(claim_date, "isoformat"):
        claim_date = claim_date.isoformat()
    return {
        "mp": "ym",
        "claim_id": str(d.get("return_id") or ""),
        "srid": str(d.get("order_id") or ""),
        "order_id": d.get("order_id"),
        "supplier_article": d.get("supplier_article"),
        "nm_id": None,
        "claim_type": d.get("reason_type") or "return",
        "claim_type_label": reason,
        "status": status_code or status_label,
        "status_label": status_label,
        "status_ex": d.get("subreason_type") or "",
        "user_comment": (d.get("return_comment") or "").strip(),
        "is_archive": not ym_returns_api.is_active_status(status_code),
        "claim_date": claim_date,
        "order_date": None,
        "photos": photos,
        "product_photo": d.get("product_photo"),
        "correction": d.get("correction"),
        "audit_status": d.get("audit_status"),
    }


def _ozon_row_to_item(row: dict) -> dict:
    from .. import ozon_returns_api

    d = dict(row)
    status_label = (d.get("status_ru") or d.get("status_sys") or "—").strip() or "—"
    reason = (d.get("return_reason") or "").strip() or "—"
    rtype = d.get("return_type")
    type_label = ozon_returns_api.type_label(rtype)
    claim_date = d.get("return_date") or d.get("status_changed_at")
    if hasattr(claim_date, "isoformat"):
        claim_date = claim_date.isoformat()
    status_changed = d.get("status_changed_at")
    if hasattr(status_changed, "isoformat"):
        status_changed = status_changed.isoformat()
    is_arch = not ozon_returns_api.is_active_row(d.get("is_opened"), d.get("status_sys"))
    schema = (d.get("schema_code") or "Fbo").upper()
    return {
        "mp": "ozon",
        "claim_id": str(d.get("return_id") or ""),
        "srid": str(d.get("posting_number") or d.get("order_number") or ""),
        "posting_number": d.get("posting_number"),
        "order_number": d.get("order_number"),
        "supplier_article": d.get("supplier_article"),
        "nm_id": d.get("ozon_sku"),
        "product_name": d.get("product_name"),
        "claim_type": rtype or "return",
        "claim_type_label": f"{type_label}: {reason}" if reason != "—" else type_label,
        "return_reason": reason,
        "return_type": rtype,
        "return_type_label": type_label,
        "schema_code": schema,
        "status": d.get("status_sys") or status_label,
        "status_label": status_label,
        "status_ex": d.get("product_name") or "",
        "user_comment": reason,
        "is_archive": is_arch,
        "claim_date": claim_date,
        "status_changed_at": status_changed,
        "order_date": None,
        "photos": [],
        "product_photo": d.get("product_photo"),
        "barcode": d.get("barcode"),
        "place_name": d.get("place_name"),
        "target_place_name": d.get("target_place_name"),
        "price": d.get("price"),
        "compensation_status": d.get("compensation_status"),
        "ozon_tab": ozon_returns_api.tab_for_status(d.get("status_sys")),
        "needs_cabinet_decision": str(d.get("status_sys") or "") == "OnSellerApproval",
        "correction": d.get("correction"),
        "audit_status": d.get("audit_status"),
        "no_photos_note": "Фото и комментарий покупателя Seller API Ozon не отдаёт — смотрите в ЛК",
        "cabinet_url": ozon_returns_api.cabinet_url(d.get("return_id")),
    }


def _list_wb_claims(db: Session, *, active: bool, status: str, claim_type: str,
                    q: str, page: int, per_page: int) -> dict:
    offset = (page - 1) * per_page
    filters, params = ["1=1"], {}
    filters.append("c.is_archive = :arch")
    params["arch"] = not active
    if status:
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
            "Нет в API WB": ["Закрыта"],
            "Отменена": ["Отменена", "Отменено"],
            "Отменено": ["Отменена", "Отменено"],
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
    for it in items:
        it["mp"] = "wb"
    return {"items": items, "total": total or 0, "page": page,
            "total_pages": max(1, -(-(total or 0) // per_page)), "mp": "wb"}


def _list_ym_claims(db: Session, *, active: bool, status: str, q: str,
                    page: int, per_page: int) -> dict:
    from .. import ym_returns_api

    offset = (page - 1) * per_page
    filters, params = ["1=1"], {}
    active_list = sorted(ym_returns_api.ACTIVE_STATUSES)
    placeholders = ", ".join(f":as{i}" for i in range(len(active_list)))
    for i, s in enumerate(active_list):
        params[f"as{i}"] = s
    if active:
        filters.append(f"c.status IN ({placeholders})")
    else:
        filters.append(f"c.status NOT IN ({placeholders})")

    if status:
        st = status.strip()
        # Фильтр по русскому ярлыку или коду
        if st in ("На рассмотрении", "Одобрено", "Отказ", "Отменено"):
            codes = [k for k, v in ym_returns_api.STATUS_RU.items() if v == st]
            if codes:
                ph = []
                for i, c in enumerate(codes):
                    key = f"st{i}"
                    ph.append(f":{key}")
                    params[key] = c
                filters.append(f"c.status IN ({', '.join(ph)})")
            else:
                filters.append("c.status_ru = :st_ru")
                params["st_ru"] = st
        else:
            filters.append("(c.status = :st OR c.status_ru = :st)")
            params["st"] = st

    q = (q or "").strip()
    if q:
        params["q"] = f"%{q}%"
        params["q_exact"] = q
        filters.append("""(
            coalesce(c.supplier_article,'') ILIKE :q
            OR c.return_id::text ILIKE :q
            OR c.order_id::text ILIKE :q
            OR coalesce(c.return_comment,'') ILIKE :q
        )""")
    where = " AND ".join(filters)
    with db.bind.connect() as conn:
        total = conn.execute(
            text(f"SELECT COUNT(*) FROM ym_returns c WHERE {where}"), params
        ).scalar()
        rows = conn.execute(text(f"""
            SELECT c.return_id, c.order_id, c.supplier_article, c.return_type,
                   c.status, c.status_ru, c.created_at, c.return_comment,
                   c.photos, c.reason_type, c.subreason_type,
                   c.correction, c.audit_status,
                   pp.photo_url AS product_photo
            FROM ym_returns c
            LEFT JOIN product_photos pp ON pp.supplier_article = c.supplier_article
            WHERE {where}
            ORDER BY c.created_at DESC NULLS LAST
            LIMIT :lim OFFSET :off
        """), {**params, "lim": per_page, "off": offset}).mappings().all()
    items = [_ym_row_to_item(dict(r)) for r in rows]
    return {"items": items, "total": total or 0, "page": page,
            "total_pages": max(1, -(-(total or 0) // per_page)), "mp": "ym"}


def _list_ozon_claims(db: Session, *, active: bool, status: str, q: str,
                      page: int, per_page: int, tab: str = "",
                      claim_type: str = "") -> dict:
    from .. import ozon_returns_api

    ozon_returns_api.ensure_schema(db.bind)
    offset = (page - 1) * per_page
    filters, params = ["1=1"], {}

    tab = (tab or "").strip().lower()
    if tab in ozon_returns_api.TAB_STATUSES:
        statuses = sorted(ozon_returns_api.TAB_STATUSES[tab])
        ph = ", ".join(f":os{i}" for i in range(len(statuses)))
        for i, s in enumerate(statuses):
            params[f"os{i}"] = s
        filters.append(f"c.status_sys IN ({ph})")
    elif tab != "all":
        active_sys = sorted(ozon_returns_api.ACTIVE_SYS)
        ph = ", ".join(f":os{i}" for i in range(len(active_sys)))
        for i, s in enumerate(active_sys):
            params[f"os{i}"] = s
        if active:
            filters.append(f"(c.is_opened IS TRUE OR c.status_sys IN ({ph}))")
        else:
            filters.append(f"(COALESCE(c.is_opened, FALSE) IS FALSE AND c.status_sys NOT IN ({ph}))")

    if status:
        st = status.strip()
        filters.append("(c.status_ru = :st OR c.status_sys = :st)")
        params["st"] = st

    if claim_type:
        filters.append("c.return_type = :rtype")
        params["rtype"] = claim_type

    q = (q or "").strip()
    if q:
        params["q"] = f"%{q}%"
        filters.append("""(
            coalesce(c.supplier_article,'') ILIKE :q
            OR c.return_id::text ILIKE :q
            OR coalesce(c.posting_number,'') ILIKE :q
            OR coalesce(c.order_number,'') ILIKE :q
            OR coalesce(c.return_reason,'') ILIKE :q
            OR coalesce(c.product_name,'') ILIKE :q
            OR coalesce(c.barcode,'') ILIKE :q
            OR coalesce(c.target_place_name,'') ILIKE :q
        )""")
    where = " AND ".join(filters)
    # Очередь согласования — старые сверху (ближе к дедлайну в ЛК)
    order_sql = (
        "ORDER BY COALESCE(c.status_changed_at, c.return_date) ASC NULLS LAST"
        if tab == "approval"
        else "ORDER BY COALESCE(c.status_changed_at, c.return_date) DESC NULLS LAST"
    )
    with db.bind.connect() as conn:
        total = conn.execute(
            text(f"SELECT COUNT(*) FROM ozon_returns c WHERE {where}"), params
        ).scalar()
        rows = conn.execute(text(f"""
            SELECT c.return_id, c.order_id, c.order_number, c.posting_number,
                   c.supplier_article, c.product_name, c.ozon_sku,
                   c.return_reason, c.status_sys, c.status_ru, c.is_opened,
                   c.return_date, c.correction, c.audit_status,
                   c.return_type, c.schema_code, c.barcode, c.place_name,
                   c.target_place_name, c.price, c.status_changed_at,
                   c.source_id, c.compensation_status,
                   pp.photo_url AS product_photo
            FROM ozon_returns c
            LEFT JOIN product_photos pp ON pp.supplier_article = c.supplier_article
            WHERE {where}
            {order_sql}
            LIMIT :lim OFFSET :off
        """), {**params, "lim": per_page, "off": offset}).mappings().all()
    items = [_ozon_row_to_item(dict(r)) for r in rows]
    return {"items": items, "total": total or 0, "page": page,
            "total_pages": max(1, -(-(total or 0) // per_page)), "mp": "ozon"}


@router.get("/claims")
def list_claims(
    mp: str = Query("wb", description="Площадка: wb | ym | ozon"),
    active: bool = True,
    status: str = "",
    claim_type: str = Query("", alias="type"),
    tab: str = Query("", description="Ozon: transit|approval|received|resolved|utilization|all"),
    q: str = Query("", description="Поиск: артикул / заказ / id / комментарий"),
    page: int = 1,
    per_page: int = 25,
    db: Session = Depends(get_db),
):
    """Список возвратов площадки с фильтрами: active, status, type, q, tab."""
    m = _normalize_mp(mp)
    if m == "ym":
        return _list_ym_claims(db, active=active, status=status, q=q, page=page, per_page=per_page)
    if m == "ozon":
        return _list_ozon_claims(
            db, active=active, status=status, q=q, page=page, per_page=per_page,
            tab=tab, claim_type=claim_type,
        )
    return _list_wb_claims(
        db, active=active, status=status, claim_type=claim_type, q=q, page=page, per_page=per_page,
    )


class ClaimDecisionPayload(BaseModel):
    action: str
    comment: str = ""
    reason: str = ""  # YM: decisionReasonType при отказе


def _detail_wb(db: Session, claim_id: str) -> dict:
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
        item["mp"] = "wb"

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
        from ..wb_mini_sync import _map_claim_status, _status_ex_label
        wb_comment = str(live.get("wb_comment") or live.get("wbComment") or "")
        st_ex_raw = live.get("status_description") or live.get("status_ex") or ""
        mapped = _map_claim_status(live.get("status", ""), str(st_ex_raw), wb_comment)
        item["status"] = mapped
        item["status_ex"] = _status_ex_label(st_ex_raw, wb_comment) or item.get("status_ex")
        item = _decode_claim(item)
        if wb_comment and not item.get("wb_comment"):
            item["wb_comment"] = wb_comment
        item["actions"] = wb_claims_api.map_actions(raw_actions, status=item.get("status"))

    item["chat"] = chat
    item["can_decide"] = bool(item.get("actions")) and any(a.get("primary") for a in item["actions"])
    item["can_change_decision"] = any(a.get("is_change") for a in (item.get("actions") or []))
    item["decision_note"] = ""
    item["cabinet_url"] = "https://seller.wildberries.ru/suppliers-returns"
    return item


def _detail_ym(db: Session, claim_id: str) -> dict:
    from .. import ym_returns_api

    with db.bind.connect() as conn:
        row = conn.execute(text("""
            SELECT c.return_id, c.order_id, c.supplier_article, c.return_type,
                   c.status, c.status_ru, c.created_at, c.return_comment,
                   c.photos, c.reason_type, c.subreason_type,
                   c.correction, c.audit_status,
                   pp.photo_url AS product_photo
            FROM ym_returns c
            LEFT JOIN product_photos pp ON pp.supplier_article = c.supplier_article
            WHERE c.return_id::text = :cid
            LIMIT 1
        """), {"cid": claim_id}).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Возврат ЯМ не найден")
        item = _ym_row_to_item(dict(row))

        chat = None
        oid = str(item.get("order_id") or "")
        rid = str(item.get("claim_id") or "")
        if oid or rid:
            chat = conn.execute(text("""
                SELECT chat_id, customer_name AS client_name, needs_answer,
                       updated_at AS last_ts
                FROM ym_chats
                WHERE (:rid <> '' AND return_id::text = :rid)
                   OR (:oid <> '' AND order_id::text = :oid)
                ORDER BY updated_at DESC NULLS LAST
                LIMIT 1
            """), {"rid": rid, "oid": oid}).mappings().first()
            if chat:
                chat = dict(chat)
                if hasattr(chat.get("last_ts"), "isoformat"):
                    chat["last_ts"] = chat["last_ts"].isoformat()

    available = ym_returns_api.fetch_available_decisions(claim_id)
    item["actions"] = ym_returns_api.map_actions(available)
    item["actions_raw"] = available
    item["chat"] = chat
    item["can_decide"] = bool(item.get("actions")) and any(a.get("primary") for a in item["actions"])
    item["can_change_decision"] = False
    item["decision_note"] = "" if item["can_decide"] else (
        "Сейчас ЯМ не отдаёт доступные действия по этому возврату."
        if not ym_returns_api.is_active_status(item.get("status"))
        else "Ждём доступные действия от ЯМ (иногда появляется с задержкой)."
    )
    item["cabinet_url"] = "https://partner.market.yandex.ru/"
    return item


def _detail_ozon(db: Session, claim_id: str) -> dict:
    from .. import ozon_returns_api

    ozon_returns_api.ensure_schema(db.bind)
    with db.bind.connect() as conn:
        row = conn.execute(text("""
            SELECT c.return_id, c.order_id, c.order_number, c.posting_number,
                   c.supplier_article, c.product_name, c.ozon_sku,
                   c.return_reason, c.status_sys, c.status_ru, c.is_opened,
                   c.return_date, c.correction, c.audit_status,
                   c.return_type, c.schema_code, c.barcode, c.place_name,
                   c.target_place_name, c.price, c.status_changed_at,
                   c.source_id, c.compensation_status,
                   pp.photo_url AS product_photo
            FROM ozon_returns c
            LEFT JOIN product_photos pp ON pp.supplier_article = c.supplier_article
            WHERE c.return_id::text = :cid
            LIMIT 1
        """), {"cid": claim_id}).mappings().first()

    posting = (row or {}).get("posting_number") if row else None
    live_raw = ozon_returns_api.fetch_return_live(claim_id, posting_number=posting)
    if live_raw:
        parsed = ozon_returns_api.parse_api_item(live_raw)
        try:
            ozon_returns_api.upsert_parsed(db, parsed)
        except Exception:
            logger.exception("[Ozon Returns] upsert live failed claim=%s", claim_id)
        with db.bind.connect() as conn:
            row = conn.execute(text("""
                SELECT c.return_id, c.order_id, c.order_number, c.posting_number,
                       c.supplier_article, c.product_name, c.ozon_sku,
                       c.return_reason, c.status_sys, c.status_ru, c.is_opened,
                       c.return_date, c.correction, c.audit_status,
                       c.return_type, c.schema_code, c.barcode, c.place_name,
                       c.target_place_name, c.price, c.status_changed_at,
                       c.source_id, c.compensation_status,
                       pp.photo_url AS product_photo
                FROM ozon_returns c
                LEFT JOIN product_photos pp ON pp.supplier_article = c.supplier_article
                WHERE c.return_id::text = :cid
                LIMIT 1
            """), {"cid": claim_id}).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Возврат Ozon не найден")

    item = _ozon_row_to_item(dict(row))
    chat = None
    posting = str(item.get("posting_number") or "")
    order_n = str(item.get("order_number") or "")
    with db.bind.connect() as conn:
        if posting or order_n:
            chat = conn.execute(text("""
                SELECT chat_id, NULL::text AS client_name, needs_answer,
                       updated_at AS last_ts
                FROM ozon_chats
                WHERE (:p <> '' AND order_number = :p)
                   OR (:o <> '' AND order_number = :o)
                   OR (:p <> '' AND order_number = split_part(:p, '-', 1) || '-' || split_part(:p, '-', 2))
                   OR (:o <> '' AND order_number LIKE :o || '-%')
                ORDER BY updated_at DESC NULLS LAST
                LIMIT 1
            """), {"p": posting, "o": order_n}).mappings().first()
            if chat:
                chat = dict(chat)
                if hasattr(chat.get("last_ts"), "isoformat"):
                    chat["last_ts"] = chat["last_ts"].isoformat()

    cap = ozon_returns_api.decision_capability(
        claim_id, status_sys=dict(row).get("status_sys")
    )
    item["actions"] = cap.get("actions") or []
    item["chat"] = chat
    item["can_decide"] = bool(cap.get("can_decide"))
    item["can_change_decision"] = False
    item["decision_note"] = cap.get("decision_note") or ""
    item["needs_cabinet_decision"] = bool(
        cap.get("needs_cabinet") or item.get("needs_cabinet_decision")
    )
    item["rfbs"] = bool(cap.get("rfbs"))
    item["cabinet_url"] = ozon_returns_api.cabinet_url(claim_id)
    item["api_limits_note"] = (
        "В ЛК Ozon есть комментарий, фото покупателя, история и «Открыть спор». "
        "Seller API этого не отдаёт — откройте полную карточку в кабинете."
    )
    return item


@router.get("/claims/{claim_id}")
def get_claim_detail(
    claim_id: str,
    mp: str = Query("wb", description="Площадка: wb | ym | ozon"),
    db: Session = Depends(get_db),
):
    """Карточка возврата + доступные действия площадки + связанный чат."""
    m = _normalize_mp(mp)
    if m == "ym":
        return _detail_ym(db, claim_id)
    if m == "ozon":
        return _detail_ozon(db, claim_id)
    return _detail_wb(db, claim_id)


@router.post("/claims/{claim_id}/open-chat")
def open_claim_chat(
    claim_id: str,
    mp: str = Query("wb", description="Площадка: wb | ym | ozon"),
    db: Session = Depends(get_db),
):
    """Открыть / создать чат с покупателем по возврату.

    - ЯМ: POST …/chats/new {type:RETURN|ORDER, id}
    - Ozon: POST /v1/chat/start {posting_number} (Premium+)
    - WB: публичный API не умеет создавать чат (покупатель начинает сам).
      Кнопка «Чат с покупателем» в ЛК — веб-кабинет. Мы ищем уже
      существующий чат по srid заказа и открываем его в CX.
    """
    m = _normalize_mp(mp)

    if m == "wb":
        detail = _detail_wb(db, claim_id)
        chat = detail.get("chat") or {}
        cid = chat.get("chat_id")
        if cid:
            return {"ok": True, "mp": "wb", "chat_id": cid, "created": False}
        raise HTTPException(
            status_code=404,
            detail=(
                "В WB через API нельзя начать чат с покупателем — чат открывает только покупатель. "
                "Кнопка «Чат с покупателем» в ЛК продавца работает в веб-кабинете. "
                "Если покупатель уже писал — чат появится здесь после синхронизации (привязка по srid заказа)."
            ),
        )

    if m == "ym":
        detail = _detail_ym(db, claim_id)
        existing = (detail.get("chat") or {}).get("chat_id")
        if existing:
            return {"ok": True, "mp": "ym", "chat_id": str(existing), "created": False}

        return_id = detail.get("claim_id") or claim_id
        order_id = detail.get("order_id")
        last_err = None
        chat_id = None
        created = False
        for ctype, ctx_id in (("RETURN", return_id), ("ORDER", order_id)):
            if not ctx_id:
                continue
            try:
                result = ym_client.create_chat(context_type=ctype, context_id=ctx_id)
                chat_id = str(result.get("chatId") or result.get("chat_id") or "").strip()
                if chat_id:
                    created = True
                    break
            except Exception as e:
                last_err = str(e)
                logger.warning("[YM] create chat %s/%s: %s", ctype, ctx_id, e)

        if not chat_id:
            raise HTTPException(
                status_code=502,
                detail=last_err or "Не удалось создать чат в ЯМ по этому возврату/заказу",
            )

        with db.bind.connect() as conn:
            conn.execute(text("""
                INSERT INTO ym_chats (chat_id, status, context_type, order_id, return_id,
                                     needs_answer, created_at, updated_at)
                VALUES (:cid, 'WAITING_FOR_PARTNER', 'RETURN', :oid, :rid,
                        false, NOW(), NOW())
                ON CONFLICT (chat_id) DO UPDATE SET
                    order_id  = COALESCE(EXCLUDED.order_id, ym_chats.order_id),
                    return_id = COALESCE(EXCLUDED.return_id, ym_chats.return_id),
                    updated_at = NOW()
            """), {
                "cid": chat_id,
                "oid": str(order_id) if order_id else None,
                "rid": str(return_id) if return_id else None,
            })
            conn.commit()
        return {"ok": True, "mp": "ym", "chat_id": chat_id, "created": created}

    # ozon
    detail = _detail_ozon(db, claim_id)
    existing = (detail.get("chat") or {}).get("chat_id")
    if existing:
        return {"ok": True, "mp": "ozon", "chat_id": str(existing), "created": False}

    posting = (detail.get("posting_number") or detail.get("order_number") or "").strip()
    if not posting:
        raise HTTPException(
            status_code=400,
            detail="У возврата Ozon нет posting_number — нельзя создать чат",
        )
    try:
        result = ozon_client.start_chat(posting)
    except Exception as e:
        logger.warning("[Ozon] start chat posting=%s: %s", posting, e)
        raise HTTPException(
            status_code=502,
            detail=f"Ozon не создал чат: {e}. Нужен Premium Plus и валидное отправление.",
        ) from e

    chat_id = str(result.get("chat_id") or result.get("chatId") or "").strip()
    if not chat_id:
        raise HTTPException(status_code=502, detail="Ozon вернул пустой chat_id")

    with db.bind.connect() as conn:
        conn.execute(text("""
            INSERT INTO ozon_chats (chat_id, chat_status, chat_type, order_number,
                                   needs_answer, created_at, updated_at)
            VALUES (:cid, 'OPENED', 'Buyer_Seller', :ord, false, NOW(), NOW())
            ON CONFLICT (chat_id) DO UPDATE SET
                order_number = COALESCE(NULLIF(EXCLUDED.order_number, ''), ozon_chats.order_number),
                updated_at = NOW()
        """), {"cid": chat_id, "ord": posting})
        conn.commit()
    return {"ok": True, "mp": "ozon", "chat_id": chat_id, "created": True}


@router.post("/claims/{claim_id}/decision")
def decide_claim(
    claim_id: str,
    payload: ClaimDecisionPayload,
    mp: str = Query("wb", description="Площадка: wb | ym | ozon"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Одобрить / отказать по возврату (WB / ЯМ / Ozon rFBS)."""
    from .. import wb_claims_api, ym_returns_api, ozon_returns_api

    action = (payload.action or "").strip()
    if not action:
        raise HTTPException(status_code=400, detail="Не указано действие")
    m = _normalize_mp(mp)
    who = current_user.get("username") or current_user.get("sub") or "?"

    if m == "ym":
        result = ym_returns_api.decide_return(
            db, claim_id, action, payload.comment or "", payload.reason or "",
        )
        if not result.get("ok"):
            code = int(result.get("status_code") or 502)
            if code < 400 or code > 599:
                code = 502
            raise HTTPException(status_code=code, detail=result.get("error") or "Ошибка ЯМ")
        logger.info("[YM Returns] decision claim=%s action=%s by=%s", claim_id, action, who)
        return result

    if m == "ozon":
        result = ozon_returns_api.decide_return(
            db, claim_id, action, payload.comment or "",
        )
        if not result.get("ok"):
            code = int(result.get("status_code") or 502)
            if code < 400 or code > 599:
                code = 502
            raise HTTPException(status_code=code, detail=result.get("error") or "Ошибка Ozon")
        logger.info("[Ozon Returns] decision claim=%s action=%s by=%s", claim_id, action, who)
        return result

    result = wb_claims_api.decide_claim(db, claim_id, action, payload.comment or "")
    if not result.get("ok"):
        code = int(result.get("status_code") or 502)
        if code < 400 or code > 599:
            code = 502
        raise HTTPException(status_code=code, detail=result.get("error") or "Ошибка WB")

    logger.info("[WB Claims] decision claim=%s action=%s by=%s", claim_id, action, who)
    return result


_RESHIP_STATUS_RU = {
    "new": "Новая",
    "matched": "Сопоставлена",
    "approved": "На отправку",
    "packed": "Собран",
    "shipped": "Отправлен",
    "in_transit": "В пути",
    "ready_for_pickup": "Готов к выдаче",
    "delivered": "Получен",
    "rejected": "Отклонён",
    "returned": "Вернулся",
}


def _find_reshipment_for_chat(conn, chat_id: str, *, mp: str = "wb") -> dict | None:
    """Заявка на доотправку, привязанная к этому чату (wb_chat_id / ym_chat_id)."""
    cid = (chat_id or "").strip()
    if not cid:
        return None
    mp = (mp or "wb").lower()
    try:
        if mp == "ym":
            row = conn.execute(text("""
                SELECT id, status, cdek_number, track_number, delivery_method,
                       client_pvz_address, customer_name, created_at, updated_at,
                       matched_srid, marketplace
                FROM reshipment_requests
                WHERE ym_chat_id = :cid
                ORDER BY id DESC LIMIT 1
            """), {"cid": cid}).mappings().first()
        else:
            # WB: в БД может быть `1:uuid` или голый uuid
            bare = cid.split(":", 1)[-1] if ":" in cid else cid
            prefixed = cid if ":" in cid else f"1:{cid}"
            row = conn.execute(text("""
                SELECT id, status, cdek_number, track_number, delivery_method,
                       client_pvz_address, customer_name, created_at, updated_at,
                       matched_srid, marketplace
                FROM reshipment_requests
                WHERE wb_chat_id = :cid
                   OR wb_chat_id = :bare
                   OR wb_chat_id = :prefixed
                   OR wb_chat_id LIKE :suf
                ORDER BY id DESC LIMIT 1
            """), {
                "cid": cid, "bare": bare, "prefixed": prefixed, "suf": f"%:{bare}",
            }).mappings().first()
        if not row:
            return None
        d = dict(row)
        st = (d.get("status") or "").strip()
        return {
            "id": d["id"],
            "status": st,
            "status_label": _RESHIP_STATUS_RU.get(st, st or "—"),
            "created_date": _ru_date(d.get("created_at")),
            "track_number": d.get("track_number") or d.get("cdek_number") or "",
            "delivery_method": d.get("delivery_method") or "",
            "pvz": d.get("client_pvz_address") or "",
            "customer_name": d.get("customer_name") or "",
            "matched_srid": d.get("matched_srid") or "",
            "marketplace": (d.get("marketplace") or mp or "wb").lower(),
        }
    except Exception:
        return None


@router.get("/chat-context/wb/{chat_id}")
def get_wb_chat_context(chat_id: str, db: Session = Depends(get_db)):
    """Конкретный отзыв/претензия клиента (по rid заказа) + все обращения по товару."""
    with db.bind.connect() as conn:
        resolved = _resolve_wb_chat_id(conn, chat_id) or chat_id
        chat = conn.execute(text(
            "SELECT nm_id, order_rid FROM wb_chats WHERE chat_id = :cid"
        ), {"cid": resolved}).mappings().first()
        if not chat:
            return {"specific": {}, "reviews": [], "questions": [], "claims": []}
        nm_raw = chat.get("nm_id")
        nm_str = str(int(nm_raw)) if nm_raw is not None else None

        # Rid: order_rid (из списка чатов) → goodCard в сообщениях → pending
        rid = (chat.get("order_rid") or "").strip() or None
        if not rid:
            rid_row = conn.execute(text("""
                SELECT attachments->'goodCard'->>'rid' AS rid
                FROM wb_chat_messages
                WHERE chat_id = :cid
                  AND (attachments->'goodCard'->>'rid') IS NOT NULL
                  AND (attachments->'goodCard'->>'rid') <> ''
                ORDER BY add_ts ASC NULLS LAST
                LIMIT 1
            """), {"cid": resolved}).mappings().first()
            rid = rid_row["rid"] if rid_row else None
        if not rid:
            p = conn.execute(text(
                "SELECT rid FROM wb_chat_pending WHERE chat_id = :cid AND rid IS NOT NULL LIMIT 1"
            ), {"cid": resolved}).mappings().first()
            rid = p["rid"] if p else None

        specific_claim  = None
        specific_review = None
        if rid:
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

            # Как в reshipment: shk_id ИЛИ дата+артикул
            rv = conn.execute(text("""
                SELECT f.id::text, f.valuation AS rating, f.state,
                       f.text, f.pros, f.cons, f.answer_text AS answer, f.created_date
                FROM wb_feedbacks f
                JOIN wb_orders o ON (
                    o.shk_id = f.last_order_shk_id
                    OR (
                      f.last_order_created_at IS NOT NULL
                      AND o.dt = f.last_order_created_at + INTERVAL '3 hours'
                      AND o.supplier_article = f.supplier_article
                    )
                )
                WHERE o.srid = :rid
                ORDER BY f.created_date DESC NULLS LAST
                LIMIT 1
            """), {"rid": rid}).mappings().first()
            if rv:
                r = dict(rv)
                r["created_date"] = _ru_date(r.get("created_date"))
                specific_review = r

        specific_reshipment = _find_reshipment_for_chat(conn, resolved, mp="wb")
        if not specific_reshipment and resolved != chat_id:
            specific_reshipment = _find_reshipment_for_chat(conn, chat_id, mp="wb")

        reviews, questions, claims = [], [], []
        if nm_str:
            reviews = conn.execute(text("""
                SELECT id::text, valuation AS rating, state,
                       text, pros,
                       to_char(created_date, 'DD.MM.YY') AS created_date
                FROM wb_feedbacks WHERE nm_id::text = :nm
                ORDER BY created_date DESC NULLS LAST LIMIT 5
            """), {"nm": nm_str}).mappings().all()

            questions = conn.execute(text("""
                SELECT q.id::text, q.text AS body, q.state,
                       to_char(q.created_date, 'DD.MM.YY') AS created_date
                FROM wb_questions q
                WHERE q.supplier_article IN (
                    SELECT DISTINCT f.supplier_article FROM wb_feedbacks f
                    WHERE f.nm_id::text = :nm AND f.supplier_article IS NOT NULL
                ) ORDER BY q.created_date DESC NULLS LAST LIMIT 3
            """), {"nm": nm_str}).mappings().all()

            claims = conn.execute(text("""
                SELECT claim_id::text, claim_type, user_comment AS body,
                       status, is_archive,
                       to_char(claim_date, 'DD.MM.YY') AS claim_date
                FROM wb_claims WHERE nm_id::text = :nm
                ORDER BY claim_date DESC NULLS LAST LIMIT 5
            """), {"nm": nm_str}).mappings().all()

    return {
        "specific": {
            "claim": specific_claim,
            "review": specific_review,
            "reshipment": specific_reshipment,
        },
        "reviews":  [dict(r) for r in reviews],
        "questions":[dict(r) for r in questions],
        "claims":   [_decode_claim(dict(c)) for c in claims],
    }


def _ozon_return_as_claim(row: dict) -> dict:
    """Привести строку ozon_returns к форме specific.claim для UI чата."""
    from .. import ozon_returns_api

    d = dict(row)
    reason = (d.get("return_reason") or "").strip() or "—"
    status = (d.get("status_ru") or d.get("status_sys") or "").strip() or "—"
    is_arch = not ozon_returns_api.is_active_row(d.get("is_opened"), d.get("status_sys"))
    return {
        "mp": "ozon",
        "claim_id": str(d.get("return_id") or d.get("posting_number") or d.get("id") or ""),
        "claim_type": "return",
        "claim_type_label": "Возврат Ozon",
        "body": reason,
        "status": status,
        "status_label": status,
        "is_archive": is_arch,
        "claim_date": _ru_date(d.get("return_date")),
        "supplier_article": d.get("supplier_article"),
        "posting_number": d.get("posting_number"),
        "order_number": d.get("order_number"),
    }


def _ym_return_as_claim(row: dict) -> dict:
    """Привести строку ym_returns к форме specific.claim для UI чата."""
    from .. import ym_returns_api

    d = dict(row)
    body = (d.get("return_comment") or d.get("reason_type") or "").strip() or "—"
    status_code = str(d.get("status") or "")
    status = ym_returns_api.status_ru(status_code, d.get("status_ru"))
    return {
        "mp": "ym",
        "claim_id": str(d.get("return_id") or d.get("id") or ""),
        "claim_type": "return",
        "claim_type_label": "Возврат ЯМ",
        "body": body,
        "status": status_code or status,
        "status_label": status,
        "is_archive": not ym_returns_api.is_active_status(status_code),
        "claim_date": _ru_date(d.get("created_at")),
        "supplier_article": d.get("supplier_article"),
        "order_id": d.get("order_id"),
    }


@router.get("/chat-context/ozon/{chat_id}")
def get_ozon_chat_context(chat_id: str, db: Session = Depends(get_db)):
    """Возврат только по заказу этого чата (не по SKU — иначе чужие возвраты)."""
    with db.bind.connect() as conn:
        chat = conn.execute(text("""
            SELECT order_number, context_sku, context_article
            FROM ozon_chats WHERE chat_id = :cid
        """), {"cid": chat_id}).mappings().first()
        if not chat:
            return {"specific": {}, "reviews": [], "questions": [], "claims": []}

        order_number = (chat.get("order_number") or "").strip() or None

        specific_claim = None
        claims = []
        # Без номера заказа в чате — не угадываем возврат по артикулу/SKU
        if order_number:
            cr = conn.execute(text("""
                SELECT return_id, posting_number, order_number, ozon_sku,
                       supplier_article, return_reason, status_ru, status_sys,
                       return_date, is_opened, id
                FROM ozon_returns
                WHERE posting_number = :ord
                   OR order_number = :ord
                   OR posting_number LIKE :ord || '-%'
                   OR order_number LIKE :ord || '-%'
                ORDER BY
                    CASE
                      WHEN posting_number = :ord OR order_number = :ord THEN 0
                      WHEN posting_number LIKE :ord || '-%' OR order_number LIKE :ord || '-%' THEN 1
                      ELSE 2
                    END,
                    return_date DESC NULLS LAST
                LIMIT 1
            """), {"ord": order_number}).mappings().first()
            if cr:
                specific_claim = _ozon_return_as_claim(dict(cr))

            rows = conn.execute(text("""
                SELECT return_id, posting_number, order_number, ozon_sku,
                       supplier_article, return_reason, status_ru, status_sys,
                       return_date, is_opened, id
                FROM ozon_returns
                WHERE posting_number = :ord
                   OR order_number = :ord
                   OR posting_number LIKE :ord || '-%'
                   OR order_number LIKE :ord || '-%'
                ORDER BY return_date DESC NULLS LAST
                LIMIT 5
            """), {"ord": order_number}).mappings().all()
            claims = [_ozon_return_as_claim(dict(r)) for r in rows]

    return {
        "specific": {"claim": specific_claim, "review": None, "reshipment": None},
        "reviews": [],
        "questions": [],
        "claims": claims,
    }


@router.get("/chat-context/ym/{chat_id}")
def get_ym_chat_context(chat_id: str, db: Session = Depends(get_db)):
    """Возврат только по return_id/order_id этого чата (не по артикулу)."""
    with db.bind.connect() as conn:
        chat = conn.execute(text("""
            SELECT return_id, order_id, context_type, customer_name
            FROM ym_chats WHERE chat_id = :cid
        """), {"cid": chat_id}).mappings().first()
        if not chat:
            return {"specific": {}, "reviews": [], "questions": [], "claims": []}

        return_id = (str(chat.get("return_id") or "").strip() or None)
        order_id = (str(chat.get("order_id") or "").strip() or None)

        specific_claim = None
        claims = []
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

            rows = conn.execute(text("""
                SELECT return_id, order_id, supplier_article, return_comment,
                       reason_type, status_ru, status, created_at, id
                FROM ym_returns
                WHERE (
                    (:rid IS NOT NULL AND return_id::text = :rid)
                    OR (:oid IS NOT NULL AND order_id::text = :oid)
                )
                ORDER BY created_at DESC NULLS LAST
                LIMIT 5
            """), {"rid": return_id, "oid": order_id}).mappings().all()
            claims = [_ym_return_as_claim(dict(r)) for r in rows]

        specific_reshipment = _find_reshipment_for_chat(conn, chat_id, mp="ym")

    return {
        "specific": {
            "claim": specific_claim,
            "review": None,
            "reshipment": specific_reshipment,
        },
        "reviews": [],
        "questions": [],
        "claims": claims,
    }


def _wb_cdn_photo_url(nm_id: int) -> str:
    """Публичный CDN WB (без Content API) — запасной вариант превью."""
    nm = int(nm_id)
    vol = nm // 100000
    part = nm // 1000
    if vol <= 143:
        host = "01"
    elif vol <= 287:
        host = "02"
    elif vol <= 431:
        host = "03"
    elif vol <= 575:
        host = "04"
    elif vol <= 719:
        host = "05"
    elif vol <= 1007:
        host = "06"
    elif vol <= 1061:
        host = "07"
    elif vol <= 1115:
        host = "08"
    elif vol <= 1169:
        host = "09"
    elif vol <= 1313:
        host = "10"
    elif vol <= 1601:
        host = "11"
    elif vol <= 1655:
        host = "12"
    elif vol <= 1919:
        host = "13"
    elif vol <= 2045:
        host = "14"
    elif vol <= 2189:
        host = "15"
    elif vol <= 2405:
        host = "16"
    elif vol <= 2621:
        host = "17"
    elif vol <= 2837:
        host = "18"
    elif vol <= 3053:
        host = "19"
    elif vol <= 3269:
        host = "20"
    elif vol <= 3485:
        host = "21"
    elif vol <= 3701:
        host = "22"
    elif vol <= 3917:
        host = "23"
    elif vol <= 4133:
        host = "24"
    elif vol <= 4349:
        host = "25"
    elif vol <= 4565:
        host = "26"
    elif vol <= 4877:
        host = "27"
    elif vol <= 5189:
        host = "28"
    elif vol <= 5501:
        host = "29"
    else:
        host = "30"
    return f"https://basket-{host}.wbbasket.ru/vol{vol}/part{part}/{nm}/images/c246x328/1.webp"


def _fetch_and_cache_nm_photo(db: Session, nm_id: int) -> str | None:
    """Догрузить одно фото в кэш (Content API textSearch → иначе CDN)."""
    url = None
    key = wb_client.content_key()
    if key:
        try:
            r = requests.post(
                f"{wb_client.CONTENT_BASE}/content/v2/get/cards/list",
                json={
                    "settings": {
                        "cursor": {"limit": 100},
                        "filter": {"textSearch": str(nm_id), "withPhoto": 1},
                    }
                },
                headers=wb_client.auth_headers(key),
                timeout=20,
            )
            if r.ok:
                for card in (r.json().get("data") or {}).get("cards") or []:
                    if int(card.get("nmID") or 0) != int(nm_id):
                        continue
                    photos = card.get("photos") or []
                    if photos:
                        url = (
                            photos[0].get("tm")
                            or photos[0].get("small")
                            or photos[0].get("big")
                            or ""
                        )
                    break
        except Exception as e:
            logger.warning("[Photo] fetch nm=%s: %s", nm_id, e)
    if not url:
        url = _wb_cdn_photo_url(nm_id)
    try:
        with db.bind.connect() as conn:
            conn.execute(text("""
                INSERT INTO wb_product_photos (nm_id, photo_url, updated_at)
                VALUES (:nm, :url, now())
                ON CONFLICT (nm_id) DO UPDATE
                SET photo_url = EXCLUDED.photo_url, updated_at = now()
            """), {"nm": nm_id, "url": url})
            conn.commit()
    except Exception as e:
        logger.warning("[Photo] cache nm=%s: %s", nm_id, e)
    return url


@router.get("/wb-photo/{nm_id}")
def wb_product_photo(nm_id: str, db: Session = Depends(get_db)):
    """Фото товара WB — редирект на CDN URL из кэша (с догрузкой при промахе)."""
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
    url = _fetch_and_cache_nm_photo(db, nm)
    if url:
        return RedirectResponse(url, status_code=302)
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


_CHAT_ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024
_CHAT_ATTACHMENT_MAX_TOTAL_BYTES = 30 * 1024 * 1024
_CHAT_ATTACHMENT_MAX_COUNT = 6
_CHAT_ATTACHMENT_TYPES = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "application/pdf": {".pdf"},
}


def _detected_chat_attachment_type(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content[:1024].lstrip().startswith(b"%PDF-"):
        return "application/pdf"
    return None


async def _validate_chat_attachments(files: list[UploadFile]) -> list[dict]:
    """Проверить magic/ext/лимиты; declared MIME может отсутствовать."""
    if len(files or []) > _CHAT_ATTACHMENT_MAX_COUNT:
        raise HTTPException(
            status_code=400,
            detail=f"Можно прикрепить не больше {_CHAT_ATTACHMENT_MAX_COUNT} файлов",
        )
    result = []
    total_bytes = 0
    for upload in files or []:
        raw_name = (upload.filename or "").replace("\\", "/")
        filename = PurePath(raw_name).name
        if not filename:
            raise HTTPException(status_code=400, detail="У файла отсутствует имя")

        content = await upload.read(_CHAT_ATTACHMENT_MAX_BYTES + 1)
        if not content:
            raise HTTPException(status_code=400, detail=f"Файл «{filename}» пустой")
        if len(content) > _CHAT_ATTACHMENT_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Файл «{filename}» больше 5 МиБ",
            )
        total_bytes += len(content)
        if total_bytes > _CHAT_ATTACHMENT_MAX_TOTAL_BYTES:
            raise HTTPException(status_code=413, detail="Общий размер файлов больше 30 МиБ")

        detected_type = _detected_chat_attachment_type(content)
        declared_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
        if declared_type == "image/jpg":
            declared_type = "image/jpeg"
        if detected_type not in _CHAT_ATTACHMENT_TYPES:
            raise HTTPException(
                status_code=415,
                detail=f"Файл «{filename}»: разрешены только JPEG, PNG и PDF",
            )
        if declared_type and declared_type not in _CHAT_ATTACHMENT_TYPES:
            raise HTTPException(
                status_code=415,
                detail=f"Файл «{filename}»: недопустимый тип {declared_type}",
            )
        if declared_type and declared_type != detected_type:
            raise HTTPException(
                status_code=415,
                detail=f"Файл «{filename}»: содержимое не соответствует типу файла",
            )

        suffix = PurePath(filename).suffix.lower()
        if suffix not in _CHAT_ATTACHMENT_TYPES[detected_type]:
            raise HTTPException(
                status_code=415,
                detail=f"Файл «{filename}»: расширение не соответствует содержимому",
            )
        result.append({"name": filename, "content": content, "content_type": detected_type})
    return result


def _new_chat_send_progress(message: str, attachments: list[dict]) -> dict:
    return {
        "status": "pending",
        "text": {
            "state": "pending" if message else "not_requested",
        },
        "files": [
            {"index": index, "name": item["name"], "state": "pending"}
            for index, item in enumerate(attachments)
        ],
        "warnings": [],
        "persistence": "confirmed",
    }


def _marketplace_failure_state(exc: Exception) -> str:
    # Timeout/connection loss can happen after the marketplace accepted a request.
    return "unknown" if isinstance(exc, (requests.Timeout, requests.ConnectionError)) else "failed"


def _stop_chat_send_progress(progress: dict, failed_step: dict, exc: Exception) -> dict:
    state = _marketplace_failure_state(exc)
    failed_step["state"] = state
    failed_step["error"] = str(exc)[:300]
    for step in [progress["text"], *progress["files"]]:
        if step["state"] == "pending":
            step["state"] = "not_attempted"
    confirmed = progress["text"]["state"] == "confirmed" or any(
        step["state"] == "confirmed" for step in progress["files"]
    )
    progress["status"] = "uncertain" if state == "unknown" else ("partial" if confirmed else "failed")
    if state == "unknown":
        progress["warnings"].append(
            "Маркетплейс мог принять последний шаг до таймаута. Не повторяйте его до сверки истории чата."
        )
    return progress


def _persist_confirmed_chat_step(
    db: Session,
    *,
    mp: str,
    chat_id: str,
    username: str,
    kind: str,
    value: str,
) -> None:
    """Persist audit/queue state only; never create synthetic history rows."""
    preview = value if kind == "text" else f"Вложение: {value}"
    table = "ozon_chats" if mp == "ozon" else "wb_chats"
    with db.bind.connect() as conn:
        if mp == "ozon":
            conn.execute(text(f"""
                UPDATE {table} SET needs_answer = false, last_sender = 'seller',
                    last_message = :preview, unread_count = 0, waiting_since = NULL,
                    updated_at = now()
                WHERE chat_id = :cid
            """), {"cid": chat_id, "preview": preview[:500]})
        else:
            conn.execute(text(f"""
                UPDATE {table} SET needs_answer = false, last_sender = 'seller',
                    last_message = :preview, last_ts = :ts, updated_at = now()
                WHERE chat_id = :cid
            """), {"cid": chat_id, "preview": preview[:1000], "ts": int(time.time() * 1000)})
        conn.execute(text("""
            INSERT INTO cx_response_log (item_type, item_id, mp, answered_by, answer_text)
            VALUES ('chat', :iid, :mp, :user, :answer)
        """), {
            "iid": chat_id,
            "mp": mp,
            "user": username,
            "answer": value if kind == "text" else f"[Вложение] {value}",
        })
        conn.commit()


async def _persist_or_stop_progress(
    progress: dict,
    step: dict,
    persist_step,
    kind: str,
    value: str,
) -> bool:
    try:
        await run_in_threadpool(persist_step, kind, value)
        return True
    except Exception as exc:
        progress["persistence"] = "failed"
        progress["status"] = "partial"
        progress["warnings"].append(
            "Шаг подтверждён маркетплейсом, но не записан локально. Нужна сверка истории перед повтором."
        )
        step["persistence_error"] = str(exc)[:300]
        for pending in [progress["text"], *progress["files"]]:
            if pending["state"] == "pending":
                pending["state"] = "not_attempted"
        return False


async def _run_ozon_chat_send_steps(
    chat_id: str,
    message: str,
    attachments: list[dict],
    persist_step,
) -> dict:
    """Execute non-atomic Ozon calls with explicit, retry-safe progress."""
    from .. import ozon_chat_sync

    progress = _new_chat_send_progress(message, attachments)
    text_step = progress["text"]
    if message:
        try:
            await run_in_threadpool(ozon_chat_sync.send_message, chat_id, message)
        except Exception as exc:
            return _stop_chat_send_progress(progress, text_step, exc)
        text_step["state"] = "confirmed"
        if not await _persist_or_stop_progress(progress, text_step, persist_step, "text", message):
            return progress

    for index, attachment in enumerate(attachments):
        file_step = progress["files"][index]
        encoded = base64.b64encode(attachment["content"]).decode("ascii")
        try:
            await run_in_threadpool(
                ozon_client.send_chat_file,
                chat_id,
                attachment["name"],
                encoded,
                timeout=20,
            )
        except Exception as exc:
            return _stop_chat_send_progress(progress, file_step, exc)
        file_step["state"] = "confirmed"
        if not await _persist_or_stop_progress(
            progress, file_step, persist_step, "file", attachment["name"]
        ):
            return progress

    progress["status"] = "complete"
    return progress


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
    visibility: str = Query("public", description="public | private"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    txt = payload.text.strip()
    if not txt:
        raise HTTPException(status_code=400, detail="Текст ответа не может быть пустым")
    if visibility not in ("public", "private"):
        raise HTTPException(status_code=400, detail="visibility: public или private")
    if not _wb_key():
        raise HTTPException(status_code=503, detail="WB_API_KEY не настроен")

    try:
        wb_client.answer_question(question_id, txt, public=visibility == "public", timeout=20)
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

    return {"status": "ok", "visibility": visibility}


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


@router.post("/ozon/chats/{chat_id}/message-with-files")
async def send_ozon_chat_message_with_files(
    chat_id: str,
    text_value: str = Form("", alias="text"),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    txt = (text_value or "").strip()
    attachments = await _validate_chat_attachments(files)
    if not txt and not attachments:
        raise HTTPException(status_code=400, detail="Добавьте текст или файл")
    if not _oz_key() or not _oz_cid():
        raise HTTPException(status_code=503, detail="OZON_API_KEY / OZON_CLIENT_ID не настроены")

    def persist_step(kind: str, value: str) -> None:
        _persist_confirmed_chat_step(
            db,
            mp="ozon",
            chat_id=chat_id,
            username=current_user["username"],
            kind=kind,
            value=value,
        )

    progress = await _run_ozon_chat_send_steps(chat_id, txt, attachments, persist_step)
    status_code = 200 if progress["status"] == "complete" else 207
    return JSONResponse(status_code=status_code, content=progress)


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


@router.post("/ym/chats/{chat_id}/message-with-files")
async def send_ym_chat_message_with_files(
    chat_id: str,
    text_value: str = Form("", alias="text"),
    files: list[UploadFile] = File(default=[]),
):
    # Multipart-параметры объявлены, чтобы клиент всегда получал явный ответ
    # нашего API, а не 422 FastAPI. У ЯМ уже есть официальный метод
    # POST /v2/businesses/{businessId}/chats/file/send, но текущий ym_client его
    # не реализует; включение ЯМ вынесено из scope этой фазы.
    _ = (chat_id, text_value, files)
    raise HTTPException(
        status_code=501,
        detail="На Яндекс Маркете вложения пока недоступны",
    )


# ── Ответ в чат WB ───────────────────────────────────────────────────────────

def _resolve_wb_reply_sign(chat_id: str, db: Session) -> str:
    """Найти актуальный replySign тем же каскадом, что использует текстовый ответ."""
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

    # Кэш часто пустой: получаем свежую подпись из списка чатов WB.
    try:
        from .. import wb_chat as _wbc
        fresh = _wbc.resolve_reply_sign(chat_id)
        if fresh:
            reply_sign = fresh
    except Exception:
        pass
    return reply_sign

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

    reply_sign = _resolve_wb_reply_sign(chat_id, db)

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


@router.post("/wb/chats/{chat_id}/message-with-files")
async def send_wb_chat_message_with_files(
    chat_id: str,
    text_value: str = Form("", alias="text"),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    txt = (text_value or "").strip()
    attachments = await _validate_chat_attachments(files)
    if not txt and not attachments:
        raise HTTPException(status_code=400, detail="Добавьте текст или файл")
    if not _wb_key():
        raise HTTPException(status_code=503, detail="WB_API_KEY не настроен")

    reply_sign = await run_in_threadpool(_resolve_wb_reply_sign, chat_id, db)
    if not reply_sign:
        raise HTTPException(
            status_code=404,
            detail="Не удалось получить ключ чата у Wildberries. Обновите список чатов и попробуйте снова.",
        )

    progress = _new_chat_send_progress(txt, attachments)
    try:
        await run_in_threadpool(
            wb_client.send_chat_message,
            reply_sign,
            txt,
            attachments=[
                (item["name"], item["content"], item["content_type"])
                for item in attachments
            ],
            timeout=20,
        )
    except Exception as exc:
        state = _marketplace_failure_state(exc)
        for step in [progress["text"], *progress["files"]]:
            if step["state"] == "pending":
                step["state"] = state
                step["error"] = str(exc)[:300]
        progress["status"] = "uncertain" if state == "unknown" else "failed"
        if state == "unknown":
            progress["warnings"].append(
                "Wildberries мог принять multipart до таймаута. Не повторяйте его до сверки истории чата."
            )
        return JSONResponse(status_code=207, content=progress)

    for step in [progress["text"], *progress["files"]]:
        if step["state"] == "pending":
            step["state"] = "confirmed"

    def persist_step(kind: str, value: str) -> None:
        _persist_confirmed_chat_step(
            db,
            mp="wb",
            chat_id=chat_id,
            username=current_user["username"],
            kind=kind,
            value=value,
        )

    if txt and not await _persist_or_stop_progress(
        progress, progress["text"], persist_step, "text", txt
    ):
        return JSONResponse(status_code=207, content=progress)
    for index, attachment in enumerate(attachments):
        if not await _persist_or_stop_progress(
            progress,
            progress["files"][index],
            persist_step,
            "file",
            attachment["name"],
        ):
            return JSONResponse(status_code=207, content=progress)

    progress["status"] = "complete"
    return JSONResponse(status_code=200, content=progress)


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
    visibility: str = Query("public", description="public | private для вопроса WB"),
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
    if visibility not in ("public", "private"):
        raise HTTPException(status_code=400, detail="visibility: public или private")

    # Отправляем через соответствующий API
    try:
        if mp == "wb" and itype == "review":
            wb_client.answer_feedback(item_id, txt, timeout=20)
            with db.bind.connect() as conn:
                conn.execute(text("UPDATE wb_feedbacks SET state='answered',answer_text=:a WHERE id=:i"),
                             {"a": txt, "i": item_id})
                conn.commit()

        elif mp == "wb" and itype == "question":
            wb_client.answer_question(item_id, txt, public=visibility == "public", timeout=20)
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
