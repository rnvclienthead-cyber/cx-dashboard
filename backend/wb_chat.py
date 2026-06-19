"""Wildberries Buyer Chat API — интеграция чата продавца с покупателем.

Безопасный режим:
  WB_CHAT_ENABLED=false  → воркер работает, события читает, в чат НЕ пишет (мониторинг)
  WB_CHAT_ENABLED=true   → авто-отвечает ссылкой на форму + шлёт уведомления об отправке

Триггер: менеджер вручную пишет WB_TRIGGER_KEYWORD (по умолч. #от) в чате продавца.
  Система видит это событие → записывает chatID → (если enabled) отвечает ссылкой на форму.
"""
import os
import asyncio
import requests as _req
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

WB_CHAT_BASE = "https://buyer-chat-api.wildberries.ru"
FORM_BASE_URL = "https://cxvo.ru/reshipment/form"

_next_event_ts: int = 0


def _key() -> str:
    return os.getenv("WB_API_KEY", "")


def _h() -> dict:
    return {"Authorization": _key()}


def is_enabled() -> bool:
    return os.getenv("WB_CHAT_ENABLED", "false").lower() == "true" and bool(_key())


def _trigger() -> str:
    return os.getenv("WB_TRIGGER_KEYWORD", "#от").lower()


# ── API вызовы ────────────────────────────────────────────────────────────────

def get_chats() -> list[dict]:
    """Список всех чатов продавца."""
    r = _req.get(f"{WB_CHAT_BASE}/api/v1/seller/chats", headers=_h(), timeout=15)
    r.raise_for_status()
    return (r.json() or {}).get("result") or []


def get_events(next_ts: int = 0) -> dict:
    """Новые события чатов. next_ts = курсор пагинации (мс)."""
    params = {"next": next_ts} if next_ts else {}
    r = _req.get(f"{WB_CHAT_BASE}/api/v1/seller/events", params=params, headers=_h(), timeout=15)
    r.raise_for_status()
    return r.json() or {}


def send_message(reply_sign: str, text: str) -> dict:
    """Отправить сообщение в чат покупателю (multipart)."""
    r = _req.post(
        f"{WB_CHAT_BASE}/api/v1/seller/message",
        files={"replySign": (None, reply_sign), "message": (None, text[:1000])},
        headers=_h(),
        timeout=15,
    )
    r.raise_for_status()
    return r.json() or {}


# ── Бизнес-логика ─────────────────────────────────────────────────────────────

def build_form_url(chat_id: str) -> str:
    return f"{FORM_BASE_URL}?cid={chat_id}"


def send_tracking_notification(reply_sign: str, cdek_number: str, confirm_url: str) -> bool:
    """Уведомить покупателя в WB чате об отправке посылки. True если успешно."""
    if not reply_sign or not _key():
        return False
    msg = (
        f"✅ Ваша деталь отправлена!\n"
        f"Трек-номер СДЭК: {cdek_number}\n"
        f"Отследить: https://www.cdek.ru/track.html?order_id={cdek_number}\n\n"
        f"Когда получите посылку — пожалуйста, подтвердите получение по ссылке:\n"
        f"{confirm_url}"
    )
    try:
        send_message(reply_sign, msg)
        return True
    except Exception as e:
        print(f"[WB Chat] send_tracking error: {e}")
        return False


def send_rejection_notification(reply_sign: str, reason: str) -> bool:
    """Уведомить покупателя об отклонении заявки."""
    if not reply_sign or not _key():
        return False
    msg = (
        f"К сожалению, в данный момент мы не можем выполнить доотправку детали.\n\n"
        f"Причина: {reason}\n\n"
        f"В качестве альтернативы предлагаем оформить возврат товара. "
        f"Свяжитесь с нашей службой поддержки — мы поможем."
    )
    try:
        send_message(reply_sign, msg)
        return True
    except Exception as e:
        print(f"[WB Chat] send_rejection error: {e}")
        return False


# ── Воркер ────────────────────────────────────────────────────────────────────

