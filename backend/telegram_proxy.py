"""Вызовы Telegram Bot API.

С RU-сервера прямой api.telegram.org часто недоступен, поэтому основной путь —
Cloudflare Worker (CF_WORKER_URL). Прямой вызов — запасной вариант (Mac, деплой).

Старый воркер понимает {chat_id, text} и шлёт sendMessage. Новые методы
передаём как {method, ...params}; если воркер их не знает, пробуем напрямую.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

import requests

logger = logging.getLogger("telegram_proxy")

CF_WORKER_URL = os.getenv("CF_WORKER_URL", "https://lingering-recipe-b1b3.mursalimov.workers.dev")
TG_API_BASE = "https://api.telegram.org"
_TOKEN_IN_URL = re.compile(r"/bot\d+:[A-Za-z0-9_-]+")
_TG_HTML_TAG = re.compile(r"</?(?:b|i|code)>", re.I)


def strip_tg_html(msg: str) -> str:
    """Cloudflare Worker часто шлёт sendMessage без parse_mode — теги видны как текст."""
    return _TG_HTML_TAG.sub("", msg or "")


def _redact(value: Any) -> str:
    text = str(value or "")
    token = bot_token()
    if token:
        text = text.replace(token, "***")
    return _TOKEN_IN_URL.sub("/bot***", text)


def bot_token() -> str:
    return (os.getenv("TG_ALERT_BOT_TOKEN") or os.getenv("TG_BOT_TOKEN") or "").strip()


def _direct_url(method: str, token: Optional[str] = None) -> str:
    tok = token or bot_token()
    return f"{TG_API_BASE}/bot{tok}/{method}"


def _file_url(file_path: str, token: Optional[str] = None) -> str:
    tok = token or bot_token()
    return f"{TG_API_BASE}/file/bot{tok}/{file_path.lstrip('/')}"


def _ok_json(resp: requests.Response) -> Optional[dict]:
    try:
        data = resp.json()
    except Exception:
        return None
    if isinstance(data, dict) and data.get("ok") is True:
        return data
    return None


def call(method: str, payload: Optional[dict] = None, *, timeout: int = 8) -> Optional[dict]:
    """Вызвать метод Bot API. Возвращает JSON Telegram ({ok, result}) или None.

    Прямой api.telegram.org с сервера часто недоступен: запасной вызов
    короткий, чтобы webhook Telegram не висел минутами.
    """
    payload = dict(payload or {})
    method = (method or "").strip()
    if not method:
        return None

    worker_timeout = timeout
    # connect, read — не ждать 20+ сек, иначе Telegram ретраит webhook.
    direct_timeout = (1.5, min(3, timeout))

    if CF_WORKER_URL:
        # Старый воркер шлёт sendMessage только по {chat_id, text}.
        if method == "sendMessage" and "text" in payload and "chat_id" in payload:
            body = {"chat_id": payload["chat_id"], "text": payload["text"]}
            if payload.get("reply_markup"):
                body["reply_markup"] = payload["reply_markup"]
            if payload.get("parse_mode"):
                body["parse_mode"] = payload["parse_mode"]
                body["disable_web_page_preview"] = True
        else:
            body = {"method": method, **payload}
        try:
            resp = requests.post(CF_WORKER_URL, json=body, timeout=worker_timeout)
            data = _ok_json(resp)
            if data:
                return data
            if method == "sendMessage" and resp.ok and not (resp.text or "").strip():
                return {"ok": True, "result": {}}
            if resp.ok and method == "sendMessage":
                # Воркер мог ответить не в формате Bot API, но HTTP 200 = ушло.
                try:
                    maybe = resp.json()
                except Exception:
                    maybe = None
                if maybe is None or maybe.get("ok") is not False:
                    return {"ok": True, "result": maybe or {}}
            logger.warning(
                "[TG] CF Worker %s %s: %s",
                method, resp.status_code, _redact((resp.text or "")[:200]),
            )
        except Exception as e:
            logger.warning("[TG] CF Worker %s exception: %s", method, _redact(e))

    token = bot_token()
    if not token:
        return None
    try:
        resp = requests.post(_direct_url(method, token), json=payload, timeout=direct_timeout)
        data = _ok_json(resp)
        if data:
            return data
        logger.warning(
            "[TG] direct %s %s: %s",
            method, resp.status_code, _redact((resp.text or "")[:200]),
        )
    except Exception as e:
        logger.warning("[TG] direct %s exception: %s", method, _redact(e))
    return None


def send_text(chat_id: Any, text: str, *, parse_mode: str = "", reply_markup: Optional[dict] = None) -> bool:
    if not chat_id or not (text or "").strip():
        return False
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
        payload["disable_web_page_preview"] = True
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return call("sendMessage", payload) is not None


def send_photo(chat_id: Any, photo_url: str, caption: str = "") -> bool:
    if not chat_id or not photo_url:
        return False
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption or "",
        "text": caption or photo_url,
    }
    if call("sendPhoto", payload):
        return True
    # Запас: ссылка текстом, если воркер умеет только sendMessage.
    link_text = caption.strip() + "\n" + photo_url if caption.strip() else photo_url
    return send_text(chat_id, link_text)


def send_document(chat_id: Any, document_url: str, caption: str = "") -> bool:
    if not chat_id or not document_url:
        return False
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "document": document_url,
        "caption": caption or "",
        "text": caption or document_url,
    }
    if call("sendDocument", payload):
        return True
    link_text = caption.strip() + "\n" + document_url if caption.strip() else document_url
    return send_text(chat_id, link_text)


def download_file(file_id: str, dest_path: str) -> bool:
    """Скачать файл Telegram по file_id. True, если записали dest_path."""
    if not file_id:
        return False
    info = call("getFile", {"file_id": file_id})
    result = (info or {}).get("result") or {}
    file_path = str(result.get("file_path") or "").strip()
    if not file_path:
        return False

    token = bot_token()
    url = _file_url(file_path, token) if token else ""
    sources = []
    if CF_WORKER_URL:
        sources.append(("worker", CF_WORKER_URL, {"method": "downloadFile", "file_path": file_path, "file_id": file_id}))
    if url:
        sources.append(("direct", url, None))

    for kind, target, body in sources:
        try:
            if body is not None:
                resp = requests.post(target, json=body, timeout=30)
            else:
                resp = requests.get(target, timeout=30)
            if not resp.ok or not resp.content:
                continue
            # Воркер мог вернуть JSON с ошибкой вместо байтов файла.
            ctype = (resp.headers.get("content-type") or "").lower()
            if "application/json" in ctype:
                continue
            os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
            with open(dest_path, "wb") as fh:
                fh.write(resp.content)
            return True
        except Exception as e:
            logger.warning("[TG] download %s failed: %s", kind, e)
    return False
