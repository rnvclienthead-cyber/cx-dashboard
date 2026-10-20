"""
Раздел «Ответы КС» — очередь обращений и отправка ответов из системы.

Доступ: admin или пользователь с правом cx_responses в role_permissions.
Фаза 1: WB отзывы, вопросы, чаты.
Фаза 2: ЯМ отзывы, вопросы. ИИ-черновики (Qwen Plus).
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
from fastapi.responses import JSONResponse, Response, RedirectResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from sqlalchemy import text, bindparam
from pydantic import BaseModel

logger = logging.getLogger("cx_responses")

_IMG_MD_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_IMG_MD_URL_RE = re.compile(r"!\[[^\]]*\]\((https?://[^)]+)\)")

_PHOTO_SYNC_INTERVAL = 86400   # раз в 24 часа
_REVIEW_AUTO_INTERVAL = 300    # автоответы на отзывы раз в 5 минут
REVIEW_AUTO_MPS = ("wb", "ym", "ozon")


def parse_star_ratings(*raw_values: str) -> list[int]:
    """Оценки из фильтра: '5' или '5,4,1'. Пусто — без фильтра."""
    stars = []
    seen = set()
    for raw in raw_values:
        for part in str(raw or "").replace(";", ",").split(","):
            p = part.strip()
            if p in ("1", "2", "3", "4", "5") and p not in seen:
                seen.add(p)
                stars.append(int(p))
    return stars


def resolve_review_product_photo(conn, mp, sku, nm, db=None):
    """Для WB фото с карточки nm_id. Ozon-фото по артикулу не подставляем — это другой товар."""
    mp_key = (mp or "").lower()
    nm_key = str(nm or "").strip()
    sku_key = (sku or "").strip()
    if mp_key == "wb" and nm_key:
        url = conn.execute(text(
            "SELECT photo_url FROM wb_product_photos WHERE nm_id::text = :nm LIMIT 1"
        ), {"nm": nm_key}).scalar()
        if url:
            return url
        if db is not None:
            try:
                return _fetch_and_cache_nm_photo(db, int(nm_key))
            except (TypeError, ValueError, Exception):
                return None
        return None
    if not sku_key:
        return None
    return conn.execute(text(
        "SELECT photo_url FROM product_photos WHERE supplier_article = :a LIMIT 1"
    ), {"a": sku_key}).scalar()


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
        if len(cards) < 100:
            break
        cursor = {"updatedAt": cur.get("updatedAt", ""), "nmID": cur.get("nmID", 0)}
        time.sleep(0.3)

    logger.info("[PhotoSync] сохранено %d фото товаров", saved)
    try:
        backfilled = _backfill_missing_wb_chat_photos(get_db_func)
        if backfilled:
            logger.info("[PhotoSync] догружено с CDN %d фото из чатов", backfilled)
    except Exception as e:
        logger.warning("[PhotoSync] догрузка чатов: %s", e)
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


async def start_review_auto_worker(get_db_func):
    """Фоновый воркер автоответов на отзывы. Каждая МП включается отдельно в настройках."""
    logger.info("[ReviewAuto] worker started, interval=%ss", _REVIEW_AUTO_INTERVAL)
    await asyncio.sleep(90)
    while True:
        try:
            result = await asyncio.to_thread(_run_auto_replies_isolated)
            if result.get("sent") or result.get("queued"):
                logger.info("[ReviewAuto] %s", result)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[ReviewAuto] worker error: %s", e)
        await asyncio.sleep(_REVIEW_AUTO_INTERVAL)


def _run_auto_replies_isolated() -> dict:
    from ..database import db_session
    with db_session() as db:
        return _run_auto_replies(db)

from ..database import get_db
from .. import chat_fulfillment, chat_order
from .auth import get_current_user
from .. import wb_client, ym_client, ozon_client
from ..review_policy import (
    MODE_APPROVE_TEMPLATE,
    MODE_AUTO_SORRY,
    MODE_AUTO_THANKS,
    PAINT_HOLES_REPLY,
    REVIEW_AI_SYSTEM,
    REVIEW_AUTO_SCENARIOS,
    apply_article_hints,
    article_facts_for_ai,
    classify_review_reply,
    needs_lived_reply,
    no_text_answer,
    no_text_scenario,
    should_auto_send,
    should_ground_constructor,
)


def _require_admin(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Только администратор может менять автоответы и шаблоны")
    return current_user


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


def _ym_chat_search_needle(*sources) -> str:
    """Номер возврата или заказа — кабинет ЯМ ищет по нему в списке чатов."""
    for src in sources:
        if not isinstance(src, dict):
            continue
        for key in ("return_id", "claim_id", "return_number", "order_id", "order_number"):
            val = str(src.get(key) or "").strip()
            if val:
                return val
    return ""


def _ym_cabinet_chat_url(
    cabinet_chat_id: str | None = None,
    context_type: str | None = None,
    search: str | None = None,
) -> str:
    """Deep-link в кабинет ЯМ.

    В URL нужен внутренний chatId вида ``0/43/<uuid>`` (не числовой Partner API id).
    Слеши в chatId нельзя кодировать как %2F — кабинет тогда не открывает чат.
    Если uuid ещё не известен — передаём номер возврата/заказа в search,
    чтобы список сразу отфильтровался, а не показал все чаты.
    """
    from urllib.parse import quote
    biz = (_ym_business_id() or "").strip()
    camp = (_ym_campaign_id() or "").strip()
    if not biz:
        return "https://partner.market.yandex.ru/"
    group = str(context_type or "DIRECT").strip().upper()
    if group not in ("DIRECT", "RETURN", "ORDER"):
        group = "DIRECT"
    parts: list[str] = [f"chatGroup={group}"]
    if camp:
        parts.append(f"campaignId={camp}")
    cab = (cabinet_chat_id or "").strip()
    # Только формат кабинета (0/43/uuid). Числовой API-id ломает страницу.
    if cab and "/" in cab:
        parts.append(f"chatId={cab}")
    else:
        needle = str(search or "").strip()
        if needle:
            parts.append(f"search={quote(needle, safe='')}")
    return f"https://partner.market.yandex.ru/business/{biz}/arbiter?{'&'.join(parts)}"


def _ozon_cabinet_chat_url(chat_id: str | None = None) -> str:
    """Deep-link в мессенджер Ozon на конкретный чат (uuid из Seller API)."""
    cid = (chat_id or "").strip()
    if cid:
        return f"https://seller.ozon.ru/app/messenger/?group=customers_v2&id={cid}"
    return "https://seller.ozon.ru/app/messenger/?group=customers_v2"


def _wb_cabinet_chat_url(chat_id: str | None = None) -> str:
    """Чат с покупателем в ЛК WB. uuid из chat_id вида `1:uuid`."""
    raw = (chat_id or "").strip()
    uuid = raw.split(":", 1)[-1] if raw else ""
    if uuid and "-" in uuid:
        return f"https://seller.wildberries.ru/chat-with-clients?chatId={uuid}"
    return "https://seller.wildberries.ru/chat-with-clients"


def readable_status_ex(raw) -> str:
    """status_ex WB — комментарий площадки. Чистый код вроде «0» людям не нужен."""
    s = str(raw or "").strip()
    if not s or s.isdigit():
        return ""
    return s[:500]


_RESHIP_FORM_RE = re.compile(r"box\.vidovito\.com/reshipment/form", re.I)
_RESHIP_OFFER_RE = re.compile(
    r"выслать\s+детал|доотправ|заполните\s+форму|отправк\w+\s+детал|замен\w+\s+детал",
    re.I,
)
_RESHIP_DENY_RE = re.compile(
    r"не можем выслать|выслать не можем|оформите возврат|деталью?\s+выслать\s+не",
    re.I,
)
_RESHIP_GREET_RE = re.compile(
    r"здесь вы можете задать вопросы по возврату",
    re.I,
)
_RESHIP_RESOLUTION_DETAIL = {
    "new": "анкета заполнена — ждёт согласования",
    "pending": "на проверке",
    "matched": "сопоставлена с заказом",
    "approved": "доотправка согласована",
    "packed": "собрана к отправке",
    "rejected": "заявка на доотправку отклонена",
    "shipped": "деталь уже отправлена",
    "in_transit": "деталь в пути",
    "ready_for_pickup": "готова к выдаче",
    "delivered": "клиент уже получил",
    "returned": "вернулась обратно",
}


def resolution_from_signals(
    *,
    request: dict | None = None,
    token_sent: bool = False,
    seller_texts: list[str] | None = None,
) -> dict | None:
    """Как сейчас закрываем вопрос: ссылка на доотправку или только предложение."""
    if request:
        st = str(request.get("status") or "").strip().lower()
        detail = _RESHIP_RESOLUTION_DETAIL.get(st) or "ссылка на доотправку уже у клиента"
        return {
            "kind": "reshipment_link",
            "label": "Ссылка на доотправку отправлена",
            "detail": detail,
            "request_id": request.get("id"),
        }
    blob = "\n".join(seller_texts or [])
    if token_sent or _RESHIP_FORM_RE.search(blob):
        return {
            "kind": "reshipment_link",
            "label": "Ссылка на доотправку отправлена",
            "detail": "ждём, пока клиент заполнит анкету",
        }
    if _RESHIP_OFFER_RE.search(blob) and not _RESHIP_DENY_RE.search(blob):
        return {
            "kind": "reshipment_offered",
            "label": "Предложили доотправку",
            "detail": "ссылку на анкету ещё не отправляли",
        }
    if _RESHIP_DENY_RE.search(blob):
        return None
    meaningful = [
        str(t or "").strip()
        for t in (seller_texts or [])
        if str(t or "").strip() and not _RESHIP_GREET_RE.search(str(t or ""))
    ]
    if meaningful:
        return {
            "kind": "open_question",
            "label": "Вопрос без решения",
            "detail": "писали клиенту, доотправки ещё нет",
        }
    return None


def _claim_chat_cabinet_url(mp: str, chat: dict | None, item: dict | None = None) -> str:
    m = (mp or "").lower()
    chat = chat or {}
    cid = str(chat.get("chat_id") or "")
    if m == "wb":
        return _wb_cabinet_chat_url(cid or None)
    if m == "ym":
        return _ym_cabinet_chat_url(
            chat.get("cabinet_chat_id"),
            chat.get("context_type") or "RETURN",
            search=_ym_chat_search_needle(chat, item),
        )
    if m == "ozon":
        return _ozon_cabinet_chat_url(cid or None)
    return ""


def _seller_texts_for_chat(conn, mp: str, chat_id: str) -> list[str]:
    cid = str(chat_id or "").strip()
    if not cid:
        return []
    texts: list[str] = []
    try:
        if mp == "wb":
            rows = conn.execute(text("""
                SELECT text FROM wb_chat_messages
                WHERE chat_id = :cid AND sender = 'seller'
                ORDER BY add_ts DESC NULLS LAST LIMIT 40
            """), {"cid": cid}).fetchall()
        elif mp == "ym":
            rows = conn.execute(text("""
                SELECT text FROM ym_chat_messages
                WHERE chat_id = :cid
                  AND lower(COALESCE(sender_type, '')) IN ('seller', 'partner')
                ORDER BY created_at DESC NULLS LAST LIMIT 40
            """), {"cid": cid}).fetchall()
        else:
            rows = conn.execute(text("""
                SELECT text FROM ozon_chat_messages
                WHERE chat_id = :cid
                  AND lower(COALESCE(sender_type, '')) IN ('seller')
                ORDER BY created_at DESC NULLS LAST LIMIT 40
            """), {"cid": cid}).fetchall()
        texts.extend(str(r[0] or "") for r in rows if r and r[0])
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    try:
        logs = conn.execute(text("""
            SELECT answer_text FROM cx_response_log
            WHERE mp = :mp AND item_type = 'chat' AND item_id = :cid
            ORDER BY sent_at DESC NULLS LAST LIMIT 20
        """), {"mp": mp, "cid": cid}).fetchall()
        texts.extend(str(r[0] or "") for r in logs if r and r[0])
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    return texts


def _chat_id_variants(chat_id: str) -> tuple[str, str, str]:
    cid = str(chat_id or "").strip()
    if not cid:
        return "", "", ""
    bare = cid.split(":", 1)[-1] if ":" in cid else cid
    prefixed = cid if ":" in cid else f"1:{cid}"
    return cid, bare, prefixed


def _lookup_reshipment_row(
    conn,
    *,
    mp: str = "",
    chat_id: str = "",
    srid: str = "",
    order_n: str = "",
) -> tuple[dict | None, bool]:
    """Сырая заявка доотправки по чату, заказу или середине rid WB."""
    cid, cid_bare, cid_pref = _chat_id_variants(chat_id)
    srid = str(srid or "").strip()
    order_n = str(order_n or "").strip()
    core = _wb_srid_core(srid)
    req = None
    if cid or srid or order_n:
        try:
            req = conn.execute(text("""
                SELECT id, status, chat_token, matched_srid, wb_chat_id, ym_chat_id, ozon_chat_id,
                       cdek_number, cdek_uuid, track_number, delivery_method,
                       client_pvz_address, yandex_pvz_address, ozon_pvz_address,
                       ozon_order_number, ozon_posting_number, eta_date,
                       customer_name, created_at, updated_at, marketplace,
                       order_number
                FROM reshipment_requests
                WHERE (
                        :cid <> '' AND (
                          COALESCE(wb_chat_id, '') IN (:cid, :cid_bare, :cid_pref)
                          OR COALESCE(ym_chat_id, '') IN (:cid, :cid_bare, :cid_pref)
                          OR COALESCE(ozon_chat_id, '') IN (:cid, :cid_bare, :cid_pref)
                          OR wb_chat_id LIKE :cid_suf
                        )
                      )
                   OR (
                        :srid <> '' AND lower(trim(COALESCE(matched_srid, ''))) = lower(:srid)
                      )
                   OR (
                        :core <> '' AND COALESCE(matched_srid, '') ILIKE '%' || :core || '%'
                      )
                   OR (
                        :ord <> '' AND COALESCE(order_number, '') = :ord
                      )
                ORDER BY
                  CASE
                    WHEN COALESCE(wb_chat_id, '') IN (:cid, :cid_bare, :cid_pref)
                      OR COALESCE(ym_chat_id, '') IN (:cid, :cid_bare, :cid_pref)
                      OR COALESCE(ozon_chat_id, '') IN (:cid, :cid_bare, :cid_pref)
                      THEN 0
                    WHEN :srid <> '' AND lower(trim(COALESCE(matched_srid, ''))) = lower(:srid)
                      THEN 1
                    ELSE 2
                  END,
                  created_at DESC NULLS LAST
                LIMIT 1
            """), {
                "cid": cid,
                "cid_bare": cid_bare or cid,
                "cid_pref": cid_pref or cid,
                "cid_suf": f"%:{cid_bare}" if cid_bare else "",
                "srid": srid,
                "core": core,
                "ord": order_n,
            }).mappings().first()
            if req:
                req = dict(req)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            req = None
    token_sent = False
    if cid:
        try:
            token_sent = bool(conn.execute(text("""
                SELECT 1 FROM reshipment_chat_tokens
                WHERE chat_id IN (:cid, :cid_bare, :cid_pref)
                  AND used_at IS NOT NULL
                LIMIT 1
            """), {
                "cid": cid,
                "cid_bare": cid_bare or cid,
                "cid_pref": cid_pref or cid,
            }).scalar())
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    return req, token_sent


def _find_reshipment_for_claim(conn, mp: str, item: dict, chat_id: str) -> tuple[dict | None, bool]:
    """Заявка доотправки и/или выданная ссылка по заказу или чату."""
    chat = item.get("chat") if isinstance(item.get("chat"), dict) else {}
    srid = str(
        item.get("srid")
        or chat.get("order_rid")
        or item.get("order_id")
        or ""
    ).strip()
    order_n = str(
        item.get("order_id")
        or item.get("order_number")
        or item.get("posting_number")
        or ""
    ).strip()
    return _lookup_reshipment_row(
        conn, mp=mp, chat_id=chat_id, srid=srid, order_n=order_n,
    )


def _attach_claim_chat_extras(db, mp: str, item: dict) -> dict:
    """Ссылка в ЛК чата + карточка доотправки, если она есть."""
    chat = item.get("chat") if isinstance(item.get("chat"), dict) else None
    item["chat_cabinet_url"] = _claim_chat_cabinet_url(mp, chat, item)
    item["status_ex"] = readable_status_ex(item.get("status_ex"))
    chat_id = str((chat or {}).get("chat_id") or "")
    try:
        with db.bind.connect() as conn:
            req, token_sent = _find_reshipment_for_claim(conn, mp, item, chat_id)
            texts = _seller_texts_for_chat(conn, mp, chat_id) if chat_id else []
    except Exception:
        req, token_sent, texts = None, False, []
    item["reshipment"] = None
    if req:
        try:
            item["reshipment"] = _reshipment_chat_card(req)
        except Exception:
            item["reshipment"] = {
                "id": req.get("id"),
                "status": req.get("status") or "",
                "status_label": str(req.get("status") or "—"),
            }
    item["resolution"] = resolution_from_signals(
        request=req, token_sent=token_sent, seller_texts=texts,
    )
    return item


def _find_claim_chat(conn, mp: str, item: dict) -> dict | None:
    """Чат по возврату — для статуса доотправки в списке, без live API."""
    m = (mp or "").lower()
    if m == "wb":
        return _find_wb_chat_for_claim(conn, item.get("srid") or "")
    if m == "ym":
        oid = str(item.get("order_id") or item.get("srid") or "")
        rid = str(item.get("claim_id") or "")
        if not oid and not rid:
            return None
        row = conn.execute(text("""
            SELECT chat_id, customer_name AS client_name, needs_answer,
                   updated_at AS last_ts
            FROM ym_chats
            WHERE (:rid <> '' AND return_id::text = :rid)
               OR (:oid <> '' AND order_id::text = :oid)
            ORDER BY updated_at DESC NULLS LAST
            LIMIT 1
        """), {"rid": rid, "oid": oid}).mappings().first()
        return dict(row) if row else None
    if m == "ozon":
        posting = str(item.get("posting_number") or "")
        order_n = str(item.get("order_number") or item.get("order_id") or "")
        if not posting and not order_n:
            return None
        row = conn.execute(text("""
            SELECT chat_id, needs_answer, updated_at AS last_ts
            FROM ozon_chats
            WHERE (:p <> '' AND order_number = :p)
               OR (:o <> '' AND order_number = :o)
               OR (:p <> '' AND order_number = split_part(:p, '-', 1) || '-' || split_part(:p, '-', 2))
               OR (:o <> '' AND order_number LIKE :o || '-%')
            ORDER BY updated_at DESC NULLS LAST
            LIMIT 1
        """), {"p": posting, "o": order_n}).mappings().first()
        return dict(row) if row else None
    return None


def _attach_list_offer_status(db: Session, mp: str, items: list[dict]) -> None:
    """На список карточек — предложили / ссылка / вопрос, без открытия заявки."""
    if not items:
        return
    try:
        with db.bind.connect() as conn:
            for it in items:
                chat = None
                try:
                    chat = _find_claim_chat(conn, mp, it)
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    chat = None
                chat_id = str((chat or {}).get("chat_id") or "")
                try:
                    req, token_sent = _find_reshipment_for_claim(conn, mp, it, chat_id)
                    texts = _seller_texts_for_chat(conn, mp, chat_id) if chat_id else []
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    req, token_sent, texts = None, False, []
                if req:
                    try:
                        it["reshipment"] = _reshipment_chat_card(req)
                    except Exception:
                        it["reshipment"] = {
                            "id": req.get("id"),
                            "status": req.get("status") or "",
                            "status_label": str(req.get("status") or "—"),
                        }
                else:
                    it["reshipment"] = None
                it["resolution"] = resolution_from_signals(
                    request=req, token_sent=token_sent, seller_texts=texts,
                )
    except Exception:
        logger.exception("[claims] list offer status failed mp=%s", mp)


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
    """Вложения ЯМ (partner API и files.messenger.yandex.net) — через наш прокси."""
    from urllib.parse import quote, urlparse
    u = (file_url or "").strip()
    if not u:
        return ""
    if u.startswith("/api/v1/cx-responses/chat-download/"):
        return u
    host = (urlparse(u).hostname or "").lower()
    if "yandex." in host or host.endswith("yandex.net") or "yandex" in host:
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
        q.supplier_article, q.nm_id::text,
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

    SELECT
        q.question_id, 'ozon', 'question',
        q.supplier_article, q.sku::text,
        q.text, NULL::int, NULL::text, NULL::text,
        NULL::text, NULL::text, q.author_name,
        q.published_at,
        EXTRACT(epoch FROM now()-q.published_at)::int,
        NULL::bigint,
        NULL::boolean,
        NULL::text
    FROM ozon_questions q
    WHERE q.status IN ('NEW', 'VIEWED', 'UNPROCESSED')
      AND q.question_id NOT IN
          (SELECT item_id FROM cx_dismissed_items WHERE mp='ozon' AND item_type='question')

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
      AND NOT (
            to_timestamp(COALESCE(c.last_ts, 0) / 1000.0) < now() - INTERVAL '3 days'
            AND lower(COALESCE(c.last_sender, '')) = 'seller'
            AND EXISTS (
              SELECT 1 FROM wb_chat_messages s
              WHERE s.chat_id = c.chat_id AND s.sender = 'seller'
            )
          )
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
        r.photos, NULL::text, NULL::text,
        r.created_at,
        EXTRACT(epoch FROM now()-r.created_at)::int,
        NULL::bigint,
        NULL::boolean,
        NULL::text
    FROM ozon_reviews r
    WHERE r.answered_at IS NULL
      AND r.review_id NOT IN
          (SELECT item_id FROM cx_dismissed_items WHERE mp='ozon' AND item_type='review')
      AND NOT EXISTS (
          SELECT 1 FROM ozon_review_outbox o
          WHERE o.review_id = r.review_id AND o.status = 'pending'
      )

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
      -- мы уже писали и тишина >3 дн — в архив, не держим в очереди
      AND NOT (
            COALESCE(
              (SELECT MAX(m.created_at) FROM ozon_chat_messages m WHERE m.chat_id = c.chat_id),
              c.waiting_since, c.created_at, c.updated_at, now()
            ) < now() - INTERVAL '3 days'
            AND lower(COALESCE(c.last_sender, '')) IN ('seller', 'courier')
            AND EXISTS (
              SELECT 1 FROM ozon_chat_messages s
              WHERE s.chat_id = c.chat_id
                AND lower(COALESCE(s.sender_type, '')) IN ('seller', 'courier')
            )
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
      AND NOT (
            COALESCE(
              (SELECT MAX(m.created_at) FROM ym_chat_messages m WHERE m.chat_id = c.chat_id),
              c.waiting_since, c.updated_at, c.created_at, now()
            ) < now() - INTERVAL '3 days'
            AND lower(COALESCE(c.last_sender, '')) IN ('seller', 'partner', 'shop', 'merchant')
            AND EXISTS (
              SELECT 1 FROM ym_chat_messages s
              WHERE s.chat_id = c.chat_id
                AND lower(COALESCE(s.sender_type, '')) IN ('seller', 'partner', 'shop', 'merchant')
            )
          )
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

# Только чаты — для быстрого раздела «Чаты» (без отзывов/вопросов/претензий).
_CHATS_QUEUE_VIEW_SQL = """
    SELECT
        c.chat_id                                          AS id,
        'wb'::text                                         AS mp,
        'chat'::text                                       AS item_type,
        c.good_name                                        AS sku,
        c.nm_id::text                                      AS nm_id,
        c.last_message                                     AS body,
        NULL::int                                         AS rating,
        NULL::text                                        AS pros,
        NULL::text                                        AS cons,
        NULL::text                                        AS photos_raw,
        NULL::text                                        AS order_number,
        c.client_name                                      AS contact,
        c.created_at                                       AS created_at,
        EXTRACT(epoch FROM now()-c.created_at)::int        AS age_sec,
        c.last_ts                                          AS last_ts,
        c.needs_answer                                     AS needs_answer,
        c.last_sender                                      AS last_sender,
        COALESCE(c.pre_purchase, false)                    AS pre_purchase,
        0                                                  AS unread_count
    FROM wb_chats c
    WHERE NOT COALESCE(c.is_closing,false)
      AND c.last_ts > EXTRACT(epoch FROM now()-INTERVAL '30 days')*1000
      AND NOT (
            to_timestamp(COALESCE(c.last_ts, 0) / 1000.0) < now() - INTERVAL '3 days'
            AND lower(COALESCE(c.last_sender, '')) = 'seller'
            AND EXISTS (
              SELECT 1 FROM wb_chat_messages s
              WHERE s.chat_id = c.chat_id AND s.sender = 'seller'
            )
          )
      AND c.chat_id NOT IN
          (SELECT item_id FROM cx_dismissed_items WHERE mp='wb' AND item_type='chat')

    UNION ALL

    SELECT
        c.chat_id, 'ozon', 'chat',
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
        EXTRACT(epoch FROM COALESCE(
            (SELECT MAX(m.created_at) FROM ozon_chat_messages m WHERE m.chat_id = c.chat_id),
            c.waiting_since, c.created_at, c.updated_at, now()
        ))::bigint * 1000,
        c.needs_answer,
        c.last_sender,
        false,
        COALESCE(c.unread_count, 0)
    FROM ozon_chats c
    WHERE c.chat_status ILIKE 'OPENED'
      AND (c.chat_type IS NULL OR upper(c.chat_type) LIKE '%BUYER%')
      AND COALESCE(
            (SELECT MAX(m.created_at) FROM ozon_chat_messages m WHERE m.chat_id = c.chat_id),
            c.waiting_since, c.created_at, c.updated_at, now()
          ) > now() - INTERVAL '30 days'
      AND NOT (
            COALESCE(
              (SELECT MAX(m.created_at) FROM ozon_chat_messages m WHERE m.chat_id = c.chat_id),
              c.waiting_since, c.created_at, c.updated_at, now()
            ) < now() - INTERVAL '3 days'
            AND lower(COALESCE(c.last_sender, '')) IN ('seller', 'courier')
            AND EXISTS (
              SELECT 1 FROM ozon_chat_messages s
              WHERE s.chat_id = c.chat_id
                AND lower(COALESCE(s.sender_type, '')) IN ('seller', 'courier')
            )
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
        c.last_sender,
        false,
        0
    FROM ym_chats c
    WHERE COALESCE(
            (SELECT MAX(m.created_at) FROM ym_chat_messages m WHERE m.chat_id = c.chat_id),
            c.waiting_since, c.updated_at, c.created_at, now()
          ) > now() - INTERVAL '30 days'
      AND NOT (
            COALESCE(
              (SELECT MAX(m.created_at) FROM ym_chat_messages m WHERE m.chat_id = c.chat_id),
              c.waiting_since, c.updated_at, c.created_at, now()
            ) < now() - INTERVAL '3 days'
            AND lower(COALESCE(c.last_sender, '')) IN ('seller', 'partner', 'shop', 'merchant')
            AND EXISTS (
              SELECT 1 FROM ym_chat_messages s
              WHERE s.chat_id = c.chat_id
                AND lower(COALESCE(s.sender_type, '')) IN ('seller', 'partner', 'shop', 'merchant')
            )
          )
      AND c.chat_id NOT IN
          (SELECT item_id FROM cx_dismissed_items WHERE mp='ym' AND item_type='chat')
"""

CHATS_QUEUE_VIEW = "cx_chats_queue"


def _auto_setting_on(val) -> bool:
    return str(val or "").strip().lower() in ("1", "true", "yes", "on")


def _auto_setting_row(row):
    if isinstance(row, dict):
        return row.get("key"), row.get("value")
    return (row[0] if row else None), (row[1] if row and len(row) > 1 else None)


def parse_review_auto_flags(rows) -> dict:
    """rows: iterable of {key, value} или (key, value). По умолчанию все МП выкл."""
    flags = {mp: False for mp in REVIEW_AUTO_MPS}
    for row in rows or []:
        key, val = _auto_setting_row(row)
        mp = str(key or "").removeprefix("auto_")
        if mp in flags:
            flags[mp] = _auto_setting_on(val)
    return flags


def parse_review_auto_scenarios(rows) -> dict:
    """Сценарии по умолчанию вкл: как раньше, площадка вкл = thanks + sorry."""
    flags = {key: True for key in REVIEW_AUTO_SCENARIOS}
    for row in rows or []:
        key, val = _auto_setting_row(row)
        raw = str(key or "")
        if not raw.startswith("auto_sc_"):
            continue
        sc = raw.removeprefix("auto_sc_")
        if sc in flags:
            flags[sc] = _auto_setting_on(val)
    return flags


def _review_auto_setting_rows(conn):
    try:
        return conn.execute(text(
            "SELECT key, value FROM cx_review_settings WHERE key LIKE 'auto_%'"
        )).mappings().all()
    except Exception:
        return None


def _load_review_auto_config(conn) -> tuple[dict, dict]:
    rows = _review_auto_setting_rows(conn)
    if rows is None:
        return (
            {mp: False for mp in REVIEW_AUTO_MPS},
            {key: True for key in REVIEW_AUTO_SCENARIOS},
        )
    return parse_review_auto_flags(rows), parse_review_auto_scenarios(rows)


def _load_review_auto_flags(conn) -> dict:
    return _load_review_auto_config(conn)[0]


def _review_full_text(item: dict) -> str:
    parts = []
    for key in ("body", "pros", "cons"):
        val = (item.get(key) or "").strip()
        if val and val != "—":
            parts.append(val)
    return ". ".join(parts)


def _iso_or_none(v):
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _attach_review_updates(db, items: list) -> None:
    """Пометка: клиент изменил отзыв; для WB ещё ссылка на доотправку."""
    from ..review_updates import update_label

    reviews = [i for i in items if i.get("item_type") == "review" and i.get("id")]
    if not reviews:
        return
    try:
        _attach_review_updates_inner(db, reviews, update_label)
    except Exception:
        logger.exception("[Reviews] attach updates failed")


def _attach_review_updates_inner(db, reviews, update_label) -> None:
    by_mp = {"wb": [], "ym": [], "ozon": []}
    for item in reviews:
        mp = item.get("mp")
        if mp in by_mp:
            by_mp[mp].append(str(item["id"]))
    found = {}
    with db.bind.connect() as conn:
        if by_mp["wb"]:
            rows = conn.execute(text("""
                SELECT id::text AS id, prev_text, prev_valuation, buyer_updated_at, update_kind
                FROM wb_feedbacks WHERE id::text IN :ids
            """).bindparams(bindparam("ids", expanding=True)), {"ids": by_mp["wb"]}).mappings().all()
            for row in rows:
                found[("wb", row["id"])] = dict(row)
            ship = conn.execute(text("""
                SELECT DISTINCT ON (f.id)
                    f.id::text AS id, r.id AS reshipment_id, r.status AS reshipment_status
                FROM wb_feedbacks f
                JOIN wb_orders o ON o.shk_id = f.last_order_shk_id
                JOIN reshipment_requests r ON r.matched_srid = o.srid
                WHERE f.id::text IN :ids
                ORDER BY f.id, r.id DESC
            """).bindparams(bindparam("ids", expanding=True)), {"ids": by_mp["wb"]}).mappings().all()
            for row in ship:
                slot = found.setdefault(("wb", row["id"]), {})
                slot["reshipment_id"] = row["reshipment_id"]
                slot["reshipment_status"] = row["reshipment_status"]
        if by_mp["ym"]:
            rows = conn.execute(text("""
                SELECT feedback_id::text AS id, prev_text, prev_valuation, buyer_updated_at, update_kind
                FROM ym_feedbacks WHERE feedback_id::text IN :ids
            """).bindparams(bindparam("ids", expanding=True)), {"ids": by_mp["ym"]}).mappings().all()
            for row in rows:
                found[("ym", row["id"])] = dict(row)
        if by_mp["ozon"]:
            rows = conn.execute(text("""
                SELECT review_id AS id, prev_text, prev_valuation, buyer_updated_at, update_kind
                FROM ozon_reviews WHERE review_id IN :ids
            """).bindparams(bindparam("ids", expanding=True)), {"ids": by_mp["ozon"]}).mappings().all()
            for row in rows:
                found[("ozon", str(row["id"]))] = dict(row)
    for item in reviews:
        extra = found.get((item.get("mp"), str(item["id"]))) or {}
        kind = extra.get("update_kind")
        item["update_kind"] = kind or ""
        item["update_label"] = update_label(kind)
        item["prev_text"] = extra.get("prev_text") or ""
        item["prev_valuation"] = extra.get("prev_valuation")
        item["buyer_updated_at"] = _iso_or_none(extra.get("buyer_updated_at"))
        item["reshipment_id"] = extra.get("reshipment_id")
        item["reshipment_status"] = extra.get("reshipment_status") or ""


def _attach_review_reply_policy(db, items: list) -> None:
    """Режим ответа и черновик по правилам: авто / конструктор / только ИИ."""
    from . import kb
    from ..review_policy import detect_review_topic
    for item in items:
        if item.get("item_type") != "review":
            continue
        full = _review_full_text(item)
        rating = item.get("rating") or 0
        item["pending_topic"] = detect_review_topic(full, rating) or ""
        mode = classify_review_reply(
            item.get("rating"),
            item.get("body") or "",
            item.get("photos"),
            item.get("pros") or "",
            item.get("cons") or "",
        )
        item["reply_mode"] = mode
        if item.get("pending_draft"):
            continue
        if mode in (MODE_AUTO_THANKS, MODE_AUTO_SORRY):
            item["pending_draft"] = kb.ensure_review_signature(no_text_answer(item.get("rating")))
            item["pending_template"] = no_text_scenario(item.get("rating"))
            continue
        if mode != MODE_APPROVE_TEMPLATE:
            continue
        if needs_lived_reply(full, rating):
            item["manual_check"] = True
            item["pending_template"] = "Подробный отзыв"
            continue
        try:
            ans, scen, manual, _meta = kb.build_answer(
                db, item.get("sku") or "", full, rating,
            )
        except Exception:
            continue
        if ans:
            item["pending_draft"] = ans
            item["pending_template"] = scen or ""
            if (_meta or {}).get("topic"):
                item["pending_topic"] = _meta["topic"]
            if manual or item.get("photos"):
                item["manual_check"] = True


PACKAGING_REVIEW_TEMPLATE_TITLE = "Упаковка"
PACKAGING_REVIEW_TEMPLATE_BODY = (
    "Здравствуйте. Очень жаль, что товар приехал в ненадлежащей упаковке. "
    "Мы учтём эту ситуацию и подумаем, как лучше сохранять товар в целости по пути к покупателю. "
    "Напишите, пожалуйста, в «Чат с продавцом» — разберёмся и подскажем, как лучше поступить. "
    "Приносим извинения."
)
PACKAGING_KB_SOLUTION = (
    "Напишите, пожалуйста, в «Чат с продавцом» — разберёмся и подскажем, как лучше поступить."
)


def ensure_packaging_review_templates(engine) -> None:
    """Шаблон и конструктор: жалоба на упаковку → призыв в чат с продавцом."""
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO cx_response_templates
                (title, body, mp, item_type, tone, auto_reply,
                 min_rating, max_rating, require_no_text, created_by)
            SELECT :title, :body, 'all', 'review', 'neutral', false,
                   1, 3, false, 'system'
            WHERE NOT EXISTS (
                SELECT 1 FROM cx_response_templates
                WHERE title = :title AND item_type IN ('review', 'all')
            )
        """), {
            "title": PACKAGING_REVIEW_TEMPLATE_TITLE,
            "body": PACKAGING_REVIEW_TEMPLATE_BODY,
        })
        conn.execute(text("""
            UPDATE cx_response_templates
            SET body = :body, min_rating = 1, max_rating = 3, updated_at = now()
            WHERE title = :title
              AND item_type IN ('review', 'all')
              AND COALESCE(created_by, '') = 'system'
        """), {
            "title": PACKAGING_REVIEW_TEMPLATE_TITLE,
            "body": PACKAGING_REVIEW_TEMPLATE_BODY,
        })
        conn.execute(text("""
            UPDATE cx_kb_rules
            SET solution = :sol
            WHERE problem ILIKE '%упаков%'
              AND (solution IS DISTINCT FROM :sol)
        """), {"sol": PACKAGING_KB_SOLUTION})
        conn.execute(text("""
            UPDATE cx_kb_rules
            SET triggers = trim(both E'\\n' from coalesce(triggers, '')
                || E'\\nупаковк\\nкоробк\\nпорван\\nпомят\\nсмят')
            WHERE problem ILIKE '%упаков%'
              AND coalesce(triggers, '') NOT ILIKE '%упаков%'
        """))
        conn.commit()


