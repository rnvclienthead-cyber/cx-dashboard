"""Telegram Mini App: проверка входа и ссылка «открыть чат в боте»."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qsl, quote

from . import telegram_proxy

logger = logging.getLogger("telegram_webapp")

SITE_ORIGIN = os.getenv("PUBLIC_SITE_ORIGIN", "https://cxvo.ru").rstrip("/")
WEBAPP_ORIGIN = os.getenv(
    "TG_WEBAPP_ORIGIN",
    os.getenv("CF_WORKER_URL", "https://lingering-recipe-b1b3.mursalimov.workers.dev"),
).rstrip("/")
BOT_USERNAME_FALLBACK = "cxvidovito_bot"
MAX_INIT_AGE_SEC = 24 * 3600
_MP_CODE = {"wb": "w", "ozon": "o", "ym": "y"}
_CODE_MP = {v: k for k, v in _MP_CODE.items()}
_cached_username = ""


def site_chat_url(mp: str, chat_id: str) -> str:
    mp_key = (mp or "wb").lower()
    if mp_key not in _MP_CODE:
        mp_key = "wb"
    origin = (WEBAPP_ORIGIN or SITE_ORIGIN).rstrip("/")
    return (
        f"{origin}/chats?chat={quote(str(chat_id), safe='')}&mp={quote(mp_key)}"
    )


def encode_start_param(mp: str, chat_id: str) -> str:
    """startapp: до 64 символов, только A-Za-z0-9_-."""
    mp_key = (mp or "wb").lower()
    code = _MP_CODE.get(mp_key, "w")
    cid = str(chat_id or "").replace(":", "__")
    cid = re.sub(r"[^A-Za-z0-9_-]", "_", cid)
    return f"{code}_{cid}"[:64]


def parse_start_param(param: str) -> tuple[str, str]:
    raw = (param or "").strip()
    if len(raw) >= 2 and raw[1] == "_" and raw[0] in _CODE_MP:
        return _CODE_MP[raw[0]], raw[2:].replace("__", ":", 1)
    return "wb", raw


def bot_username() -> str:
    global _cached_username
    env_name = (os.getenv("TG_BOT_USERNAME") or "").strip().lstrip("@")
    if env_name:
        return env_name
    if _cached_username:
        return _cached_username
    data = telegram_proxy.call("getMe", timeout=5)
    name = str(((data or {}).get("result") or {}).get("username") or "").strip()
    if name:
        _cached_username = name
        return name
    return BOT_USERNAME_FALLBACK


def mini_app_link(mp: str, chat_id: str) -> str:
    return f"https://t.me/{bot_username()}?startapp={encode_start_param(mp, chat_id)}"


def _is_private_chat(chat_id: Any) -> bool:
    try:
        return int(chat_id) > 0
    except (TypeError, ValueError):
        return False


def chat_open_markup(mp: str, chat_id: str, dest_chat_id: Any) -> dict:
    """В личке — Mini App внутри Telegram. В группе web_app нельзя, там обычная ссылка."""
    site = site_chat_url(mp, chat_id)
    if _is_private_chat(dest_chat_id):
        return {"inline_keyboard": [[{"text": "Открыть чат", "web_app": {"url": site}}]]}
    return {"inline_keyboard": [[{"text": "Открыть чат", "url": site}]]}


def validate_init_data(init_data: str, bot_token: str = "", *, now: Optional[float] = None) -> dict:
    """Проверяет подпись Mini App. Возвращает объект user."""
    raw = (init_data or "").strip()
    token = (bot_token or telegram_proxy.bot_token() or "").strip()
    if not raw or not token:
        raise ValueError("нет данных Telegram")
    parsed = dict(parse_qsl(raw, keep_blank_values=True))
    received = parsed.pop("hash", "")
    if not received:
        raise ValueError("нет подписи")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    calc = hmac.new(secret, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        raise ValueError("подпись не совпала")
    auth_date = int(parsed.get("auth_date") or 0)
    stamp = now if now is not None else time.time()
    if auth_date <= 0 or abs(stamp - auth_date) > MAX_INIT_AGE_SEC:
        raise ValueError("данные устарели")
    user = json.loads(parsed.get("user") or "{}")
    if not isinstance(user, dict) or not user.get("id"):
        raise ValueError("нет пользователя")
    return user


def configure_menu_button() -> bool:
    """Кнопка меню бота открывает экран чатов. Нужно для ссылок t.me/?startapp=."""
    result = telegram_proxy.call("setChatMenuButton", {
        "menu_button": {
            "type": "web_app",
            "text": "Чаты",
            "web_app": {"url": f"{WEBAPP_ORIGIN}/chats"},
        }
    }, timeout=8)
    ok = bool(result)
    if ok:
        logger.info("[TG WebApp] menu button → %s/chats", WEBAPP_ORIGIN)
    else:
        logger.warning("[TG WebApp] не удалось поставить кнопку меню Mini App")
    return ok
