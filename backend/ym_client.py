"""Единая точка входа в API Яндекс Маркета для CX-модулей (чаты, отзывы, вопросы).

ym_worker.py уже имел собственный аккуратный слой _request/_get/_post — он
просто переехал сюда без изменений в поведении, чтобы routers/cx_responses.py
(ответы на отзывы/вопросы) и backend/ym_chat_sync.py (чат) могли пользоваться
тем же кодом вместо копий с ключами/URL "от руки".
"""
from __future__ import annotations

import os
import time

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


def list_campaigns(*, key: str | None = None, timeout: int = 30) -> list[dict]:
    """Все магазины кабинета (FBY / FBS / DBS) из GET /v2/campaigns."""
    data = get("/v2/campaigns", key=key, timeout=timeout)
    return list(data.get("campaigns") or [])


def available_campaigns(*, key: str | None = None, timeout: int = 30) -> list[dict]:
    """Кампании с apiAvailability=AVAILABLE (сюда входят FBY и FBS)."""
    out = []
    for c in list_campaigns(key=key, timeout=timeout):
        if str(c.get("apiAvailability") or "") != "AVAILABLE":
            continue
        cid = c.get("id")
        if cid is None:
            continue
        out.append({
            "id": int(cid),
            "placementType": str(c.get("placementType") or "").upper() or "UNKNOWN",
            "domain": c.get("domain") or "",
        })
    # Если API пуст — хотя бы основной YM_CAMPAIGN_ID
    if not out:
        main = (campaign_id() or "").strip()
        if main.isdigit():
            out.append({"id": int(main), "placementType": "FBY", "domain": "default"})
    return out


# 500/502/503 у ЯМ бывают «морганием». Чтения повторяем; запись в кабинет — нет,
# чтобы не отправить ответ покупателю дважды.
_TRANSIENT = {429, 500, 502, 503, 504}


def request(
    method: str,
    path: str,
    *,
    params=None,
    json_body=None,
    key: str | None = None,
    timeout: int = 30,
    mutate: bool = False,
) -> requests.Response:
    """HTTP-запрос к ЯМ. 420 — RateLimitError (крону лучше подождать цикл).
    5xx на чтениях — несколько попыток с паузой."""
    attempts = 1 if mutate else 4
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            r = getattr(requests, method)(
                f"{BASE}{path}",
                headers=headers(key),
                timeout=timeout,
                params=params,
                json=json_body,
            )
        except requests.RequestException as e:
            last_exc = e
            if mutate or attempt >= attempts - 1:
                raise
            time.sleep(min(20, 3 * (2 ** attempt)))
            continue
        if r.status_code == 420:
            raise RateLimitError(f"420 rate limit на {path}. Попробуем позже.")
        if r.status_code in _TRANSIENT and not mutate and attempt < attempts - 1:
            time.sleep(min(20, 3 * (2 ** attempt)))
            continue
        r.raise_for_status()
        return r
    if last_exc:
        raise last_exc
    r.raise_for_status()
    return r


def get(path: str, params: dict | None = None, *, key: str | None = None, timeout: int = 30) -> dict:
    return request("get", path, params=params or {}, key=key, timeout=timeout).json()


def raise_if_error(data, *, path: str = "") -> dict:
    """ЯМ часто отвечает HTTP 200 с status=ERROR — для записи это провал."""
    if not isinstance(data, dict):
        return data or {}
    if str(data.get("status") or "").upper() != "ERROR":
        return data
    errs = data.get("errors") or []
    parts = []
    for err in errs:
        if isinstance(err, dict):
            parts.append(str(err.get("message") or err.get("code") or err))
        else:
            parts.append(str(err))
    msg = "; ".join(x for x in parts if x) or "ERROR"
    raise requests.HTTPError(f"ЯМ API ERROR {path}: {msg[:300]}")


def post(
    path: str,
    body: dict | None = None,
    *,
    params: dict | None = None,
    key: str | None = None,
    timeout: int = 30,
    mutate: bool = False,
) -> dict:
    data = request(
        "post", path, params=params,
        json_body=body if body is not None else {},
        key=key, timeout=timeout, mutate=mutate,
    ).json()
    if mutate:
        raise_if_error(data, path=path)
    return data


