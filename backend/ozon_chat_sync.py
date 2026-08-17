"""
Синхронизация чатов Ozon с покупателями (Seller API v3).

Требуется Premium Plus. Интервал ~3 мин.
Таблицы: ozon_chats, ozon_chat_messages.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone

import requests
from sqlalchemy import text

from . import ozon_client

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logger = logging.getLogger("ozon_chat_sync")

OZON_BASE = "https://api-seller.ozon.ru"
SYNC_INTERVAL = 45  # как ближе к WB: копим ленту в БД заранее
HISTORY_LIMIT = 100
LIST_PAGE = 100
MAX_LIST_PAGES = 5
MAX_HISTORY_CHATS = 120  # за цикл дотягиваем историю в БД
# Рефреш history, если last_message_id из list новее, чем в БД (ответ из ЛК и т.п.)
FORCE_HISTORY_ON_LMID_CHANGE = True


def _cid() -> str:
    return ozon_client.client_id()


def _key() -> str:
    return ozon_client.api_key()


def _hdr() -> dict:
    return ozon_client.headers(_cid(), _key())


# Единая реализация — backend/ozon_client.py (используется и ozon_worker.py,
# и routers/cx_responses.py). Обёртка с прежней сигнатурой (path, body) сохранена,
# чтобы не переписывать все вызовы _post(...) ниже по файлу.
def _post(path: str, body: dict) -> dict:
    return ozon_client.post(path, body, timeout=25)


def _parse_ts(raw) -> datetime | None:
    if not raw:
        return None
    try:
        s = str(raw).replace("Z", "+00:00")
        return datetime.fromisoformat(s).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def _msg_text(data) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, list):
        parts = [str(x).strip() for x in data if str(x).strip()]
        return "\n".join(parts)
    if isinstance(data, dict):
        return str(data.get("text") or data.get("data") or "").strip()
    return str(data).strip()


_IMG_RE = re.compile(r"!\[[^\]]*\]\((https?://[^)]+)\)")
# Полный номер отправления Ozon: 0162883533-0295-1
_POSTING_RE = re.compile(r"\b(\d{5,}-\d{2,5}-\d{1,3})\b")
_ORDER_RE = re.compile(r"\b(\d{5,}-\d{2,5})\b")
# Сколько чатов за цикл синка резолвим через Posting API
_API_RESOLVE_BUDGET = 25


def _preview_text(body: str) -> str:
    """Превью для списка: не показываем сырой markdown картинок."""
    s = (body or "").strip()
    if not s:
        return ""
    if _IMG_RE.fullmatch(s) or (s.startswith("![") and "http" in s and len(s) < 400):
        return "📷 Фото"
    cleaned = _IMG_RE.sub("📷", s).strip()
    return cleaned[:500]


def _posting_candidates(order_number: str | None, texts: list[str]) -> list[str]:
    """Кандидаты posting_number: из текста сообщений + суффиксы -1..-10 к заказу."""
    found: list[str] = []

    def _add(p: str):
        p = (p or "").strip()
        if p and p not in found:
            found.append(p)

    for t in texts:
        for m in _POSTING_RE.findall(t or ""):
            _add(m)

    base = (order_number or "").strip()
    if base and _POSTING_RE.fullmatch(base):
        _add(base)
        parts = base.rsplit("-", 1)
        base = parts[0] if len(parts) == 2 else base
    if base and _ORDER_RE.fullmatch(base):
        for i in range(1, 11):
            _add(f"{base}-{i}")
    return found


def _fetch_posting_product(posting_number: str) -> dict | None:
    """FBO/FBS get → offer_id (наш артикул) и sku."""
    for path in ("/v2/posting/fbo/get", "/v3/posting/fbs/get"):
        try:
            r = _post(path, {"posting_number": posting_number})
        except requests.HTTPError as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (400, 404):
                continue
            logger.debug("[OzonChat] posting get %s %s: %s", path, posting_number, e)
            continue
        except Exception as e:
            logger.debug("[OzonChat] posting get %s %s: %s", path, posting_number, e)
            continue
        res = r.get("result") or r
        prods = res.get("products") or []
        if not prods:
            continue
        p = prods[0] or {}
        article = str(p.get("offer_id") or "").strip()
        if not article:
            continue
        sku_raw = p.get("sku")
        try:
            ozon_sku = int(sku_raw) if sku_raw is not None else None
        except (TypeError, ValueError):
            ozon_sku = None
        return {
            "posting_number": str(res.get("posting_number") or posting_number),
            "order_number": str(res.get("order_number") or "") or None,
            "order_id": res.get("order_id"),
            "status": res.get("status"),
            "supplier_article": article,
            "sku_name": p.get("name"),
            "ozon_sku": ozon_sku,
            "price": p.get("price"),
            "created_at": res.get("in_process_at") or res.get("created_at"),
        }
    return None


def _cache_ozon_order(conn, info: dict) -> None:
    """Кэш отправления в ozon_orders (чтобы не бить API повторно)."""
    if not info.get("posting_number") or not info.get("supplier_article"):
        return
    try:
        conn.execute(text("""
            INSERT INTO ozon_orders
                (posting_number, order_id, order_number, status,
                 supplier_article, sku_name, created_at, price)
            VALUES
                (:pnum, :oid, :onum, :status,
                 :article, :sname, :created, :price)
            ON CONFLICT (posting_number) DO UPDATE SET
                supplier_article = COALESCE(EXCLUDED.supplier_article, ozon_orders.supplier_article),
                sku_name         = COALESCE(EXCLUDED.sku_name, ozon_orders.sku_name),
                status           = COALESCE(EXCLUDED.status, ozon_orders.status),
                synced_at        = NOW()
        """), {
            "pnum": info["posting_number"],
            "oid": info.get("order_id"),
            "onum": info.get("order_number"),
            "status": info.get("status"),
            "article": info["supplier_article"],
            "sname": info.get("sku_name"),
            "created": _parse_ts(info.get("created_at")),
            "price": info.get("price"),
        })
    except Exception as e:
        logger.debug("[OzonChat] cache ozon_orders: %s", e)


def _msg_images(data, is_image: bool = False) -> list[str]:
    """Достаёт URL вложений из data сообщения Ozon (markdown / список / строка)."""
    urls: list[str] = []
    text = _msg_text(data)
    urls.extend(_IMG_RE.findall(text))

    def _maybe(u: str):
        s = str(u or "").strip()
        if not s:
            return
        if s.startswith("http"):
            urls.append(s)
        elif s.startswith("/v2/chat/file/") or s.startswith("v2/chat/file/"):
            urls.append(f"{OZON_BASE}/" + s.lstrip("/"))

    if isinstance(data, list):
        for part in data:
            if isinstance(part, dict):
                _maybe(part.get("url") or part.get("file") or part.get("src"))
            else:
                _maybe(part)
    elif isinstance(data, dict):
        _maybe(data.get("url") or data.get("file") or data.get("src"))
        for key in ("files", "images", "attachments"):
            arr = data.get(key)
            if isinstance(arr, list):
                for part in arr:
                    if isinstance(part, dict):
                        _maybe(part.get("url") or part.get("file") or part.get("src"))
                    else:
                        _maybe(part)
    elif is_image:
        _maybe(data)

    # unique preserve order
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _sender_type(user: dict | None) -> str:
    t = str((user or {}).get("type") or "").lower()
    if t in ("customer", "buyer", "client"):
        return "customer"
    if t in ("seller", "courier"):
        return "seller"
    if t in ("chatbot", "system", "support", "ozon", "notificationuser", "notification"):
        return "system"
    return t or "unknown"


def _list_chats(*, unread_only: bool = False, chat_status: str | None = "Opened") -> list[dict]:
    """Пагинация /v3/chat/list. Возвращает сырые элементы chats[]."""
    out: list[dict] = []
    cursor = None
    for _ in range(MAX_LIST_PAGES):
        body: dict = {"limit": LIST_PAGE}
        filt: dict = {}
        if chat_status:
            filt["chat_status"] = chat_status
        if unread_only:
            filt["unread_only"] = True
        if filt:
            body["filter"] = filt
        if cursor:
            body["cursor"] = cursor
        data = _post("/v3/chat/list", body)
        batch = data.get("chats") or []
        out.extend(batch)
        if not data.get("has_next"):
            break
        cursor = data.get("cursor")
        if not cursor:
            break
    return out


def _upsert_chat(conn, item: dict) -> str | None:
    chat = item.get("chat") or {}
    chat_id = str(chat.get("chat_id") or item.get("chat_id") or "").strip()
    if not chat_id:
        return None

    status = str(chat.get("chat_status") or "OPENED")
    chat_type = str(chat.get("chat_type") or "")
    unread = int(item.get("unread_count") or 0)
    created = _parse_ts(chat.get("created_at"))
    needs = unread > 0 and status.upper() == "OPENED"

    # Контекст из последнего сообщения подтянем в history; здесь — метаданные
    conn.execute(text("""
        INSERT INTO ozon_chats
            (chat_id, chat_status, chat_type, unread_count, last_message_id,
             needs_answer, waiting_since, updated_at, created_at)
        VALUES
            (:cid, :status, :ctype, :unread, :lmid,
             :needs,
             CASE WHEN :needs THEN COALESCE(
                 (SELECT waiting_since FROM ozon_chats WHERE chat_id = :cid),
                 NOW()
             ) ELSE NULL END,
             NOW(), COALESCE(:created, NOW()))
        ON CONFLICT (chat_id) DO UPDATE SET
            chat_status     = EXCLUDED.chat_status,
            chat_type       = COALESCE(EXCLUDED.chat_type, ozon_chats.chat_type),
            unread_count    = EXCLUDED.unread_count,
            last_message_id = EXCLUDED.last_message_id,
            -- Не затираем точный needs_answer после history: unread один ненадёжен
            -- (system/notification тоже дают unread). Если last_sender уже известен —
            -- считаем только customer; иначе временно берём unread.
            needs_answer    = CASE
                WHEN EXCLUDED.chat_status NOT ILIKE 'OPENED' THEN false
                WHEN ozon_chats.last_sender IN ('customer', 'buyer', 'client') THEN true
                WHEN ozon_chats.last_sender IN ('seller', 'system', 'notificationuser', 'chatbot')
                    THEN false
                ELSE EXCLUDED.needs_answer
            END,
            waiting_since   = CASE
                WHEN (
                    CASE
                        WHEN EXCLUDED.chat_status NOT ILIKE 'OPENED' THEN false
                        WHEN ozon_chats.last_sender IN ('customer', 'buyer', 'client') THEN true
                        WHEN ozon_chats.last_sender IN ('seller', 'system', 'notificationuser', 'chatbot')
                            THEN false
                        ELSE EXCLUDED.needs_answer
                    END
                ) AND ozon_chats.waiting_since IS NULL
                    THEN NOW()
                WHEN NOT (
                    CASE
                        WHEN EXCLUDED.chat_status NOT ILIKE 'OPENED' THEN false
                        WHEN ozon_chats.last_sender IN ('customer', 'buyer', 'client') THEN true
                        WHEN ozon_chats.last_sender IN ('seller', 'system', 'notificationuser', 'chatbot')
                            THEN false
                        ELSE EXCLUDED.needs_answer
                    END
                ) THEN NULL
                ELSE ozon_chats.waiting_since
            END,
            updated_at      = NOW()
    """), {
        "cid": chat_id,
        "status": status,
        "ctype": chat_type,
        "unread": unread,
        "lmid": str(item.get("last_message_id") or "") or None,
        "needs": needs,
        "created": created,
    })
    return chat_id


def _sync_history(conn, chat_id: str) -> int:
    data = _post("/v3/chat/history", {"chat_id": chat_id, "limit": HISTORY_LIMIT})
    messages = data.get("messages") or []
    stored = 0
    last_text = ""
    last_sender = ""
    last_sku = None
    last_order = None

    # API отдаёт от новых к старым — для превью берём первое «человеческое»
    for m in messages:
        mid = str(m.get("message_id") or "").strip()
        if not mid:
            continue
        user = m.get("user") or {}
        sender = _sender_type(user)
        body = _msg_text(m.get("data"))
        imgs = _msg_images(m.get("data"), bool(m.get("is_image")))
        created = _parse_ts(m.get("created_at"))
        ctx = m.get("context") or {}
        sku = ctx.get("sku") or None
        order_no = ctx.get("order_number") or None
        if sku and not last_sku:
            last_sku = str(sku)
        if order_no and not last_order:
            last_order = str(order_no)

        conn.execute(text("""
            INSERT INTO ozon_chat_messages
                (message_id, chat_id, sender_type, sender_id, text, is_image,
                 created_at, is_read, attachments)
            VALUES
                (:mid, :cid, :sender, :sid, :txt, :img, :created, :read, :att)
            ON CONFLICT (message_id) DO UPDATE SET
                text       = EXCLUDED.text,
                is_read    = EXCLUDED.is_read,
                attachments = COALESCE(EXCLUDED.attachments, ozon_chat_messages.attachments)
        """), {
            "mid": mid,
            "cid": chat_id,
            "sender": sender,
            "sid": str(user.get("id") or ""),
            "txt": body[:4000],
            "img": bool(m.get("is_image")),
            "created": created,
            "read": bool(m.get("is_read")),
            "att": ",".join(imgs) if imgs else None,
        })
        # Убираем локальный/бот-дубль после синка реального сообщения из API
        # (message_id = local-... или bot-...). Сравниваем по нормализованным пробелам —
        # Ozon иногда меняет переносы/пробелы относительно текста при отправке.
        if sender == "seller" and body and not str(mid).startswith(("local-", "bot-")):
            conn.execute(text("""
                DELETE FROM ozon_chat_messages
                WHERE chat_id = :cid
                  AND sender_type = 'seller'
                  AND (message_id LIKE 'local-%' OR message_id LIKE 'bot-%')
                  AND regexp_replace(lower(coalesce(text,'')), '\\s+', ' ', 'g')
                    = regexp_replace(lower(:txt), '\\s+', ' ', 'g')
            """), {"cid": chat_id, "txt": body[:4000]})
        stored += 1

        if not last_text and body and sender in ("customer", "seller"):
            last_text = body
            last_sender = sender
        elif not last_text and body:
            last_text = body
            last_sender = sender
        # Номер отправления часто в тексте клиента (контекст API пустой)
        if sender == "customer" and body and not last_order:
            m_post = _POSTING_RE.search(body)
            if m_post:
                last_order = "-".join(m_post.group(1).split("-")[:2])
            else:
                m_ord = _ORDER_RE.search(body)
                if m_ord:
                    last_order = m_ord.group(1)

    # Если превью пустое — любое первое сообщение
    if not last_text and messages:
        last_text = _msg_text(messages[0].get("data"))
        last_sender = _sender_type(messages[0].get("user"))

    # needs_answer только если последнее значимое — от покупателя.
    # unread_count один не хватает: Ozon шлёт system/notification и держит unread.
    customer_last = False
    for m in messages:
        sender = _sender_type(m.get("user"))
        if sender == "customer":
            customer_last = True
            break
        if sender == "seller":
            customer_last = False
            break

    # Превью: предпочитаем customer/seller; system — только если другого нет
    if last_sender in ("system", "notificationuser", "chatbot", "unknown", ""):
        for m in messages:
            s = _sender_type(m.get("user"))
            if s in ("customer", "seller"):
                body = _msg_text(m.get("data"))
                imgs = _msg_images(m.get("data"), bool(m.get("is_image")))
                if body or imgs:
                    last_text = body or last_text
                    last_sender = s
                    break

    # needs_answer только если последнее «человеческое» сообщение — от покупателя
    # (не от system/notification Ozon и не от seller).
    needs = bool(customer_last)

    # Время последнего сообщения клиента — для «ждёт N дн», не время синка
    last_cust_at = None
    for m in messages:
        if _sender_type(m.get("user")) == "customer":
            last_cust_at = _parse_ts(m.get("created_at"))
            break

    conn.execute(text("""
        UPDATE ozon_chats SET
            last_message    = COALESCE(NULLIF(:msg, ''), last_message),
            last_sender     = COALESCE(NULLIF(:sender, ''), last_sender),
            context_sku     = COALESCE(:sku, context_sku),
            order_number    = COALESCE(:ord, order_number),
            needs_answer    = CASE
                WHEN chat_status ILIKE 'OPENED' AND :needs THEN true
                ELSE false
            END,
            waiting_since   = CASE
                WHEN chat_status ILIKE 'OPENED' AND :needs THEN COALESCE(:wait_at, waiting_since, NOW())
                ELSE NULL
            END,
            updated_at = NOW()
        WHERE chat_id = :cid
    """), {
        "cid": chat_id,
        "msg": _preview_text(last_text or ""),
        "sender": last_sender,
        "sku": int(last_sku) if last_sku and str(last_sku).isdigit() else None,
        "ord": last_order,
        "needs": needs,
        "wait_at": last_cust_at,
    })

    # Ozon SKU / заказ / отправление → наш supplier_article (для фото и возвратов)
    _resolve_article(conn, chat_id, allow_api=True)

    # Клиент написал — вернуть из архива
    if needs:
        conn.execute(text("""
            DELETE FROM cx_dismissed_items
            WHERE mp = 'ozon' AND item_type = 'chat' AND item_id = :cid
        """), {"cid": chat_id})
    # Мы ответили, клиент молчит >3 дней — в архив (как массовое правило)
    elif last_sender == "seller":
        last_any = None
        for m in messages:
            last_any = _parse_ts(m.get("created_at"))
            if last_any:
                break
        if last_any and (datetime.now(timezone.utc).replace(tzinfo=None) - last_any).total_seconds() > 2 * 86400:
            conn.execute(text("""
                INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
                VALUES ('chat', :cid, 'ozon', 'ozon_chat_sync',
                        'Авто: нет ответа клиента >2 дн после нашего')
                ON CONFLICT (item_type, item_id, mp) DO NOTHING
            """), {"cid": chat_id})
    return stored


def _resolve_article(conn, chat_id: str, allow_api: bool = True) -> bool:
    """Заполнить context_article (+ sku) из returns/orders или Posting API по отправлению.

    Возвращает True, если артикул уже есть / удалось заполнить.
    """
    global _api_resolve_left

    row = conn.execute(text("""
        SELECT context_article, context_sku, order_number
        FROM ozon_chats WHERE chat_id = :cid
    """), {"cid": chat_id}).mappings().first()
    if not row:
        return False
    if (row.get("context_article") or "").strip():
        return True

    # 1) БД: returns + orders (в т.ч. posting LIKE order-% )
    conn.execute(text("""
        UPDATE ozon_chats c SET
            context_article = COALESCE(
                NULLIF(TRIM(c.context_article), ''),
                (
                    SELECT r.supplier_article
                    FROM ozon_returns r
                    WHERE c.context_sku IS NOT NULL
                      AND r.ozon_sku = c.context_sku
                      AND NULLIF(TRIM(r.supplier_article), '') IS NOT NULL
                    ORDER BY r.return_date DESC NULLS LAST
                    LIMIT 1
                ),
                (
                    SELECT r.supplier_article
                    FROM ozon_returns r
                    WHERE NULLIF(TRIM(c.order_number), '') IS NOT NULL
                      AND (
                        r.posting_number = c.order_number
                        OR r.order_number = c.order_number
                        OR r.posting_number LIKE c.order_number || '-%'
                      )
                      AND NULLIF(TRIM(r.supplier_article), '') IS NOT NULL
                    ORDER BY r.return_date DESC NULLS LAST
                    LIMIT 1
                ),
                (
                    SELECT o.supplier_article
                    FROM ozon_orders o
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
            context_sku = COALESCE(
                c.context_sku,
                (
                    SELECT r.ozon_sku FROM ozon_returns r
                    WHERE c.context_sku IS NULL
                      AND NULLIF(TRIM(c.order_number), '') IS NOT NULL
                      AND (
                        r.posting_number = c.order_number
                        OR r.order_number = c.order_number
                        OR r.posting_number LIKE c.order_number || '-%'
                      )
                      AND r.ozon_sku IS NOT NULL
                    ORDER BY r.return_date DESC NULLS LAST
                    LIMIT 1
                )
            )
        WHERE c.chat_id = :cid
          AND NULLIF(TRIM(c.context_article), '') IS NULL
    """), {"cid": chat_id})

    art = conn.execute(text(
        "SELECT context_article FROM ozon_chats WHERE chat_id = :cid"
    ), {"cid": chat_id}).scalar()
    if (art or "").strip():
        return True

    if not allow_api or _api_resolve_left <= 0:
        return False

    # 2) Тексты сообщений → кандидаты posting_number → FBO/FBS API
    msgs = conn.execute(text("""
        SELECT text FROM ozon_chat_messages
        WHERE chat_id = :cid AND text IS NOT NULL AND text <> ''
        ORDER BY created_at DESC NULLS LAST
        LIMIT 30
    """), {"cid": chat_id}).fetchall()
    texts = [r[0] for r in msgs if r and r[0]]
    order_number = (row.get("order_number") or "").strip() or None
    candidates = _posting_candidates(order_number, texts)
    if not candidates:
        return False

    _api_resolve_left -= 1
    for pn in candidates:
        info = _fetch_posting_product(pn)
        if not info:
            continue
        _cache_ozon_order(conn, info)
        conn.execute(text("""
            UPDATE ozon_chats SET
                context_article = COALESCE(NULLIF(TRIM(context_article), ''), :art),
                context_sku     = COALESCE(context_sku, :sku),
                order_number    = COALESCE(NULLIF(TRIM(order_number), ''), :ord),
                updated_at      = NOW()
            WHERE chat_id = :cid
        """), {
            "cid": chat_id,
            "art": info["supplier_article"],
            "sku": info.get("ozon_sku"),
            "ord": info.get("order_number") or order_number,
        })
        logger.info(
            "[OzonChat] article via posting %s → %s (chat=%s)",
            pn, info["supplier_article"], chat_id[:8],
        )
        return True
    return False


_api_resolve_left = _API_RESOLVE_BUDGET


def backfill_chat_articles(db, limit: int = 40) -> int:
    """One-shot / периодический резолв артикулов для открытых чатов без article."""
    global _api_resolve_left
    _api_resolve_left = max(limit, _API_RESOLVE_BUDGET)
    done = 0
    with db.bind.connect() as conn:
        rows = conn.execute(text("""
            SELECT chat_id FROM ozon_chats
            WHERE chat_status ILIKE 'OPENED'
              AND (chat_type IS NULL OR upper(chat_type) LIKE '%BUYER%')
              AND NULLIF(TRIM(context_article), '') IS NULL
              AND (
                NULLIF(TRIM(order_number), '') IS NOT NULL
                OR context_sku IS NOT NULL
                OR EXISTS (
                  SELECT 1 FROM ozon_chat_messages m
                  WHERE m.chat_id = ozon_chats.chat_id
                    AND m.text ~ '[0-9]{5,}-[0-9]{2,}-[0-9]+'
                )
              )
            ORDER BY updated_at DESC NULLS LAST
            LIMIT :lim
        """), {"lim": limit}).fetchall()
        for (cid,) in rows:
            try:
                if _resolve_article(conn, cid, allow_api=True):
                    done += 1
                conn.commit()
            except Exception as e:
                logger.warning("[OzonChat] backfill %s: %s", cid, e)
                try:
                    conn.rollback()
                except Exception:
                    pass
    logger.info("[OzonChat] backfill articles: %d / %d", done, len(rows))
    return done


def sync_chats(db) -> int:
    global _api_resolve_left
    _api_resolve_left = _API_RESOLVE_BUDGET

    if not _cid() or not _key():
        return 0

    try:
        unread = _list_chats(unread_only=True, chat_status="Opened")
        opened = _list_chats(unread_only=False, chat_status="Opened")
    except Exception as e:
        logger.error("[OzonChat] list error: %s", e)
        return 0

    by_id: dict[str, dict] = {}
    for item in opened + unread:
        chat = item.get("chat") or {}
        cid = str(chat.get("chat_id") or "").strip()
        if not cid:
            continue
        ctype = str(chat.get("chat_type") or "")
        # Только переписка с покупателем (не новости/поддержка API)
        if ctype and "BUYER" not in ctype.upper():
            continue
        prev = by_id.get(cid)
        if not prev or int(item.get("unread_count") or 0) >= int(prev.get("unread_count") or 0):
            by_id[cid] = item

    synced = 0
    history_done = 0
    touched_ids: list[str] = []
    with db.bind.connect() as conn:
        # Сначала непрочитанные
        priority = sorted(
            by_id.values(),
            key=lambda x: (-int(x.get("unread_count") or 0), str((x.get("chat") or {}).get("created_at") or "")),
        )
        for item in priority:
            try:
                cid = _upsert_chat(conn, item)
                if not cid:
                    continue
                synced += 1
                touched_ids.append(cid)
                unread_n = int(item.get("unread_count") or 0)
                # Всегда копим историю в БД (как WB копит события) — открытие чата без live API.
                # Приоритет: непрочитанные / без ленты / сменился last_message_id / первые N.
                has_msgs = conn.execute(text(
                    "SELECT 1 FROM ozon_chat_messages WHERE chat_id = :cid LIMIT 1"
                ), {"cid": cid}).scalar()
                list_lmid = str(item.get("last_message_id") or "").strip()
                db_lmid = ""
                if FORCE_HISTORY_ON_LMID_CHANGE and list_lmid:
                    row_lmid = conn.execute(text(
                        "SELECT last_message_id FROM ozon_chats WHERE chat_id = :cid"
                    ), {"cid": cid}).scalar()
                    db_lmid = str(row_lmid or "").strip()
                    # last_message_id в upsert уже обновлён — сравниваем с messages
                    has_lmid_msg = conn.execute(text("""
                        SELECT 1 FROM ozon_chat_messages
                        WHERE chat_id = :cid AND message_id = :mid LIMIT 1
                    """), {"cid": cid, "mid": list_lmid}).scalar()
                    lmid_missing = not has_lmid_msg
                else:
                    lmid_missing = False
                need_hist = (
                    history_done < MAX_HISTORY_CHATS
                    and (
                        unread_n > 0
                        or not has_msgs
                        or lmid_missing
                        or history_done < 50
                    )
                )
                if need_hist:
                    try:
                        _sync_history(conn, cid)
                        history_done += 1
                    except Exception as he:
                        logger.warning("[OzonChat] history %s: %s", cid, he)
            except Exception as e:
                logger.error("[OzonChat] upsert error: %s", e)
        conn.commit()

        # Realtime + предрасчёт подсказки для затронутых чатов
        if touched_ids:
            try:
                from . import chat_realtime
                rows = conn.execute(text("""
                    SELECT chat_id, context_article, context_sku, order_number,
                           last_message, last_sender, needs_answer, updated_at,
                           EXTRACT(epoch FROM COALESCE(
                             (SELECT MAX(m.created_at) FROM ozon_chat_messages m WHERE m.chat_id = c.chat_id),
                             c.waiting_since, c.created_at, c.updated_at, now()
                           ))::bigint * 1000 AS last_ts
                    FROM ozon_chats c
                    WHERE chat_id = ANY(:ids)
                """), {"ids": list(set(touched_ids))}).mappings().all()
                for row in rows:
                    chat_realtime.publish_chat_updated({
                        "chat_id": row["chat_id"],
                        "mp": "ozon",
                        "sku": row.get("context_article") or "",
                        "nm_id": row.get("context_sku"),
                        "last_message": row.get("last_message"),
                        "last_sender": row.get("last_sender"),
                        "last_ts": row.get("last_ts"),
                        "needs_answer": row.get("needs_answer"),
                    })
                    if row.get("needs_answer") and str(row.get("last_sender") or "").lower() in (
                        "customer", "buyer", "client", ""
                    ):
                        chat_realtime.schedule_suggest_precompute("ozon", row["chat_id"])
            except Exception as e:
                logger.warning("[OzonChat] realtime publish: %s", e)

        # Добиваем хвост: seller last + тишина >3д (даже если history в этом цикле не трогали)
        archived_ids = [
            str(r[0]) for r in conn.execute(text("""
            WITH targets AS (
              SELECT c.chat_id
              FROM ozon_chats c
              LEFT JOIN cx_dismissed_items d
                ON d.item_id = c.chat_id AND d.mp = 'ozon' AND d.item_type = 'chat'
              WHERE d.item_id IS NULL
                AND lower(COALESCE(c.last_sender, '')) IN ('seller', 'courier')
                AND COALESCE(
                  (SELECT MAX(m.created_at) FROM ozon_chat_messages m WHERE m.chat_id = c.chat_id),
                  c.waiting_since, c.updated_at, c.created_at
                ) < now() - interval '2 days'
            ),
            ins AS (
              INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
              SELECT 'chat', chat_id, 'ozon', 'ozon_chat_sync',
                     'Авто: нет ответа клиента >2 дн после нашего'
              FROM targets
              ON CONFLICT (item_type, item_id, mp) DO NOTHING
              RETURNING item_id
            )
            SELECT item_id FROM ins
        """)).fetchall()
        ]
        if archived_ids:
            conn.execute(text("""
                UPDATE ozon_chats c SET needs_answer = false, waiting_since = NULL, updated_at = now()
                WHERE EXISTS (
                  SELECT 1 FROM cx_dismissed_items d
                  WHERE d.item_id = c.chat_id AND d.mp = 'ozon' AND d.item_type = 'chat'
                    AND d.note LIKE 'Авто: нет ответа клиента%'
                )
                  AND c.needs_answer = true
            """))
            conn.commit()
            logger.info("[OzonChat] auto-archived seller-silent>2d: %s", len(archived_ids))
            try:
                from . import chat_realtime
                for cid in archived_ids:
                    chat_realtime.publish_chat_removed(cid, "ozon")
            except Exception as e:
                logger.warning("[OzonChat] realtime remove: %s", e)

        # Чаты с уже согласованным/закрытым возвратом — как WB «Одобрено»
        try:
            closed_ret = archive_waiting_chats_with_settled_returns(conn)
            if closed_ret:
                conn.commit()
                logger.info("[OzonChat] auto-archived settled returns: %s", closed_ret)
        except Exception as e:
            logger.warning("[OzonChat] archive settled returns: %s", e)
            try:
                conn.rollback()
            except Exception:
                pass

    # Добиваем артикулы по номерам отправлений (FBO/FBS get) — чаты без sku в контексте
    try:
        filled = backfill_chat_articles(db, limit=30)
        if filled:
            logger.info("[OzonChat] posting→article filled=%d", filled)
    except Exception as e:
        logger.warning("[OzonChat] article backfill: %s", e)

    logger.info(
        "[OzonChat] synced chats=%d history=%d unique=%d",
        synced, history_done, len(by_id),
    )
    return synced


# Статусы возврата Ozon, после которых чат по возврату уже не актуален
# (аналог WB «Одобрено» / закрытых заявок).
_SETTLED_RETURN_STATUS = (
    "ReturnedToOzon", "ReturnedToSeller", "ReceivedBySeller",
    "Utilized", "Utilizing", "WriteOff", "Lost",
    "Cancelled", "CancelledByClient", "CancelledByOzon",
    "MoneyReturned", "Compensated",
)


def archive_waiting_chats_with_settled_returns(conn) -> int:
    """Ждущие ответа чаты, у которых по order/sku уже закрытый возврат → архив."""
    statuses = list(_SETTLED_RETURN_STATUS)
    note = "Авто: возврат согласован/закрыт"
    closed = conn.execute(text("""
        WITH waiting AS (
          SELECT c.chat_id, c.order_number, c.context_sku
          FROM ozon_chats c
          LEFT JOIN cx_dismissed_items d
            ON d.item_id = c.chat_id AND d.mp = 'ozon' AND d.item_type = 'chat'
          WHERE d.item_id IS NULL
            AND c.needs_answer = true
            AND lower(COALESCE(c.last_sender, '')) IN ('customer', 'buyer', 'client')
        ),
        matched AS (
          SELECT DISTINCT w.chat_id
          FROM waiting w
          JOIN ozon_returns r ON (
                NULLIF(TRIM(w.order_number), '') IS NOT NULL
                AND (
                  r.posting_number = w.order_number
                  OR r.order_number = w.order_number
                  OR r.posting_number LIKE w.order_number || '-%'
                )
            )
          WHERE COALESCE(NULLIF(TRIM(r.status_sys), ''), '') = ANY(:st)
             OR lower(COALESCE(r.status_ru, '')) LIKE '%%одобр%%'
             OR lower(COALESCE(r.status_ru, '')) LIKE '%%заверш%%'
             OR lower(COALESCE(r.status_ru, '')) LIKE '%%возвращ%%денег%%'
        ),
        ins AS (
          INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
          SELECT 'chat', chat_id, 'ozon', 'ozon_chat_sync', :note
          FROM matched
          ON CONFLICT (item_type, item_id, mp) DO NOTHING
          RETURNING item_id
        )
        SELECT count(*) FROM ins
    """), {"st": statuses, "note": note}).scalar() or 0
    if closed:
        conn.execute(text("""
            UPDATE ozon_chats c SET needs_answer = false, waiting_since = NULL, updated_at = now()
            WHERE EXISTS (
              SELECT 1 FROM cx_dismissed_items d
              WHERE d.item_id = c.chat_id AND d.mp = 'ozon' AND d.item_type = 'chat'
                AND d.note = :note
            )
              AND c.needs_answer = true
        """), {"note": note})
    return int(closed)


def send_message(chat_id: str, text: str) -> None:
    """Отправка текста покупателю через Seller Chat API."""
    ozon_client.send_chat_message(chat_id, text)


async def start_ozon_chat_sync_worker(get_db_func) -> None:
    logger.info("[OzonChat] worker starting (interval=%ds)", SYNC_INTERVAL)
    await asyncio.sleep(45)

    if not _cid() or not _key():
        logger.warning("[OzonChat] OZON_CLIENT_ID / OZON_API_KEY не заданы — idle")
        return

    while True:
        try:
            db = next(get_db_func())
            try:
                await asyncio.to_thread(sync_chats, db)
            finally:
                db.close()
        except Exception as e:
            logger.error("[OzonChat] cycle error: %s", e)
        await asyncio.sleep(SYNC_INTERVAL)