PAINT_HOLES_REVIEW_TEMPLATE_TITLE = "Технологические отверстия"
PAINT_HOLES_REVIEW_TEMPLATE_BODY = f"Здравствуйте. {PAINT_HOLES_REPLY}"
PAINT_HOLES_KB_TRIGGERS = (
    "для чего отверст\nзачем отверст\nзачем дырк\nтехнологическ отверст\n"
    "дырочк в поднос\nотверстия на полк\nдыры на полк\nдыры сбоку"
)


def ensure_paint_holes_review_templates(engine) -> None:
    """Шаблон и конструктор: дырки на полке/подносе — линия покраски, не брак."""
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO cx_response_templates
                (title, body, mp, item_type, tone, auto_reply,
                 min_rating, max_rating, require_no_text, created_by)
            SELECT :title, :body, 'all', 'review', 'neutral', false,
                   1, 5, false, 'system'
            WHERE NOT EXISTS (
                SELECT 1 FROM cx_response_templates
                WHERE title = :title AND item_type IN ('review', 'all')
            )
        """), {
            "title": PAINT_HOLES_REVIEW_TEMPLATE_TITLE,
            "body": PAINT_HOLES_REVIEW_TEMPLATE_BODY,
        })
        conn.execute(text("""
            UPDATE cx_response_templates
            SET body = :body, min_rating = 1, max_rating = 5, updated_at = now()
            WHERE title = :title
              AND item_type IN ('review', 'all')
              AND COALESCE(created_by, '') = 'system'
        """), {
            "title": PAINT_HOLES_REVIEW_TEMPLATE_TITLE,
            "body": PAINT_HOLES_REVIEW_TEMPLATE_BODY,
        })
        conn.execute(text("""
            INSERT INTO cx_kb_rules
                (group_id, problem, triggers, ratings, empathy, solution,
                 energy, manual_check, sort_order, active)
            SELECT g.id, :problem, :trigs, '1,2,3,4,5', '', :sol, '', false, 8, true
            FROM cx_kb_groups g
            WHERE g.group_no = 0
              AND NOT EXISTS (
                  SELECT 1 FROM cx_kb_rules
                  WHERE problem = :problem
              )
        """), {
            "problem": PAINT_HOLES_REVIEW_TEMPLATE_TITLE,
            "trigs": PAINT_HOLES_KB_TRIGGERS,
            "sol": PAINT_HOLES_REPLY,
        })
        conn.execute(text("""
            UPDATE cx_kb_rules
            SET solution = :sol, triggers = :trigs
            WHERE problem = :problem
              AND (solution IS DISTINCT FROM :sol OR triggers IS DISTINCT FROM :trigs)
        """), {
            "problem": PAINT_HOLES_REVIEW_TEMPLATE_TITLE,
            "sol": PAINT_HOLES_REPLY,
            "trigs": PAINT_HOLES_KB_TRIGGERS,
        })
        conn.commit()


def init_review_settings(engine) -> None:
    """Тумблеры автоответов на отзывы: отдельно WB / ЯМ / Ozon. По умолчанию выкл."""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_review_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT DEFAULT 'false',
                updated_at TIMESTAMP DEFAULT now(),
                updated_by TEXT DEFAULT ''
            )
        """))
        for mp in REVIEW_AUTO_MPS:
            conn.execute(text("""
                INSERT INTO cx_review_settings (key, value, updated_by)
                VALUES (:k, 'false', 'system')
                ON CONFLICT (key) DO NOTHING
            """), {"k": f"auto_{mp}"})
        conn.commit()


