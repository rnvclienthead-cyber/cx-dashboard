"""Единый «переводчик» для внешних HTTP-вызовов: одни таймауты и повторы на всех.

Зачем. У каждой площадки повторы были свои: у WB — в шести местах и по-разному,
у Ozon превышение лимита сразу роняло запрос, у Яндекс-доставки и Salebot
повторов не было вовсе. Из-за этого «моргнула сеть — уведомление клиенту
потерялось молча». Здесь одна предсказуемая политика, её переиспользуют клиенты.

Главная осторожность — не наделать дублей. Повтор по таймауту чтения (ответ
потерян, но сервер мог всё выполнить) безопасен только для идемпотентных
вызовов (GET и повторяемые PATCH). Для отправок клиенту (POST сообщения/ответа)
повторяем лишь то, что заведомо не дошло до сервера: обрыв соединения и 429
(«слишком часто» — запрос не обработан). Прочие коды (обычные 4xx, 422-конец
пагинации и т.п.) отдаём вызывающему как есть — их разбирает он сам.
"""
from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)

# (connect, read) в секундах — единое значение по умолчанию.
DEFAULT_TIMEOUT = (10, 30)
DEFAULT_RETRIES = 2          # до 3 попыток всего
DEFAULT_BACKOFF = 2.0        # пауза растёт: 2с, 4с, ...
MAX_BACKOFF = 30.0
RETRYABLE_5XX = (500, 502, 503, 504)


def _retry_on_exc(exc: Exception, idempotent: bool) -> bool:
    """Повторять ли после сетевого исключения."""
    # ConnectionError и ConnectTimeout — запрос не дошёл до сервера: безопасно всегда.
    if isinstance(exc, requests.ConnectionError):
        return True
    # ReadTimeout — сервер мог всё выполнить, ответ потерян: только идемпотентные.
    if isinstance(exc, requests.Timeout):
        return idempotent
    return False


def _retry_on_status(code: int, *, idempotent: bool, retry_on_429: bool,
                     retry_statuses: tuple[int, ...]) -> bool:
    """Повторять ли по коду ответа."""
    if code == 429:
        # 429 — запрос не обработан, повтор безопасен и для отправок.
        return retry_on_429
    if code in retry_statuses:
        # 5xx для записи неоднозначен — повторяем только идемпотентные вызовы.
        return idempotent
    return False


def _sleep_seconds(resp, attempt: int, backoff: float) -> float:
    # Если сервер прислал Retry-After — уважаем его.
    if resp is not None:
        ra = resp.headers.get("Retry-After")
        if ra:
            try:
                return min(float(ra), MAX_BACKOFF)
            except (TypeError, ValueError):
                pass
    return min(backoff * (attempt + 1), MAX_BACKOFF)


def request(
    method: str,
    url: str,
    *,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF,
    idempotent: bool = True,
    retry_on_429: bool = True,
    retry_statuses: tuple[int, ...] = RETRYABLE_5XX,
    timeout=DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
    **kwargs,
) -> requests.Response:
    """Выполнить HTTP-запрос с едиными повторами. Возвращает requests.Response.

    idempotent=False — для отправок (POST сообщения/ответа): не повторяем по
    таймауту чтения и по 5xx, чтобы не задвоить сообщение клиенту.
    retry_on_429=False — если вызывающий сам разбирает 429 своим графиком.
    """
    caller = session or requests
    resp = None
    for attempt in range(retries + 1):
        try:
            resp = caller.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            if attempt >= retries or not _retry_on_exc(exc, idempotent):
                raise
            time.sleep(_sleep_seconds(None, attempt, backoff))
            logger.warning("http %s %s: повтор %s/%s после %s",
                           method, url, attempt + 1, retries, type(exc).__name__)
            continue

        if attempt >= retries or not _retry_on_status(
            resp.status_code, idempotent=idempotent,
            retry_on_429=retry_on_429, retry_statuses=retry_statuses,
        ):
            return resp

        time.sleep(_sleep_seconds(resp, attempt, backoff))
        logger.warning("http %s %s: повтор %s/%s после %s",
                       method, url, attempt + 1, retries, resp.status_code)
    return resp


def get(url: str, **kw) -> requests.Response:
    return request("GET", url, **kw)


def post(url: str, **kw) -> requests.Response:
    # Отправки по умолчанию неидемпотентны — бережём от дублей.
    kw.setdefault("idempotent", False)
    return request("POST", url, **kw)


def patch(url: str, **kw) -> requests.Response:
    return request("PATCH", url, **kw)


def put(url: str, **kw) -> requests.Response:
    kw.setdefault("idempotent", False)
    return request("PUT", url, **kw)
