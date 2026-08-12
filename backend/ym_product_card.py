"""Точечная карточка товара для рабочего места КС.

По одному артикулу забираем фактические характеристики из API Яндекса
и сохраняем их в локальной БД, чтобы затем использовать как общую карточку товара.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from . import ym_client


CACHE_TTL_SECONDS = 12 * 60 * 60
MAX_VISIBLE_PARAMS = 32
HIDDEN_PARAM_NAMES = {
    "бренд",
    "название модели (для объединения в одну карточку)",
    "#хештеги",
    "ключевые слова",
    "название группы",
    "единиц в одном товаре",
    "страна-изготовитель",
    "планирую доставлять товар в нескольких упаковках",
    "вид выпуска товара",
    "код продавца",
    "количество заводских упаковок",
    "название",
    "назначение",
    "аннотация",
    "тн вэд коды еаэс",
}
VISIBLE_PARAM_KEYWORDS = (
    "цвет",
    "материал",
    "комплект",
    "вес",
    "габарит",
    "длина",
    "ширина",
    "высота",
    "размер",
    "конструк",
)


def init_product_card_cache(engine) -> None:
    """Создаёт маленький persistent-кэш. Вызывается из lifespan приложения."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ym_product_card_cache (
                offer_id   TEXT PRIMARY KEY,
                payload    JSONB NOT NULL,
                fetched_at TIMESTAMP NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMP NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_ym_product_card_cache_expires
            ON ym_product_card_cache (expires_at)
        """))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return ""


def _param_value(param: dict) -> str:
    """Параметр API встречается как value, values или optionValues."""
    direct = _text(param.get("value"))
    if direct:
        return direct
    for field in ("values", "optionValues"):
        values = param.get(field)
        if not isinstance(values, list):
            continue
        parts = []
        for value in values:
            if isinstance(value, dict):
                value = value.get("value") or value.get("name")
            value = _text(value)
            if value:
                parts.append(value)
        if parts:
            return ", ".join(parts)
    return ""


def _is_hidden_param(name: str) -> bool:
    return _text(name).strip().lower() in HIDDEN_PARAM_NAMES


def _is_visible_param(name: str) -> bool:
    normalized = _text(name).strip().lower()
    return any(key in normalized for key in VISIBLE_PARAM_KEYWORDS)


def _normalize_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return payload
    product = payload.get("product")
    if not isinstance(product, dict):
        return payload
    params = product.get("params")
    if isinstance(params, list):
        filtered = []
        for param in params:
            if not isinstance(param, dict):
                continue
            name = _text(param.get("name"))
            value = _text(param.get("value"))
            if name and value and not _is_hidden_param(name) and _is_visible_param(name):
                filtered.append({"name": name, "value": value})
        product["params"] = filtered[:MAX_VISIBLE_PARAMS]
    product.pop("name", None)
    product.pop("manuals", None)
    return payload


def parse_offer_card(data: dict, article: str) -> dict:
    """Берёт только поля, которые вернул API; отсутствующие не придумывает."""
    result = data.get("result") if isinstance(data, dict) else None
    source = result if isinstance(result, dict) else data if isinstance(data, dict) else {}
    mappings = source.get("offerMappings") or []
    normalized_article = article.strip()
    offer: dict | None = None
    for mapping in mappings:
        candidate = mapping.get("offer") if isinstance(mapping, dict) else None
        if not isinstance(candidate, dict):
            continue
        # API уже фильтрует offerIds, но проверка не позволяет показать чужую карту.
        if _text(candidate.get("offerId")) == normalized_article:
            offer = candidate
            break
    if offer is None:
        return {"status": "not_found", "article": normalized_article, "source": "yandex_market"}

    dimensions = offer.get("weightDimensions")
    if not isinstance(dimensions, dict):
        dimensions = {}
    dimensions = {
        key: dimensions[key]
        for key in ("length", "width", "height", "weight")
        if dimensions.get(key) is not None
    }
    params: list[dict[str, str]] = []
    for param in offer.get("params") or []:
        if not isinstance(param, dict):
            continue
        name = _text(param.get("name"))
        value = _param_value(param)
        if name and value and not _is_hidden_param(name) and _is_visible_param(name):
            params.append({"name": name, "value": value})

    return {
        "status": "ok",
        "article": normalized_article,
        "source": "yandex_market",
        "product": {
            "offer_id": _text(offer.get("offerId")),
            "dimensions": dimensions,
            "params": params[:MAX_VISIBLE_PARAMS],
        },
    }


def _cached_card(db, article: str) -> dict | None:
    row = db.execute(text("""
        SELECT payload FROM ym_product_card_cache
        WHERE offer_id = :article AND expires_at > NOW()
    """), {"article": article}).mappings().first()
    if not row:
        return None
    payload = row.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    return {**_normalize_payload(payload), "cached": True}


def _store_card(db, article: str, payload: dict) -> None:
    payload = _normalize_payload(payload)
    db.execute(text("""
        INSERT INTO ym_product_card_cache (offer_id, payload, fetched_at, expires_at)
        VALUES (
            :article, CAST(:payload AS jsonb), NOW(),
            NOW() + (:ttl_seconds * INTERVAL '1 second')
        )
        ON CONFLICT (offer_id) DO UPDATE SET
            payload = EXCLUDED.payload,
            fetched_at = EXCLUDED.fetched_at,
            expires_at = EXCLUDED.expires_at
    """), {
        "article": article,
        "payload": json.dumps(payload, ensure_ascii=False),
        "ttl_seconds": CACHE_TTL_SECONDS,
    })
    db.commit()


def get_product_card(db, article: str) -> dict:
    article = _text(article)
    if not article:
        return {"status": "not_found", "article": "", "source": "yandex_market"}
    cached = _cached_card(db, article)
    if cached:
        return cached
    try:
        data = ym_client.post(
            f"/v2/businesses/{ym_client.business_id()}/offer-mappings",
            {"offerIds": [article]},
            timeout=15,
        )
    except Exception:
        # Токены и технические детали API не попадают в интерфейс КС.
        return {"status": "unavailable", "article": article, "source": "yandex_market"}
    payload = parse_offer_card(data, article)
    _store_card(db, article, payload)
    return {**_normalize_payload(payload), "cached": False}