def init_unified_queue_view(engine) -> None:
    """Создаёт/обновляет cx_unified_queue и быстрый cx_chats_queue.

    Идемпотентно (CREATE OR REPLACE VIEW), вызывается при каждом старте
    приложения из backend/main.py, как и другие init_*_tables() модулей.
    Требует уже существующих таблиц cx_dismissed_items, wb_feedbacks,
    ym_feedbacks, wb_questions, ym_questions, ozon_questions, wb_chats, ozon_reviews,
    ozon_chats, ozon_chat_messages, ym_chats, ym_chat_messages, wb_claims.
    """
    with engine.connect() as conn:
        conn.execute(text(f"CREATE OR REPLACE VIEW {UNIFIED_QUEUE_VIEW} AS {_UNIFIED_QUEUE_VIEW_SQL}"))
        conn.execute(text(f"CREATE OR REPLACE VIEW {CHATS_QUEUE_VIEW} AS {_CHATS_QUEUE_VIEW_SQL}"))
        conn.commit()


def restore_client_waiting_chats(engine) -> dict:
    """Вернуть в очередь чаты, где последнее слово за клиентом, а автоархив спрятал их как «тишину 3 дня»."""
    counts = {"wb": 0, "ozon": 0, "ym": 0}
    with engine.connect() as conn:
        for mp, table, last_ok in (
            ("wb", "wb_chats", ("client", "customer", "buyer")),
            ("ozon", "ozon_chats", ("customer", "buyer", "client")),
            ("ym", "ym_chats", ("customer", "buyer", "client", "user")),
        ):
            if not conn.execute(text("""
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = :t
            """), {"t": table}).scalar():
                continue
            last_sql = ", ".join(f"'{x}'" for x in last_ok)
            ids = [str(r[0]) for r in conn.execute(text(f"""
                DELETE FROM cx_dismissed_items d
                USING {table} c
                WHERE d.item_id = c.chat_id
                  AND d.mp = :mp
                  AND d.item_type = 'chat'
                  AND d.note LIKE 'Авто: нет общения%'
                  AND lower(COALESCE(c.last_sender, '')) IN ({last_sql})
                RETURNING d.item_id
            """), {"mp": mp}).fetchall()]
            if ids:
                conn.execute(text(f"""
                    UPDATE {table}
                    SET needs_answer = true, updated_at = now()
                    WHERE chat_id = ANY(:ids)
                """), {"ids": ids})
            counts[mp] = len(ids)
        conn.commit()
    return counts


def init_chat_seen_table(engine) -> None:
    """Общий «просмотрено» по чату: открыл один — не новое у всех с доступом."""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_chat_seen (
                mp       VARCHAR(10) NOT NULL,
                chat_id  TEXT        NOT NULL,
                seen_ts  BIGINT      NOT NULL DEFAULT 0,
                seen_by  TEXT,
                seen_at  TIMESTAMP   DEFAULT now(),
                PRIMARY KEY (mp, chat_id)
            )
        """))
        try:
            conn.execute(text(f"""
                INSERT INTO cx_chat_seen (mp, chat_id, seen_ts, seen_by)
                SELECT q.mp, q.id, COALESCE(q.last_ts, 0), 'backfill'
                FROM {CHATS_QUEUE_VIEW} q
                WHERE q.id IS NOT NULL
                ON CONFLICT (mp, chat_id) DO NOTHING
            """))
        except Exception:
            pass
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
    mp_filter = "" if mp == "all" else "AND q.mp = :mp"
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
                CASE
                    WHEN q.mp = 'wb' AND q.item_type = 'question' THEN COALESCE(
                        q.nm_id,
                        (SELECT wl.nm_id::text
                         FROM wb_logistics wl
                         WHERE wl.supplier_article = q.sku
                           AND wl.nm_id IS NOT NULL
                         ORDER BY wl.last_change_date DESC NULLS LAST, wl.dt DESC NULLS LAST
                         LIMIT 1),
                        (SELECT wf.nm_id::text
                         FROM wb_feedbacks wf
                         WHERE wf.supplier_article = q.sku
                           AND wf.nm_id IS NOT NULL
                         ORDER BY wf.created_date DESC NULLS LAST
                         LIMIT 1)
                    )
                    ELSE NULL
                END AS question_wb_nm_id,
                COALESCE(
                    (SELECT wpp.photo_url
                     FROM wb_product_photos wpp
                     WHERE q.mp = 'wb' AND wpp.nm_id::text = q.nm_id
                     LIMIT 1),
                    CASE
                        WHEN q.mp = 'wb' AND NULLIF(TRIM(COALESCE(q.nm_id, '')), '') IS NOT NULL
                        THEN NULL
                        ELSE pp.photo_url
                    END
                ) AS product_photo,
                CASE
                    WHEN q.mp = 'ozon' AND q.item_type = 'question' THEN oq.product_url
                    ELSE NULL
                END AS question_product_url,
                CASE
                    WHEN q.mp = 'ym' AND q.item_type = 'question' THEN COALESCE(
                        yq.market_sku::text, ya.market_sku::text, yb.market_sku::text
                    )
                    ELSE NULL
                END AS question_market_sku
            FROM {UNIFIED_QUEUE_VIEW} q
            LEFT JOIN product_photos pp ON pp.supplier_article = q.sku
            LEFT JOIN ozon_questions oq
              ON q.mp = 'ozon' AND q.item_type = 'question' AND oq.question_id = q.id
            LEFT JOIN ym_questions yq
              ON q.mp = 'ym' AND q.item_type = 'question' AND yq.question_id::text = q.id
            LEFT JOIN ym_assortment ya
              ON q.mp = 'ym' AND q.item_type = 'question' AND ya.supplier_article = q.sku
            LEFT JOIN ym_assortment yb
              ON q.mp = 'ym' AND q.item_type = 'question' AND yb.offer_id = q.sku
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
                        SELECT chat_id, cabinet_chat_id, context_type, return_id, order_id
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
                meta.get("cabinet_chat_id"), ctx,
                search=_ym_chat_search_needle(meta, item),
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

        _attach_review_updates(db, items)
        _attach_review_reply_policy(db, items)

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


@router.get("/chats-queue")
def get_chats_queue(
    db: Session = Depends(get_db),
    mp: str = Query("all"),
    current_user: dict = Depends(get_current_user),
):
    """Быстрая очередь только чатов (кэш + VIEW без отзывов/вопросов/претензий).

    Все фильтры и функции раздела Чаты сохраняются — меняется только источник данных.
    """
    from .. import chat_realtime

    _ = current_user
    mp_key = (mp or "all").lower()
    cached = chat_realtime.get_cached_chats_queue(mp_key)
    if cached is not None:
        out = dict(cached)
        out["cached"] = True
        return out

    mp_filter = "" if mp_key == "all" else "AND q.mp = :mp"
    mp_params = {} if mp_key == "all" else {"mp": mp_key}

    with db.bind.connect() as conn:
        counts = conn.execute(text(f"""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE mp='wb') AS wb,
                COUNT(*) FILTER (WHERE mp='ym') AS ym,
                COUNT(*) FILTER (WHERE mp='ozon') AS ozon,
                COUNT(*) FILTER (WHERE needs_answer) AS pending
            FROM {CHATS_QUEUE_VIEW} q WHERE 1=1 {mp_filter}
        """), mp_params).mappings().first()

        rows = conn.execute(text(f"""
            SELECT q.*,
                s.seen_ts,
                COALESCE(
                    pp.photo_url,
                    (SELECT wpp.photo_url
                     FROM wb_product_photos wpp
                     WHERE q.mp = 'wb' AND wpp.nm_id::text = q.nm_id
                     LIMIT 1)
                ) AS product_photo
            FROM {CHATS_QUEUE_VIEW} q
            LEFT JOIN cx_chat_seen s ON s.mp = q.mp AND s.chat_id = q.id
            LEFT JOIN product_photos pp ON pp.supplier_article = q.sku
            WHERE 1=1 {mp_filter}
            ORDER BY q.needs_answer DESC NULLS LAST, q.last_ts DESC NULLS LAST
        """), mp_params).mappings().all()

    def _row(r):
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat() if hasattr(d["created_at"], "isoformat") else str(d["created_at"])
        d.pop("photos_raw", None)
        d["photos"] = []
        try:
            d["seen_ts"] = int(d.get("seen_ts") or 0)
        except (TypeError, ValueError):
            d["seen_ts"] = 0
        return d

    items = [_row(r) for r in rows]

    ym_ids = [str(i["id"]) for i in items if i.get("mp") == "ym" and i.get("id")]
    ym_cab: dict[str, dict] = {}
    if ym_ids:
        with db.bind.connect() as conn:
            try:
                rows_cab = conn.execute(
                    text("""
                        SELECT chat_id, cabinet_chat_id, context_type, return_id, order_id
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
        if item.get("mp") == "ozon":
            item["cabinet_url"] = _ozon_cabinet_chat_url(str(item.get("id") or ""))
        elif item.get("mp") == "ym":
            meta = ym_cab.get(str(item.get("id") or ""), {})
            item["context_type"] = meta.get("context_type")
            item["cabinet_chat_id"] = meta.get("cabinet_chat_id")
            item["cabinet_url"] = _ym_cabinet_chat_url(
                meta.get("cabinet_chat_id"), meta.get("context_type"),
                search=_ym_chat_search_needle(meta, item),
            )

    payload = {
        "items": items,
        "counts": dict(counts) if counts else {},
        "cached": False,
    }
    chat_realtime.set_cached_chats_queue(mp_key, payload)
    return payload


def _chat_search_tokens(q: str) -> list[str]:
    """Слова для поиска по имени и тексту. Короткие обрывки отбрасываем."""
    parts = [p.strip()[:80] for p in re.split(r"\s+", (q or "").strip()) if p.strip()]
    return [p for p in parts if len(p) >= 2][:8]