# ── Отзывы и вопросы ──────────────────────────────────────────────────────────

def answer_feedback(feedback_id: str, text: str, *, biz: str | None = None, key: str | None = None, timeout: int = 20) -> dict:
    """POST — опубликовать комментарий к отзыву ЯМ.

    Актуальный метод: /v2/businesses/{id}/goods-feedback/comments/update.
    Старый /comments только читает список комментариев и молча «успешен».
    """
    biz = biz or business_id()
    try:
        fid = int(str(feedback_id).strip())
    except (TypeError, ValueError) as e:
        raise ValueError(f"feedback_id должен быть числом: {feedback_id!r}") from e
    txt = (text or "").strip()
    if not txt:
        raise ValueError("текст ответа пустой")
    return post(
        f"/v2/businesses/{biz}/goods-feedback/comments/update",
        {"feedbackId": fid, "comment": {"text": txt[:4096]}},
        key=key,
        timeout=timeout,
        mutate=True,
    )


def feedback_gone_from_cabinet(exc: BaseException) -> bool:
    """404 Feedbacks not found — отзыв уже снят с Маркета."""
    resp = getattr(exc, "response", None)
    if resp is None or getattr(resp, "status_code", None) != 404:
        return False
    raw = (getattr(resp, "text", None) or "").lower()
    return "feedbacks not found" in raw or "not_found" in raw


def answer_question(question_id: str, text: str, *, biz: str | None = None, key: str | None = None, timeout: int = 20) -> dict:
    """POST — ответить на вопрос ЯМ.

    Актуальный метод: /v1/businesses/{id}/goods-questions/update
    (CREATE + parentEntityId=вопрос). Старый .../questions/{id}/answers даёт 404.
    """
    biz = biz or business_id()
    try:
        qid = int(str(question_id).strip())
    except (TypeError, ValueError) as e:
        raise ValueError(f"question_id должен быть числом: {question_id!r}") from e
    txt = (text or "").strip()
    if not txt:
        raise ValueError("текст ответа пустой")
    return post(
        f"/v1/businesses/{biz}/goods-questions/update",
        {
            "operationType": "CREATE",
            "parentEntityId": {"id": qid, "type": "QUESTION"},
            "text": txt[:5000],
        },
        key=key,
        timeout=timeout,
        mutate=True,
    )


# ── Чаты ──────────────────────────────────────────────────────────────────────

def list_chats(statuses: list[str], *, limit: int = 50, biz: str | None = None, key: str | None = None, timeout: int = 30) -> list[dict]:
    biz = biz or business_id()
    out: list[dict] = []
    page_token = None
    for _ in range(40):
        body: dict = {"statuses": statuses, "limit": limit}
        if page_token:
            body["pageToken"] = page_token
        data = post(f"/v2/businesses/{biz}/chats", body, key=key, timeout=timeout)
        result = data.get("result") or data
        out.extend(result.get("chats") or [])
        paging = result.get("paging") if isinstance(result.get("paging"), dict) else {}
        page_token = paging.get("nextPageToken") or paging.get("next_page_token")
        if not page_token:
            break
    return out


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


def send_chat_file(
    chat_id: str,
    filename: str,
    content: bytes,
    *,
    content_type: str | None = None,
    biz: str | None = None,
    key: str | None = None,
    timeout: int = 25,
) -> None:
    """Отправка файла: POST /v2/businesses/{businessId}/chats/file/send (multipart)."""
    biz = biz or business_id()
    name = (filename or "").strip()
    if not name:
        raise ValueError("имя файла обязательно")
    if not content:
        raise ValueError("файл пустой")
    cid: int | str = int(chat_id) if str(chat_id).isdigit() else chat_id
    # Не ставим Content-Type: application/json — иначе сломается multipart boundary.
    hdrs = {"Api-Key": key or api_key()}
    r = requests.post(
        f"{BASE}/v2/businesses/{biz}/chats/file/send",
        headers=hdrs,
        params={"chatId": cid},
        files={
            "file": (name, content, content_type or "application/octet-stream"),
        },
        timeout=timeout,
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
