"""Единая точка входа в API Wildberries для CX-модулей (чаты, отзывы, вопросы, возвраты).

До этого модуля один и тот же URL/заголовок авторизации был написан заново
в 6+ местах (worker.py, wb_mini_sync.py, wb_chat.py, wb_chat_sync.py,
wb_claims_api.py, routers/cx_responses.py) — каждый раз с чуть своими нюансами
(таймауты, ретраи на 429, наличие фоллбэка на другой токен). Теперь URL и
заголовки живут только здесь; воркеры и роутеры зовут эти функции и оставляют
себе только свою бизнес-логику (пагинацию, ретраи, разбор ответа под свою БД).

Специально НЕ трогаем поведение при ошибках там, где вызывающий код сам
разбирает status_code (пагинация по 422, ретраи по 429 с разным графиком
в разных местах) — для таких эндпоинтов ниже есть *_raw() версии, которые
просто возвращают requests.Response без raise_for_status(), чтобы вызывающий
код продолжал работать ровно как раньше. Там, где все вызывающие места и так
хотели raise_for_status()+parse (например answer_feedback), это сделано прямо
в функции клиента.
"""
from __future__ import annotations

import os
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

RETURNS_BASE     = "https://returns-api.wildberries.ru"
FEEDBACKS_BASE   = "https://feedbacks-api.wildberries.ru"
CHAT_BASE        = "https://buyer-chat-api.wildberries.ru"
CONTENT_BASE     = "https://content-api.wildberries.ru"


def api_key() -> str:
    """Основной токен WB. Фоллбэк на WB_RETURNS_TOKEN/WB_FEEDBACKS_TOKEN — как
    было в части мест (wb_claims_api, photo-sync) — не меняет поведение там,
    где WB_API_KEY задан (обычный случай), только подстраховывает остальные."""
    return (
        os.getenv("WB_API_KEY", "")
        or os.getenv("WB_RETURNS_TOKEN", "")
        or os.getenv("WB_FEEDBACKS_TOKEN", "")
        or ""
    ).strip().strip('"')


def content_key() -> str:
    """Токен для Content API (фото карточек) — отдельное право «Контент»."""
    return (
        os.getenv("WB_CONTENT_KEY", "")
        or os.getenv("WB_API_KEY", "")
        or os.getenv("WB_FEEDBACKS_TOKEN", "")
        or ""
    ).strip()


def auth_headers(key: str | None = None) -> dict:
    return {"Authorization": key or api_key(), "Content-Type": "application/json"}


# ── Претензии / возвраты (Returns API) ──────────────────────────────────────

def get_claims_page_raw(
    *, is_archive: bool, limit: int = 100, offset: int = 0,
    claim_id=None, nm_id=None, key: str | None = None, timeout: int = 30,
) -> requests.Response:
    """Одна страница претензий, без raise_for_status — вызывающий код сам решает,
    что делать с 429/иными кодами (у каждого места сейчас свой график ретраев)."""
    params: dict = {"limit": limit, "offset": offset, "is_archive": "true" if is_archive else "false"}
    if claim_id:
        params["id"] = claim_id
    if nm_id:
        params["nm_id"] = int(nm_id) if str(nm_id).isdigit() else nm_id
    return requests.get(
        f"{RETURNS_BASE}/api/v1/claims", params=params,
        headers=auth_headers(key), timeout=timeout,
    )


def patch_claim_raw(
    claim_id: str, action: str, comment: str = "",
    *, key: str | None = None, timeout: int = 20,
) -> requests.Response:
    """PATCH /api/v1/claim — решение по претензии. Без raise_for_status: вызывающий
    (wb_claims_api.patch_claim) сам формирует понятный ответ для UI по status_code."""
    body: dict = {"id": str(claim_id), "action": action}
    if comment:
        body["comment"] = comment[:1000]
    return requests.patch(
        f"{RETURNS_BASE}/api/v1/claim", headers=auth_headers(key),
        json=body, timeout=timeout,
    )


# ── Отзывы (Feedbacks API) ───────────────────────────────────────────────────

def list_feedbacks_raw(
    *, is_answered: bool, take: int = 100, skip: int = 0, order: str = "dateDesc",
    nm_id=None, key: str | None = None, timeout: int = 30,
) -> requests.Response:
    params: dict = {"isAnswered": "true" if is_answered else "false", "take": take, "skip": skip, "order": order}
    if nm_id:
        params["nm"] = int(nm_id) if str(nm_id).isdigit() else nm_id
    return requests.get(
        f"{FEEDBACKS_BASE}/api/v1/feedbacks", params=params,
        headers=auth_headers(key), timeout=timeout,
    )


def list_feedbacks(
    *, is_answered: bool, take: int = 100, skip: int = 0, order: str = "dateDesc",
    nm_id=None, key: str | None = None, timeout: int = 30,
) -> list:
    """Разобранный список отзывов. WB отдаёт 422 на конце пагинации — это не
    ошибка, а сигнал «дальше пусто», поэтому здесь превращается в []."""
    r = list_feedbacks_raw(is_answered=is_answered, take=take, skip=skip, order=order,
                            nm_id=nm_id, key=key, timeout=timeout)
    if r.status_code == 422:
        return []
    r.raise_for_status()
    return (r.json() or {}).get("data", {}).get("feedbacks") or []


def list_feedbacks_archive_raw(
    *, take: int = 5000, skip: int = 0, order: str = "dateDesc",
    key: str | None = None, timeout: int = 30,
) -> requests.Response:
    return requests.get(
        f"{FEEDBACKS_BASE}/api/v1/feedbacks/archive",
        params={"isAnswered": "true", "take": take, "skip": skip, "order": order},
        headers=auth_headers(key), timeout=timeout,
    )