async def _worker_once(get_db) -> None:
    """Один проход: опросить события, обработать триггеры."""
    global _next_event_ts

    if not _key():
        return

    from sqlalchemy import text as sql_text

    try:
        data = get_events(_next_event_ts)
    except Exception as e:
        print(f"[WB Chat] get_events error: {e}")
        return

    events = data.get("events") or []
    new_next = data.get("next") or _next_event_ts

    for event in events:
        chat_id = str(event.get("chatID", ""))
        message = event.get("message") or {}
        sender = message.get("sender", "")
        text_body = message.get("text", "")

        # Реагируем только на сообщения от продавца с ключевым словом
        if sender != "seller" or _trigger() not in text_body.lower():
            continue

        print(f"[WB Chat] Trigger '{_trigger()}' in chat {chat_id}")

        # Получаем данные чата (reply_sign, clientName, nmId)
        try:
            await asyncio.sleep(1)  # соблюдаем rate limit WB (1 req/s)
            chats = get_chats()
        except Exception as e:
            print(f"[WB Chat] get_chats error: {e}")
            continue

        chat = next((c for c in chats if str(c.get("chatID", "")) == chat_id), None)
        if not chat:
            print(f"[WB Chat] Chat {chat_id} not found in list")
            continue

        reply_sign  = chat.get("replySign", "")
        client_name = chat.get("clientName", "")
        client_id   = str(chat.get("clientID", ""))
        good_card   = chat.get("goodCard") or {}
        nm_id       = good_card.get("nmID")
        rid         = str(good_card.get("rid", ""))

        # Отправляем ссылку на форму (только если включено)
        if is_enabled():
            form_url = build_form_url(chat_id)
            greeting = client_name or "покупатель"
            msg = (
                f"Здравствуйте, {greeting}!\n\n"
                f"Для оформления доотправки детали, пожалуйста, заполните форму — это займёт 2-3 минуты:\n"
                f"{form_url}\n\n"
                f"После проверки мы свяжемся с вами и организуем отправку."
            )
            try:
                send_message(reply_sign, msg)
                print(f"[WB Chat] Form link sent → chat={chat_id}, client={client_name}, nm={nm_id}")
            except Exception as e:
                print(f"[WB Chat] send error: {e}")
        else:
            form_url = build_form_url(chat_id)
            print(f"[WB Chat] MONITOR MODE: would send form link to {chat_id} ({client_name}), nm={nm_id}")
            print(f"[WB Chat]   url: {form_url}")

        # Сохраняем pending запись в БД (в любом режиме — для связки когда форма придёт)
        try:
            db = next(get_db())
            db.execute(sql_text("""
                INSERT INTO wb_chat_pending
                    (chat_id, reply_sign, client_name, client_id, nm_id, rid, status)
                VALUES (:cid, :rs, :cn, :ci, :nm, :rid, 'waiting_form')
                ON CONFLICT (chat_id) DO UPDATE SET
                    reply_sign  = EXCLUDED.reply_sign,
                    client_name = EXCLUDED.client_name,
                    updated_at  = NOW()
            """), {
                "cid": chat_id,
                "rs":  reply_sign,
                "cn":  client_name,
                "ci":  client_id,
                "nm":  nm_id,
                "rid": rid,
            })
            db.commit()
        except Exception as e:
            print(f"[WB Chat] DB pending write error: {e}")

    _next_event_ts = new_next


async def start_worker(get_db) -> None:
    """Фоновый asyncio воркер. Запускается из lifespan FastAPI."""
    print("[WB Chat] Worker started (polls every 60s)")
    while True:
        await asyncio.sleep(60)
        if _key():
            await _worker_once(get_db)


def init_event_cursor() -> None:
    """Вызвать при старте приложения: пропустить старые события, начать с текущего момента."""
    global _next_event_ts
    if not _key():
        return
    try:
        data = get_events(0)
        # Берём самый новый timestamp — с него начнём при следующем поллинге
        _next_event_ts = data.get("newestEventTime") or data.get("next") or 0
        print(f"[WB Chat] Initialized, next_ts={_next_event_ts}, skipped existing events")
    except Exception as e:
        print(f"[WB Chat] Init cursor failed (no WB API key?): {e}")
