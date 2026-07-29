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

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logger = logging.getLogger("ozon_chat_sync")

OZON_BASE = "https://api-seller.ozon.ru"
SYNC_INTERVAL = 45  # как ближе к WB: копим ленту в БД заранее
HISTORY_LIMIT = 50
LIST_PAGE = 100
MAX_LIST_PAGES = 5
MAX_HISTORY_CHATS = 80  # за цикл дотягиваем историю в БД


def _cid() -> str:
    return os.getenv("OZON_CLIENT_ID", "")


def _key() -> str:
    return os.getenv("OZON_API_KEY", "")


def _hdr() -> dict:
    return {
        "Client-Id": _cid(),
        "Api-Key": _key(),
        "Content-Type": "application/json",
    }


def _post(path: str, body: dict) -> dict:
    r = requests.post(f"{OZON_BASE}{path}", headers=_hdr(), json=body, timeout=25)
    r.raise_for_status()
    return r.json() or {}


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
        stored += 1

        if not last_text and body and sender in ("customer", "seller"):
            last_text = body
            last_sender = sender
        elif not last_text and body:
            last_text = body
            last_sender = sender

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
                WHEN chat_status ILIKE 'OPENED' AND :needs AND waiting_since IS NULL THEN NOW()
                WHEN NOT (chat_status ILIKE 'OPENED' AND :needs) THEN NULL
                ELSE waiting_since
            END,
            updated_at = NOW()
        WHERE chat_id = :cid
    """), {
        "cid": chat_id,
        "msg": (last_text or "")[:500],
        "sender": last_sender,
        "sku": int(last_sku) if last_sku and str(last_sku).isdigit() else None,
        "ord": last_order,
        "needs": needs,
    })
    return stored


def sync_chats(db) -> int:
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
                unread_n = int(item.get("unread_count") or 0)
                # Всегда копим историю в БД (как WB копит события) — открытие чата без live API.
                # Приоритет: непрочитанные / без ленты / первые N.
                has_msgs = conn.execute(text(
                    "SELECT 1 FROM ozon_chat_messages WHERE chat_id = :cid LIMIT 1"
                ), {"cid": cid}).scalar()
                need_hist = (
                    history_done < MAX_HISTORY_CHATS
                    and (unread_n > 0 or not has_msgs or history_done < 40)
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

    logger.info(
        "[OzonChat] synced chats=%d history=%d unique=%d",
        synced, history_done, len(by_id),
    )
    return synced


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