def _ilike_contains(token: str) -> str:
    t = (token or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{t}%"


def _token_match_sql(columns: list[str], tokens: list[str], prefix: str,
                     extra_or: str | None = None) -> tuple[str, dict]:
    """Каждое слово — хотя бы в одном поле или в extra_or (EXISTS по сообщениям)."""
    params: dict = {}
    ands = []
    for i, tok in enumerate(tokens):
        key = f"{prefix}_{i}"
        params[key] = _ilike_contains(tok)
        ors = [f"{f} ILIKE :{key} ESCAPE '\\'" for f in columns]
        if extra_or:
            ors.append(extra_or.replace(":tok", f":{key}"))
        ands.append("(" + " OR ".join(ors) + ")")
    return " AND ".join(ands), params


def _search_chats_rows(conn, tokens: list[str], mp: str, limit: int) -> list[dict]:
    """Поиск чатов WB / Ozon / ЯМ по имени, артикулу, заказу и тексту переписки."""
    mps = ["wb", "ozon", "ym"] if mp == "all" else [mp]
    out: list[dict] = []
    per = max(limit, 10)
    for marketplace in mps:
        if marketplace == "wb":
            where, params = _token_match_sql(
                [
                    "COALESCE(c.client_name,'')",
                    "COALESCE(c.good_name,'')",
                    "COALESCE(c.last_message,'')",
                    "COALESCE(c.chat_id,'')",
                    "COALESCE(c.nm_id::text,'')",
                ],
                tokens, "wb",
                extra_or=(
                    "EXISTS (SELECT 1 FROM wb_chat_messages m"
                    " WHERE m.chat_id = c.chat_id AND m.text ILIKE :tok ESCAPE '\\')"
                ),
            )
            params["lim"] = per
            rows = conn.execute(text(f"""
                SELECT c.chat_id AS id, 'wb'::text AS mp, 'chat'::text AS item_type,
                       c.good_name AS sku, c.nm_id::text AS nm_id,
                       c.last_message AS body, c.client_name AS contact,
                       c.last_ts, c.needs_answer, c.last_sender,
                       COALESCE(s.seen_ts, 0) AS seen_ts,
                       EXISTS (
                         SELECT 1 FROM cx_dismissed_items d
                         WHERE d.item_type='chat' AND d.mp='wb' AND d.item_id=c.chat_id
                       ) AS is_archive
                FROM wb_chats c
                LEFT JOIN cx_chat_seen s ON s.mp='wb' AND s.chat_id=c.chat_id
                WHERE {where}
                ORDER BY c.last_ts DESC NULLS LAST
                LIMIT :lim
            """), params).mappings().all()
        elif marketplace == "ozon":
            where, params = _token_match_sql(
                [
                    "COALESCE(c.order_number,'')",
                    "COALESCE(c.context_article,'')",
                    "COALESCE(c.context_sku::text,'')",
                    "COALESCE(c.last_message,'')",
                    "COALESCE(c.chat_id,'')",
                ],
                tokens, "oz",
                extra_or=(
                    "EXISTS (SELECT 1 FROM ozon_chat_messages m"
                    " WHERE m.chat_id = c.chat_id AND m.text ILIKE :tok ESCAPE '\\')"
                ),
            )
            params["lim"] = per
            rows = conn.execute(text(f"""
                SELECT c.chat_id AS id, 'ozon'::text AS mp, 'chat'::text AS item_type,
                       COALESCE(NULLIF(TRIM(c.context_article), ''), c.order_number) AS sku,
                       c.context_sku::text AS nm_id,
                       c.last_message AS body,
                       CASE
                         WHEN NULLIF(TRIM(c.context_article), '') IS NOT NULL THEN TRIM(c.context_article)
                         WHEN NULLIF(TRIM(c.order_number), '') IS NOT NULL THEN 'Заказ ' || TRIM(c.order_number)
                         ELSE 'Покупатель'
                       END AS contact,
                       EXTRACT(epoch FROM COALESCE(
                         (SELECT MAX(m.created_at) FROM ozon_chat_messages m WHERE m.chat_id=c.chat_id),
                         c.waiting_since, c.created_at, c.updated_at, now()
                       ))::bigint * 1000 AS last_ts,
                       c.needs_answer, c.last_sender,
                       COALESCE(s.seen_ts, 0) AS seen_ts,
                       EXISTS (
                         SELECT 1 FROM cx_dismissed_items d
                         WHERE d.item_type='chat' AND d.mp='ozon' AND d.item_id=c.chat_id
                       ) AS is_archive
                FROM ozon_chats c
                LEFT JOIN cx_chat_seen s ON s.mp='ozon' AND s.chat_id=c.chat_id
                WHERE {where}
                ORDER BY last_ts DESC NULLS LAST
                LIMIT :lim
            """), params).mappings().all()
        else:
            where, params = _token_match_sql(
                [
                    "COALESCE(c.customer_name,'')",
                    "COALESCE(c.context_article,'')",
                    "COALESCE(c.order_id,'')",
                    "COALESCE(c.return_id,'')",
                    "COALESCE(c.last_message,'')",
                    "COALESCE(c.chat_id,'')",
                ],
                tokens, "ym",
                extra_or=(
                    "EXISTS (SELECT 1 FROM ym_chat_messages m"
                    " WHERE m.chat_id = c.chat_id AND m.text ILIKE :tok ESCAPE '\\')"
                ),
            )
            params["lim"] = per
            rows = conn.execute(text(f"""
                SELECT c.chat_id AS id, 'ym'::text AS mp, 'chat'::text AS item_type,
                       COALESCE(NULLIF(TRIM(c.context_article), ''), c.order_id) AS sku,
                       NULL::text AS nm_id,
                       c.last_message AS body,
                       COALESCE(NULLIF(TRIM(c.customer_name), ''), 'Покупатель') AS contact,
                       EXTRACT(epoch FROM COALESCE(
                         (SELECT MAX(m.created_at) FROM ym_chat_messages m WHERE m.chat_id=c.chat_id),
                         c.waiting_since, c.updated_at, c.created_at, now()
                       ))::bigint * 1000 AS last_ts,
                       c.needs_answer, c.last_sender,
                       COALESCE(s.seen_ts, 0) AS seen_ts,
                       EXISTS (
                         SELECT 1 FROM cx_dismissed_items d
                         WHERE d.item_type='chat' AND d.mp='ym' AND d.item_id=c.chat_id
                       ) AS is_archive
                FROM ym_chats c
                LEFT JOIN cx_chat_seen s ON s.mp='ym' AND s.chat_id=c.chat_id
                WHERE {where}
                ORDER BY last_ts DESC NULLS LAST
                LIMIT :lim
            """), params).mappings().all()
        out.extend(dict(r) for r in rows)
    out.sort(key=lambda r: int(r.get("last_ts") or 0), reverse=True)
    return out[:limit]


@router.get("/chats-search")
def search_chats(
    q: str = Query(""),
    mp: str = Query("all"),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Поиск чатов по имени клиента, артикулу, заказу и словам из переписки."""
    _ = current_user
    tokens = _chat_search_tokens(q)
    if not tokens:
        return {"items": [], "q": q}
    mp_key = (mp or "all").lower()
    if mp_key not in ("all", "wb", "ozon", "ym"):
        mp_key = "all"
    with db.bind.connect() as conn:
        rows = _search_chats_rows(conn, tokens, mp_key, limit)
    items = []
    for r in rows:
        item = dict(r)
        item["item_type"] = "chat"
        item["is_archive"] = bool(item.get("is_archive"))
        item["seen_ts"] = int(item.get("seen_ts") or 0)
        item["last_ts"] = int(item.get("last_ts") or 0)
        item["photos"] = []
        if item.get("mp") == "ozon":
            item["cabinet_url"] = _ozon_cabinet_chat_url(str(item.get("id") or ""))
        items.append(item)
    return {"items": items, "q": q}


class ChatSeenPayload(BaseModel):
    mp: str = "wb"
    chat_id: str
    seen_ts: int | None = None


def _upsert_chat_seen(conn, mp: str, chat_id: str, seen_ts: int, username: str) -> None:
    conn.execute(text("""
        INSERT INTO cx_chat_seen (mp, chat_id, seen_ts, seen_by, seen_at)
        VALUES (:mp, :cid, :ts, :u, now())
        ON CONFLICT (mp, chat_id) DO UPDATE SET
            seen_ts = GREATEST(cx_chat_seen.seen_ts, EXCLUDED.seen_ts),
            seen_by = EXCLUDED.seen_by,
            seen_at = now()
    """), {"mp": mp, "cid": chat_id, "ts": int(seen_ts or 0), "u": username})


@router.post("/chats/seen")
def mark_chat_seen(
    payload: ChatSeenPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Открыли чат — он перестаёт быть «новым» у всех, кто видит раздел."""
    mp = _normalize_mp(payload.mp)
    cid = (payload.chat_id or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="Нет chat_id")
    seen_ts = int(payload.seen_ts or 0)
    with db.bind.connect() as conn:
        if seen_ts <= 0:
            row = conn.execute(text(f"""
                SELECT last_ts FROM {CHATS_QUEUE_VIEW}
                WHERE mp = :mp AND id = :cid LIMIT 1
            """), {"mp": mp, "cid": cid}).mappings().first()
            if row and row.get("last_ts"):
                seen_ts = int(row["last_ts"])
            else:
                seen_ts = int(time.time() * 1000)
        _upsert_chat_seen(conn, mp, cid, seen_ts, current_user.get("username") or "")
        conn.commit()
    try:
        from .. import chat_realtime
        chat_realtime.publish_chat_seen(cid, mp, seen_ts)
    except Exception:
        pass
    return {"status": "ok", "mp": mp, "chat_id": cid, "seen_ts": seen_ts}


@router.post("/chats/mark-all-seen")
def mark_all_chats_seen(
    mp: str = Query("all"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Кнопка «Все прочитано»: снять «новые» у всей команды по площадке."""
    mp_key = (mp or "all").lower()
    if mp_key not in ("all", "wb", "ozon", "ym"):
        mp_key = "all"
    mp_filter = "" if mp_key == "all" else "AND q.mp = :mp"
    params = {} if mp_key == "all" else {"mp": mp_key}
    with db.bind.connect() as conn:
        conn.execute(text(f"""
            INSERT INTO cx_chat_seen (mp, chat_id, seen_ts, seen_by, seen_at)
            SELECT q.mp, q.id, COALESCE(q.last_ts, 0), :u, now()
            FROM {CHATS_QUEUE_VIEW} q
            WHERE q.id IS NOT NULL {mp_filter}
            ON CONFLICT (mp, chat_id) DO UPDATE SET
                seen_ts = GREATEST(cx_chat_seen.seen_ts, EXCLUDED.seen_ts),
                seen_by = EXCLUDED.seen_by,
                seen_at = now()
        """), {**params, "u": current_user.get("username") or ""})
        n = conn.execute(text(f"""
            SELECT COUNT(*) FROM {CHATS_QUEUE_VIEW} q WHERE 1=1 {mp_filter}
        """), params).scalar()
        conn.commit()
    try:
        from .. import chat_realtime
        chat_realtime.publish_chats_seen_all(mp_key)
    except Exception:
        pass
    return {"status": "ok", "marked": int(n or 0)}


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
                        AND q.id::text = c.question_id
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
    """Поставить открытый вопрос WB/ЯМ/Ozon в общую очередь отдела качества."""
    _cs_access(current_user, db)
    if mp not in ("wb", "ym", "ozon"):
        raise HTTPException(status_code=400, detail="Поддерживаются вопросы WB, ЯМ и Ozon")
    note = (payload.note or "").strip()
    if len(note) > 2000:
        raise HTTPException(status_code=400, detail="Комментарий не должен быть длиннее 2000 символов")

    with db.bind.connect() as conn:
        exists = conn.execute(text(f"""
            SELECT 1 FROM {UNIFIED_QUEUE_VIEW}
            WHERE mp = :mp AND item_type = 'question' AND id::text = :id
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
    if mp not in ("all", "wb", "ym", "ozon"):
        raise HTTPException(status_code=400, detail="mp: all, wb, ym или ozon")
    params = {} if mp == "all" else {"mp": mp}
    mp_filter = "" if mp == "all" else "AND c.mp = :mp"
    with db.bind.connect() as conn:
        _cancel_obsolete_question_clarifications(conn)
        rows = conn.execute(text(f"""
            SELECT
                q.*, c.id AS clarification_id, c.note AS clarification_note,
                c.requested_by AS clarification_requested_by,
                c.requested_at AS clarification_requested_at,
                CASE
                    WHEN q.mp = 'wb' AND q.item_type = 'question' THEN COALESCE(
                        q.nm_id,
                        (SELECT wl.nm_id::text
                         FROM wb_logistics wl
                         WHERE wl.supplier_article = q.sku
                           AND wl.nm_id IS NOT NULL
                         ORDER BY wl.last_change_date DESC NULLS LAST, wl.dt DESC NULLS LAST
                         LIMIT 1),
                        (SELECT wf.nm_id::text
                         FROM wb_feedbacks wf
                         WHERE wf.supplier_article = q.sku
                           AND wf.nm_id IS NOT NULL
                         ORDER BY wf.created_date DESC NULLS LAST
                         LIMIT 1)
                    )
                    ELSE NULL
                END AS question_wb_nm_id,
                COALESCE(
                    (SELECT wpp.photo_url
                     FROM wb_product_photos wpp
                     WHERE q.mp = 'wb' AND wpp.nm_id::text = q.nm_id
                     LIMIT 1),
                    CASE
                        WHEN q.mp = 'wb' AND NULLIF(TRIM(COALESCE(q.nm_id, '')), '') IS NOT NULL
                        THEN NULL
                        ELSE pp.photo_url
                    END
                ) AS product_photo,
                CASE
                    WHEN q.mp = 'ozon' AND q.item_type = 'question' THEN oq.product_url
                    ELSE NULL
                END AS question_product_url,
                CASE
                    WHEN q.mp = 'ym' AND q.item_type = 'question' THEN COALESCE(
                        yq.market_sku::text, ya.market_sku::text, yb.market_sku::text
                    )
                    ELSE NULL
                END AS question_market_sku
            FROM cx_question_clarifications c
            JOIN {UNIFIED_QUEUE_VIEW} q
              ON q.mp = c.mp AND q.item_type = 'question' AND q.id::text = c.question_id
            LEFT JOIN product_photos pp ON pp.supplier_article = q.sku
            LEFT JOIN ozon_questions oq
              ON q.mp = 'ozon' AND q.item_type = 'question' AND oq.question_id = q.id
            LEFT JOIN ym_questions yq
              ON q.mp = 'ym' AND q.item_type = 'question' AND yq.question_id::text = q.id
            LEFT JOIN ym_assortment ya
              ON q.mp = 'ym' AND q.item_type = 'question' AND ya.supplier_article = q.sku
            LEFT JOIN ym_assortment yb
              ON q.mp = 'ym' AND q.item_type = 'question' AND yb.offer_id = q.sku
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
                    AND q.id::text = c.question_id
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
    """Сохранить предложение QA. В API WB/ЯМ/Ozon этот метод не ходит."""
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
                             AND q.id::text = c.question_id
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
    q: str = Query(""),             # поиск по тексту отзыва / артикулу
    rating: str = Query(""),        # '' | '1'..'5' (старый одиночный фильтр)
    ratings: str = Query(""),       # '5,4,1' — несколько оценок
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
    if q.strip():
        conds += (
            " AND (COALESCE(a.body,'') ILIKE :q"
            " OR COALESCE(a.sku,'') ILIKE :q"
            " OR COALESCE(a.nm_id,'') ILIKE :q)"
        )
        params["q"] = f"%{q.strip()}%"
    star_filter = parse_star_ratings(ratings, rating)
    if star_filter:
        conds += " AND a.rating = ANY(:ratings)"
        params["ratings"] = star_filter
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
            COALESCE(NULLIF(TRIM(f.text),''), NULLIF(TRIM(f.pros),''), NULLIF(TRIM(f.cons),''), '—') AS body,
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
            COALESCE(NULLIF(TRIM(y.comment),''), NULLIF(TRIM(y.pro_text),''), NULLIF(TRIM(y.contra_text),''), '—'),
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
            COALESCE(NULLIF(TRIM(r.review_text),''), NULLIF(TRIM(r.pros),''), NULLIF(TRIM(r.cons),''), '—'),
            r.rating, r.answer_text, r.created_at,
            NULL::text, NULL::text, 'answered'
        FROM ozon_reviews r WHERE r.answered_at IS NOT NULL
        UNION ALL
        SELECT
            d.item_id, d.mp, d.item_type,
            COALESCE(wf.supplier_article, yf.supplier_article, oz.supplier_article),
            wf.nm_id::text,
            COALESCE(
                NULLIF(TRIM(wf.text),''), NULLIF(TRIM(wf.pros),''), NULLIF(TRIM(wf.cons),''),
                NULLIF(TRIM(yf.comment),''), NULLIF(TRIM(yf.pro_text),''), NULLIF(TRIM(yf.contra_text),''),
                NULLIF(TRIM(oz.review_text),''), NULLIF(TRIM(oz.pros),''), NULLIF(TRIM(oz.cons),''),
                '—'
            ),
            COALESCE(wf.valuation, yf.valuation, oz.rating),
            COALESCE(wf.answer_text, yf.answer_text, oz.answer_text),
            COALESCE(wf.created_date, yf.created_date, oz.created_at, d.dismissed_at),
            d.dismissed_by, d.note, 'dismissed'
        FROM cx_dismissed_items d
        LEFT JOIN wb_feedbacks wf
            ON d.mp = 'wb' AND d.item_type = 'review' AND wf.id::text = d.item_id
        LEFT JOIN ym_feedbacks yf
            ON d.mp = 'ym' AND d.item_type = 'review' AND yf.feedback_id::text = d.item_id
        LEFT JOIN ozon_reviews oz
            ON d.mp = 'ozon' AND d.item_type = 'review' AND oz.review_id = d.item_id
    """

    with db.bind.connect() as conn:
        total = conn.execute(text(f"""
            SELECT COUNT(*) FROM ({archive_union}) a WHERE 1=1 {mp_cond} {type_cond} {conds}
        """), params).scalar() or 0

        rows = conn.execute(text(f"""
            SELECT a.*, l.answered_by, l.sent_at AS answered_at,
                COALESCE(
                    (SELECT wpp.photo_url
                     FROM wb_product_photos wpp
                     WHERE a.mp = 'wb' AND wpp.nm_id::text = a.nm_id
                     LIMIT 1),
                    CASE
                        WHEN a.mp = 'wb' AND NULLIF(TRIM(COALESCE(a.nm_id, '')), '') IS NOT NULL
                        THEN NULL
                        ELSE pp.photo_url
                    END
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
    if payload.item_type == "chat":
        try:
            from .. import chat_bot
            chat_bot.set_bot_disabled(db, payload.item_id, True)
            db.commit()
        except Exception:
            pass
        try:
            from .. import chat_realtime
            chat_realtime.publish_chat_removed(payload.item_id, payload.mp)
        except Exception:
            pass
    return {"status": "ok"}


@router.delete("/dismiss/{mp}/{item_type}/{item_id}")
def undismiss_item(mp: str, item_type: str, item_id: str, db: Session = Depends(get_db)):
    with db.bind.connect() as conn:
        conn.execute(text(
            "DELETE FROM cx_dismissed_items WHERE mp=:mp AND item_type=:it AND item_id=:iid"
        ), {"mp": mp, "it": item_type, "iid": item_id})
        conn.commit()
    if item_type == "chat":
        try:
            from .. import chat_bot
            chat_bot.set_bot_disabled(db, item_id, False)
            db.commit()
        except Exception:
            pass
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
    mime = (mime_type or "").lower()
    probe = f"{name} {url} {mime}".lower().split("?", 1)[0]
    if mime.startswith("video/") or probe.endswith((".mp4", ".webm", ".mov", ".m4v")):
        return "video"
    if mime.startswith("image/") or probe.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
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


def _chat_card_dict(mp: str, row) -> dict:
    d = dict(row)
    d["mp"] = mp
    d["item_type"] = "chat"
    if mp == "wb":
        d["sku"] = d.get("good_name") or (f"Арт. {d['nm_id']}" if d.get("nm_id") else "")
    elif not d.get("sku"):
        d["sku"] = ""
    d["pre_purchase"] = bool(d.get("pre_purchase"))
    return d


def _attach_chat_fulfillment(db: Session, mp: str, chat_id: str, payload: dict) -> dict:
    try:
        with db.bind.connect() as conn:
            try:
                payload["fulfillment"] = chat_fulfillment.resolve_chat_fulfillment(conn, mp, chat_id)
            except Exception:
                payload.setdefault("fulfillment", None)
            try:
                payload["order"] = chat_order.resolve_chat_order(conn, mp, chat_id)
            except Exception:
                payload.setdefault("order", None)
    except Exception:
        payload.setdefault("fulfillment", None)
        payload.setdefault("order", None)
    return payload


@router.get("/chat-card/{mp}/{chat_id}")
def get_chat_card(mp: str, chat_id: str, db: Session = Depends(get_db)):
    """Карточка чата по id (WB: голый uuid из ЛК → `1:uuid`; ЯМ и Ozon — как есть)."""
    m = _normalize_mp(mp)
    with db.bind.connect() as conn:
        if m == "wb":
            cid = _resolve_wb_chat_id(conn, chat_id)
            if not cid:
                raise HTTPException(status_code=404, detail="Чат не найден")
            row = conn.execute(text("""
                SELECT c.chat_id AS id, c.client_name AS contact, c.nm_id,
                       c.good_name, c.last_message AS body, c.last_ts,
                       c.needs_answer, c.order_rid,
                       COALESCE(c.pre_purchase, false) AS pre_purchase,
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
        elif m == "ym":
            row = conn.execute(text("""
                SELECT c.chat_id AS id,
                       COALESCE(NULLIF(TRIM(c.customer_name), ''), 'Покупатель') AS contact,
                       c.last_message AS body, c.needs_answer,
                       EXTRACT(epoch FROM COALESCE(c.updated_at, c.created_at, now()))::bigint * 1000 AS last_ts,
                       COALESCE(NULLIF(TRIM(c.context_article), ''), c.order_id) AS sku,
                       NULL::text AS nm_id,
                       NULL::text AS product_photo,
                       GREATEST(0, EXTRACT(epoch FROM now()
                         - COALESCE(c.updated_at, c.created_at, now()))::int) AS age_sec
                FROM ym_chats c
                WHERE c.chat_id::text = :cid
                LIMIT 1
            """), {"cid": str(chat_id)}).mappings().first()
        elif m == "ozon":
            row = conn.execute(text("""
                SELECT c.chat_id AS id,
                       COALESCE(NULLIF(TRIM(c.context_article), ''),
                                CASE WHEN NULLIF(TRIM(c.order_number), '') IS NOT NULL
                                     THEN 'Заказ ' || TRIM(c.order_number)
                                     ELSE 'Покупатель' END) AS contact,
                       c.last_message AS body, c.needs_answer,
                       EXTRACT(epoch FROM COALESCE(c.updated_at, c.created_at, now()))::bigint * 1000 AS last_ts,
                       COALESCE(NULLIF(TRIM(c.context_article), ''), c.order_number) AS sku,
                       c.context_sku::text AS nm_id,
                       NULL::text AS product_photo,
                       GREATEST(0, EXTRACT(epoch FROM now()
                         - COALESCE(c.updated_at, c.created_at, now()))::int) AS age_sec
                FROM ozon_chats c
                WHERE c.chat_id::text = :cid
                LIMIT 1
            """), {"cid": str(chat_id)}).mappings().first()
        else:
            raise HTTPException(status_code=404, detail="Чат не найден")
    if not row:
        raise HTTPException(status_code=404, detail="Чат не найден")
    card = _chat_card_dict(m, row)
    return _attach_chat_fulfillment(db, m, str(card.get("id") or chat_id), card)


_REPLY_WS_RE = re.compile(r"\s+")


def _norm_reply_text(value: str) -> str:
    return _REPLY_WS_RE.sub(" ", str(value or "")).strip()


def _is_bot_answered_by(name: str) -> bool:
    n = str(name or "").strip().lower()
    return n in ("chat_bot", "bot") or n.startswith("auto_reply")


def match_seller_reply_source(text: str, logs: list, *, message_id: str = "") -> dict:
    """Кто написал исходящее: автоответ или менеджер. Для ленты CX, клиенту не уходит."""
    mid = str(message_id or "")
    if mid.startswith("bot-"):
        return {"repliedBy": "bot", "repliedByName": "Автоответ"}
    needle = _norm_reply_text(text)
    if not needle:
        return {"repliedBy": "manager", "repliedByName": "Менеджер"}
    sample = needle[:120]
    for log in logs or []:
        body = _norm_reply_text(log.get("answer_text") or "")
        if not body:
            continue
        if sample in body or body[:120] in needle:
            by = log.get("answered_by") or ""
            if _is_bot_answered_by(by):
                return {"repliedBy": "bot", "repliedByName": "Автоответ"}
            return {"repliedBy": "manager", "repliedByName": str(by).strip() or "Менеджер"}
    return {"repliedBy": "manager", "repliedByName": "Менеджер"}


def _load_chat_reply_logs(conn, mp: str, chat_id: str) -> list:
    try:
        rows = conn.execute(text("""
            SELECT answered_by, answer_text
            FROM cx_response_log
            WHERE item_type = 'chat' AND mp = :mp AND item_id = :cid
            ORDER BY sent_at DESC NULLS LAST
            LIMIT 80
        """), {"mp": mp, "cid": str(chat_id)}).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _annotate_chat_messages(conn, mp: str, chat_id: str, msgs: list) -> list:
    logs = _load_chat_reply_logs(conn, mp, chat_id)
    for msg in msgs or []:
        if str(msg.get("senderType") or "").lower() != "seller":
            continue
        msg.update(match_seller_reply_source(
            msg.get("text") or "",
            logs,
            message_id=str(msg.get("messageId") or ""),
        ))
    return msgs


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
        with db.bind.connect() as conn:
            _annotate_chat_messages(conn, "wb", resolved, msgs)
        return _attach_chat_fulfillment(db, "wb", resolved, {"messages": msgs, "partial": False, "chat_id": resolved})

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
    with db.bind.connect() as conn:
        _annotate_chat_messages(conn, "wb", resolved, msgs)
    return _attach_chat_fulfillment(db, "wb", resolved, {"messages": msgs, "partial": True})


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


def _parse_ym_attachment_raw(raw) -> list[dict]:
    """Старые строки-URL и новый JSON {url,name,size} из payload ЯМ."""
    import json as _json
    s = (raw or "").strip() if isinstance(raw, str) else raw
    if not s:
        return []
    items = []
    if isinstance(s, (list, dict)):
        data = s if isinstance(s, list) else [s]
    elif isinstance(s, str) and s[:1] in "[{":
        try:
            parsed = _json.loads(s)
        except Exception:
            parsed = None
        data = parsed if isinstance(parsed, list) else ([parsed] if isinstance(parsed, dict) else None)
    else:
        data = None
    if data is not None:
        for a in data:
            if isinstance(a, str) and a.startswith("http"):
                items.append({"url": a, "name": "", "size": 0, "mime": ""})
            elif isinstance(a, dict):
                u = str(a.get("url") or "").strip()
                if u.startswith("http"):
                    items.append({
                        "url": u,
                        "name": str(a.get("name") or ""),
                        "size": a.get("size") or 0,
                        "mime": str(a.get("mime") or a.get("contentType") or a.get("mimeType") or ""),
                    })
        if items:
            return items
    text_raw = s if isinstance(s, str) else ""
    for u in text_raw.split("\n"):
        u = u.strip()
        if u.startswith("http"):
            items.append({"url": u, "name": "", "size": 0, "mime": ""})
    return items


def _sniff_ym_content_type(url: str) -> str:
    """Content-Type без скачивания тела — у messenger-файлов часто нет расширения."""
    try:
        r = requests.get(
            url,
            headers=ym_client.headers(),
            timeout=8,
            stream=True,
            allow_redirects=True,
        )
        mime = (r.headers.get("Content-Type") or "").split(";")[0].strip()
        r.close()
        return mime
    except Exception:
        return ""


def _typed_ym_attachments(raw) -> list[dict]:
    result = []
    for item in _parse_ym_attachment_raw(raw):
        url = item.get("url") or ""
        name = item.get("name") or ""
        mime = item.get("mime") or ""
        kind = _attachment_kind(name, url, mime, default="")
        if not kind:
            from urllib.parse import urlparse
            path_name = urlparse(url).path.rsplit("/", 1)[-1]
            if "." not in path_name:
                mime = mime or _sniff_ym_content_type(url)
            kind = _attachment_kind(name, url, mime, default="image")
        if kind == "image":
            label = name or "Изображение из чата"
        elif kind == "video":
            label = name or "Видео из чата"
        else:
            label = name or "Файл из чата"
        result.append({
            "type": kind,
            "url": _ym_proxy_or_direct(url),
            "name": label,
            "mimeType": mime or None,
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
            SELECT message_id, sender_type, text, attachments, created_at, is_image
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
                "messageId": r.get("message_id") or "",
            })
        with db.bind.connect() as conn:
            _annotate_chat_messages(conn, "ozon", chat_id, msgs)
        return _attach_chat_fulfillment(db, "ozon", chat_id, {"messages": msgs, "partial": False})

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
    with db.bind.connect() as conn:
        _annotate_chat_messages(conn, "ozon", chat_id, msgs)
    return _attach_chat_fulfillment(db, "ozon", chat_id, {"messages": msgs, "partial": True})


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
                SELECT message_id, sender_type, text, created_at, attachments
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
            typed = _typed_ym_attachments(r.get("attachments") or "")
            if typed:
                imgs = [item["url"] for item in typed if item.get("type") == "image"]
            else:
                raw = r.get("attachments") or ""
                if raw:
                    imgs = [_ym_proxy_or_direct(u) for u in str(raw).split("\n") if u.startswith("http")]
            msgs.append({
                "text": r.get("text") or "",
                "senderType": sender,
                "createdAt": _iso_to_ms(r.get("created_at")),
                "attachments": typed,
                "images": imgs,
                "messageId": r.get("message_id") or "",
            })
        with db.bind.connect() as conn:
            _annotate_chat_messages(conn, "ym", chat_id, msgs)
        return _attach_chat_fulfillment(db, "ym", chat_id, {"messages": msgs, "partial": False})

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
    with db.bind.connect() as conn:
        _annotate_chat_messages(conn, "ym", chat_id, msgs)
    return _attach_chat_fulfillment(db, "ym", chat_id, {"messages": msgs, "partial": True})


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
    if not row.get("return_number"):
        row["return_number"] = str(row.get("claim_id") or "")
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
    linked_reshipment = None

    with db.bind.connect() as conn:
        # ── Сам отзыв ──
        if mp == "wb":
            r = conn.execute(text(
                "SELECT id::text AS id, supplier_article AS sku, nm_id::text AS nm_id, "
                "valuation AS rating, text AS body, pros, cons, photos AS photos_raw, "
                "answer_text, state, created_date AS created_at, "
                "last_order_shk_id, last_order_created_at, "
                "prev_text, prev_valuation, buyer_updated_at, update_kind "
                "FROM wb_feedbacks WHERE id::text=:i"
            ), {"i": item_id}).mappings().first()
        elif mp == "ym":
            r = conn.execute(text(
                "SELECT feedback_id::text AS id, supplier_article AS sku, NULL::text AS nm_id, "
                "valuation AS rating, comment AS body, pro_text AS pros, contra_text AS cons, "
                "NULL::text AS photos_raw, answer_text, "
                "CASE WHEN answered_at IS NULL THEN 'active' ELSE 'answered' END AS state, "
                "created_date AS created_at, "
                "prev_text, prev_valuation, buyer_updated_at, update_kind "
                "FROM ym_feedbacks WHERE feedback_id::text=:i"
            ), {"i": item_id}).mappings().first()
        else:  # ozon
            r = conn.execute(text(
                "SELECT review_id AS id, supplier_article AS sku, NULL::text AS nm_id, "
                "rating, review_text AS body, pros, cons, NULL::text AS photos_raw, answer_text, "
                "CASE WHEN answered_at IS NULL THEN 'active' ELSE 'answered' END AS state, "
                "created_at, prev_text, prev_valuation, buyer_updated_at, update_kind "
                "FROM ozon_reviews WHERE review_id=:i"
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
        review["product_photo"] = resolve_review_product_photo(conn, mp, sku, nm, db=db)
        from ..review_updates import update_label as _upd_label
        kind = review.get("update_kind")
        review["update_kind"] = kind or ""
        review["update_label"] = _upd_label(kind)
        review["prev_text"] = review.get("prev_text") or ""
        review["buyer_updated_at"] = _iso(review.get("buyer_updated_at"))

        linked_reshipment = None

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

                rs = conn.execute(text("""
                    SELECT id, status FROM reshipment_requests
                    WHERE matched_srid = :r
                    ORDER BY id DESC LIMIT 1
                """), {"r": rid}).mappings().first()
                if rs:
                    linked_reshipment = {"id": rs["id"], "status": rs["status"]}

    return {
        "review": review,
        "linked_chat": linked_chat,
        "linked_return": linked_return,
        "linked_reshipment": linked_reshipment,
        "chats": [],
        "returns": [],
        "reshipments": [linked_reshipment] if linked_reshipment else [],
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
    cid = str(d.get("claim_id") or "")
    d["return_number"] = cid
    return d


def _attach_articles_from_nm(items, by_nm: dict) -> list:
    """Подставить наш артикул по nm_id. Возвращает пары (claim_id, article) для записи в БД."""
    from ..wb_mini_sync import _nm_id_key
    persist = []
    for it in items or []:
        if str(it.get("supplier_article") or "").strip():
            continue
        nm = _nm_id_key(it.get("nm_id"))
        art = str((by_nm or {}).get(nm) or "").strip()
        if not art:
            continue
        it["supplier_article"] = art
        cid = str(it.get("claim_id") or "").strip()
        if cid:
            persist.append((cid, art))
    return persist


def _fill_wb_claim_articles(conn, items) -> None:
    """Если WB не прислал наш артикул — взять его по nm_id и сохранить."""
    if not items:
        return
    from ..wb_mini_sync import lookup_wb_articles_by_nm, _nm_id_key
    try:
        missing_nm = []
        seen = set()
        for it in items:
            if str(it.get("supplier_article") or "").strip():
                continue
            nm = _nm_id_key(it.get("nm_id"))
            if nm and nm not in seen:
                seen.add(nm)
                missing_nm.append(nm)
        if not missing_nm:
            return
        by_nm = lookup_wb_articles_by_nm(conn, missing_nm)
        persist = _attach_articles_from_nm(items, by_nm)
        for cid, art in persist:
            conn.execute(text("""
                UPDATE wb_claims
                SET supplier_article = :art
                WHERE claim_id::text = :cid
                  AND (supplier_article IS NULL OR btrim(supplier_article) IN ('', 'Без артикула'))
            """), {"art": art, "cid": cid})
        need_photo = [
            str(it.get("supplier_article") or "").strip()
            for it in items
            if not it.get("product_photo") and str(it.get("supplier_article") or "").strip()
        ]
        if not need_photo:
            return
        rows = conn.execute(text("""
            SELECT supplier_article, photo_url
            FROM product_photos
            WHERE supplier_article = ANY(CAST(:arts AS text[]))
              AND NULLIF(TRIM(photo_url), '') IS NOT NULL
        """), {"arts": need_photo}).mappings().all()
        by_art = {
            str(r.get("supplier_article") or "").strip(): r.get("photo_url")
            for r in rows if r.get("photo_url")
        }
        for it in items:
            if it.get("product_photo"):
                continue
            art = str(it.get("supplier_article") or "").strip()
            if art and by_art.get(art):
                it["product_photo"] = by_art[art]
    except Exception:
        logger.exception("[WB] fill claim articles")
        try:
            conn.rollback()
        except Exception:
            pass



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
    rid = str(d.get("return_id") or "")
    force_arch = bool(d.get("force_archive"))
    placement = (d.get("placement_type") or "").strip().upper() or None
    return {
        "mp": "ym",
        "claim_id": rid,
        "return_number": rid,
        "srid": str(d.get("order_id") or ""),
        "order_id": d.get("order_id"),
        "supplier_article": d.get("supplier_article"),
        "nm_id": None,
        "claim_type": d.get("reason_type") or "return",
        "claim_type_label": reason,
        "status": status_code or status_label,
        "status_label": status_label,
        # status_ex — расшифровка только для WB; у ЯМ сюда не кладём subreason
        "status_ex": "",
        "user_comment": (d.get("return_comment") or "").strip(),
        "is_archive": force_arch or (not ym_returns_api.is_active_status(status_code)),
        "claim_date": claim_date,
        "order_date": None,
        "photos": photos,
        "product_photo": d.get("product_photo"),
        "correction": d.get("correction"),
        "audit_status": d.get("audit_status"),
        "campaign_id": d.get("campaign_id"),
        "placement_type": placement,
        "schema_code": placement,
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
    buyer_comment = (d.get("buyer_comment") or "").strip()
    photos_raw = (d.get("photos") or "").strip()
    photos = [u for u in photos_raw.split() if u.startswith("http")]
    rid = str(d.get("return_id") or "")
    return {
        "mp": "ozon",
        "claim_id": rid,
        "return_number": rid,
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
        "user_comment": buyer_comment or reason,
        "buyer_comment": buyer_comment or None,
        "is_archive": is_arch,
        "claim_date": claim_date,
        "status_changed_at": status_changed,
        "order_date": None,
        "photos": photos,
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
        "no_photos_note": (
            None
            if (photos or buyer_comment)
            else "Фото и комментарий подтягивает Mac-бот из кабинета (пока нет — откройте ЛК)"
        ),
        "cabinet_url": ozon_returns_api.cabinet_url(d.get("return_id")),
        "cabinet_synced_at": (
            d.get("cabinet_synced_at").isoformat()
            if hasattr(d.get("cabinet_synced_at"), "isoformat")
            else d.get("cabinet_synced_at")
        ),
    }


def _sort_sql(column: str, sort: str) -> str:
    direction = "ASC NULLS LAST" if str(sort or "").lower() in ("old", "asc", "oldest") else "DESC NULLS LAST"
    return f"ORDER BY {column} {direction}"


_OFFER_SQL_RE = (
    r"выслать[[:space:]]+детал|доотправ|заполните[[:space:]]+форму"
    r"|отправк[[:alpha:]]*[[:space:]]+детал|замен[[:alpha:]]*[[:space:]]+детал"
    r"|box\\.vidovito\\.com/reshipment/form"
)
_OFFER_SQL_DENY_RE = (
    r"не можем выслать|выслать не можем|оформите возврат"
    r"|детал(ью)?[[:space:]]+выслать[[:space:]]+не"
)
_OFFER_SQL_GREET_RE = r"здесь вы можете задать вопросы по возврату"


def _add_offer_keys(bucket: set, *values) -> None:
    for v in values:
        s = str(v or "").strip()
        if s:
            bucket.add(s)


def _match_claim_keys_sql(mp: str, keys, *, positive: bool = True) -> tuple[str, dict]:
    """Равенство по заранее собранным ключам — без коррелированных NOT EXISTS."""
    keys = {str(x).strip() for x in (keys or []) if str(x or "").strip()}
    if not keys:
        return ("FALSE" if positive else "TRUE"), {}
    params = {"offer_keys": list(keys)}
    m = (mp or "").lower()
    if m == "wb":
        expr = "NULLIF(TRIM(c.srid), '') = ANY(CAST(:offer_keys AS text[]))"
        cores = sorted({
            _wb_srid_core(x) for x in keys
            if len(_wb_srid_core(x)) >= 8
        })
        if cores:
            params["offer_cores"] = cores
            expr = (
                f"({expr} OR ("
                f"length(split_part(c.srid, '.', 2)) >= 8"
                f" AND split_part(c.srid, '.', 2) = ANY(CAST(:offer_cores AS text[]))"
                f"))"
            )
    elif m == "ym":
        expr = (
            "(c.return_id::text = ANY(CAST(:offer_keys AS text[]))"
            " OR c.order_id::text = ANY(CAST(:offer_keys AS text[])))"
        )
    else:
        expr = (
            "(COALESCE(c.posting_number, '') = ANY(CAST(:offer_keys AS text[]))"
            " OR COALESCE(c.order_number, '') = ANY(CAST(:offer_keys AS text[]))"
            " OR COALESCE(c.order_id::text, '') = ANY(CAST(:offer_keys AS text[])))"
        )
    if not positive:
        expr = f"(NOT {expr})"
    return expr, params


def _bare_chat_id(chat_id) -> str:
    cid = str(chat_id or "").strip()
    if not cid:
        return ""
    return cid.split(":", 1)[-1] if ":" in cid else cid


def _collect_offer_key_sets(conn, mp: str) -> dict[str, set[str]]:
    """Один проход: какие заказы с ссылкой / предложением / вопросом."""
    link, offered, question = set(), set(), set()
    m = (mp or "").lower()
    link_chats = set()
    try:
        if m == "wb":
            for r in conn.execute(text("""
                SELECT matched_srid, order_number, wb_chat_id
                FROM reshipment_requests
            """)).mappings():
                _add_offer_keys(link, r.get("matched_srid"), r.get("order_number"))
                bare = _bare_chat_id(r.get("wb_chat_id"))
                if bare:
                    link_chats.add(bare)
            for (cid,) in conn.execute(text(
                "SELECT chat_id FROM reshipment_chat_tokens WHERE used_at IS NOT NULL"
            )):
                bare = _bare_chat_id(cid)
                if bare:
                    link_chats.add(bare)
            if link_chats:
                rows = conn.execute(text("""
                    SELECT order_rid FROM wb_chats
                    WHERE NULLIF(TRIM(order_rid), '') IS NOT NULL
                      AND split_part(chat_id, ':', 2) = ANY(CAST(:ids AS text[]))
                """), {"ids": list(link_chats)}).fetchall()
                for (rid,) in rows:
                    _add_offer_keys(link, rid)
            card_rids: dict[str, set[str]] = {}
            for r in conn.execute(text("""
                SELECT chat_id, NULLIF(TRIM(attachments->'goodCard'->>'rid'), '') AS rid
                FROM wb_chat_messages
                WHERE NULLIF(TRIM(attachments->'goodCard'->>'rid'), '') IS NOT NULL
            """)).mappings():
                cid = str(r.get("chat_id") or "")
                rid = r.get("rid")
                if cid and rid:
                    card_rids.setdefault(cid, set()).add(rid)
            for r in conn.execute(text(f"""
                SELECT ch.chat_id, ch.order_rid,
                       bool_or(COALESCE(m.text, '') ~* 'box\\.vidovito\\.com/reshipment/form') AS has_form,
                       bool_or(COALESCE(m.text, '') ~* '{_OFFER_SQL_RE}'
                               AND COALESCE(m.text, '') !~* '{_OFFER_SQL_DENY_RE}') AS has_offer,
                       bool_or(COALESCE(m.text, '') ~* '{_OFFER_SQL_DENY_RE}') AS has_deny,
                       bool_or(length(trim(COALESCE(m.text, ''))) > 0
                               AND COALESCE(m.text, '') !~* '{_OFFER_SQL_GREET_RE}') AS has_talk
                FROM wb_chats ch
                JOIN wb_chat_messages m
                  ON m.chat_id = ch.chat_id AND m.sender = 'seller'
                GROUP BY ch.chat_id, ch.order_rid
            """)).mappings():
                cid = str(r.get("chat_id") or "")
                keys = [r.get("order_rid"), *card_rids.get(cid, ())]
                if r.get("has_form"):
                    _add_offer_keys(link, *keys)
                elif r.get("has_offer"):
                    _add_offer_keys(offered, *keys)
                elif r.get("has_talk") and not r.get("has_deny"):
                    _add_offer_keys(question, *keys)
        elif m == "ym":
            for r in conn.execute(text("""
                SELECT order_number, ym_chat_id FROM reshipment_requests
            """)).mappings():
                _add_offer_keys(link, r.get("order_number"))
                bare = _bare_chat_id(r.get("ym_chat_id"))
                if bare:
                    link_chats.add(bare)
            for (cid,) in conn.execute(text(
                "SELECT chat_id FROM reshipment_chat_tokens WHERE used_at IS NOT NULL"
            )):
                bare = _bare_chat_id(cid)
                if bare:
                    link_chats.add(bare)
            if link_chats:
                rows = conn.execute(text("""
                    SELECT return_id::text, order_id::text FROM ym_chats
                    WHERE split_part(chat_id, ':', 2) = ANY(CAST(:ids AS text[]))
                       OR chat_id = ANY(CAST(:ids AS text[]))
                """), {"ids": list(link_chats)}).fetchall()
                for ret, ordn in rows:
                    _add_offer_keys(link, ret, ordn)
            for r in conn.execute(text(f"""
                SELECT ch.return_id::text AS return_id, ch.order_id::text AS order_id,
                       bool_or(COALESCE(m.text, '') ~* 'box\\.vidovito\\.com/reshipment/form') AS has_form,
                       bool_or(COALESCE(m.text, '') ~* '{_OFFER_SQL_RE}'
                               AND COALESCE(m.text, '') !~* '{_OFFER_SQL_DENY_RE}') AS has_offer,
                       bool_or(COALESCE(m.text, '') ~* '{_OFFER_SQL_DENY_RE}') AS has_deny,
                       bool_or(length(trim(COALESCE(m.text, ''))) > 0
                               AND COALESCE(m.text, '') !~* '{_OFFER_SQL_GREET_RE}') AS has_talk
                FROM ym_chats ch
                JOIN ym_chat_messages m ON m.chat_id = ch.chat_id
                  AND lower(COALESCE(m.sender_type, '')) IN ('seller', 'partner')
                GROUP BY ch.return_id, ch.order_id
            """)).mappings():
                keys = (r.get("return_id"), r.get("order_id"))
                if r.get("has_form"):
                    _add_offer_keys(link, *keys)
                elif r.get("has_offer"):
                    _add_offer_keys(offered, *keys)
                elif r.get("has_talk") and not r.get("has_deny"):
                    _add_offer_keys(question, *keys)
        else:
            for r in conn.execute(text("""
                SELECT order_number, ozon_order_number, ozon_posting_number, ozon_chat_id
                FROM reshipment_requests
            """)).mappings():
                _add_offer_keys(
                    link,
                    r.get("order_number"),
                    r.get("ozon_order_number"),
                    r.get("ozon_posting_number"),
                )
                bare = _bare_chat_id(r.get("ozon_chat_id"))
                if bare:
                    link_chats.add(bare)
            for (cid,) in conn.execute(text(
                "SELECT chat_id FROM reshipment_chat_tokens WHERE used_at IS NOT NULL"
            )):
                bare = _bare_chat_id(cid)
                if bare:
                    link_chats.add(bare)
            if link_chats:
                rows = conn.execute(text("""
                    SELECT order_number FROM ozon_chats
                    WHERE chat_id = ANY(CAST(:ids AS text[]))
                """), {"ids": list(link_chats)}).fetchall()
                for (ordn,) in rows:
                    _add_offer_keys(link, ordn)
            for r in conn.execute(text(f"""
                SELECT ch.order_number,
                       bool_or(COALESCE(m.text, '') ~* 'box\\.vidovito\\.com/reshipment/form') AS has_form,
                       bool_or(COALESCE(m.text, '') ~* '{_OFFER_SQL_RE}'
                               AND COALESCE(m.text, '') !~* '{_OFFER_SQL_DENY_RE}') AS has_offer,
                       bool_or(COALESCE(m.text, '') ~* '{_OFFER_SQL_DENY_RE}') AS has_deny,
                       bool_or(length(trim(COALESCE(m.text, ''))) > 0
                               AND COALESCE(m.text, '') !~* '{_OFFER_SQL_GREET_RE}') AS has_talk
                FROM ozon_chats ch
                JOIN ozon_chat_messages m ON m.chat_id = ch.chat_id
                  AND lower(COALESCE(m.sender_type, '')) IN ('seller')
                WHERE NULLIF(TRIM(ch.order_number), '') IS NOT NULL
                GROUP BY ch.order_number
            """)).mappings():
                ordn = r.get("order_number")
                if r.get("has_form"):
                    _add_offer_keys(link, ordn)
                elif r.get("has_offer"):
                    _add_offer_keys(offered, ordn)
                elif r.get("has_talk") and not r.get("has_deny"):
                    _add_offer_keys(question, ordn)
    except Exception:
        logger.exception("[claims] offer key collect failed mp=%s", mp)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"link": set(), "offered": set(), "question": set(), "any": set()}

    offered -= link
    question -= link
    question -= offered
    any_keys = set(link) | set(offered) | set(question)
    return {"link": link, "offered": offered, "question": question, "any": any_keys}


def _offer_filter_bind(conn, mp: str, kind: str) -> tuple[str, dict]:
    k = str(kind or "").strip().lower()
    if k not in ("link", "offered", "question", "none"):
        return "", {}
    sets = _collect_offer_key_sets(conn, mp)
    if k == "none":
        return _match_claim_keys_sql(mp, sets.get("any"), positive=False)
    return _match_claim_keys_sql(mp, sets.get(k), positive=True)


def _offer_filter_sql(mp: str, kind: str, keys=None) -> str:
    """Совместимость для тестов: SQL по готовым ключам, без скана чатов."""
    k = str(kind or "").strip().lower()
    if k not in ("link", "offered", "question", "none"):
        return ""
    sql, _params = _match_claim_keys_sql(
        mp,
        keys if keys is not None else {"_"},
        positive=(k != "none"),
    )
    return sql


_OFFER_SCAN_CAP = 800


def _offer_kind_of_item(item: dict | None) -> str:
    """Тот же статус, что бейдж на карточке."""
    if not item:
        return "none"
    res = item.get("resolution") if isinstance(item.get("resolution"), dict) else {}
    if item.get("reshipment") or res.get("kind") == "reshipment_link":
        return "link"
    if res.get("kind") == "reshipment_offered":
        return "offered"
    if res.get("kind") == "open_question":
        return "question"
    return "none"


def _apply_offer_page(items: list, offer: str, page: int, per_page: int) -> tuple[list, int]:
    k = str(offer or "").strip().lower()
    if k in ("link", "offered", "question", "none"):
        items = [it for it in items if _offer_kind_of_item(it) == k]
    total = len(items)
    start = max(0, (max(1, int(page or 1)) - 1) * int(per_page or 25))
    return items[start:start + int(per_page or 25)], total


def _offer_scan_limits(offer: str, per_page: int, offset: int) -> tuple[bool, int, int]:
    k = str(offer or "").strip().lower()
    if k in ("link", "offered", "question", "none"):
        return True, _OFFER_SCAN_CAP, 0
    return False, per_page, offset


def _list_wb_claims(db: Session, *, active: bool, status: str, claim_type: str,
                    q: str, page: int, per_page: int, sort: str = "new",
                    offer: str = "") -> dict:
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
    order_sql = _sort_sql("c.claim_date", sort)
    scan_offer, lim, off = _offer_scan_limits(offer, per_page, offset)
    with db.bind.connect() as conn:
        offer_sql, offer_params = _offer_filter_bind(conn, "wb", offer)
        if offer_sql:
            filters.append(offer_sql)
            params.update(offer_params)
        where = " AND ".join(filters)
        total = 0
        if not scan_offer:
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
            {order_sql}
            LIMIT :lim OFFSET :off
        """), {**params, "lim": lim, "off": off}).mappings().all()
        items = [_claim_row_to_item(dict(r)) for r in rows]
        for it in items:
            it["mp"] = "wb"
        _fill_wb_claim_articles(conn, items)
        conn.commit()
    _attach_list_offer_status(db, "wb", items)
    if scan_offer:
        items, total = _apply_offer_page(items, offer, page, per_page)
    return {"items": items, "total": total or 0, "page": page,
            "total_pages": max(1, -(-(total or 0) // per_page)), "mp": "wb"}


def _list_ym_claims(db: Session, *, active: bool, status: str, q: str,
                    page: int, per_page: int, sort: str = "new",
                    offer: str = "") -> dict:
    from .. import ym_returns_api

    ym_returns_api.ensure_schema(db.bind)
    offset = (page - 1) * per_page
    filters, params = ["1=1"], {}
    active_list = sorted(ym_returns_api.ACTIVE_STATUSES)
    placeholders = ", ".join(f":as{i}" for i in range(len(active_list)))
    for i, s in enumerate(active_list):
        params[f"as{i}"] = s
    if active:
        filters.append(f"c.status IN ({placeholders})")
        filters.append("COALESCE(c.force_archive, false) = false")
    else:
        filters.append(
            f"(c.status NOT IN ({placeholders}) OR COALESCE(c.force_archive, false) = true)"
        )

    if status:
        st = status.strip()
        # Фильтр по русскому ярлыку или коду
        if st in (
            "На рассмотрении",
            "Ждёт нашего решения",
            "Ждём Яндекс",
            "Спор",
            "Ожидает возврат",
            "Одобрено",
            "Отказ",
            "Отменено",
        ):
            # Старое «На рассмотрении» = всё, где ещё нужно внимание (наш ответ + ждём ЯМ)
            if st == "На рассмотрении":
                codes = [
                    k for k, v in ym_returns_api.STATUS_RU.items()
                    if v in ("Ждёт нашего решения", "Ждём Яндекс", "Спор", "Ожидает возврат")
                ]
            else:
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
    order_sql = _sort_sql("c.created_at", sort)
    scan_offer, lim, off = _offer_scan_limits(offer, per_page, offset)
    with db.bind.connect() as conn:
        offer_sql, offer_params = _offer_filter_bind(conn, "ym", offer)
        if offer_sql:
            filters.append(offer_sql)
            params.update(offer_params)
        where = " AND ".join(filters)
        total = 0
        if not scan_offer:
            total = conn.execute(
                text(f"SELECT COUNT(*) FROM ym_returns c WHERE {where}"), params
            ).scalar()
        rows = conn.execute(text(f"""
            SELECT c.return_id, c.order_id, c.supplier_article, c.return_type,
                   c.status, c.status_ru, c.created_at, c.return_comment,
                   c.photos, c.reason_type, c.subreason_type,
                   c.correction, c.audit_status,
                   c.campaign_id, c.placement_type, c.force_archive,
                   pp.photo_url AS product_photo
            FROM ym_returns c
            LEFT JOIN product_photos pp ON pp.supplier_article = c.supplier_article
            WHERE {where}
            {order_sql}
            LIMIT :lim OFFSET :off
        """), {**params, "lim": lim, "off": off}).mappings().all()
    items = [_ym_row_to_item(dict(r)) for r in rows]
    _attach_list_offer_status(db, "ym", items)
    if scan_offer:
        items, total = _apply_offer_page(items, offer, page, per_page)
    return {"items": items, "total": total or 0, "page": page,
            "total_pages": max(1, -(-(total or 0) // per_page)), "mp": "ym"}


def _list_ozon_claims(db: Session, *, active: bool, status: str, q: str,
                      page: int, per_page: int, tab: str = "",
                      claim_type: str = "", sort: str = "new",
                      offer: str = "") -> dict:
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
    # Согласование Ozon: по умолчанию старые сверху (ближе к дедлайну), пока явно не выбрали «новые».
    sort_eff = sort
    if not sort_eff and tab == "approval":
        sort_eff = "old"
    if tab == "approval" and str(sort or "").lower() in ("", "default"):
        sort_eff = "old"
    order_sql = _sort_sql("COALESCE(c.status_changed_at, c.return_date)", sort_eff)
    scan_offer, lim, off = _offer_scan_limits(offer, per_page, offset)
    with db.bind.connect() as conn:
        offer_sql, offer_params = _offer_filter_bind(conn, "ozon", offer)
        if offer_sql:
            filters.append(offer_sql)
            params.update(offer_params)
        where = " AND ".join(filters)
        total = 0
        if not scan_offer:
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
                   c.buyer_comment, c.photos, c.cabinet_synced_at,
                   pp.photo_url AS product_photo
            FROM ozon_returns c
            LEFT JOIN product_photos pp ON pp.supplier_article = c.supplier_article
            WHERE {where}
            {order_sql}
            LIMIT :lim OFFSET :off
        """), {**params, "lim": lim, "off": off}).mappings().all()
    items = [_ozon_row_to_item(dict(r)) for r in rows]
    _attach_list_offer_status(db, "ozon", items)
    if scan_offer:
        items, total = _apply_offer_page(items, offer, page, per_page)
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
    sort: str = Query("new", description="new = сначала новые, old = сначала старые"),
    offer: str = Query("", description="link|offered|question|none — статус доотправки"),
    db: Session = Depends(get_db),
):
    """Список возвратов площадки с фильтрами: active, status, type, q, tab, sort, offer."""
    m = _normalize_mp(mp)
    if m == "ym":
        return _list_ym_claims(
            db, active=active, status=status, q=q, page=page, per_page=per_page,
            sort=sort, offer=offer,
        )
    if m == "ozon":
        return _list_ozon_claims(
            db, active=active, status=status, q=q, page=page, per_page=per_page,
            tab=tab, claim_type=claim_type, sort=sort, offer=offer,
        )
    return _list_wb_claims(
        db, active=active, status=status, claim_type=claim_type, q=q, page=page, per_page=per_page,
        sort=sort, offer=offer,
    )


class ClaimDecisionPayload(BaseModel):
    action: str
    comment: str = ""
    reason: str = ""  # YM: decisionReasonType при отказе
    staff_tag_ids: list[int] = []


def _wb_srid_core(srid: str) -> str:
    """Уникальная середина rid WB (`eB5.iaxxxx.0.0` → `iaxxxx`)."""
    s = (srid or "").strip()
    if not s:
        return ""
    parts = s.split(".")
    if len(parts) >= 2 and len(parts[1]) >= 8:
        return parts[1]
    return s


def _find_wb_chat_for_claim(conn, srid: str):
    srid = (srid or "").strip()
    if not srid:
        return None
    core = _wb_srid_core(srid)
    chat = conn.execute(text("""
        SELECT chat_id, client_name, needs_answer, last_ts
        FROM wb_chats
        WHERE :r <> '' AND (
            order_rid = :r
            OR (:core <> '' AND order_rid ILIKE '%' || :core || '%')
            OR EXISTS (
              SELECT 1 FROM wb_chat_messages m
              WHERE m.chat_id = wb_chats.chat_id
                AND (
                  m.attachments->'goodCard'->>'rid' = :r
                  OR (:core <> '' AND COALESCE(m.attachments->'goodCard'->>'rid', '')
                        ILIKE '%' || :core || '%')
                )
            )
        )
        ORDER BY last_ts DESC NULLS LAST
        LIMIT 1
    """), {"r": srid, "core": core}).mappings().first()
    if not chat:
        return None
    chat = dict(chat)
    if hasattr(chat.get("last_ts"), "isoformat"):
        chat["last_ts"] = chat["last_ts"].isoformat()
    return chat


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
        _fill_wb_claim_articles(conn, [item])
        conn.commit()

        chat = _find_wb_chat_for_claim(conn, item.get("srid") or "")

    live = wb_claims_api.fetch_claim_from_wb(claim_id)
    raw_actions = list((live or {}).get("actions") or [])
    live_status = (live or {}).get("status") if live else item.get("status")
    item["actions"] = wb_claims_api.map_actions(raw_actions, status=live_status or item.get("status"))
    item["actions_raw"] = raw_actions
    if live:
        from ..wb_mini_sync import _map_claim_status, _status_ex_label, persist_live_claim_status
        live_art = str(
            live.get("supplier_article") or live.get("supplierArticle") or live.get("saName") or ""
        ).strip()
        if live_art and not str(item.get("supplier_article") or "").strip():
            item["supplier_article"] = live_art
        wb_comment = str(live.get("wb_comment") or live.get("wbComment") or "")
        st_ex_raw = live.get("status_description") or live.get("status_ex") or ""
        mapped = _map_claim_status(live.get("status", ""), str(st_ex_raw), wb_comment)
        item["status"] = mapped
        live_ex = readable_status_ex(_status_ex_label(st_ex_raw, wb_comment))
        if live_ex:
            item["status_ex"] = live_ex
        else:
            from ..claim_auto_reject import is_auto_reject_comment
            if is_auto_reject_comment(item.get("status_ex")):
                item["status_ex"] = ""
            else:
                item["status_ex"] = readable_status_ex(item.get("status_ex"))
        item = _decode_claim(item)
        if mapped in ("Одобрено", "Отказ", "Отменена", "Закрыта"):
            item["is_archive"] = True
        if wb_comment and not item.get("wb_comment"):
            item["wb_comment"] = wb_comment
        item["actions"] = wb_claims_api.map_actions(raw_actions, status=item.get("status"))
        try:
            persist_live_claim_status(
                db,
                claim_id=claim_id,
                status=mapped,
                status_ex=item.get("status_ex") or wb_comment,
                srid=item.get("srid") or "",
                overwrite_ex=not bool(item.get("status_ex") or wb_comment),
            )
        except Exception:
            logger.exception("[WB Claims] persist live status %s", claim_id)

    item["chat"] = chat
    item["can_decide"] = bool(item.get("actions")) and any(a.get("primary") for a in item["actions"])
    item["can_change_decision"] = any(a.get("is_change") for a in (item.get("actions") or []))
    item["decision_note"] = ""
    item["cabinet_url"] = "https://seller.wildberries.ru/suppliers-returns"
    return _attach_claim_chat_extras(db, "wb", item)


def _detail_ym(db: Session, claim_id: str) -> dict:
    from .. import ym_returns_api

    ym_returns_api.ensure_schema(db.bind)
    with db.bind.connect() as conn:
        row = conn.execute(text("""
            SELECT c.return_id, c.order_id, c.supplier_article, c.return_type,
                   c.status, c.status_ru, c.created_at, c.return_comment,
                   c.photos, c.reason_type, c.subreason_type,
                   c.correction, c.audit_status,
                   c.campaign_id, c.placement_type, c.force_archive,
                   pp.photo_url AS product_photo
            FROM ym_returns c
            LEFT JOIN product_photos pp ON pp.supplier_article = c.supplier_article
            WHERE c.return_id::text = :cid
            LIMIT 1
        """), {"cid": claim_id}).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Возврат ЯМ не найден")
        item = _ym_row_to_item(dict(row))
        camp = row.get("campaign_id")
        force_arch = bool(row.get("force_archive"))

        chat = None
        oid = str(item.get("order_id") or "")
        rid = str(item.get("claim_id") or "")
        if oid or rid:
            try:
                chat = conn.execute(text("""
                    SELECT chat_id, customer_name AS client_name, needs_answer,
                           updated_at AS last_ts, cabinet_chat_id, context_type
                    FROM ym_chats
                    WHERE (:rid <> '' AND return_id::text = :rid)
                       OR (:oid <> '' AND order_id::text = :oid)
                    ORDER BY updated_at DESC NULLS LAST
                    LIMIT 1
                """), {"rid": rid, "oid": oid}).mappings().first()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
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

    # При открытии карточки сверяемся с ЯМ: иначе «На рассмотрении» зависает,
    # а кнопки пустые, потому что решение уже принято в кабинете.
    # force_archive — не оживляем статус с площадки.
    if not force_arch:
        live = ym_returns_api.fetch_return(
            item.get("order_id"), claim_id, campaign_id=camp,
        )
        live_status = ym_returns_api.effective_refund_status(live) if live else ""
        if live_status and live_status != str(item.get("status") or "").strip():
            try:
                ym_returns_api.apply_status_code(db, claim_id, live_status)
            except Exception:
                logger.exception("[YM Returns] refresh status failed claim=%s", claim_id)
            item["status"] = live_status
            item["status_label"] = ym_returns_api.status_ru(live_status)
            item["is_archive"] = not ym_returns_api.is_active_status(live_status)

    available = (
        [] if force_arch
        else ym_returns_api.fetch_available_decisions(claim_id, campaign_id=camp)
    )
    item["actions"] = ym_returns_api.map_actions(available)
    item["actions_raw"] = available
    item["chat"] = chat
    item["can_decide"] = bool(item.get("actions")) and any(a.get("primary") for a in item["actions"])
    item["can_change_decision"] = False
    if force_arch:
        item["decision_note"] = "Заявка скрыта из активных как неактуальная."
        item["is_archive"] = True
    elif item["can_decide"]:
        item["decision_note"] = ""
    elif str(item.get("status") or "") == "PREMODERATION_DECISION_MADE":
        item["decision_note"] = (
            "Решение уже ушло в Яндекс. Покупатель может открыть спор — "
            "пока окно спора открыто, заявка остаётся в активных (как в ЛК ЯМ)."
        )
    elif str(item.get("status") or "") == "PREMODERATION_SELECT_DELIVERY":
        item["decision_note"] = (
            "Возврат уже одобрен — часто автоматически, без нашего решения. "
            "Сейчас покупатель выбирает, как отправить товар. "
            "Действий с нашей стороны нет."
        )
    elif str(item.get("status") or "") == "PREMODERATION_DISPUTE":
        item["decision_note"] = "Покупатель открыл спор. Дальше — в кабинете Яндекс Маркета / чате с арбитром."
    elif not ym_returns_api.is_active_status(item.get("status")):
        label = item.get("status_label") or ym_returns_api.status_ru(item.get("status"))
        item["decision_note"] = (
            f"В Яндексе уже решено: {label}. Менять решение здесь нельзя."
        )
    else:
        item["decision_note"] = (
            "Яндекс пока не открыл действия по этой заявке "
            "(или окно решения уже закрыто)."
        )
    item["cabinet_url"] = "https://partner.market.yandex.ru/"
    return _attach_claim_chat_extras(db, "ym", item)


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
                   c.buyer_comment, c.photos, c.cabinet_synced_at,
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
                       c.buyer_comment, c.photos, c.cabinet_synced_at,
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
    return _attach_claim_chat_extras(db, "ozon", item)


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

    - WB: кабинетный Chrome на сервере ищет заявку по номеру и открывает чат.
    - ЯМ: POST …/chats/new {type:RETURN|ORDER, id}
    - Ozon: POST /v1/chat/start {posting_number} (Premium+)
    """
    m = _normalize_mp(mp)

    if m == "wb":
        from .. import wb_cabinet_bot

        bot = wb_cabinet_bot.ensure_wb_claim_chat(db.bind, claim_id, wait_lock=True)
        if bot.get("missing"):
            raise HTTPException(status_code=404, detail="Претензия не найдена")
        cid = str((bot or {}).get("chat_id") or "").strip()
        if not cid:
            raise HTTPException(
                status_code=502,
                detail=(bot or {}).get("error")
                or "Не удалось открыть чат WB через кабинет. Если сессия слетела — нужно снова войти в Chrome бота.",
            )
        return {"ok": True, "mp": "wb", "chat_id": cid, "created": bool((bot or {}).get("created"))}

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

    result = wb_claims_api.decide_claim(
        db, claim_id, action, payload.comment or "", payload.reason or "",
    )
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


def _fill_reshipment_eta(d: dict) -> dict:
    """Подтянуть ориентировочную дату и номер заказа Ozon, если в заявке пусто."""
    if d.get("eta_date") and not (
        (d.get("delivery_method") or "") == "ozon" and not (d.get("ozon_order_number") or "").strip()
    ):
        return d
    method = (d.get("delivery_method") or "").strip()
    try:
        if method == "ozon":
            from .. import ozon_delivery
            info = ozon_delivery.get_order_status(
                d.get("ozon_posting_number") or d.get("ozon_order_number") or d.get("track_number") or ""
            )
            posting = info.get("posting") or {}
            if posting.get("order_number"):
                d["ozon_order_number"] = posting["order_number"]
            if posting.get("posting_number"):
                d["ozon_posting_number"] = posting["posting_number"]
            d["eta_date"] = d.get("eta_date") or info.get("eta_raw") or posting.get("delivery_at")
        elif method == "yandex":
            from .. import yandex_ndd
            info = yandex_ndd.get_order_status(d.get("track_number") or "")
            d["eta_date"] = d.get("eta_date") or info.get("eta_raw")
        elif d.get("cdek_uuid"):
            from ..cdek import get_order_by_uuid
            info = get_order_by_uuid(d["cdek_uuid"])
            d["eta_date"] = d.get("eta_date") or info.get("planned_delivery_date")
    except Exception:
        pass
    return d


def _reshipment_chat_card(row: dict) -> dict:
    """Сводка доотправки для шапки чата."""
    from ..salebot import format_eta_date
    d = _fill_reshipment_eta(dict(row))
    st = (d.get("status") or "").strip()
    method = (d.get("delivery_method") or "").strip()
    ozon_order = (d.get("ozon_order_number") or "").strip()
    if method == "ozon":
        pvz = (d.get("ozon_pvz_address") or d.get("client_pvz_address") or "").strip()
        track_number = ""
        order_number = ozon_order or (d.get("track_number") or "").strip()
        marketplace_order = ""
    elif method == "yandex":
        pvz = (d.get("yandex_pvz_address") or d.get("client_pvz_address") or "").strip()
        track_number = (d.get("track_number") or "").strip()
        order_number = ""
        marketplace_order = d.get("matched_srid") or ""
    else:
        pvz = (d.get("client_pvz_address") or "").strip()
        track_number = (d.get("track_number") or d.get("cdek_number") or "").strip()
        order_number = ""
        marketplace_order = d.get("matched_srid") or ""
    return {
        "id": d["id"],
        "status": st,
        "status_label": _RESHIP_STATUS_RU.get(st, st or "—"),
        "created_date": _ru_date(d.get("created_at")),
        "track_number": track_number,
        "order_number": order_number,
        "delivery_method": method,
        "pvz": pvz,
        "eta_label": format_eta_date(d.get("eta_date")),
        "customer_name": d.get("customer_name") or "",
        "matched_srid": marketplace_order,
        "marketplace": (d.get("marketplace") or "wb").lower(),
    }


def _find_reshipment_for_chat(
    conn, chat_id: str, *, mp: str = "wb", srid: str = "", order_n: str = "",
) -> dict | None:
    """Заявка на доотправку, привязанная к этому чату или заказу."""
    row, _ = _lookup_reshipment_row(
        conn, mp=mp, chat_id=chat_id, srid=srid, order_n=order_n,
    )
    if not row:
        return None
    return _reshipment_chat_card(row)


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

        specific_reshipment = _find_reshipment_for_chat(
            conn, resolved, mp="wb", srid=rid or "",
        )
        if not specific_reshipment and resolved != chat_id:
            specific_reshipment = _find_reshipment_for_chat(
                conn, chat_id, mp="wb", srid=rid or "",
            )
        specific_order = chat_order.resolve_chat_order(
            conn, "wb", resolved, rid=rid or "",
        )

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
            "order": specific_order,
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
        "return_number": str(d.get("return_id") or d.get("posting_number") or d.get("id") or ""),
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
        "return_number": str(d.get("return_id") or d.get("id") or ""),
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

        specific_reshipment = _find_reshipment_for_chat(
            conn, chat_id, mp="ozon", srid=order_number or "", order_n=order_number or "",
        )
        specific_order = chat_order.resolve_chat_order(
            conn, "ozon", chat_id, order_n=order_number or "",
        )

    return {
        "specific": {
            "claim": specific_claim,
            "review": None,
            "reshipment": specific_reshipment,
            "order": specific_order,
        },
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

        specific_reshipment = _find_reshipment_for_chat(
            conn, chat_id, mp="ym", srid=order_id or "", order_n=order_id or "",
        )
        specific_order = chat_order.resolve_chat_order(
            conn, "ym", chat_id, order_n=order_id or "",
        )

    return {
        "specific": {
            "claim": specific_claim,
            "review": None,
            "reshipment": specific_reshipment,
            "order": specific_order,
        },
        "reviews": [],
        "questions": [],
        "claims": claims,
    }


_WB_BASKET_MAX = 60


def _backfill_missing_wb_chat_photos(get_db_func) -> int:
    """CDN-фото для nm_id из живых чатов, которых нет в кэше Content API."""
    db = next(get_db_func())
    saved = 0
    try:
        with db.bind.connect() as conn:
            rows = conn.execute(text("""
                SELECT DISTINCT c.nm_id
                FROM wb_chats c
                WHERE c.nm_id IS NOT NULL
                  AND c.last_ts > EXTRACT(epoch FROM now() - INTERVAL '30 days') * 1000
                  AND NOT EXISTS (
                      SELECT 1 FROM wb_product_photos p WHERE p.nm_id = c.nm_id
                  )
            """)).fetchall()
        for (nm,) in rows:
            try:
                nm_id = int(nm)
            except (TypeError, ValueError):
                continue
            url = _probe_wb_cdn_photo(nm_id)
            if not url:
                continue
            with db.bind.connect() as conn:
                conn.execute(text("""
                    INSERT INTO wb_product_photos (nm_id, photo_url, updated_at)
                    VALUES (:nm, :url, now())
                    ON CONFLICT (nm_id) DO UPDATE
                    SET photo_url = EXCLUDED.photo_url, updated_at = now()
                """), {"nm": nm_id, "url": url})
                conn.commit()
            saved += 1
    finally:
        db.close()
    return saved


def _wb_basket_host_num(vol: int) -> int:
    """Стартовая догадка шарда CDN. Таблица плывёт — рядом всегда пробуем соседей."""
    if vol <= 143:
        return 1
    if vol <= 287:
        return 2
    if vol <= 431:
        return 3
    if vol <= 575:
        return 4
    if vol <= 719:
        return 5
    if vol <= 1007:
        return 6
    if vol <= 1061:
        return 7
    if vol <= 1115:
        return 8
    if vol <= 1169:
        return 9
    if vol <= 1313:
        return 10
    if vol <= 1601:
        return 11
    if vol <= 1655:
        return 12
    if vol <= 1919:
        return 13
    if vol <= 2045:
        return 14
    if vol <= 2189:
        return 15
    if vol <= 2405:
        return 16
    if vol <= 2621:
        return 17
    if vol <= 2837:
        return 18
    if vol <= 3053:
        return 19
    if vol <= 3269:
        return 20
    if vol <= 3485:
        return 21
    if vol <= 3701:
        return 22
    if vol <= 3917:
        return 23
    if vol <= 4133:
        return 24
    if vol <= 4349:
        return 25
    if vol <= 4565:
        return 26
    if vol <= 4877:
        return 27
    if vol <= 5189:
        return 28
    if vol <= 5501:
        return 29
    if vol <= 5717:
        return 30
    # После vol 5501 WB добавляет шарды. Шаг ~450, потолок — живые basket-NN.
    return min(_WB_BASKET_MAX, 30 + max(1, (int(vol) - 5501) // 450))


def _wb_cdn_photo_url(nm_id: int, host: int | None = None) -> str:
    """Публичный CDN WB (без Content API) — запасной вариант превью."""
    nm = int(nm_id)
    vol = nm // 100000
    part = nm // 1000
    n = int(host or _wb_basket_host_num(vol))
    nn = f"{n:02d}" if n < 10 else str(n)
    return f"https://basket-{nn}.wbbasket.ru/vol{vol}/part{part}/{nm}/images/c246x328/1.webp"


_WB_VOL_HOST_CACHE: dict[int, int] = {}


def _http_ok(url: str) -> bool:
    try:
        r = requests.get(url, timeout=6, stream=True)
        ok = r.status_code == 200
        r.close()
        return ok
    except Exception:
        return False


def _probe_wb_cdn_photo(nm_id: int) -> str | None:
    """Таблица шардов WB устаревает. Пробуем догадку и соседей, кэшируем живой хост по vol."""
    nm = int(nm_id)
    vol = nm // 100000
    guessed = int(_wb_basket_host_num(vol))
    hosts: list[int] = []
    seen: set[int] = set()

    def _add(h) -> None:
        try:
            n = int(h)
        except (TypeError, ValueError):
            return
        if 1 <= n <= _WB_BASKET_MAX and n not in seen:
            seen.add(n)
            hosts.append(n)

    _add(_WB_VOL_HOST_CACHE.get(vol))
    _add(guessed)
    for delta in range(1, _WB_BASKET_MAX):
        _add(guessed - delta)
        _add(guessed + delta)
    for h in hosts:
        url = _wb_cdn_photo_url(nm, h)
        if _http_ok(url):
            _WB_VOL_HOST_CACHE[vol] = h
            return url
    return None


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
        url = _probe_wb_cdn_photo(nm_id)
    if not url:
        return None
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


@public_router.get("/wb-photo/{nm_id}")
def wb_product_photo(nm_id: str, db: Session = Depends(get_db)):
    """Фото товара WB — редирект на CDN URL из кэша (с догрузкой при промахе).

    Публичный: <img src> в чатах не умеет слать Bearer, иначе все превью дают 401.
    """
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
    """Прокси вложений ЯМ (partner API и files.messenger.yandex.net)."""
    from urllib.parse import urlparse
    if not url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Недопустимый URL файла ЯМ")
    host = (urlparse(url).hostname or "").lower()
    if "yandex." not in host and not host.endswith("yandex.net") and "yandex" not in host:
        raise HTTPException(status_code=400, detail="Недопустимый URL файла ЯМ")
    try:
        r = requests.get(
            url,
            headers=ym_client.headers(),
            timeout=60,
            stream=True,
            allow_redirects=True,
        )
        r.raise_for_status()
    except requests.HTTPError as e:
        logger.error("[YmChatFile] %s → %s", url[:120], e.response.status_code if e.response else "?")
        raise HTTPException(status_code=502, detail=str(e))
    mime = r.headers.get("Content-Type") or "application/octet-stream"
    headers = {}
    length = r.headers.get("Content-Length")
    if length:
        headers["Content-Length"] = length

    def _chunks():
        try:
            for chunk in r.iter_content(64 * 1024):
                if chunk:
                    yield chunk
        finally:
            r.close()

    return StreamingResponse(_chunks(), media_type=mime, headers=headers)


# ── ИИ черновик ответа ───────────────────────────────────────────────────────

def _make_ai_draft(*, item_type, mp, body, rating, pros, cons, sku, has_media, db):
    from ..llm import complete_sync

    mp_label = {"wb": "Wildberries", "ym": "Яндекс Маркет", "ozon": "Ozon"}.get(mp, mp)
    type_label = {"review": "отзыв", "question": "вопрос", "chat": "сообщение в чате"}.get(item_type, item_type)

    parts = []
    if sku:
        parts.append(f"Артикул: {sku}")
        if item_type == "review":
            facts = article_facts_for_ai(sku)
            if facts:
                parts.append(facts)
    if rating:
        parts.append(f"Оценка: {rating}/5")
    if pros:
        parts.append(f"Плюсы: {pros}")
    if cons:
        parts.append(f"Минусы: {cons}")
    if body:
        parts.append(f"Текст: {body}")

    context = "\n".join(parts) if parts else "Текст не указан"
    photos_flag = bool(str(has_media or "").strip() in ("1", "true", "yes", "on"))
    reply_mode = classify_review_reply(rating, body, ["x"] if photos_flag else None, pros, cons)

    reference = ""
    if item_type == "review" and sku and should_ground_constructor(reply_mode, body or "", rating):
        try:
            from . import kb
            full = ". ".join(p for p in [body, pros, cons] if p)
            ans, scen, _manual, _meta = kb.build_answer(db, sku, full, rating)
            if ans:
                reference = (
                    f"\n\nФирменный образец ({scen}) — возьми структуру и факты, "
                    f"но перефразируй коротко, без лишних эмоций:\n«{ans}»"
                )
        except Exception:
            reference = ""

    system = REVIEW_AI_SYSTEM if item_type == "review" else (
        "Ты менеджер клиентского сервиса бренда товаров для дома Vidovito на маркетплейсе. "
        "Пиши живо, тепло и по-человечески, без канцелярита и клише. "
        "Отвечай только на русском языке. "
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

    from ..qwen_quota import is_quota_or_model_error

    try:
        draft = complete_sync(
            system=system,
            user=user_msg,
            model_key="qwen-plus",
            temperature=0.4,
            max_tokens=500,
        ).strip()
    except Exception as e:
        last_err = str(e)
        if is_quota_or_model_error(last_err):
            raise HTTPException(
                status_code=502,
                detail="Сейчас ИИ недоступен — закончился бесплатный лимит. Напишите ответ вручную или нажмите шаблон.",
            )
        raise HTTPException(status_code=502, detail="ИИ недоступен. Попробуйте ещё раз или напишите вручную.")
    if item_type == "review" and sku:
        draft = apply_article_hints(sku, draft, rating)
    if item_type == "review":
        from .kb import ensure_review_signature
        draft = ensure_review_signature(draft)
    else:
        draft += "\n\nАлександр, команда Видовито"
    return {"draft": draft}


@router.get("/draft")
def get_ai_draft(
    item_type: str = Query(..., pattern="^(review|question|chat)$"),
    mp: str        = Query("wb", pattern="^(wb|ym|ozon)$"),
    body: str      = Query(""),
    rating: int    = Query(0),
    pros: str      = Query(""),
    cons: str      = Query(""),
    sku: str       = Query(""),
    has_media: str = Query(""),
    db: Session    = Depends(get_db),
):
    """Генерирует черновик ответа через Qwen Plus, опираясь на фирменную базу шаблонов Vidovito."""
    return _make_ai_draft(
        item_type=item_type, mp=mp, body=body, rating=rating,
        pros=pros, cons=cons, sku=sku, has_media=has_media, db=db,
    )


class ReviewDraftReq(BaseModel):
    item_type: str = "review"
    mp: str = "wb"
    body: str = ""
    rating: int = 0
    pros: str = ""
    cons: str = ""
    sku: str = ""
    has_media: str = ""


@router.post("/draft")
def post_ai_draft(req: ReviewDraftReq, db: Session = Depends(get_db),
                  current_user: dict = Depends(get_current_user)):
    """Тот же черновик ИИ, но текстом в теле — длинный отзыв не обрезает URL."""
    item_type = (req.item_type or "review").strip()
    if item_type not in ("review", "question", "chat"):
        raise HTTPException(status_code=400, detail="Некорректный тип")
    mp = (req.mp or "wb").strip()
    if mp not in ("wb", "ym", "ozon"):
        raise HTTPException(status_code=400, detail="Некорректная площадка")
    return _make_ai_draft(
        item_type=item_type, mp=mp, body=req.body or "", rating=req.rating or 0,
        pros=req.pros or "", cons=req.cons or "", sku=req.sku or "",
        has_media=req.has_media or "", db=db,
    )


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
    with db.bind.connect() as conn:
        if mp == "ozon":
            conn.execute(text("""
                UPDATE ozon_chats SET needs_answer = false, last_sender = 'seller',
                    last_message = :preview, unread_count = 0, waiting_since = NULL,
                    updated_at = now()
                WHERE chat_id = :cid
            """), {"cid": chat_id, "preview": preview[:500]})
        elif mp == "ym":
            conn.execute(text("""
                UPDATE ym_chats SET needs_answer = false, last_sender = 'seller',
                    last_message = :preview, waiting_since = NULL,
                    status = 'WAITING_FOR_CUSTOMER', updated_at = now()
                WHERE chat_id = :cid
            """), {"cid": chat_id, "preview": preview[:500]})
        else:
            conn.execute(text("""
                UPDATE wb_chats SET needs_answer = false, last_sender = 'seller',
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
        if kind == "text":
            from .. import chat_bot
            chat_bot.bind_form_tokens_to_chat(conn, value, chat_id, mp)
        conn.commit()
    _publish_seller_replied(mp, chat_id, preview)


def _publish_seller_replied(mp: str, chat_id: str, preview: str) -> None:
    """После ответа менеджера — мгновенно обновить строку в списке чатов."""
    try:
        from .. import chat_realtime
        chat_realtime.publish_chat_updated({
            "chat_id": chat_id,
            "mp": mp,
            "last_message": (preview or "")[:500],
            "last_sender": "seller",
            "needs_answer": False,
            "last_ts": int(time.time() * 1000),
        })
    except Exception:
        pass


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


async def _run_ym_chat_send_steps(
    chat_id: str,
    message: str,
    attachments: list[dict],
    persist_step,
) -> dict:
    """Текст и файлы ЯМ — отдельные вызовы API, с явной прогресс-моделью как у Ozon."""
    from .. import ym_chat_sync

    progress = _new_chat_send_progress(message, attachments)
    text_step = progress["text"]
    if message:
        try:
            await run_in_threadpool(ym_chat_sync.send_message, chat_id, message)
        except Exception as exc:
            return _stop_chat_send_progress(progress, text_step, exc)
        text_step["state"] = "confirmed"
        if not await _persist_or_stop_progress(progress, text_step, persist_step, "text", message):
            return progress

    for index, attachment in enumerate(attachments):
        file_step = progress["files"][index]
        try:
            await run_in_threadpool(
                ym_chat_sync.send_file,
                chat_id,
                attachment["name"],
                attachment["content"],
                content_type=attachment["content_type"],
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


_SIMILAR_STOP = {
    "этот", "этого", "какой", "какая", "какие", "можно", "нужно", "после",
    "товар", "товара", "пожалуйста", "подскажите", "здравствуйте", "добрый",
}
# Длинные окончания раньше коротких: «вешалку» и «вешалка» сходятся в «вешалк».
_SIMILAR_ENDINGS = (
    "иями", "ями", "ами", "ого", "ему", "ыми", "ими",
    "ой", "ей", "ий", "ый", "ая", "яя", "ое", "ее", "ые", "ие", "ую", "юю",
    "ов", "ев", "ам", "ям", "ом", "ем", "ах", "ях",
    "ть", "ти", "ся", "сь",
    "а", "я", "у", "ю", "е", "и", "ы", "о",
)


def _similar_stem(word: str) -> str:
    w = (word or "").lower().strip()
    if len(w) < 5:
        return w
    for end in _SIMILAR_ENDINGS:
        if w.endswith(end) and len(w) - len(end) >= 4:
            return w[: -len(end)]
    return w


def _similar_question_tokens(text: str) -> list[str]:
    words = re.findall(r"[а-яёa-z0-9]{4,}", (text or "").lower())
    out = []
    seen = set()
    for w in words:
        if w in _SIMILAR_STOP:
            continue
        stem = _similar_stem(w)
        if len(stem) < 4 or stem in _SIMILAR_STOP or stem in seen:
            continue
        seen.add(stem)
        out.append(stem)
        if len(out) >= 6:
            break
    return out


def _similar_match_score(text, answer, tokens) -> int:
    """Смысл важнее категории: совпадение в вопросе весит больше, чем в ответе."""
    question = (text or "").lower()
    reply = (answer or "").lower()
    score = 0
    for token in tokens or []:
        word = _similar_stem(token)
        if len(word) < 4:
            continue
        if word in question:
            score += 3
        if word in reply:
            score += 1
    return score


def _group_similar_by_category(rows, categories, tokens=None, per_category=6, max_groups=4):
    """Сначала отбор по словам вопроса, потом группы по категории товара.

    В каждой категории остаётся несколько ответов, лучший — первым.
    Слабые совпадения и длинный хвост категорий не показываем.
    """
    tokens = [t for t in (tokens or []) if t]
    scored = []
    for row in rows:
        score = _similar_match_score(row.get("text"), row.get("answer"), tokens)
        scored.append((score, row))
    if tokens:
        best = max((score for score, _row in scored), default=0)
        floor = 3 if len(tokens) >= 2 else 1
        if best >= floor:
            scored = [(score, row) for score, row in scored if score >= floor]
    scored.sort(key=lambda pair: pair[0], reverse=True)

    buckets = {}
    for score, row in scored:
        sku = str((row.get("sku") if isinstance(row, dict) else "") or "").strip().lower()
        cat = (categories or {}).get(sku) or "Без категории"
        buckets.setdefault(cat, [])
        if len(buckets[cat]) >= per_category:
            continue
        buckets[cat].append({
            "id": row.get("id"),
            "mp": row.get("mp"),
            "sku": row.get("sku") or "",
            "text": row.get("text") or "",
            "answer": row.get("answer") or "",
            "category": cat,
            "score": score,
        })
    ranked = sorted(
        buckets.items(),
        key=lambda item: max((row.get("score") or 0) for row in item[1]),
        reverse=True,
    )
    named = [(cat, items) for cat, items in ranked if cat != "Без категории"]
    chosen = named[:max_groups]
    unnamed = buckets.get("Без категории")
    if unnamed and len(chosen) < max_groups:
        chosen.append(("Без категории", unnamed))
    return [{"category": cat, "items": items} for cat, items in chosen]


def _assortment_categories(db, skus) -> dict:
    arts = []
    seen = set()
    for raw in skus:
        art = str(raw or "").strip()
        key = art.lower()
        if not art or key in seen:
            continue
        seen.add(key)
        arts.append(art)
    if not arts:
        return {}
    try:
        rows = db.execute(text("""
            SELECT supplier_article, category_1
            FROM wb_assortment
            WHERE supplier_article IN :arts
              AND coalesce(category_1, '') <> ''
        """).bindparams(bindparam("arts", expanding=True)), {"arts": arts}).mappings().all()
    except Exception:
        return {}
    out = {}
    for row in rows:
        key = str(row.get("supplier_article") or "").strip().lower()
        if key and key not in out:
            out[key] = str(row.get("category_1") or "").strip()
    return out


@router.get("/questions/similar")
def similar_questions(
    q: str = Query(""),
    mp: str = Query("all"),
    exclude: str = Query(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Уже отвеченные похожие вопросы — чтобы не писать одно и то же заново."""
    tokens = _similar_question_tokens(q)
    if len(tokens) < 1:
        return {"items": [], "groups": []}
    m = _normalize_mp(mp) if mp and mp != "all" else ""
    params = {f"w{i}": f"%{tok}%" for i, tok in enumerate(tokens)}
    params["ex"] = str(exclude or "").strip()
    unions = []
    if m in ("", "wb"):
        unions.append(f"""
            SELECT id::text AS id, 'wb' AS mp, supplier_article AS sku,
                   text AS qtext, answer_text, created_date
            FROM wb_questions
            WHERE state = 'answered'
              AND coalesce(answer_text, '') <> ''
              AND id::text <> :ex
        """)
    if m in ("", "ym"):
        unions.append(f"""
            SELECT question_id::text AS id, 'ym' AS mp, supplier_article AS sku,
                   text AS qtext, answer_text, created_date
            FROM ym_questions
            WHERE state = 'ANSWERED'
              AND coalesce(answer_text, '') <> ''
              AND question_id::text <> :ex
        """)
    if m in ("", "ozon"):
        unions.append(f"""
            SELECT question_id AS id, 'ozon' AS mp, supplier_article AS sku,
                   text AS qtext, answer_text, published_at AS created_date
            FROM ozon_questions
            WHERE status = 'PROCESSED'
              AND coalesce(answer_text, '') <> ''
              AND question_id <> :ex
        """)
    if not unions:
        return {"items": [], "groups": []}
    where_outer = " AND (" + " OR ".join(
        [f"x.qtext ILIKE :w{i} OR x.answer_text ILIKE :w{i}" for i in range(len(tokens))]
    ) + ")"
    rank = " + ".join(
        f"(CASE WHEN x.qtext ILIKE :w{i} THEN 3 ELSE 0 END)"
        f" + (CASE WHEN x.answer_text ILIKE :w{i} THEN 1 ELSE 0 END)"
        for i in range(len(tokens))
    )
    sql = f"""
        SELECT id, mp, sku, left(qtext, 240) AS text, left(answer_text, 500) AS answer
        FROM (
            {' UNION ALL '.join(unions)}
        ) x
        WHERE 1=1 {where_outer}
        ORDER BY ({rank}) DESC, created_date DESC NULLS LAST
        LIMIT 80
    """
    try:
        rows = [dict(r) for r in db.execute(text(sql), params).mappings().all()]
    except Exception:
        return {"items": [], "groups": []}
    categories = _assortment_categories(db, [r.get("sku") for r in rows])
    groups = _group_similar_by_category(rows, categories, tokens)
    items = [g["items"][0] for g in groups if g.get("items")]
    return {"items": items, "groups": groups}


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
        gone = False
    except requests.HTTPError as e:
        if ym_client.feedback_gone_from_cabinet(e):
            gone = True
            txt = ""
        else:
            raise HTTPException(status_code=502, detail=f"ЯМ API {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    with db.bind.connect() as conn:
        if gone:
            conn.execute(text(
                "UPDATE ym_feedbacks SET answered_at = now() WHERE feedback_id = :fid AND answered_at IS NULL"
            ), {"fid": feedback_id})
        else:
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

    return {"status": "ok", "gone": gone}


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


# ── Ответ на вопрос Ozon ─────────────────────────────────────────────────────

@router.post("/ozon/questions/{question_id}/answer")
def answer_ozon_question(
    question_id: str,
    payload: AnswerPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    txt = payload.text.strip()
    if not 2 <= len(txt) <= 3000:
        raise HTTPException(status_code=400, detail="Ответ Ozon должен содержать от 2 до 3000 символов")
    if not _oz_key() or not _oz_cid():
        raise HTTPException(status_code=503, detail="OZON_API_KEY / OZON_CLIENT_ID не настроены")

    with db.bind.connect() as conn:
        question = conn.execute(text("""
            SELECT sku, status
            FROM ozon_questions
            WHERE question_id = :qid
        """), {"qid": question_id}).mappings().first()
    if not question:
        raise HTTPException(status_code=404, detail="Вопрос Ozon не найден")
    if question["status"] not in ("NEW", "VIEWED", "UNPROCESSED"):
        raise HTTPException(status_code=409, detail="Вопрос Ozon уже обработан")

    try:
        ozon_client.answer_question(question_id, int(question["sku"]), txt, timeout=20)
    except requests.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Ozon API {exc.response.status_code}: {exc.response.text[:200]}",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    with db.bind.connect() as conn:
        conn.execute(text("""
            UPDATE ozon_questions
            SET status = 'PROCESSED', answer_text = :answer,
                answered_at = NOW(), synced_at = NOW()
            WHERE question_id = :qid
        """), {"qid": question_id, "answer": txt})
        conn.execute(text("""
            INSERT INTO cx_response_log (item_type, item_id, mp, answered_by, answer_text)
            VALUES ('question', :qid, 'ozon', :user, :answer)
        """), {
            "qid": question_id,
            "user": current_user["username"],
            "answer": txt,
        })
        conn.execute(text("""
            DELETE FROM cx_pending_moderation
            WHERE item_type = 'question' AND mp = 'ozon' AND item_id = :qid
        """), {"qid": question_id})
        conn.commit()

    return {"status": "ok", "visibility": "public"}


# ── Ответ на отзыв Ozon ──────────────────────────────────────────────────────

def _oz_key() -> str:
    return ozon_client.api_key()


def _oz_cid() -> str:
    return ozon_client.client_id()


def _queue_ozon_review_reply(conn, review_id: str, answer: str, user: str) -> None:
    """Кладёт ответ в очередь кабинетного бота. Повтор обновляет текст, а не теряет его."""
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS ozon_review_outbox (
            id SERIAL PRIMARY KEY,
            review_id TEXT NOT NULL,
            answer_text TEXT NOT NULL,
            requested_by TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            sent_at TIMESTAMPTZ
        )
    """))
    updated = conn.execute(text("""
        UPDATE ozon_review_outbox
        SET answer_text = :ans, requested_by = :user, error = NULL
        WHERE review_id = :rid AND status = 'pending'
    """), {"ans": answer, "rid": review_id, "user": user})
    if updated.rowcount:
        return
    conn.execute(text("""
        INSERT INTO ozon_review_outbox (review_id, answer_text, requested_by, status)
        SELECT :rid, :ans, :user, 'pending'
        WHERE NOT EXISTS (
            SELECT 1 FROM ozon_review_outbox
            WHERE review_id = :rid AND status = 'pending'
        )
    """), {"rid": review_id, "ans": answer, "user": user})


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

    api_ok = False
    try:
        ozon_client.answer_review(review_id, txt, timeout=20)
        api_ok = True
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        body = (e.response.text or "")[:200] if e.response is not None else ""
        if code != 403:
            raise HTTPException(status_code=502, detail=f"Ozon API {code}: {body}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    with db.bind.connect() as conn:
        if api_ok:
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
            return {"status": "ok", "via": "seller_api"}

        _queue_ozon_review_reply(conn, review_id, txt, current_user["username"])
        conn.commit()
        return {"status": "queued", "via": "cabinet_bot"}


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
        from .. import chat_bot
        chat_bot.bind_form_tokens_to_chat(conn, txt, chat_id, "ozon")
        conn.commit()

    _publish_seller_replied("ozon", chat_id, txt)
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
        from .. import chat_bot
        chat_bot.bind_form_tokens_to_chat(conn, txt, chat_id, "ym")
        conn.commit()

    _publish_seller_replied("ym", chat_id, txt)
    return {"status": "ok"}


@router.post("/ym/chats/{chat_id}/message-with-files")
async def send_ym_chat_message_with_files(
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
    if not _ym_key() or not _ym_business_id():
        raise HTTPException(status_code=503, detail="YM_API_KEY / YM_BUSINESS_ID не настроены")

    def persist_step(kind: str, value: str) -> None:
        _persist_confirmed_chat_step(
            db,
            mp="ym",
            chat_id=chat_id,
            username=current_user["username"],
            kind=kind,
            value=value,
        )

    try:
        progress = await _run_ym_chat_send_steps(chat_id, txt, attachments, persist_step)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    status_code = 200 if progress["status"] == "complete" else 207
    return JSONResponse(status_code=status_code, content=progress)


# ── Ответ в чат WB ───────────────────────────────────────────────────────────

def _resolve_wb_reply_sign(chat_id: str, db: Session, refresh: bool = False) -> str:
    """Ключ чата WB: сначала из нашей базы, список WB — только если ключа нет или он не сработал."""
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

    if reply_sign and not refresh:
        return reply_sign

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
    last_http = None
    from .. import wb_chat as _wbc
    for attempt in range(3):
        if not reply_sign:
            reply_sign = _resolve_wb_reply_sign(chat_id, db, refresh=True)
        if not reply_sign:
            break
        try:
            wb_client.send_chat_message(reply_sign, txt, timeout=20)
            last_http = None
            break
        except requests.HTTPError as e:
            last_http = e
            code = getattr(e.response, "status_code", 0)
            if code == 429 or (code in (400, 401, 403, 422) and attempt < 2):
                _wbc.invalidate_chats_cache()
                reply_sign = ""
                time.sleep(1.5 * (attempt + 1))
                continue
            raise HTTPException(
                status_code=502,
                detail=f"WB Chat API {code}: {(e.response.text or '')[:200]}",
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    if last_http is not None:
        code = getattr(last_http.response, "status_code", 0)
        raise HTTPException(
            status_code=429 if code == 429 else 502,
            detail=f"WB Chat API {code}: {(last_http.response.text or '')[:200]}",
        )
    if not reply_sign:
        raise HTTPException(
            status_code=404,
            detail="Не удалось получить ключ чата у Wildberries. Обновите список чатов и попробуйте снова.",
        )

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
        from .. import chat_bot
        chat_bot.bind_form_tokens_to_chat(conn, txt, chat_id, "wb")
        conn.commit()

    _publish_seller_replied("wb", chat_id, txt)
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
        reply_sign = await run_in_threadpool(_resolve_wb_reply_sign, chat_id, db, True)
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
    current_user: dict = Depends(_require_admin),
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
def update_template(
    tpl_id: int,
    payload: TemplatePayload,
    db: Session = Depends(get_db),
    _admin: dict = Depends(_require_admin),
):
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
def delete_template(
    tpl_id: int,
    db: Session = Depends(get_db),
    _admin: dict = Depends(_require_admin),
):
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
            try:
                ym_client.answer_feedback(item_id, txt, biz=_ym_business_id(), key=_ym_key(), timeout=20)
                gone = False
            except requests.HTTPError as e:
                if not ym_client.feedback_gone_from_cabinet(e):
                    raise
                gone = True
            with db.bind.connect() as conn:
                if gone:
                    conn.execute(text(
                        "UPDATE ym_feedbacks SET answered_at=now() WHERE feedback_id=:i AND answered_at IS NULL"
                    ), {"i": item_id})
                else:
                    conn.execute(text(
                        "UPDATE ym_feedbacks SET answered_at=now(),answer_text=:a WHERE feedback_id=:i"
                    ), {"a": txt, "i": item_id})
                conn.commit()

        elif mp == "ym" and itype == "question":
            ym_client.answer_question(item_id, txt, biz=_ym_business_id(), key=_ym_key(), timeout=20)
            with db.bind.connect() as conn:
                conn.execute(text("UPDATE ym_questions SET state='ANSWERED',answer_text=:a WHERE question_id=:i"),
                             {"a": txt, "i": item_id})
                conn.commit()

        elif mp == "ozon" and itype == "question":
            if not 2 <= len(txt.strip()) <= 3000:
                raise HTTPException(
                    status_code=400,
                    detail="Ответ Ozon должен содержать от 2 до 3000 символов",
                )
            with db.bind.connect() as conn:
                sku = conn.execute(text(
                    "SELECT sku FROM ozon_questions WHERE question_id=:i"
                ), {"i": item_id}).scalar()
            if not sku:
                raise HTTPException(status_code=404, detail="Вопрос Ozon не найден")
            ozon_client.answer_question(item_id, int(sku), txt, timeout=20)
            with db.bind.connect() as conn:
                conn.execute(text("""
                    UPDATE ozon_questions
                    SET status='PROCESSED', answer_text=:a,
                        answered_at=NOW(), synced_at=NOW()
                    WHERE question_id=:i
                """), {"a": txt, "i": item_id})
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
def get_stats(
    days: int = Query(30, ge=1, le=365),
    item_type: str | None = Query(None),
    db: Session = Depends(get_db),
):
    type_sql = ""
    params = {"days": days}
    if item_type in ("review", "question", "chat"):
        type_sql = " AND item_type = :item_type"
        params["item_type"] = item_type

    with db.bind.connect() as conn:
        managers = conn.execute(text(f"""
            SELECT
                COALESCE(answered_by, '—')                             AS manager,
                COUNT(*)                                               AS total,
                COUNT(*) FILTER (WHERE item_type='review')             AS reviews,
                COUNT(*) FILTER (WHERE item_type='question')           AS questions,
                COUNT(*) FILTER (WHERE item_type='chat')               AS chats,
                COUNT(*) FILTER (WHERE mp='wb')                        AS wb,
                COUNT(*) FILTER (WHERE mp='ym')                        AS ym,
                COUNT(*) FILTER (WHERE mp='ozon')                      AS ozon,
                COUNT(*) FILTER (WHERE answered_by LIKE 'auto_reply%') AS auto_sent,
                MAX(sent_at)                                           AS last_at
            FROM cx_response_log
            WHERE sent_at >= now() - make_interval(days => :days){type_sql}
            GROUP BY answered_by
            ORDER BY total DESC
        """), params).mappings().all()

        daily = conn.execute(text(f"""
            SELECT
                DATE(sent_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow') AS day,
                COUNT(*)                                                        AS total,
                COUNT(*) FILTER (WHERE answered_by NOT LIKE 'auto_reply%')     AS manual
            FROM cx_response_log
            WHERE sent_at >= now() - make_interval(days => :days){type_sql}
            GROUP BY day ORDER BY day
        """), params).mappings().all()

        by_mp   = conn.execute(text(
            "SELECT mp, COUNT(*) AS cnt FROM cx_response_log "
            f"WHERE sent_at >= now()-make_interval(days=>:days){type_sql} GROUP BY mp"
        ), params).mappings().all()

        by_type = conn.execute(text(
            "SELECT item_type, COUNT(*) AS cnt FROM cx_response_log "
            f"WHERE sent_at >= now()-make_interval(days=>:days){type_sql} GROUP BY item_type"
        ), params).mappings().all()

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

class ReviewAutoSettingsPayload(BaseModel):
    wb: bool | None = None
    ym: bool | None = None
    ozon: bool | None = None
    thanks_5: bool | None = None
    thanks_4: bool | None = None
    sorry: bool | None = None


@router.get("/review-auto-settings")
def get_review_auto_settings(db: Session = Depends(get_db)):
    with db.bind.connect() as conn:
        flags, scenarios = _load_review_auto_config(conn)
        n_tpl = conn.execute(text(
            "SELECT COUNT(*) FROM cx_response_templates WHERE auto_reply = true"
        )).scalar()
    return {"enabled": flags, "scenarios": scenarios, "auto_templates": int(n_tpl or 0)}


@router.put("/review-auto-settings")
def update_review_auto_settings(
    payload: ReviewAutoSettingsPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(_require_admin),
):
    mp_updates = {mp: getattr(payload, mp) for mp in REVIEW_AUTO_MPS if getattr(payload, mp) is not None}
    sc_updates = {
        key: getattr(payload, key)
        for key in REVIEW_AUTO_SCENARIOS
        if getattr(payload, key) is not None
    }
    if not mp_updates and not sc_updates:
        raise HTTPException(status_code=400, detail="Нечего сохранять")
    who = current_user.get("username") or ""
    with db.bind.connect() as conn:
        for mp, on in mp_updates.items():
            conn.execute(text("""
                INSERT INTO cx_review_settings (key, value, updated_at, updated_by)
                VALUES (:k, :v, now(), :who)
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = now(), updated_by = EXCLUDED.updated_by
            """), {"k": f"auto_{mp}", "v": "true" if on else "false", "who": who})
        for sc, on in sc_updates.items():
            conn.execute(text("""
                INSERT INTO cx_review_settings (key, value, updated_at, updated_by)
                VALUES (:k, :v, now(), :who)
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = now(), updated_by = EXCLUDED.updated_by
            """), {"k": f"auto_sc_{sc}", "v": "true" if on else "false", "who": who})
        conn.commit()
        flags, scenarios = _load_review_auto_config(conn)
    return {"enabled": flags, "scenarios": scenarios}


def _check_stop_words(text_content: str, stop_words_raw: str) -> bool:
    """True если нужно пропустить (найдено стоп-слово)."""
    if not stop_words_raw:
        return False
    words = [w.strip().lower() for w in stop_words_raw.split("\n") if w.strip()]
    haystack = text_content.lower()
    return any(sw in haystack for sw in words)


def _run_auto_replies(db) -> dict:
    """Шлёт только включённые сценарии без текста и фото. Тумблеры площадок по умолчанию выкл."""
    from . import kb
    sent = 0
    queued = 0

    with db.bind.connect() as conn:
        flags, scenarios = _load_review_auto_config(conn)

    if not any(flags.values()) or not any(scenarios.values()):
        return {"sent": 0, "queued": 0, "disabled": True}

    def _not_logged(mp_col, id_expr):
        return (
            f"{id_expr} NOT IN (SELECT item_id FROM cx_response_log WHERE mp='{mp_col}' AND item_type='review') "
            f"AND {id_expr} NOT IN (SELECT item_id FROM cx_pending_moderation WHERE mp='{mp_col}' AND item_type='review')"
        )

    def _log_and_mark(conn, mp, iid, body, extra_sql, extra_params):
        conn.execute(text(extra_sql), extra_params)
        conn.execute(text(
            "INSERT INTO cx_response_log(item_type,item_id,mp,answered_by,answer_text)"
            " VALUES('review',:i,:mp,'auto_reply',:a)"
        ), {"i": iid, "mp": mp, "a": body})
        conn.commit()

    if flags.get("wb") and _wb_key():
        with db.bind.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, valuation FROM wb_feedbacks "
                "WHERE state='active' "
                "AND TRIM(COALESCE(text,''))='' AND TRIM(COALESCE(pros,''))='' AND TRIM(COALESCE(cons,''))='' "
                "AND (photos IS NULL OR TRIM(photos)='') "
                f"AND {_not_logged('wb', 'id::text')} "
                "LIMIT 40"
            )).fetchall()
        for row in rows:
            iid, rating = str(row[0]), row[1]
            mode = classify_review_reply(rating, "", None, "", "")
            if not should_auto_send(mode, rating, scenarios):
                continue
            body = kb.ensure_review_signature(no_text_answer(rating))
            try:
                wb_client.answer_feedback(iid, body, timeout=20)
                with db.bind.connect() as conn:
                    _log_and_mark(
                        conn, "wb", iid, body,
                        "UPDATE wb_feedbacks SET state='answered',answer_text=:a WHERE id=:i",
                        {"a": body, "i": iid},
                    )
                sent += 1
            except Exception:
                pass

    if flags.get("ym") and _ym_key() and _ym_business_id():
        with db.bind.connect() as conn:
            rows = conn.execute(text(
                "SELECT feedback_id, valuation FROM ym_feedbacks "
                "WHERE answered_at IS NULL "
                "AND TRIM(COALESCE(comment,''))='' AND TRIM(COALESCE(pro_text,''))='' "
                "AND TRIM(COALESCE(contra_text,''))='' "
                f"AND {_not_logged('ym', 'feedback_id::text')} "
                "LIMIT 40"
            )).fetchall()
        for row in rows:
            iid, rating = str(row[0]), row[1]
            mode = classify_review_reply(rating, "", None, "", "")
            if not should_auto_send(mode, rating, scenarios):
                continue
            body = kb.ensure_review_signature(no_text_answer(rating))
            try:
                ym_client.answer_feedback(row[0], body, biz=_ym_business_id(), key=_ym_key(), timeout=20)
                with db.bind.connect() as conn:
                    _log_and_mark(
                        conn, "ym", iid, body,
                        "UPDATE ym_feedbacks SET answered_at=now(),answer_text=:a WHERE feedback_id=:i",
                        {"a": body, "i": row[0]},
                    )
                sent += 1
            except Exception:
                pass

    if flags.get("ozon") and _oz_key() and _oz_cid():
        with db.bind.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS ozon_review_outbox (
                    id SERIAL PRIMARY KEY,
                    review_id TEXT NOT NULL,
                    answer_text TEXT NOT NULL,
                    requested_by TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    sent_at TIMESTAMPTZ
                )
            """))
            rows = conn.execute(text(
                "SELECT review_id, rating FROM ozon_reviews "
                "WHERE answered_at IS NULL "
                "AND TRIM(COALESCE(review_text,''))='' AND TRIM(COALESCE(pros,''))='' "
                "AND TRIM(COALESCE(cons,''))='' "
                "AND (photos IS NULL OR TRIM(photos)='') "
                f"AND {_not_logged('ozon', 'review_id')} "
                "LIMIT 40"
            )).fetchall()
        for row in rows:
            iid, rating = str(row[0]), row[1]
            mode = classify_review_reply(rating, "", None, "", "")
            if not should_auto_send(mode, rating, scenarios):
                continue
            body = kb.ensure_review_signature(no_text_answer(rating))
            with db.bind.connect() as conn:
                try:
                    ozon_client.answer_review(iid, body, timeout=20)
                    _log_and_mark(
                        conn, "ozon", iid, body,
                        "UPDATE ozon_reviews SET answered_at=now(), answer_text=:a WHERE review_id=:i",
                        {"a": body, "i": iid},
                    )
                    sent += 1
                except Exception:
                    _queue_ozon_review_reply(conn, iid, body, "auto_reply")
                    conn.commit()
                    queued += 1

    return {"sent": sent, "queued": queued}



@router.post("/auto-reply/run")
def run_auto_replies(db: Session = Depends(get_db), _admin: dict = Depends(_require_admin)):
    result = _run_auto_replies(db)
    return {"status": "ok", **result}


@router.get("/auto-reply/preview")
def preview_auto_replies(db: Session = Depends(get_db)):
    with db.bind.connect() as conn:
        flags = _load_review_auto_flags(conn)
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
        oz_el = conn.execute(text(
            "SELECT COUNT(*) FROM ozon_reviews WHERE answered_at IS NULL"
            " AND review_id NOT IN (SELECT item_id FROM cx_response_log WHERE mp='ozon' AND item_type='review')"
        )).scalar()

    return {"templates_active": len(templates), "wb_eligible": wb_el or 0,
            "ym_eligible": ym_el or 0, "ozon_eligible": oz_el or 0,
            "enabled": flags,
            "templates": [dict(t) for t in templates]}
