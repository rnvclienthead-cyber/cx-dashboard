"""
Синхронизация чатов Яндекс Маркета (Partner API).

Polling: WAITING_FOR_PARTNER + недавние WAITING_FOR_CUSTOMER.
Отправка сообщений требует scope communication / all-methods у токена.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

import requests
from sqlalchemy import text

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logger = logging.getLogger("ym_chat_sync")

YM_BASE = "https://api.partner.market.yandex.ru"
SYNC_INTERVAL = 45  # секунд
HISTORY_LIMIT_CHATS = 30


def _key() -> str:
    return os.getenv("YM_API_KEY", "")


def _business_id() -> str:
    return os.getenv("YM_BUSINESS_ID", "")


def _hdr() -> dict:
    return {"Api-Key": _key(), "Content-Type": "application/json"}


def _parse_ts(raw) -> datetime | None:
    if not raw:
        return None
    try:
        s = str(raw).replace("Z", "+00:00")
        return datetime.fromisoformat(s).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def _list_chats(statuses: list[str], *, limit: int = 50) -> list[dict]:
    biz = _business_id()
    url = f"{YM_BASE}/v2/businesses/{biz}/chats"
    r = requests.post(url, headers=_hdr(), json={"statuses": statuses, "limit": limit}, timeout=30)
    r.raise_for_status()
    data = r.json() or {}
    result = data.get("result") or data
    return result.get("chats") or []


def _fetch_history(chat_id: int | str) -> dict:
    biz = _business_id()
    url = f"{YM_BASE}/v2/businesses/{biz}/chats/history"
    r = requests.post(url, headers=_hdr(), params={"chatId": chat_id}, json={}, timeout=30)
    r.raise_for_status()
    data = r.json() or {}
    return data.get("result") or data


def _context_fields(ctx: dict | None) -> dict:
    ctx = ctx or {}
    ctype = str(ctx.get("type") or "")
    customer = ctx.get("customer") or {}
    name = customer.get("name") or ""
    public_id = customer.get("publicId") or ""
    order_id = None
    return_id = None
    order = ctx.get("order") or {}
    if isinstance(order, dict):
        order_id = order.get("orderId") or order.get("id")
    ret = ctx.get("return") or {}
    if isinstance(ret, dict):
        return_id = ret.get("returnId") or ret.get("id")
    # иногда orderId на верхнем уровне контекста
    order_id = order_id or ctx.get("orderId")
    return_id = return_id or ctx.get("returnId")
    return {
        "context_type": ctype,
        "customer_name": name,
        "customer_public_id": public_id,
        "order_id": str(order_id) if order_id is not None else None,
        "return_id": str(return_id) if return_id is not None else None,
    }


def _map_sender(sender: str) -> str:
    s = str(sender or "").upper()
    if s == "PARTNER":
        return "seller"
    if s == "CUSTOMER":
        return "customer"
    if s in ("MARKET", "SUPPORT"):
        return "system"
    return s.lower() or "unknown"


def _upsert_chat(conn, chat: dict, *, needs_answer: bool) -> str | None:
    chat_id = str(chat.get("chatId") or chat.get("id") or "").strip()
    if not chat_id:
        return None
    ctx = _context_fields(chat.get("context"))
    status = str(chat.get("status") or "")
    created = _parse_ts(chat.get("createdAt"))
    updated = _parse_ts(chat.get("updatedAt"))

    conn.execute(text("""
        INSERT INTO ym_chats
            (chat_id, status, context_type, customer_name, customer_public_id,
             order_id, return_id, needs_answer, waiting_since, created_at, updated_at)
        VALUES
            (:cid, :status, :ctype, :cname, :cpid,
             :oid, :rid, :needs,
             CASE WHEN :needs THEN COALESCE(
                 (SELECT waiting_since FROM ym_chats WHERE chat_id = :cid), NOW()
             ) ELSE NULL END,
             COALESCE(:created, NOW()), COALESCE(:updated, NOW()))
        ON CONFLICT (chat_id) DO UPDATE SET
            status              = EXCLUDED.status,
            context_type        = COALESCE(EXCLUDED.context_type, ym_chats.context_type),
            customer_name       = COALESCE(NULLIF(EXCLUDED.customer_name, ''), ym_chats.customer_name),
            customer_public_id  = COALESCE(EXCLUDED.customer_public_id, ym_chats.customer_public_id),
            order_id            = COALESCE(EXCLUDED.order_id, ym_chats.order_id),
            return_id           = COALESCE(EXCLUDED.return_id, ym_chats.return_id),
            needs_answer        = EXCLUDED.needs_answer,
            waiting_since       = CASE
                WHEN EXCLUDED.needs_answer AND ym_chats.waiting_since IS NULL THEN NOW()
                WHEN NOT EXCLUDED.needs_answer THEN NULL
                ELSE ym_chats.waiting_since
            END,
            updated_at          = COALESCE(EXCLUDED.updated_at, NOW())
    """), {
        "cid": chat_id,
        "status": status,
        "ctype": ctx["context_type"],
        "cname": ctx["customer_name"],
        "cpid": ctx["customer_public_id"],
        "oid": ctx["order_id"],
        "rid": ctx["return_id"],
        "needs": needs_answer,
        "created": created,
        "updated": updated,
    })
    return chat_id


def _sync_history(conn, chat_id: str) -> int:
    data = _fetch_history(chat_id)
    ctx = _context_fields(data.get("context"))
    messages = data.get("messages") or []
    stored = 0
    last_text = ""
    last_sender = ""

    for m in messages:
        mid = str(m.get("messageId") or m.get("id") or "").strip()
        if not mid:
            continue
        sender = _map_sender(m.get("sender"))
        body = str(m.get("message") or m.get("text") or "").strip()
        created = _parse_ts(m.get("createdAt"))
        # payload[] = вложения {name, url, size}
        att_urls = []
        for p in (m.get("payload") or []):
            if isinstance(p, dict):
                u = str(p.get("url") or "").strip()
                if u.startswith("http"):
                    att_urls.append(u)
            elif isinstance(p, str) and p.startswith("http"):
                att_urls.append(p)
        conn.execute(text("""
            INSERT INTO ym_chat_messages
                (message_id, chat_id, sender_type, text, created_at, attachments)
            VALUES (:mid, :cid, :sender, :txt, :created, :att)
            ON CONFLICT (message_id) DO UPDATE SET
                text = EXCLUDED.text,
                attachments = COALESCE(EXCLUDED.attachments, ym_chat_messages.attachments)
        """), {
            "mid": mid,
            "cid": chat_id,
            "sender": sender,
            "txt": body[:4000],
            "created": created,
            "att": "\n".join(att_urls) if att_urls else None,
        })
        stored += 1
        # messages обычно от старых к новым — берём последнее
        if body or att_urls:
            last_text = body or (f"[файл] {att_urls[0]}" if att_urls else "")
            last_sender = sender

    conn.execute(text("""
        UPDATE ym_chats SET
            last_message = COALESCE(NULLIF(:msg, ''), last_message),
            last_sender  = COALESCE(NULLIF(:sender, ''), last_sender),
            context_type = COALESCE(NULLIF(:ctype, ''), context_type),
            customer_name = COALESCE(NULLIF(:cname, ''), customer_name),
            customer_public_id = COALESCE(:cpid, customer_public_id),
            order_id = COALESCE(:oid, order_id),
            return_id = COALESCE(:rid, return_id),
            updated_at = NOW()
        WHERE chat_id = :cid
    """), {
        "cid": chat_id,
        "msg": (last_text or "")[:500],
        "sender": last_sender,
        "ctype": ctx["context_type"],
        "cname": ctx["customer_name"],
        "cpid": ctx["customer_public_id"],
        "oid": ctx["order_id"],
        "rid": ctx["return_id"],
    })
    return stored


def sync_chats(db) -> int:
    if not _key() or not _business_id():
        return 0

    try:
        waiting = _list_chats(["WAITING_FOR_PARTNER"], limit=50)
        # ответы покупателю уже отправлены — держим в списке короткий хвост
        answered = _list_chats(["WAITING_FOR_CUSTOMER"], limit=20)
    except Exception as e:
        logger.error("[YmChat] list error: %s", e)
        return 0

    synced = 0
    hist = 0
    with db.bind.connect() as conn:
        for chat in waiting:
            try:
                cid = _upsert_chat(conn, chat, needs_answer=True)
                if not cid:
                    continue
                synced += 1
                if hist < HISTORY_LIMIT_CHATS:
                    try:
                        _sync_history(conn, cid)
                        hist += 1
                    except Exception as he:
                        logger.warning("[YmChat] history %s: %s", cid, he)
            except Exception as e:
                logger.error("[YmChat] upsert: %s", e)

        for chat in answered:
            try:
                cid = _upsert_chat(conn, chat, needs_answer=False)
                if not cid:
                    continue
                synced += 1
                if hist < HISTORY_LIMIT_CHATS:
                    try:
                        _sync_history(conn, cid)
                        hist += 1
                    except Exception as he:
                        logger.warning("[YmChat] history %s: %s", cid, he)
            except Exception as e:
                logger.error("[YmChat] upsert answered: %s", e)

        conn.commit()

    logger.info("[YmChat] synced=%d history=%d waiting=%d", synced, hist, len(waiting))
    return synced


def send_message(chat_id: str, text: str) -> None:
    """Отправка текста. Требует scope communication у YM_API_KEY."""
    biz = _business_id()
    url = f"{YM_BASE}/v2/businesses/{biz}/chats/message"
    # chatId — обязательный query-параметр (как у /chats/history)
    cid: int | str = int(chat_id) if str(chat_id).isdigit() else chat_id
    r = requests.post(
        url,
        headers=_hdr(),
        params={"chatId": cid},
        json={"message": text[:4000]},
        timeout=25,
    )
    if r.status_code == 403:
        raise PermissionError(
            "YM токен без scope communication/all-methods — отправка недоступна. "
            "Выпустите новый Api-Key с правом «Общение с покупателями»."
        )
    r.raise_for_status()


async def start_ym_chat_sync_worker(get_db_func) -> None:
    logger.info("[YmChat] worker starting (interval=%ds)", SYNC_INTERVAL)
    await asyncio.sleep(50)

    if not _key() or not _business_id():
        logger.warning("[YmChat] YM_API_KEY / YM_BUSINESS_ID не заданы — idle")
        return

    while True:
        try:
            db = next(get_db_func())
            try:
                await asyncio.to_thread(sync_chats, db)
            finally:
                db.close()
        except Exception as e:
            logger.error("[YmChat] cycle error: %s", e)
        await asyncio.sleep(SYNC_INTERVAL)
