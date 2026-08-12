"""Единая точка входа в API Ozon (Seller API) для CX-модулей (чаты, отзывы).

ozon_worker.py и backend/ozon_chat_sync.py независимо друг от друга завели
одинаковый по смыслу _post(path, body) с Client-Id/Api-Key — теперь это
единственная реализация, оба модуля используют её.
"""
from __future__ import annotations

import os
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BASE = "https://api-seller.ozon.ru"


def client_id() -> str:
    return os.getenv("OZON_CLIENT_ID", "").strip()


def api_key() -> str:
    return os.getenv("OZON_API_KEY", "").strip()


def headers(client_id_: str | None = None, key: str | None = None) -> dict:
    return {
        "Client-Id":    client_id_ or client_id(),
        "Api-Key":      key or api_key(),
        "Content-Type": "application/json",
    }


def post(path: str, body: dict, *, timeout: int = 30) -> dict:
    """POST helper. 429 -> RuntimeError понятного вида (как было в ozon_worker.py);
    остальные ошибки — обычный requests.HTTPError через raise_for_status()."""
    r = requests.post(f"{BASE}{path}", headers=headers(), json=body, timeout=timeout)
    if r.status_code == 429:
        raise RuntimeError(f"Rate limit (429) на {path}")
    r.raise_for_status()
    return r.json() or {}


# ── Отзывы ────────────────────────────────────────────────────────────────────

def answer_review(review_id: str, text: str, *, timeout: int = 20) -> dict:
    """POST — ответить на отзыв Ozon (требует подписку Premium Plus / «Управление отзывами»)."""
    return post("/v1/review/comment/create", {"review_uuid": review_id, "text": text}, timeout=timeout)


# ── Вопросы о товарах ────────────────────────────────────────────────────────

def list_questions_page(*, body: dict, timeout: int = 25) -> dict:
    """Одна страница вопросов Ozon; пагинацию ведёт ``ozon_worker.py``."""
    return post("/v1/question/list", body, timeout=timeout)


def answer_question(question_id: str, sku: int, text: str, *, timeout: int = 20) -> dict:
    """Опубликовать ответ на вопрос о товаре Ozon.

    В отличие от WB, Ozon Question API не предлагает отдельную private/public
    видимость: ответ относится к публичному Q&A карточки товара.
    """
    qid = (question_id or "").strip()
    txt = (text or "").strip()
    if not qid:
        raise ValueError("question_id пустой")
    if int(sku or 0) <= 0:
        raise ValueError("sku вопроса отсутствует")
    if not 2 <= len(txt) <= 3000:
        raise ValueError("ответ Ozon должен содержать от 2 до 3000 символов")
    return post(
        "/v1/question/answer/create",
        {"question_id": qid, "sku": int(sku), "text": txt},
        timeout=timeout,
    )


# ── Чаты ──────────────────────────────────────────────────────────────────────

def list_chats_page(*, body: dict, timeout: int = 25) -> dict:
    """Одна страница /v3/chat/list. Пагинация (cursor/has_next) остаётся на
    стороне ozon_chat_sync.py — здесь только сам вызов."""
    return post("/v3/chat/list", body, timeout=timeout)


def fetch_chat_history(chat_id: str, *, limit: int = 50, timeout: int = 25) -> dict:
    return post("/v3/chat/history", {"chat_id": chat_id, "limit": limit}, timeout=timeout)


def start_chat(posting_number: str, *, timeout: int = 25) -> dict:
    """POST /v1/chat/start — создать чат с покупателем по номеру отправления.

    Требует Premium Plus. Если чат уже есть — обычно возвращает существующий chat_id.
    """
    if not client_id() or not api_key():
        raise RuntimeError("OZON_CLIENT_ID / OZON_API_KEY не заданы")
    pn = (posting_number or "").strip()
    if not pn:
        raise ValueError("posting_number пустой")
    data = post("/v1/chat/start", {"posting_number": pn}, timeout=timeout)
    return data.get("result") or data


def send_chat_message(chat_id: str, text: str, *, timeout: int = 20) -> None:
    """Отправка текста покупателю через Seller Chat API."""
    if not client_id() or not api_key():
        raise RuntimeError("OZON_CLIENT_ID / OZON_API_KEY не заданы")
    txt = (text or "").strip()
    if not txt:
        raise ValueError("пустой текст")
    post("/v1/chat/send/message", {"chat_id": chat_id, "text": txt[:1000]}, timeout=timeout)


def send_chat_file(
    chat_id: str,
    name: str,
    base64_content: str,
    *,
    timeout: int = 20,
) -> None:
    """Отправить файл в чат через ``POST /v1/chat/send/file``."""
    if not client_id() or not api_key():
        raise RuntimeError("OZON_CLIENT_ID / OZON_API_KEY не заданы")
    filename = (name or "").strip()
    encoded = (base64_content or "").strip()
    if not filename or not encoded:
        raise ValueError("имя и содержимое файла обязательны")
    post(
        "/v1/chat/send/file",
        {"chat_id": chat_id, "name": filename, "base64_content": encoded},
        timeout=timeout,
    )


def download_chat_file(url: str, *, timeout: int = 30) -> requests.Response:
    """Прокси вложений чата: url — полный адрес api-seller.ozon.ru/v2/chat/file/...
    (не REST-путь, поэтому не через post())."""
    r = requests.get(url, headers={"Client-Id": client_id(), "Api-Key": api_key()}, timeout=timeout)
    r.raise_for_status()
    return r
