#!/usr/bin/env python3
"""Поставить webhook Telegram-бота поддержки Т-Шопинга.

Запускать с Mac (с сервера api.telegram.org часто недоступен):

  python3 scripts/set_tshopping_webhook.py

Токен берётся из TG_ALERT_BOT_TOKEN в окружении.
"""
import hashlib
import os
import sys
import requests

WEBHOOK_URL = os.getenv(
    "TSHOPPING_WEBHOOK_URL",
    "https://cxvo.ru/api/v1/tshopping/telegram-webhook",
)


def secret_for(token: str) -> str:
    explicit = (os.getenv("TG_WEBHOOK_SECRET") or "").strip()
    if explicit:
        return explicit
    return hashlib.sha256(f"tshopping:{token}".encode("utf-8")).hexdigest()[:32]


def main() -> int:
    token = (os.getenv("TG_ALERT_BOT_TOKEN") or os.getenv("TG_BOT_TOKEN") or "").strip()
    if not token:
        print("Нет TG_ALERT_BOT_TOKEN. Задайте в окружении и повторите.")
        return 1
    secret = secret_for(token)
    payload = {
        "url": WEBHOOK_URL,
        "ip_address": os.getenv("TSHOPPING_WEBHOOK_IP", "72.56.240.22"),
        "secret_token": secret,
        "allowed_updates": ["message", "edited_message", "callback_query"],
        "drop_pending_updates": False,
    }
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json=payload,
        timeout=20,
    )
    print(resp.status_code, resp.text[:500])
    info = requests.get(
        f"https://api.telegram.org/bot{token}/getWebhookInfo",
        timeout=20,
    )
    print("info:", info.text[:500])
    cmds = requests.post(
        f"https://api.telegram.org/bot{token}/setMyCommands",
        json={"commands": [
            {"command": "start", "description": "Меню"},
            {"command": "new", "description": "Оставить заявку"},
            {"command": "cancel", "description": "Отменить заявку"},
        ]},
        timeout=20,
    )
    print("commands:", cmds.status_code, cmds.text[:300])
    data = resp.json() if resp.ok else {}
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
