#!/usr/bin/env python3
"""Входящие сообщения бота: забирать с Mac и сразу отдавать на cxvo.ru.

Ответы бота (меню с кнопками) шлём отсюда напрямую в Telegram —
Cloudflare Worker кнопки часто отбрасывает.
"""
from __future__ import annotations

import hashlib
import os
import sys
import time

import requests

ORIGIN = os.getenv(
    "TSHOPPING_WEBHOOK_URL",
    "https://cxvo.ru/api/v1/tshopping/telegram-webhook",
)

ABOUT = "Заявки в CX Видовито. Нажмите «Оставить заявку», укажите номер заказа и проблему."
DESCRIPTION = (
    "Бот заявок в клиентский сервис Видовито.\n\n"
    "Нажмите кнопку «Оставить заявку».\n"
    "Напишите номер заказа и в чём проблема.\n"
    "Ответ придёт в этот чат."
)


def secret_for(token: str) -> str:
    explicit = (os.getenv("TG_WEBHOOK_SECRET") or "").strip()
    if explicit:
        return explicit
    return hashlib.sha256(f"tshopping:{token}".encode("utf-8")).hexdigest()[:32]


def _chat_id(update: dict):
    msg = update.get("message") or update.get("edited_message") or {}
    if not msg and isinstance(update.get("callback_query"), dict):
        msg = (update["callback_query"].get("message") or {})
    chat = msg.get("chat") or {}
    return chat.get("id")


def apply_bot_profile(base: str) -> None:
    requests.post(f"{base}/setMyShortDescription", json={"short_description": ABOUT}, timeout=20)
    requests.post(f"{base}/setMyDescription", json={"description": DESCRIPTION}, timeout=20)
    requests.post(
        f"{base}/setMyCommands",
        json={"commands": [
            {"command": "start", "description": "Меню"},
            {"command": "new", "description": "Оставить заявку"},
            {"command": "cancel", "description": "Отмена"},
        ]},
        timeout=20,
    )


def main() -> int:
    token = (os.getenv("TG_ALERT_BOT_TOKEN") or os.getenv("TG_BOT_TOKEN") or "").strip()
    if not token:
        print("Нет TG_ALERT_BOT_TOKEN", file=sys.stderr)
        return 1
    secret = secret_for(token)
    base = f"https://api.telegram.org/bot{token}"
    requests.post(
        f"{base}/deleteWebhook",
        json={"drop_pending_updates": False},
        timeout=20,
    )
    apply_bot_profile(base)
    print("poller on", flush=True)
    offset = 0
    while True:
        try:
            resp = requests.get(
                f"{base}/getUpdates",
                params={
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": ["message", "edited_message", "callback_query"],
                },
                timeout=40,
            )
            data = resp.json() if resp.ok else {}
            for upd in data.get("result") or []:
                uid = int(upd.get("update_id") or 0)
                offset = max(offset, uid + 1)
                fwd = requests.post(
                    ORIGIN,
                    json=upd,
                    headers={
                        "X-Telegram-Bot-Api-Secret-Token": secret,
                        "X-Tshopping-Poller": "1",
                    },
                    timeout=20,
                )
                out = fwd.json() if fwd.ok else {}
                reply = (out.get("bot_reply") or "").strip()
                chat_id = out.get("tg_chat_id") or _chat_id(upd)
                if reply and chat_id:
                    payload = {"chat_id": chat_id, "text": reply}
                    markup = out.get("bot_markup")
                    if markup:
                        payload["reply_markup"] = markup
                    requests.post(f"{base}/sendMessage", json=payload, timeout=20)
            if not data.get("ok"):
                time.sleep(2)
        except Exception as e:
            print("poller error:", e, file=sys.stderr, flush=True)
            time.sleep(3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
