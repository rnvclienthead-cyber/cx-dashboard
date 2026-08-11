"""Единая точка входа в API Яндекс Маркета для CX-модулей (чаты, отзывы, вопросы).

ym_worker.py уже имел собственный аккуратный слой _request/_get/_post — он
просто переехал сюда без изменений в поведении, чтобы routers/cx_responses.py
(ответы на отзывы/вопросы) и backend/ym_chat_sync.py (чат) могли пользоваться
тем же кодом вместо копий с ключами/URL "от руки".
"""
from __future__ import annotations

import os
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BASE = "https://api.partner.market.yandex.ru"


class RateLimitError(Exception):
    """420 от API ЯМ — на следующем cron-запуске/цикле само пройдёт."""
    pass


def api_key() -> str:
    return os.getenv("YM_API_KEY", "").strip()


def business_id() -> str:
    return os.getenv("YM_BUSINESS_ID", "").strip()


def campaign_id() -> str:
    return os.getenv("YM_CAMPAIGN_ID", "").strip()


def headers(key: str | None = None) -> dict:
    return {"Api-Key": key or api_key(), "Content-Type": "application/json"}


def request(method: str, path: str, *, params=None, json_body=None, key: str | None = None, timeout: int = 30) -> requests.Response:
    """HTTP-запрос к ЯМ. При 420 (rate limit) — сразу RateLimitError, дальше
    caller сам решает: пропустить задачу (воркеры) или отдать 502 (роутер)."""
    r = getattr(requests, method)(
        f"{BASE}{path}", headers=headers(key), timeout=timeout, params=params, json=json_body,
    )
    if r.status_code == 420:
        raise RateLimitError(f"420 rate limit на {path}. Попробуем позже.")
    r.raise_for_status()
    return r


def get(path: str, params: dict | None = None, *, key: str | None = None, timeout: int = 30) -> dict:
    return request("get", path, params=params or {}, key=key, timeout=timeout).json()


def post(path: str, body: dict | None = None, *, params: dict | None = None, key: str | None = None, timeout: int = 30) -> dict:
    return request("post", path, params=params, json_body=body if body is not None else {}, key=key, timeout=timeout).json()


# ── Отзывы и вопросы ──────────────────────────────────────────────────────────

def answer_feedback(feedback_id: str, text: str, *, biz: str | None = None, key: str | None = None, timeout: int = 20) -> dict:
    """POST — опубликовать комментарий к отзыву ЯМ. Раньше дублировалось 3 раза
    в cx_responses.py (прямой ответ, подтверждение модерации, авто-ответ по шаблону)."""
    biz = biz or business_id()
    return post(f"/v2/businesses/{biz}/goods-feedback/comments",
                {"feedbackId": feedback_id, "text": text}, key=key, timeout=timeout)


def answer_question(question_id: str, text: str, *, biz: str | None = None, key: str | None = None, timeout: int = 20) -> dict:
    """POST — ответить на вопрос ЯМ (эндпоинт .../questions/{id}/answers — не
    путать с .../goods-questions, который используется только для листинга)."""
    biz = biz or business_id()
    return post(f"/v1/businesses/{biz}/questions/{question_id}/answers",
                {"text": text}, key=key, timeout=timeout)


# ── Чаты ──────────────────────────────────────────────────────────────────────

def list_chats(statuses: list[str], *, limit: int = 50, biz: str | None = None, key: str | None = None, timeout: int = 30) -> list[dict]:
    biz = biz or business_id()
    data = post(f"/v2/businesses/{biz}/chats", {"statuses": statuses, "limit": limit}, key=key, timeout=timeout)
    result = data.get("result") or data
    return result.get("chats") or []


def fetch_chat_history(chat_id: int | str, *, biz: str | None = None, key: str | None = None, timeout: int = 30) -> dict:
    biz = biz or business_id()
    data = post(f"/v2/businesses/{biz}/chats/history", {}, params={"chatId": chat_id}, key=key, timeout=timeout)
    return data.get("result") or data


def create_chat(*, context_type: str, context_id: int | str,
                 biz: str | None = None, key: str | None = None, timeout: int = 25) -> dict:
    """POST /v2/businesses/{id}/chats/new — создать чат по заказу (ORDER) или возврату (RETURN).

    Если чат с таким контекстом уже есть — API вернёт существующий chatId.
    """
    biz = biz or business_id()
    ctype = (context_type or "").strip().upper()
    if ctype not in ("ORDER", "RETURN"):
        raise ValueError("context_type должен быть ORDER или RETURN")
    try:
        cid = int(context_id)
    except (TypeError, ValueError) as e:
        raise ValueError(f"context_id должен быть числом: {context_id!r}") from e
    data = post(
        f"/v2/businesses/{biz}/chats/new",
        {"context": {"type": ctype, "id": cid}},
        key=key, timeout=timeout,
    )
    return data.get("result") or data


def send_chat_message(chat_id: str, text: str, *, biz: str | None = None, key: str | None = None, timeout: int = 25) -> None:
    """Отправка текста. Требует scope communication у YM_API_KEY."""
    biz = biz or business_id()
    cid: int | str = int(chat_id) if str(chat_id).isdigit() else chat_id
    r = requests.post(
        f"{BASE}/v2/businesses/{biz}/chats/message",
        headers=headers(key), params={"chatId": cid}, json={"message": text[:4000]}, timeout=timeout,
    )
    if r.status_code == 403:
        raise PermissionError(
            "YM токен без scope communication/all-methods — отправка недоступна. "
            "Выпустите новый Api-Key с правом «Общение с покупателями»."
        )
    r.raise_for_status()


def download_chat_file(url: str, *, key: str | None = None, timeout: int = 30) -> requests.Response:
    """Прокси вложений чата ЯМ: url — полный адрес (не REST-путь), поэтому не через get()/post()."""
    r = requests.get(url, headers=headers(key), timeout=timeout)
    r.raise_for_status()
    return r