def answer_feedback(feedback_id: str, text: str, *, key: str | None = None, timeout: int = 20) -> requests.Response:
    """PATCH — опубликовать/обновить ответ на отзыв WB. Раньше был продублирован
    3 раза в cx_responses.py (прямой ответ, подтверждение модерации, авто-ответ по шаблону)."""
    r = requests.patch(
        f"{FEEDBACKS_BASE}/api/v1/feedbacks",
        headers=auth_headers(key), json={"id": feedback_id, "text": text}, timeout=timeout,
    )
    r.raise_for_status()
    return r


# ── Вопросы (Feedbacks API) ──────────────────────────────────────────────────

def list_questions_raw(
    *, is_answered: bool, take: int = 100, skip: int = 0, order: str = "dateDesc",
    key: str | None = None, timeout: int = 30,
) -> requests.Response:
    params = {"isAnswered": "true" if is_answered else "false", "take": take, "skip": skip, "order": order}
    return requests.get(
        f"{FEEDBACKS_BASE}/api/v1/questions", params=params,
        headers=auth_headers(key), timeout=timeout,
    )


def list_questions(
    *, is_answered: bool, take: int = 100, skip: int = 0, order: str = "dateDesc",
    key: str | None = None, timeout: int = 30,
) -> list:
    r = list_questions_raw(is_answered=is_answered, take=take, skip=skip, order=order, key=key, timeout=timeout)
    if r.status_code == 422:
        return []
    r.raise_for_status()
    return (r.json() or {}).get("data", {}).get("questions") or []


def answer_question(question_id: str, text: str, *, key: str | None = None, timeout: int = 20) -> requests.Response:
    """PATCH /api/v1/questions — ответ автору вопроса (= кнопка «Отправить автору» в ЛК).

    Тело по доке WB: id + answer.text + state=wbRu (публикация ответа).
    """
    txt = (text or "").strip()
    if not txt:
        raise ValueError("пустой текст ответа")
    r = requests.patch(
        f"{FEEDBACKS_BASE}/api/v1/questions",
        headers=auth_headers(key),
        json={
            "id": question_id,
            "answer": {"text": txt},
            "state": "wbRu",
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r


# ── Чаты (Buyer Chat API) ─────────────────────────────────────────────────────

def get_chats(*, key: str | None = None, timeout: int = 90) -> list:
    r = requests.get(f"{CHAT_BASE}/api/v1/seller/chats", headers=auth_headers(key), timeout=timeout)
    r.raise_for_status()
    return (r.json() or {}).get("result") or []


def get_chat_events_raw(next_ts: int = 0, *, key: str | None = None, timeout: int = 25) -> requests.Response:
    """Без raise_for_status: wb_chat_sync сам ретраит на 429 по своему графику."""
    params = {"next": next_ts} if next_ts else {}
    return requests.get(f"{CHAT_BASE}/api/v1/seller/events", params=params, headers=auth_headers(key), timeout=timeout)


def get_chat_messages(chat_id: str, take: int = 30, *, key: str | None = None, timeout: int = 15) -> list:
    r = requests.get(
        f"{CHAT_BASE}/api/v1/seller/chat/messages",
        headers=auth_headers(key), params={"chatID": chat_id, "take": take}, timeout=timeout,
    )
    r.raise_for_status()
    return (r.json() or {}).get("messages") or []


def send_chat_message(
    reply_sign: str,
    text: str = "",
    *,
    attachments: list[tuple[str, bytes, str]] | None = None,
    key: str | None = None,
    timeout: int = 20,
) -> requests.Response:
    """Отправить текст и/или файлы в чат покупателю через multipart WB.

    ``attachments`` содержит кортежи ``(filename, content, content_type)``.
    Повторяющееся поле ``file`` передаём списком пар, иначе requests свернёт
    несколько файлов в один.
    """
    txt = (text or "").strip()
    items = attachments or []
    if not txt and not items:
        raise ValueError("пустое сообщение")

    multipart: list[tuple[str, tuple]] = [("replySign", (None, reply_sign))]
    if txt:
        multipart.append(("message", (None, txt[:1000])))
    multipart.extend(
        ("file", (filename, content, content_type))
        for filename, content, content_type in items
    )
    r = requests.post(
        f"{CHAT_BASE}/api/v1/seller/message",
        files=multipart,
        headers={"Authorization": key or api_key()},
        timeout=timeout,
    )
    r.raise_for_status()
    return r


def download_chat_file(file_id: str, *, key: str | None = None, timeout: int = 25) -> requests.Response:
    r = requests.get(
        f"{CHAT_BASE}/api/v1/seller/download/{file_id}",
        headers={"Authorization": key or api_key()}, timeout=timeout,
    )
    r.raise_for_status()
    return r


# ── Контент (фото карточек, для wb_product_photos) ───────────────────────────

def list_cards_page_raw(cursor: dict | None = None, limit: int = 100, *, key: str | None = None, timeout: int = 30) -> requests.Response:
    """Без raise_for_status: вызывающий (photo-sync) отдельно ловит 401 —
    «токен без права Контент» — и мягко останавливается вместо ошибки."""
    body = {"settings": {"cursor": {**{"limit": limit}, **(cursor or {})}, "filter": {"withPhoto": 1}}}
    return requests.post(
        f"{CONTENT_BASE}/content/v2/get/cards/list",
        json=body, headers=auth_headers(key or content_key()), timeout=timeout,
    )
