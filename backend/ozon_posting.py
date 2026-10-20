"""Карточка заказа Ozon по номеру отправления (для заявок Т-Шопинга)."""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import ozon_client

logger = logging.getLogger("ozon_posting")

STATUS_LABELS = {
    "awaiting_registration": "Ожидает регистрации",
    "awaiting_approve": "Ожидает подтверждения",
    "awaiting_packaging": "Ждёт сборки",
    "awaiting_deliver": "Ждёт отгрузки",
    "delivering": "В пути",
    "driver_pickup": "Курьер забрал",
    "delivered": "Доставлен",
    "cancelled": "Отменён",
    "cancelled_from_split": "Отменён",
    "not_accepted": "Не принят",
    "arbitration": "Спор",
    "client_arbitration": "Спор клиента",
    "sent_by_seller": "Отправлен продавцом",
}

WITH_FLAGS = {
    "analytics_data": True,
    "financial_data": True,
}


def _money(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        value = value.get("amount") or value.get("price") or value.get("value")
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _qty(value: Any) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def candidates(order: str) -> list[str]:
    raw = (order or "").strip()
    if not raw:
        return []
    out = [raw]
    if raw.count("-") == 1:
        out.append(f"{raw}-1")
    return list(dict.fromkeys(out))


def _product_row(item: dict) -> dict:
    price = _money(item.get("price"))
    qty = _qty(item.get("quantity"))
    return {
        "name": str(item.get("name") or "").strip(),
        "article": str(item.get("offer_id") or item.get("supplier_article") or "").strip(),
        "sku": str(item.get("sku") or item.get("ozon_sku") or "").strip(),
        "quantity": qty,
        "price": price,
        "sum": round(price * qty, 2) if price is not None else None,
    }


def _status_label(status: str) -> str:
    key = str(status or "").strip()
    return STATUS_LABELS.get(key, key or "—")


def summarize_api(res: dict, schema: str) -> dict:
    products = [_product_row(p) for p in (res.get("products") or []) if isinstance(p, dict)]
    total = sum(p["sum"] for p in products if p.get("sum") is not None)
    if not total:
        fin = ((res.get("financial_data") or {}) if isinstance(res.get("financial_data"), dict) else {}).get("products") or []
        for item in fin:
            if not isinstance(item, dict):
                continue
            price = _money(item.get("price") or item.get("customer_price"))
            qty = _qty(item.get("quantity"))
            if price is not None:
                total += price * qty
    analytics = res.get("analytics_data") if isinstance(res.get("analytics_data"), dict) else {}
    delivery = res.get("delivery_method") if isinstance(res.get("delivery_method"), dict) else {}
    customer = res.get("customer") if isinstance(res.get("customer"), dict) else {}
    addressee = res.get("addressee") if isinstance(res.get("addressee"), dict) else {}
    status = str(res.get("status") or "").strip()
    return {
        "found": True,
        "schema": schema,
        "posting_number": str(res.get("posting_number") or "").strip(),
        "order_number": str(res.get("order_number") or "").strip(),
        "order_id": res.get("order_id"),
        "status": status,
        "status_label": _status_label(status),
        "substatus": str(res.get("substatus") or "").strip(),
        "created_at": res.get("in_process_at") or res.get("created_at"),
        "shipment_date": res.get("shipment_date"),
        "products": products,
        "total": round(total, 2) if total else None,
        "tracking_number": str(res.get("tracking_number") or "").strip(),
        "city": str(analytics.get("city") or analytics.get("region") or "").strip(),
        "warehouse": str(analytics.get("warehouse") or delivery.get("warehouse") or "").strip(),
        "delivery": str(delivery.get("name") or "").strip(),
        "customer": str(customer.get("name") or addressee.get("name") or "").strip(),
    }


def summarize_rows(rows: list[dict]) -> Optional[dict]:
    if not rows:
        return None
    products = []
    total = 0.0
    has_price = False
    first = rows[0]
    for row in rows:
        item = _product_row({
            "name": row.get("sku_name"),
            "offer_id": row.get("supplier_article"),
            "sku": row.get("ozon_sku"),
            "quantity": row.get("quantity"),
            "price": row.get("price"),
        })
        products.append(item)
        if item.get("sum") is not None:
            total += item["sum"]
            has_price = True
    status = str(first.get("status") or "").strip()
    schema = str(first.get("schema_code") or "").strip()
    if schema.lower() == "fbo":
        schema = "FBO"
    elif schema.lower() in {"fbs", "rfbs"}:
        schema = "FBS"
    return {
        "found": True,
        "schema": schema or None,
        "posting_number": str(first.get("posting_number") or "").strip(),
        "order_number": str(first.get("order_number") or "").strip(),
        "order_id": first.get("order_id"),
        "status": status,
        "status_label": _status_label(status),
        "substatus": "",
        "created_at": first.get("created_at"),
        "shipment_date": None,
        "products": products,
        "total": round(total, 2) if has_price else None,
        "tracking_number": "",
        "city": "",
        "warehouse": "",
        "delivery": "",
        "customer": "",
        "from_cache": True,
    }


def fetch_api(posting_number: str) -> Optional[dict]:
    number = (posting_number or "").strip()
    if not number or not ozon_client.api_key():
        return None
    payload = {"posting_number": number, "with": WITH_FLAGS}
    for path, schema in (("/v2/posting/fbo/get", "FBO"), ("/v3/posting/fbs/get", "FBS")):
        try:
            data = ozon_client.post(path, payload, timeout=20, idempotent=True)
        except requests.HTTPError as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (400, 404):
                continue
            logger.info("[OzonPosting] %s %s: %s", path, number, e)
            continue
        except Exception as e:
            logger.info("[OzonPosting] %s %s: %s", path, number, e)
            continue
        res = data.get("result") or data
        if isinstance(res, dict) and (res.get("products") or res.get("posting_number") or res.get("status")):
            return summarize_api(res, schema)
    return None


def from_db(db: Session, order: str) -> Optional[dict]:
    keys = candidates(order)
    if not keys:
        return None
    rows = db.execute(text("""
        SELECT posting_number, order_id, order_number, status,
               supplier_article, sku_name, created_at, price
        FROM ozon_orders
        WHERE posting_number = ANY(:keys)
           OR order_number = ANY(:keys)
           OR posting_number LIKE :like
        ORDER BY
            CASE
                WHEN posting_number = :exact THEN 0
                WHEN order_number = :exact THEN 1
                ELSE 2
            END,
            created_at DESC NULLS LAST
        LIMIT 12
    """), {
        "keys": keys,
        "exact": keys[0],
        "like": f"{keys[0]}-%",
    }).mappings().all()
    if not rows:
        return None
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row["posting_number"] or ""), []).append(dict(row))
    best = grouped.get(keys[0]) or next(iter(grouped.values()))
    return summarize_rows(best)


def lookup(db: Session, order: str, *, live: bool = True) -> Optional[dict]:
    cached = from_db(db, order)
    if cached and not live:
        return cached
    if live:
        for number in candidates(order):
            fresh = fetch_api(number)
            if fresh:
                return fresh
    return cached
