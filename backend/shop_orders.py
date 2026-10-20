"""Заказы витрины lk.vidovito.com: приём из CS-Cart и очередь склада."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from . import cdek
from . import ozon_delivery
from . import shop_delivery
from . import yandex_ndd
from .database import add_column_if_missing

logger = logging.getLogger("shop_orders")

CARRIERS = ("ozon", "cdek", "yandex")
STATUSES = (
    "new", "paid", "incomplete", "packing", "packed", "shipped",
    "in_transit", "ready_for_pickup", "delivered", "returned",
    "cancelled", "cancelled_seller", "cancelled_buyer",
)
STATUS_CHAIN = (
    "new", "packing", "packed", "shipped",
    "in_transit", "ready_for_pickup", "delivered",
)
LOCKED_STATUSES = frozenset({
    "packing", "packed", "shipped", "in_transit",
    "ready_for_pickup", "delivered", "returned",
    "cancelled", "cancelled_seller", "cancelled_buyer",
})
PACKED_OR_LATER = frozenset({
    "packed", "shipped", "in_transit", "ready_for_pickup", "delivered", "returned",
})
SHIPPED_OR_LATER = frozenset({
    "shipped", "in_transit", "ready_for_pickup", "delivered", "returned",
})
CSCART_CANCELLED = frozenset({"I", "D", "F"})
CSCART_PAID = frozenset({"P", "C"})
CSCART_PACKED = "packed"
CSCART_SHIPPED = "shipped"
CSCART_IN_TRANSIT = "in_transit"
CSCART_READY = "ready_for_pickup"
CSCART_DELIVERED = "delivered"
SHIP_REQ_BASE = 900000
DEFAULT_BOX = (40, 30, 20, 2000)
SHOP_CSCART_URL = os.getenv("SHOP_CSCART_URL", "https://lk.vidovito.com").rstrip("/")

STATUS_LABELS = {
    "new": "Заказ оформлен",
    "paid": "Оплачен",
    "incomplete": "Незавершённый заказ",
    "packing": "Собираем",
    "packed": "Собран",
    "shipped": "Отправлен",
    "in_transit": "В пути",
    "ready_for_pickup": "Готов к выдаче",
    "delivered": "Выдан",
    "returned": "Возвращён",
    "cancelled": "Отменён",
    "cancelled_seller": "Отменён продавцом",
    "cancelled_buyer": "Отменён покупателем",
}
SHOP_STATUS_BY_KIND = {
    "placed": "new",
    "new": "new",
    "paid": "paid",
    "packed": "packed",
    "shipped": "shipped",
    "in_transit": "in_transit",
    "ready_for_pickup": "ready_for_pickup",
    "delivered": "delivered",
    "returned": "returned",
    "cancelled": "cancelled",
    "cancelled_seller": "cancelled_seller",
    "cancelled_buyer": "cancelled_buyer",
    "incomplete": "incomplete",
}
SHOP_STATUS_BY_TITLE = {
    "заказ оформлен": "new",
    "открыт": "new",
    "принят": "new",
    "оплачен": "paid",
    "обработан": "paid",
    "собран": "packed",
    "собраны": "packed",
    "отправлен": "shipped",
    "отправлены": "shipped",
    "в пути": "in_transit",
    "готов к выдаче": "ready_for_pickup",
    "выдан": "delivered",
    "выданы": "delivered",
    "возвращен": "returned",
    "отменен продавцом": "cancelled_seller",
    "отменен покупателем": "cancelled_buyer",
    "отменен": "cancelled",
    "незавершенный заказ": "incomplete",
    "незавершенный": "incomplete",
    "незавершен": "incomplete",
}
SHOP_STATUS_BY_LETTER = {
    "O": "new",
    "P": "paid",
    "K": "packed",
    "S": "shipped",
    "T": "in_transit",
    "G": "ready_for_pickup",
    "W": "delivered",
    "I": "cancelled",
    "D": "cancelled",
    "F": "cancelled",
}
_SOFT_SHOP_STATUSES = frozenset({"new", "paid"})
_CANCEL_SHOP_STATUSES = frozenset({"cancelled", "cancelled_seller", "cancelled_buyer"})
CARRIER_LABELS = {
    "ozon": "Ozon",
    "cdek": "СДЭК",
    "yandex": "Яндекс",
}
CSCART_STATUS_LABELS = {
    "O": "Открыт",
    "P": "Обработан",
    "C": "Выполнен",
    "F": "Неудачный",
    "D": "Отклонён",
    "B": "Предзаказ",
    "I": "Отменён",
    "Y": "Ожидает звонка",
    "K": "Собран",
    "H": "На удержании",
    "S": "Отправлен",
    "T": "В пути",
    "G": "Готов к выдаче",
    "W": "Выдан",
    "PACKED": "Собран",
    "SHIPPED": "Отправлен",
    "IN_TRANSIT": "В пути",
    "READY_FOR_PICKUP": "Готов к выдаче",
    "DELIVERED": "Выдан",
}

CDEK_STATUS_MAP = {
    "RECEIVED_AT_SHIPMENT_WAREHOUSE": "shipped",
    "READY_FOR_SHIPMENT_IN_SENDER_CITY": "in_transit",
    "TAKEN_BY_TRANSPORTER_FROM_SENDER_CITY": "in_transit",
    "SENT_TO_TRANSIT_CITY": "in_transit",
    "ACCEPTED_AT_TRANSIT_WAREHOUSE": "in_transit",
    "READY_FOR_SHIPMENT_IN_TRANSIT_CITY": "in_transit",
    "TAKEN_BY_TRANSPORTER_FROM_TRANSIT_CITY": "in_transit",
    "SENT_TO_RECIPIENT_CITY": "in_transit",
    "ACCEPTED_IN_TRANSIT_CITY": "in_transit",
    "ACCEPTED_IN_RECIPIENT_CITY": "in_transit",
    "TAKEN_BY_COURIER": "in_transit",
    "ACCEPTED_AT_PICK_UP_POINT": "ready_for_pickup",
    "READY_FOR_PICKUP": "ready_for_pickup",
    "POSTOMAT_RECEIVED": "ready_for_pickup",
    "DELIVERED": "delivered",
    "NOT_DELIVERED": "returned",
    "RETURNED_TO_SENDER": "returned",
    "RETURNED_TO_SENDER_WAREHOUSE": "returned",
}


def init_tables(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS shop_orders (
                id               SERIAL PRIMARY KEY,
                cscart_order_id  INTEGER UNIQUE NOT NULL,
                cscart_status    VARCHAR(32),
                status           VARCHAR(24) NOT NULL DEFAULT 'new',
                paid             BOOLEAN DEFAULT FALSE,
                payment_name     TEXT,
                shipping_method  TEXT,
                total            NUMERIC(12,2) DEFAULT 0,
                shipping_cost    NUMERIC(12,2) DEFAULT 0,
                currency         VARCHAR(8) DEFAULT 'RUB',
                customer_name    TEXT,
                customer_email   TEXT,
                customer_phone   TEXT,
                customer_company TEXT,
                shipping_city    TEXT,
                shipping_address TEXT,
                shipping_zip     TEXT,
                shipping_state   TEXT,
                shipping_country TEXT,
                notes            TEXT,
                admin_notes      TEXT,
                carrier          VARCHAR(16),
                pvz_code         TEXT,
                pvz_name         TEXT,
                pvz_address      TEXT,
                pvz_lat          DOUBLE PRECISION,
                pvz_lon          DOUBLE PRECISION,
                delivery_time    TEXT,
                shop_barcode     TEXT,
                placed_at        TIMESTAMP,
                packed_at        TIMESTAMP,
                shipped_at       TIMESTAMP,
                processed_by     TEXT,
                raw_json         JSONB,
                created_at       TIMESTAMP DEFAULT now(),
                updated_at       TIMESTAMP DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS shop_order_items (
                id               SERIAL PRIMARY KEY,
                order_id         INTEGER NOT NULL REFERENCES shop_orders(id) ON DELETE CASCADE,
                line_no          INTEGER NOT NULL DEFAULT 1,
                product_id       INTEGER,
                product_code     TEXT,
                name             TEXT,
                qty              INTEGER NOT NULL DEFAULT 1,
                price            NUMERIC(12,2) DEFAULT 0,
                weight_g         INTEGER,
                length_cm        INTEGER,
                width_cm         INTEGER,
                height_cm        INTEGER,
                status           VARCHAR(24) NOT NULL DEFAULT 'new',
                carrier_order_id TEXT,
                track_number     TEXT,
                shipping_cost    NUMERIC(12,2),
                error            TEXT,
                packed_at        TIMESTAMP,
                shipped_at       TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS shop_orders_status_idx
            ON shop_orders (status, created_at DESC)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS shop_order_items_order_idx
            ON shop_order_items (order_id, line_no)
        """))
        add_column_if_missing(
            conn, "shop_order_items", "cdek_uuid",
            "ALTER TABLE shop_order_items ADD COLUMN cdek_uuid TEXT",
        )
        add_column_if_missing(
            conn, "shop_order_items", "ozon_order_number",
            "ALTER TABLE shop_order_items ADD COLUMN ozon_order_number TEXT",
        )
        add_column_if_missing(
            conn, "shop_order_items", "ozon_posting_number",
            "ALTER TABLE shop_order_items ADD COLUMN ozon_posting_number TEXT",
        )
        add_column_if_missing(
            conn, "shop_order_items", "yandex_sharing_url",
            "ALTER TABLE shop_order_items ADD COLUMN yandex_sharing_url TEXT",
        )
        add_column_if_missing(
            conn, "shop_orders", "cscart_push_key",
            "ALTER TABLE shop_orders ADD COLUMN cscart_push_key TEXT",
        )
        conn.execute(text(
            "ALTER TABLE shop_orders ALTER COLUMN cscart_status TYPE VARCHAR(32)"
        ))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS shop_order_status_log (
                id         SERIAL PRIMARY KEY,
                order_id   INTEGER NOT NULL REFERENCES shop_orders(id) ON DELETE CASCADE,
                old_status VARCHAR(32),
                new_status VARCHAR(32) NOT NULL,
                changed_by VARCHAR(255),
                comment    TEXT,
                changed_at TIMESTAMP DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_shop_order_status_log
            ON shop_order_status_log (order_id, changed_at)
        """))
        conn.execute(text("""
            CREATE OR REPLACE FUNCTION fn_log_shop_order_status()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF OLD.status IS DISTINCT FROM NEW.status THEN
                    INSERT INTO shop_order_status_log(order_id, old_status, new_status, changed_at)
                    VALUES (NEW.id, OLD.status, NEW.status, NOW());
                END IF;
                RETURN NEW;
            END;
            $$
        """))
        conn.execute(text("""
            CREATE OR REPLACE FUNCTION fn_log_shop_order_status_insert()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                INSERT INTO shop_order_status_log(order_id, old_status, new_status, changed_at)
                VALUES (NEW.id, '', NEW.status, NOW());
                RETURN NEW;
            END;
            $$
        """))
        conn.execute(text("DROP TRIGGER IF EXISTS trg_shop_order_status_log ON shop_orders"))
        conn.execute(text("""
            CREATE TRIGGER trg_shop_order_status_log
            AFTER UPDATE ON shop_orders
            FOR EACH ROW EXECUTE FUNCTION fn_log_shop_order_status()
        """))
        conn.execute(text("DROP TRIGGER IF EXISTS trg_shop_order_status_log_insert ON shop_orders"))
        conn.execute(text("""
            CREATE TRIGGER trg_shop_order_status_log_insert
            AFTER INSERT ON shop_orders
            FOR EACH ROW EXECUTE FUNCTION fn_log_shop_order_status_insert()
        """))
        conn.execute(text("""
            INSERT INTO shop_order_status_log (order_id, old_status, new_status, changed_at)
            SELECT o.id, '', o.status, COALESCE(o.updated_at, o.created_at, now())
            FROM shop_orders o
            WHERE NOT EXISTS (
                SELECT 1 FROM shop_order_status_log l WHERE l.order_id = o.id
            )
        """))
        conn.commit()


def _as_dict(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _as_list(raw) -> list:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _text(value, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _box(item: dict) -> tuple[int, int, int, int]:
    length = _int(item.get("length_cm") or item.get("length"), 0)
    width = _int(item.get("width_cm") or item.get("width"), 0)
    height = _int(item.get("height_cm") or item.get("height"), 0)
    weight = _int(item.get("weight_g") or item.get("weight"), 0)
    if length < 1 or width < 1 or height < 1:
        length, width, height = DEFAULT_BOX[:3]
    if weight < 1:
        weight = DEFAULT_BOX[3]
    return length, width, height, weight


def explode_items(raw_items) -> list[dict]:
    """Каждая единица товара — отдельная посылка, как на складе."""
    out = []
    line = 0
    for item in _as_list(raw_items)[:80]:
        if not isinstance(item, dict):
            continue
        amount = max(1, min(30, _int(item.get("amount") or item.get("qty"), 1)))
        length, width, height, weight = _box(item)
        for _ in range(amount):
            line += 1
            out.append({
                "line_no": line,
                "product_id": _int(item.get("product_id"), 0) or None,
                "product_code": _text(item.get("product_code") or item.get("sku") or item.get("code"), 80),
                "name": _text(item.get("name") or item.get("product"), 240) or "Товар",
                "qty": 1,
                "price": round(_float(item.get("price")), 2),
                "weight_g": weight,
                "length_cm": length,
                "width_cm": width,
                "height_cm": height,
            })
            if len(out) >= 80:
                return out
    return out


def normalize_ingest(payload: dict) -> dict:
    customer = _as_dict(payload.get("customer"))
    address = _as_dict(payload.get("shipping_address") or payload.get("address"))
    delivery = _as_dict(payload.get("delivery"))
    carrier = _text(delivery.get("carrier") or payload.get("carrier"), 16).lower()
    if carrier not in CARRIERS:
        carrier = ""
    cscart_status = _text(payload.get("cscart_status") or payload.get("status"), 32).upper()
    paid = bool(payload.get("paid"))
    if cscart_status in CSCART_PAID:
        paid = True
    payment_name = _text(payload.get("payment_name"), 160)
    if not paid and "тест" in payment_name.lower():
        paid = True
    phone = _text(
        customer.get("phone") or payload.get("customer_phone") or address.get("phone"),
        40,
    )
    name = _text(
        customer.get("name")
        or " ".join(filter(None, [customer.get("firstname"), customer.get("lastname")]))
        or payload.get("customer_name"),
        160,
    )
    city = _text(address.get("city") or delivery.get("city") or payload.get("city"), 80)
    street = _text(
        address.get("address")
        or delivery.get("pvz_address")
        or payload.get("shipping_address_text"),
        240,
    )
    return {
        "cscart_order_id": _int(payload.get("cscart_order_id") or payload.get("order_id")),
        "cscart_status": cscart_status or "O",
        "paid": paid,
        "payment_name": payment_name,
        "shipping_method": _text(payload.get("shipping_method"), 160),
        "total": round(_float(payload.get("total")), 2),
        "shipping_cost": round(_float(payload.get("shipping_cost") or delivery.get("cost")), 2),
        "currency": _text(payload.get("currency"), 8) or "RUB",
        "customer_name": name or "Клиент",
        "customer_email": _text(customer.get("email") or payload.get("email"), 160),
        "customer_phone": phone,
        "customer_company": _text(customer.get("company"), 160),
        "shipping_city": city,
        "shipping_address": street,
        "shipping_zip": _text(address.get("zip") or address.get("zipcode") or delivery.get("zip"), 20),
        "shipping_state": _text(address.get("state"), 80),
        "shipping_country": _text(address.get("country"), 80) or "Россия",
        "notes": _text(payload.get("notes"), 2000),
        "admin_notes": _text(payload.get("details") or payload.get("admin_notes"), 2000),
        "carrier": carrier,
        "pvz_code": _text(delivery.get("pvz_code") or payload.get("pvz_code"), 80),
        "pvz_name": _text(delivery.get("pvz_name") or payload.get("pvz_name"), 160),
        "pvz_address": _text(delivery.get("pvz_address") or payload.get("pvz_address"), 240),
        "pvz_lat": _float(delivery.get("lat"), None) if delivery.get("lat") not in (None, "") else None,
        "pvz_lon": _float(delivery.get("lon"), None) if delivery.get("lon") not in (None, "") else None,
        "delivery_time": _text(delivery.get("delivery_time"), 120),
        "shop_barcode": _text(payload.get("shop_barcode") or payload.get("tracking_number"), 80),
        "placed_at": payload.get("created_at") or payload.get("placed_at"),
        "status_kind": _text(payload.get("status_kind"), 40).lower(),
        "status_title": _text(payload.get("status_title"), 80),
        "items": explode_items(payload.get("items") or payload.get("products")),
        "raw_json": payload,
    }


def _row(mapping) -> Optional[dict]:
    if mapping is None:
        return None
    if hasattr(mapping, "_mapping"):
        return dict(mapping._mapping)
    return dict(mapping)


def ozon_parcel_description(order: dict, item: dict) -> str:
    bits = []
    oid = order.get("cscart_order_id")
    if oid:
        bits.append(f"Заказ сайта #{oid}")
    barcode = (order.get("shop_barcode") or "").strip()
    if barcode:
        bits.append(barcode)
    who = (order.get("customer_name") or "").strip()
    if who:
        bits.append(who)
    sku = (item.get("product_code") or "").strip()
    title = (item.get("name") or "Товар").strip()
    bits.append(f"{sku} {title}".strip() if sku else title)
    city = (order.get("shipping_city") or "").strip()
    if city:
        bits.append(city)
    return ", ".join(bits)[:500]


def ozon_parcel_declared(order: dict, item: dict) -> float:
    try:
        price = float(item.get("price") or 0)
    except (TypeError, ValueError):
        price = 0
    if price >= 1:
        return price
    try:
        return max(1.0, float(order.get("total") or 1))
    except (TypeError, ValueError):
        return 1.0


def ozon_parcel_dims(item: dict) -> dict:
    length_cm = max(1, int(item.get("length_cm") or DEFAULT_BOX[0]))
    width_cm = max(1, int(item.get("width_cm") or DEFAULT_BOX[1]))
    height_cm = max(1, int(item.get("height_cm") or DEFAULT_BOX[2]))
    weight_g = max(1, int(item.get("weight_g") or DEFAULT_BOX[3]))
    return {
        "weight_g": weight_g,
        "length_mm": length_cm * 10,
        "width_mm": width_cm * 10,
        "height_mm": height_cm * 10,
    }


def tracking_url_for(carrier: str, item: Optional[dict] = None) -> str:
    """Ссылки как в доотправках: СДЭК — трек-страница, Яндекс — sharing_url, Ozon — номер в ЛК Ozon."""
    row = item or {}
    carrier = (carrier or "").strip().lower()
    sharing = (row.get("yandex_sharing_url") or "").strip()
    track = (
        (row.get("track_number") or "")
        or (row.get("ozon_posting_number") or "")
        or (row.get("ozon_order_number") or "")
        or (row.get("cdek_uuid") or "")
    ).strip()
    if carrier == "yandex":
        if sharing:
            return sharing
        if track:
            return "https://dostavka.yandex.ru/tracking?track_id=" + urllib.parse.quote(track, safe="")
        return ""
    if carrier == "cdek" and track:
        return "https://www.cdek.ru/track.html?order_id=" + urllib.parse.quote(track, safe="")
    if carrier == "ozon" and track:
        return "https://www.ozon.ru/my/orderdetails/?order=" + urllib.parse.quote(track, safe="")
    return ""


def _public_item(row: dict, carrier: str = "") -> dict:
    data = {
        "id": row.get("id"),
        "line_no": row.get("line_no"),
        "product_id": row.get("product_id"),
        "product_code": row.get("product_code") or "",
        "name": row.get("name") or "Товар",
        "qty": int(row.get("qty") or 1),
        "price": float(row.get("price") or 0),
        "weight_g": row.get("weight_g"),
        "length_cm": row.get("length_cm"),
        "width_cm": row.get("width_cm"),
        "height_cm": row.get("height_cm"),
        "status": row.get("status") or "new",
        "status_label": STATUS_LABELS.get(row.get("status") or "new", row.get("status")),
        "carrier_order_id": row.get("carrier_order_id") or "",
        "cdek_uuid": row.get("cdek_uuid") or "",
        "ozon_order_number": row.get("ozon_order_number") or "",
        "ozon_posting_number": row.get("ozon_posting_number") or "",
        "yandex_sharing_url": row.get("yandex_sharing_url") or "",
        "track_number": row.get("track_number") or "",
        "shipping_cost": float(row["shipping_cost"]) if row.get("shipping_cost") is not None else None,
        "error": row.get("error") or "",
        "packed_at": row.get("packed_at"),
        "shipped_at": row.get("shipped_at"),
        "has_label": bool(
            (row.get("track_number") or "")
            or (row.get("ozon_posting_number") or "")
            or (row.get("cdek_uuid") or "")
        ),
    }
    data["tracking_url"] = tracking_url_for(carrier, data)
    return data


def _public_order(row: dict, items: Optional[list] = None) -> dict:
    carrier = (row.get("carrier") or "").lower()
    status = row.get("status") or "new"
    cscart_status = (row.get("cscart_status") or "").upper()
    parcels = items if items is not None else []
    return {
        "id": row.get("id"),
        "cscart_order_id": row.get("cscart_order_id"),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "cscart_status": cscart_status,
        "cscart_status_label": CSCART_STATUS_LABELS.get(cscart_status, cscart_status or "—"),
        "paid": bool(row.get("paid")),
        "payment_name": row.get("payment_name") or "",
        "shipping_method": row.get("shipping_method") or "",
        "total": float(row.get("total") or 0),
        "shipping_cost": float(row.get("shipping_cost") or 0),
        "currency": row.get("currency") or "RUB",
        "customer_name": row.get("customer_name") or "Клиент",
        "customer_email": row.get("customer_email") or "",
        "customer_phone": row.get("customer_phone") or "",
        "customer_company": row.get("customer_company") or "",
        "shipping_city": row.get("shipping_city") or "",
        "shipping_address": row.get("shipping_address") or "",
        "shipping_zip": row.get("shipping_zip") or "",
        "shipping_state": row.get("shipping_state") or "",
        "shipping_country": row.get("shipping_country") or "",
        "notes": row.get("notes") or "",
        "admin_notes": row.get("admin_notes") or "",
        "carrier": carrier,
        "carrier_label": CARRIER_LABELS.get(carrier, carrier or "—"),
        "pvz_code": row.get("pvz_code") or "",
        "pvz_name": row.get("pvz_name") or "",
        "pvz_address": row.get("pvz_address") or "",
        "delivery_time": row.get("delivery_time") or "",
        "shop_barcode": row.get("shop_barcode") or "",
        "item_count": int(row.get("item_count") or len(parcels)),
        "shipped_count": int(row.get("shipped_count") or 0),
        "items": [_public_item(it, carrier) for it in parcels],
        "placed_at": row.get("placed_at") or row.get("created_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "packed_at": row.get("packed_at"),
        "shipped_at": row.get("shipped_at"),
        "processed_by": row.get("processed_by") or "",
        "has_pvz": bool(row.get("pvz_code") and carrier),
    }


def _load_items(db: Session, order_id: int) -> list[dict]:
    rows = db.execute(text("""
        SELECT * FROM shop_order_items
        WHERE order_id = :id
        ORDER BY line_no, id
    """), {"id": order_id}).fetchall()
    return [_row(r) for r in rows]


def get_order(db: Session, order_id: int) -> Optional[dict]:
    row = _row(db.execute(text("SELECT * FROM shop_orders WHERE id = :id"), {"id": order_id}).fetchone())
    if not row:
        return None
    return _public_order(row, _load_items(db, order_id))


def list_orders(db: Session, status: str = "", q: str = "") -> list[dict]:
    where = ["TRUE"]
    params: dict[str, Any] = {}
    if status and status != "all":
        if status == "open":
            where.append("o.status IN ('new', 'paid', 'packing', 'packed')")
        elif status == "cancelled":
            where.append("o.status IN ('cancelled', 'cancelled_seller', 'cancelled_buyer')")
        elif status == "on_way":
            where.append("o.status IN ('shipped', 'in_transit', 'ready_for_pickup')")
        else:
            where.append("o.status = :status")
            params["status"] = status
    if q.strip():
        where.append("""(
            o.customer_name ILIKE :q
            OR o.customer_phone ILIKE :q
            OR o.customer_email ILIKE :q
            OR CAST(o.cscart_order_id AS TEXT) ILIKE :q
            OR o.pvz_address ILIKE :q
            OR EXISTS (
                SELECT 1 FROM shop_order_items i
                WHERE i.order_id = o.id
                  AND (i.product_code ILIKE :q OR i.name ILIKE :q)
            )
        )""")
        params["q"] = f"%{q.strip()}%"
    rows = db.execute(text(f"""
        SELECT o.*,
               (SELECT COUNT(*) FROM shop_order_items i WHERE i.order_id = o.id) AS item_count,
               (SELECT COUNT(*) FROM shop_order_items i
                 WHERE i.order_id = o.id AND i.status = ANY(:shipped_like)) AS shipped_count
        FROM shop_orders o
        WHERE {' AND '.join(where)}
        ORDER BY o.created_at DESC, o.id DESC
        LIMIT 300
    """), {**params, "shipped_like": list(SHIPPED_OR_LATER)}).fetchall()
    return [_public_order(_row(r), []) for r in rows]


def stats(db: Session) -> dict:
    rows = db.execute(text("""
        SELECT status, COUNT(*) AS cnt
        FROM shop_orders
        GROUP BY status
    """)).fetchall()
    by_status = {s: 0 for s in STATUSES}
    for row in rows:
        data = _row(row)
        by_status[data["status"]] = int(data["cnt"])
    return {
        "by_status": by_status,
        "new": by_status["new"] + by_status["paid"] + by_status["packing"],
        "open": by_status["new"] + by_status["paid"] + by_status["packing"] + by_status["packed"],
    }


def _replace_items(db: Session, order_id: int, items: list[dict]) -> None:
    db.execute(text("DELETE FROM shop_order_items WHERE order_id = :id"), {"id": order_id})
    for item in items:
        db.execute(text("""
            INSERT INTO shop_order_items (
                order_id, line_no, product_id, product_code, name, qty, price,
                weight_g, length_cm, width_cm, height_cm, status
            ) VALUES (
                :order_id, :line_no, :product_id, :product_code, :name, 1, :price,
                :weight_g, :length_cm, :width_cm, :height_cm, 'new'
            )
        """), {"order_id": order_id, **item})


def _norm_shop_title(value: str) -> str:
    text_value = " ".join(str(value or "").replace("ё", "е").replace("Ё", "е").split())
    return text_value.lower()


def shop_status_from_payload(payload: dict) -> str:
    kind = _norm_shop_title(payload.get("status_kind") or "").replace(" ", "_")
    if kind in SHOP_STATUS_BY_KIND:
        return SHOP_STATUS_BY_KIND[kind]
    title = _norm_shop_title(payload.get("status_title") or "")
    if title in SHOP_STATUS_BY_TITLE:
        return SHOP_STATUS_BY_TITLE[title]
    letter = str(payload.get("cscart_status") or "").strip().upper()
    return SHOP_STATUS_BY_LETTER.get(letter, "")


def resolve_shop_status(current: str, payload: dict) -> str:
    """Сайт прислал явный статус — он и есть правда. Старая буква без названия склад назад не откатывает."""
    incoming = shop_status_from_payload(payload)
    explicit = bool(
        _norm_shop_title(payload.get("status_kind") or "")
        or _norm_shop_title(payload.get("status_title") or "")
    )
    current = current or ""
    if explicit and incoming:
        return incoming
    if not incoming:
        letter = str(payload.get("cscart_status") or "").strip().upper()
        if current == "cancelled" and letter not in CSCART_CANCELLED:
            return "new"
        return current or "new"
    if current in SHIPPED_OR_LATER and incoming in (_SOFT_SHOP_STATUSES | _CANCEL_SHOP_STATUSES | {"incomplete"}):
        return current
    if current in LOCKED_STATUSES and incoming in _SOFT_SHOP_STATUSES:
        return current
    return incoming


def _align_items_to_status(db: Session, order_id: int, status: str) -> None:
    if status not in PACKED_OR_LATER:
        return
    extra = ""
    if status == "packed":
        extra = ", packed_at = COALESCE(packed_at, now())"
    if status in SHIPPED_OR_LATER:
        extra = ", packed_at = COALESCE(packed_at, now()), shipped_at = COALESCE(shipped_at, now())"
    db.execute(text(f"""
        UPDATE shop_order_items
        SET status = :status
            {extra}
        WHERE order_id = :id AND status <> 'cancelled'
    """), {"id": order_id, "status": status})


def upsert_from_shop(db: Session, payload: dict) -> dict:
    data = normalize_ingest(payload)
    if data["cscart_order_id"] < 1:
        raise ValueError("Нет номера заказа CS-Cart")
    existing = _row(db.execute(text("""
        SELECT * FROM shop_orders WHERE cscart_order_id = :oid
    """), {"oid": data["cscart_order_id"]}).fetchone())

    header = {
        k: v for k, v in data.items()
        if k not in {"items", "raw_json", "status_kind", "status_title"}
    }
    header["raw_json"] = json.dumps(data["raw_json"], ensure_ascii=False, default=str)
    placed = str(header.get("placed_at") or "")
    if "T" in placed:
        header["placed_at"] = placed.replace("T", " ").split("+")[0].split("Z")[0][:19]
    elif not placed:
        header["placed_at"] = None

    if existing:
        header["status"] = resolve_shop_status(existing["status"], data)
        if header["status"] == "paid":
            header["paid"] = True
        db.execute(text("""
            UPDATE shop_orders SET
                cscart_status = :cscart_status,
                status = :status,
                paid = :paid,
                payment_name = :payment_name,
                shipping_method = :shipping_method,
                total = :total,
                shipping_cost = :shipping_cost,
                currency = :currency,
                customer_name = :customer_name,
                customer_email = :customer_email,
                customer_phone = :customer_phone,
                customer_company = :customer_company,
                shipping_city = :shipping_city,
                shipping_address = :shipping_address,
                shipping_zip = :shipping_zip,
                shipping_state = :shipping_state,
                shipping_country = :shipping_country,
                notes = :notes,
                admin_notes = :admin_notes,
                carrier = COALESCE(NULLIF(:carrier, ''), carrier),
                pvz_code = COALESCE(NULLIF(:pvz_code, ''), pvz_code),
                pvz_name = COALESCE(NULLIF(:pvz_name, ''), pvz_name),
                pvz_address = COALESCE(NULLIF(:pvz_address, ''), pvz_address),
                pvz_lat = COALESCE(:pvz_lat, pvz_lat),
                pvz_lon = COALESCE(:pvz_lon, pvz_lon),
                delivery_time = COALESCE(NULLIF(:delivery_time, ''), delivery_time),
                shop_barcode = COALESCE(NULLIF(:shop_barcode, ''), shop_barcode),
                raw_json = CAST(:raw_json AS JSONB),
                updated_at = now()
            WHERE id = :id
        """), {**header, "id": existing["id"]})
        if existing["status"] not in LOCKED_STATUSES and data["items"]:
            _replace_items(db, existing["id"], data["items"])
        _align_items_to_status(db, existing["id"], header["status"])
        db.commit()
        return get_order(db, existing["id"])

    status = resolve_shop_status("", data)
    if status == "paid":
        header["paid"] = True
    row = db.execute(text("""
        INSERT INTO shop_orders (
            cscart_order_id, cscart_status, status, paid, payment_name, shipping_method,
            total, shipping_cost, currency, customer_name, customer_email, customer_phone,
            customer_company, shipping_city, shipping_address, shipping_zip, shipping_state,
            shipping_country, notes, admin_notes, carrier, pvz_code, pvz_name, pvz_address,
            pvz_lat, pvz_lon, delivery_time, shop_barcode, placed_at, raw_json
        ) VALUES (
            :cscart_order_id, :cscart_status, :status, :paid, :payment_name, :shipping_method,
            :total, :shipping_cost, :currency, :customer_name, :customer_email, :customer_phone,
            :customer_company, :shipping_city, :shipping_address, :shipping_zip, :shipping_state,
            :shipping_country, :notes, :admin_notes, :carrier, :pvz_code, :pvz_name, :pvz_address,
            :pvz_lat, :pvz_lon, :delivery_time, :shop_barcode, :placed_at, CAST(:raw_json AS JSONB)
        )
        RETURNING id
    """), {**header, "status": status}).fetchone()
    order_id = _row(row)["id"]
    _replace_items(db, order_id, data["items"])
    _align_items_to_status(db, order_id, status)
    db.commit()
    return get_order(db, order_id)


def status_history(db: Session, order_id: int) -> list[dict]:
    rows = db.execute(text("""
        SELECT old_status, new_status, changed_by, comment,
               changed_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow' AS changed_at_msk
        FROM shop_order_status_log
        WHERE order_id = :id
        ORDER BY changed_at ASC, id ASC
    """), {"id": order_id}).fetchall()
    out = []
    for row in rows:
        item = _row(row) or {}
        changed = item.get("changed_at_msk")
        out.append({
            "old_status": item.get("old_status") or "",
            "new_status": item.get("new_status") or "",
            "changed_by": item.get("changed_by") or "",
            "comment": item.get("comment") or "",
            "changed_at": changed.isoformat() if changed else None,
        })
    return out


def _stamp_status_log(db: Session, order_id: int, actor: str = "", comment: str = "") -> None:
    db.execute(text("""
        UPDATE shop_order_status_log
        SET changed_by = COALESCE(NULLIF(:actor, ''), changed_by),
            comment = COALESCE(NULLIF(:comment, ''), comment)
        WHERE id = (
            SELECT id FROM shop_order_status_log
            WHERE order_id = :id
            ORDER BY changed_at DESC, id DESC
            LIMIT 1
        )
    """), {"id": order_id, "actor": _text(actor, 80), "comment": _text(comment, 500)})


def set_status(db: Session, order_id: int, status: str, username: str = "", comment: str = "") -> dict:
    if status not in STATUSES:
        raise ValueError("Неизвестный статус")
    current = db.execute(text("SELECT status FROM shop_orders WHERE id = :id"), {"id": order_id}).scalar()
    if current is None:
        raise KeyError("Заказ не найден")
    if current == status:
        return get_order(db, order_id)
    fields = {
        "id": order_id,
        "status": status,
        "processed_by": _text(username, 80),
        "packed_at": None,
        "shipped_at": None,
    }
    extra = ""
    if status == "paid":
        extra = ", paid = TRUE"
    elif status == "packed":
        extra = ", packed_at = COALESCE(packed_at, now())"
    elif status in SHIPPED_OR_LATER:
        extra = ", packed_at = COALESCE(packed_at, now()), shipped_at = COALESCE(shipped_at, now())"
    updated = db.execute(text(f"""
        UPDATE shop_orders
        SET status = :status,
            processed_by = COALESCE(NULLIF(:processed_by, ''), processed_by),
            updated_at = now()
            {extra}
        WHERE id = :id
        RETURNING id
    """), fields).fetchone()
    if not updated:
        raise KeyError("Заказ не найден")
    if status in PACKED_OR_LATER:
        _align_items_to_status(db, order_id, status)
    _stamp_status_log(db, order_id, username, comment)
    db.commit()
    if status in PACKED_OR_LATER:
        _rollup_order(db, order_id, username)
    _push_cscart_status(db, order_id)
    return get_order(db, order_id)


def pack_item(db: Session, order_id: int, item_id: int, username: str = "") -> dict:
    item = _row(db.execute(text("""
        SELECT * FROM shop_order_items WHERE id = :iid AND order_id = :oid
    """), {"iid": item_id, "oid": order_id}).fetchone())
    if not item:
        raise KeyError("Посылка не найдена")
    if item.get("status") not in SHIPPED_OR_LATER:
        db.execute(text("""
            UPDATE shop_order_items
            SET status = 'packed', packed_at = COALESCE(packed_at, now()), error = NULL
            WHERE id = :id
        """), {"id": item_id})
    db.execute(text("""
        UPDATE shop_orders
        SET status = CASE
                WHEN status = ANY(:keep) THEN status
                ELSE 'packing'
            END,
            processed_by = COALESCE(NULLIF(:who, ''), processed_by),
            updated_at = now()
        WHERE id = :id
    """), {"id": order_id, "who": _text(username, 80), "keep": list(SHIPPED_OR_LATER | _CANCEL_SHOP_STATUSES)})
    leftover = db.execute(text("""
        SELECT COUNT(*) FROM shop_order_items
        WHERE order_id = :id AND status <> ALL(:done)
    """), {"id": order_id, "done": list(PACKED_OR_LATER | {"cancelled"})}).scalar()
    if leftover == 0:
        db.execute(text("""
            UPDATE shop_orders
            SET status = CASE WHEN status = ANY(:later) THEN status ELSE 'packed' END,
                packed_at = COALESCE(packed_at, now()),
                updated_at = now()
            WHERE id = :id
        """), {"id": order_id, "later": list(SHIPPED_OR_LATER)})
    db.commit()
    _rollup_order(db, order_id, username)
    _push_cscart_status(db, order_id)
    return get_order(db, order_id)


def _carrier_req_id(item_id: int) -> int:
    return SHIP_REQ_BASE + int(item_id)


def create_item_shipment(
    db: Session,
    order_id: int,
    item_id: int,
    username: str = "",
    _silent: bool = False,
) -> dict:
    order = get_order(db, order_id)
    if not order:
        raise KeyError("Заказ не найден")
    item = next((row for row in order["items"] if row["id"] == item_id), None)
    if not item:
        raise KeyError("Посылка не найдена")
    if item.get("track_number") or item.get("ozon_posting_number") or item.get("cdek_uuid"):
        return order
    carrier = order.get("carrier") or ""
    pvz = order.get("pvz_code") or ""
    if carrier not in CARRIERS or not pvz:
        if _silent:
            return order
        raise ValueError("Нет пункта выдачи. Клиент должен выбрать Ozon, СДЭК или Яндекс на сайте.")
    phone = order.get("customer_phone") or ""
    name = order.get("customer_name") or "Клиент"
    title = item.get("name") or "Товар"
    req_id = _carrier_req_id(item_id)
    track = ""
    carrier_order_id = ""
    cdek_uuid = ""
    ozon_order_number = ""
    ozon_posting_number = ""
    yandex_sharing_url = ""
    cost = None
    try:
        if carrier == "ozon":
            dropoff = ozon_delivery.dropoff_barcode_info()
            dims = ozon_parcel_dims(item)
            result = ozon_delivery.create_order(
                req_id=req_id,
                customer_name=name,
                customer_phone=phone,
                destination_pvz_id=str(pvz),
                items_description=ozon_parcel_description(order, item),
                shipment_method_id=dropoff.get("method_id"),
                declared_cost=ozon_parcel_declared(order, item),
                weight_g=dims["weight_g"],
                length_mm=dims["length_mm"],
                width_mm=dims["width_mm"],
                height_mm=dims["height_mm"],
            )
            ozon_order_number = result.get("order_number") or ""
            ozon_posting_number = result.get("posting_number") or ""
            track = ozon_order_number or ozon_posting_number
            carrier_order_id = ozon_posting_number or track
        elif carrier == "cdek":
            dims = ozon_parcel_dims(item)
            result = cdek.create_order(
                req_id,
                name,
                phone,
                str(pvz),
                title,
                customer_email=order.get("customer_email") or None,
                weight_g=dims["weight_g"],
                length_cm=max(1, dims["length_mm"] // 10),
                width_cm=max(1, dims["width_mm"] // 10),
                height_cm=max(1, dims["height_mm"] // 10),
                declared_cost=max(1, int(ozon_parcel_declared(order, item))),
                comment=f"Заказ сайта #{order.get('cscart_order_id')}: {title[:180]}",
            )
            cdek_uuid = result.get("uuid") or ""
            track = result.get("cdek_number") or cdek_uuid
            carrier_order_id = cdek_uuid or track
        elif carrier == "yandex":
            result = yandex_ndd.create_order(
                req_id,
                name,
                phone,
                str(pvz),
                title,
                weight_g=int(item.get("weight_g") or DEFAULT_BOX[3]),
                declared_cost=max(1.0, float(item.get("price") or 1)),
                note=f"Заказ сайта #{order.get('cscart_order_id')}",
            )
            track = str(result.get("request_id") or "")
            carrier_order_id = track
            yandex_sharing_url = result.get("sharing_url") or ""
            cost = result.get("price")
    except Exception as exc:
        db.execute(text("""
            UPDATE shop_order_items SET error = :err WHERE id = :id
        """), {"id": item_id, "err": str(exc)[:500]})
        db.commit()
        raise
    db.execute(text("""
        UPDATE shop_order_items
        SET track_number = :track,
            carrier_order_id = :cid,
            cdek_uuid = COALESCE(NULLIF(:cdek_uuid, ''), cdek_uuid),
            ozon_order_number = COALESCE(NULLIF(:ozon_order, ''), ozon_order_number),
            ozon_posting_number = COALESCE(NULLIF(:ozon_posting, ''), ozon_posting_number),
            yandex_sharing_url = COALESCE(NULLIF(:yandex_url, ''), yandex_sharing_url),
            shipping_cost = COALESCE(:cost, shipping_cost),
            error = NULL
        WHERE id = :id
    """), {
        "id": item_id,
        "track": track,
        "cid": carrier_order_id,
        "cdek_uuid": cdek_uuid,
        "ozon_order": ozon_order_number,
        "ozon_posting": ozon_posting_number,
        "yandex_url": yandex_sharing_url,
        "cost": cost,
    })
    db.execute(text("""
        UPDATE shop_orders
        SET processed_by = COALESCE(NULLIF(:who, ''), processed_by),
            updated_at = now()
        WHERE id = :oid
    """), {
        "oid": order_id,
        "who": _text(username, 80),
    })
    db.commit()
    _rollup_order(db, order_id, username)
    _push_cscart_status(db, order_id)
    return get_order(db, order_id)


def mark_item_shipped(
    db: Session,
    order_id: int,
    item_id: int,
    track_number: str = "",
    username: str = "",
) -> dict:
    item = _row(db.execute(text("""
        SELECT * FROM shop_order_items WHERE id = :iid AND order_id = :oid
    """), {"iid": item_id, "oid": order_id}).fetchone())
    if not item:
        raise KeyError("Посылка не найдена")
    db.execute(text("""
        UPDATE shop_order_items
        SET status = 'shipped',
            packed_at = COALESCE(packed_at, now()),
            shipped_at = COALESCE(shipped_at, now()),
            track_number = COALESCE(NULLIF(:track, ''), track_number),
            error = NULL
        WHERE id = :id
    """), {"id": item_id, "track": _text(track_number, 80)})
    leftover = db.execute(text("""
        SELECT COUNT(*) FROM shop_order_items
        WHERE order_id = :id AND status <> ALL(:done)
    """), {"id": order_id, "done": list(SHIPPED_OR_LATER | {"cancelled"})}).scalar()
    if leftover == 0:
        db.execute(text("""
            UPDATE shop_orders
            SET status = CASE WHEN status = ANY(:later) THEN status ELSE 'shipped' END,
                packed_at = COALESCE(packed_at, now()),
                shipped_at = COALESCE(shipped_at, now()),
                processed_by = COALESCE(NULLIF(:who, ''), processed_by),
                updated_at = now()
            WHERE id = :id
        """), {"id": order_id, "who": _text(username, 80), "later": list(SHIPPED_OR_LATER)})
    else:
        db.execute(text("""
            UPDATE shop_orders
            SET status = CASE WHEN status = ANY(:keep) THEN status ELSE 'packing' END,
                processed_by = COALESCE(NULLIF(:who, ''), processed_by),
                updated_at = now()
            WHERE id = :id
        """), {
            "id": order_id,
            "who": _text(username, 80),
            "keep": list(SHIPPED_OR_LATER | _CANCEL_SHOP_STATUSES),
        })
    db.commit()
    _rollup_order(db, order_id, username)
    _push_cscart_status(db, order_id)
    return get_order(db, order_id)


def _status_rank(status: str) -> int:
    if status == "packing":
        return STATUS_CHAIN.index("new") + 1
    if status in STATUS_CHAIN:
        return STATUS_CHAIN.index(status)
    if status == "returned":
        return len(STATUS_CHAIN) + 1
    if status == "cancelled":
        return -1
    return 0


def _can_advance(current: str, new_status: str) -> bool:
    if new_status not in STATUSES or new_status == current:
        return False
    if current == "cancelled":
        return False
    if new_status == "returned":
        return current in SHIPPED_OR_LATER or current == "packed"
    return _status_rank(new_status) > _status_rank(current)


def rollup_from_items(item_statuses: list[str]) -> str:
    active = [s for s in item_statuses if s and s != "cancelled"]
    if not active:
        return "cancelled" if item_statuses else "new"
    if all(s == "returned" for s in active):
        return "returned"
    ranks = [_status_rank(s if s != "returned" else "delivered") for s in active]
    lowest_rank = min(ranks)
    if lowest_rank < 0:
        return "new"
    if lowest_rank >= len(STATUS_CHAIN):
        return "delivered"
    lowest = STATUS_CHAIN[lowest_rank]
    if lowest == "new" and any(s != "new" for s in active):
        return "packing"
    return lowest


def _cscart_stored_status(mapped: str, data: Optional[dict] = None) -> str:
    inner = (data or {}).get("data") if isinstance((data or {}).get("data"), dict) else {}
    letter = str(inner.get("status") or "").strip()
    if letter:
        return letter[:32]
    return (mapped or "")[:32]


def cscart_status_for(status: str) -> str:
    return {
        "new": "placed",
        "paid": "paid",
        "packed": CSCART_PACKED,
        "shipped": CSCART_SHIPPED,
        "in_transit": CSCART_IN_TRANSIT,
        "ready_for_pickup": CSCART_READY,
        "delivered": CSCART_DELIVERED,
        "returned": "returned",
        "cancelled": "cancelled_seller",
        "cancelled_seller": "cancelled_seller",
        "cancelled_buyer": "cancelled_buyer",
        "incomplete": "incomplete",
    }.get(status or "", "")


def cscart_push_payload(order: dict) -> dict:
    mapped = cscart_status_for(order.get("status") or "")
    carrier = (order.get("carrier") or "").lower()
    parcels = []
    for item in order.get("items") or []:
        track = (
            (item.get("track_number") or "")
            or (item.get("ozon_posting_number") or "")
            or (item.get("ozon_order_number") or "")
        ).strip()
        parcels.append({
            "name": item.get("name") or "Товар",
            "sku": item.get("product_code") or "",
            "status": item.get("status") or "",
            "status_label": item.get("status_label") or STATUS_LABELS.get(item.get("status") or "", ""),
            "track_number": track,
            "tracking_url": item.get("tracking_url") or tracking_url_for(carrier, item),
            "ozon_posting_number": item.get("ozon_posting_number") or "",
            "ozon_order_number": item.get("ozon_order_number") or "",
        })
    return {
        "order_id": order.get("cscart_order_id"),
        "status": mapped,
        "status_label": STATUS_LABELS.get(order.get("status") or "", mapped),
        "carrier": carrier,
        "carrier_label": CARRIER_LABELS.get(carrier, carrier or ""),
        "pvz_name": order.get("pvz_name") or "",
        "pvz_address": order.get("pvz_address") or "",
        "parcels": parcels,
    }


def _rollup_order(db: Session, order_id: int, username: str = "") -> None:
    rows = db.execute(text("""
        SELECT status FROM shop_order_items WHERE order_id = :id
    """), {"id": order_id}).fetchall()
    statuses = [(_row(r) or {}).get("status") or "new" for r in rows]
    if not statuses:
        return
    current = db.execute(text(
        "SELECT status FROM shop_orders WHERE id = :id"
    ), {"id": order_id}).scalar() or ""
    rolled = rollup_from_items(statuses)
    if current in _CANCEL_SHOP_STATUSES:
        return
    if current in {"paid", "incomplete"} and rolled in {"new", "packing"}:
        return
    extra = ""
    if rolled == "packed":
        extra = ", packed_at = COALESCE(packed_at, now())"
    if rolled in SHIPPED_OR_LATER:
        extra = ", packed_at = COALESCE(packed_at, now()), shipped_at = COALESCE(shipped_at, now())"
    db.execute(text(f"""
        UPDATE shop_orders
        SET status = :status,
            processed_by = COALESCE(NULLIF(:who, ''), processed_by),
            updated_at = now()
            {extra}
        WHERE id = :id AND status <> 'cancelled'
    """), {"id": order_id, "status": rolled, "who": _text(username, 80)})
    db.commit()


def _shop_token() -> str:
    return (shop_delivery.SHOP_TOKEN or os.getenv("SHOP_DELIVERY_TOKEN", "")).strip()


def _push_cscart_status(db: Session, order_id: int) -> None:
    order = get_order(db, order_id)
    if not order:
        return
    mapped = cscart_status_for(order.get("status") or "")
    current = (order.get("cscart_status") or "").upper()
    if current in CSCART_CANCELLED:
        return
    body = cscart_push_payload(order)
    if not body.get("order_id"):
        return
    has_tracks = any((p.get("track_number") or "").strip() for p in body.get("parcels") or [])
    if not mapped and not has_tracks:
        return
    key = hashlib.sha1(
        json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    existing_key = ""
    row = _row(db.execute(text("""
        SELECT cscart_push_key FROM shop_orders WHERE id = :id
    """), {"id": order_id}).fetchone())
    if row:
        existing_key = row.get("cscart_push_key") or ""
    if existing_key == key:
        return
    token = _shop_token()
    if not token:
        logger.warning("shop CS-Cart push skipped: no shop token")
        return
    raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
    url = f"{SHOP_CSCART_URL}/index.php?dispatch=vidovito_shop_orders.set_status"
    req = urllib.request.Request(
        url,
        data=raw_body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Shop-Token": token,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict) or data.get("status") != "ok":
            logger.warning("shop CS-Cart status %s for #%s: %s", mapped, order_id, raw[:300])
            return
    except Exception as exc:
        logger.warning("shop CS-Cart status %s for #%s failed: %s", mapped, order_id, exc)
        return
    db.execute(text("""
        UPDATE shop_orders
        SET cscart_status = COALESCE(NULLIF(:st, ''), cscart_status),
            cscart_push_key = :key,
            updated_at = now()
        WHERE id = :id
    """), {"id": order_id, "st": _cscart_stored_status(mapped, data), "key": key})
    db.commit()


def label_rows_for_order(order: dict, item_id: Optional[int] = None) -> list[dict]:
    carrier = (order.get("carrier") or "").lower()
    rows = []
    for item in order.get("items") or []:
        if item_id and item.get("id") != item_id:
            continue
        if not item.get("has_label") and not item.get("track_number") and not item.get("ozon_posting_number"):
            continue
        rows.append({
            "id": item.get("id") or 0,
            "customer_name": order.get("customer_name") or "",
            "address_city": order.get("shipping_city") or "",
            "client_pvz_city": order.get("shipping_city") or "",
            "delivery_method": carrier,
            "track_number": item.get("track_number") or "",
            "cdek_uuid": item.get("cdek_uuid") or "",
            "cdek_number": (item.get("track_number") or "") if carrier == "cdek" else "",
            "ozon_order_number": item.get("ozon_order_number") or "",
            "ozon_posting_number": item.get("ozon_posting_number") or "",
        })
    return rows


def persist_ozon_postings(db: Session, order_id: int, rows: list[dict]) -> None:
    changed = False
    for row in rows:
        if row.get("delivery_method") != "ozon":
            continue
        existing = (row.get("ozon_posting_number") or "").strip()
        if existing:
            continue
        barcode = ozon_delivery.resolve_label_barcode(
            req_id=_carrier_req_id(int(row.get("id") or 0)),
            order_number=row.get("track_number") or row.get("ozon_order_number") or "",
            posting_number="",
        )
        order_no = (row.get("track_number") or row.get("ozon_order_number") or "").strip()
        if not barcode or barcode == order_no or str(barcode).upper().startswith("REQ"):
            continue
        db.execute(text("""
            UPDATE shop_order_items
            SET ozon_posting_number = :posting
            WHERE id = :id AND order_id = :oid AND COALESCE(ozon_posting_number, '') = ''
        """), {"posting": barcode, "id": row["id"], "oid": order_id})
        row["ozon_posting_number"] = barcode
        changed = True
    if changed:
        db.commit()


def apply_item_carrier_status(
    db: Session,
    item_id: int,
    new_status: str,
    *,
    track_number: str = "",
    cdek_uuid: str = "",
    ozon_posting_number: str = "",
    ozon_order_number: str = "",
    yandex_sharing_url: str = "",
) -> Optional[int]:
    item = _row(db.execute(text("SELECT * FROM shop_order_items WHERE id = :id"), {"id": item_id}).fetchone())
    if not item:
        return None
    current = item.get("status") or "new"
    if not _can_advance(current, new_status):
        if track_number or cdek_uuid or ozon_posting_number or ozon_order_number or yandex_sharing_url:
            db.execute(text("""
                UPDATE shop_order_items
                SET track_number = COALESCE(NULLIF(:track, ''), track_number),
                    cdek_uuid = COALESCE(NULLIF(:cdek_uuid, ''), cdek_uuid),
                    ozon_posting_number = COALESCE(NULLIF(:ozon_posting, ''), ozon_posting_number),
                    ozon_order_number = COALESCE(NULLIF(:ozon_order, ''), ozon_order_number),
                    yandex_sharing_url = COALESCE(NULLIF(:yandex_url, ''), yandex_sharing_url)
                WHERE id = :id
            """), {
                "id": item_id,
                "track": track_number,
                "cdek_uuid": cdek_uuid,
                "ozon_posting": ozon_posting_number,
                "ozon_order": ozon_order_number,
                "yandex_url": yandex_sharing_url,
            })
            db.commit()
            _push_cscart_status(db, int(item["order_id"]))
        return None
    extra = ""
    if new_status in SHIPPED_OR_LATER:
        extra = ", packed_at = COALESCE(packed_at, now()), shipped_at = COALESCE(shipped_at, now())"
    db.execute(text(f"""
        UPDATE shop_order_items
        SET status = :st,
            error = NULL,
            track_number = COALESCE(NULLIF(:track, ''), track_number),
            cdek_uuid = COALESCE(NULLIF(:cdek_uuid, ''), cdek_uuid),
            ozon_posting_number = COALESCE(NULLIF(:ozon_posting, ''), ozon_posting_number),
            ozon_order_number = COALESCE(NULLIF(:ozon_order, ''), ozon_order_number),
            yandex_sharing_url = COALESCE(NULLIF(:yandex_url, ''), yandex_sharing_url)
            {extra}
        WHERE id = :id
    """), {
        "id": item_id,
        "st": new_status,
        "track": track_number,
        "cdek_uuid": cdek_uuid,
        "ozon_posting": ozon_posting_number,
        "ozon_order": ozon_order_number,
        "yandex_url": yandex_sharing_url,
    })
    db.commit()
    order_id = int(item["order_id"])
    _rollup_order(db, order_id)
    _push_cscart_status(db, order_id)
    return order_id


def apply_cdek_webhook(db: Session, cdek_number: str, status_code: str, uuid: str = "") -> int:
    number = (cdek_number or "").strip()
    uid = (uuid or "").strip()
    if not number and not uid:
        return 0
    new_status = CDEK_STATUS_MAP.get((status_code or "").strip())
    rows = db.execute(text("""
        SELECT id FROM shop_order_items
        WHERE (NULLIF(:num, '') IS NOT NULL AND (track_number = :num OR carrier_order_id = :num))
           OR (NULLIF(:uid, '') IS NOT NULL AND (cdek_uuid = :uid OR track_number = :uid OR carrier_order_id = :uid))
    """), {"num": number, "uid": uid}).fetchall()
    changed = 0
    for row in rows:
        item_id = (_row(row) or {}).get("id")
        if not item_id:
            continue
        if new_status:
            if apply_item_carrier_status(db, item_id, new_status, track_number=number, cdek_uuid=uid):
                changed += 1
        elif number:
            db.execute(text("""
                UPDATE shop_order_items
                SET track_number = COALESCE(NULLIF(:num, ''), track_number),
                    cdek_uuid = COALESCE(NULLIF(:uid, ''), cdek_uuid)
                WHERE id = :id
            """), {"id": item_id, "num": number, "uid": uid})
            db.commit()
    return changed


def fill_cdek_numbers(db: Session) -> int:
    rows = db.execute(text("""
        SELECT id, cdek_uuid, track_number FROM shop_order_items
        WHERE COALESCE(cdek_uuid, '') <> ''
          AND status <> ALL(:done)
    """), {"done": ["cancelled", "delivered", "returned"]}).fetchall()
    filled = 0
    for raw in rows:
        item = _row(raw) or {}
        uid = (item.get("cdek_uuid") or "").strip()
        if not uid:
            continue
        try:
            info = cdek.get_order_by_uuid(uid)
        except Exception as exc:
            logger.warning("shop cdek fill #%s: %s", item.get("id"), exc)
            continue
        number = (info.get("cdek_number") or "").strip()
        code = (info.get("status_code") or "").strip()
        if number:
            db.execute(text("""
                UPDATE shop_order_items
                SET track_number = :num
                WHERE id = :id
            """), {"id": item["id"], "num": number})
            db.commit()
            filled += 1
        mapped = CDEK_STATUS_MAP.get(code)
        if mapped:
            apply_item_carrier_status(db, item["id"], mapped, track_number=number, cdek_uuid=uid)
    return filled


def sync_open_yandex(db: Session) -> int:
    rows = db.execute(text("""
        SELECT i.id, i.track_number, i.status, i.yandex_sharing_url
        FROM shop_order_items i
        JOIN shop_orders o ON o.id = i.order_id
        WHERE o.carrier = 'yandex'
          AND COALESCE(i.track_number, '') <> ''
          AND i.status = ANY(:open)
    """), {"open": ["new", "packing", "packed", "shipped", "in_transit", "ready_for_pickup"]}).fetchall()
    moved = 0
    for raw in rows:
        item = _row(raw) or {}
        track = (item.get("track_number") or "").strip()
        try:
            info = yandex_ndd.get_order_status(track)
        except Exception as exc:
            logger.warning("shop yandex #%s: %s", item.get("id"), exc)
            continue
        mapped = yandex_ndd.STATUS_MAP.get(info.get("status") or "")
        sharing = (info.get("sharing_url") or "").strip()
        if mapped and apply_item_carrier_status(
            db, item["id"], mapped, track_number=track, yandex_sharing_url=sharing,
        ):
            moved += 1
        elif sharing:
            db.execute(text("""
                UPDATE shop_order_items
                SET yandex_sharing_url = COALESCE(NULLIF(:url, ''), yandex_sharing_url)
                WHERE id = :id
            """), {"id": item["id"], "url": sharing})
            db.commit()
    return moved


def sync_open_ozon(db: Session) -> int:
    rows = db.execute(text("""
        SELECT i.id, i.track_number, i.ozon_order_number, i.ozon_posting_number, i.status
        FROM shop_order_items i
        JOIN shop_orders o ON o.id = i.order_id
        WHERE o.carrier = 'ozon'
          AND COALESCE(NULLIF(i.track_number, ''), NULLIF(i.ozon_order_number, ''), NULLIF(i.ozon_posting_number, '')) IS NOT NULL
          AND i.status = ANY(:open)
    """), {"open": ["new", "packing", "packed", "shipped", "in_transit", "ready_for_pickup"]}).fetchall()
    moved = 0
    for raw in rows:
        item = _row(raw) or {}
        track = (
            item.get("ozon_posting_number")
            or item.get("track_number")
            or item.get("ozon_order_number")
            or ""
        ).strip()
        if not track:
            continue
        try:
            info = ozon_delivery.get_order_status(track)
            posting = info.get("posting") or {}
            order_no = (info.get("order_number") or posting.get("order_number") or "").strip()
            posting_no = (info.get("posting_number") or posting.get("posting_number") or "").strip()
            raw_status = info.get("raw_status") or ""
            if ozon_delivery.needs_posting_approve(raw_status):
                pn = posting_no or track
                ozon_delivery.approve_posting(pn)
                ozon_delivery.ensure_ready_for_shipping(pn, wait_s=8)
                try:
                    info = ozon_delivery.get_order_status(track)
                except Exception:
                    pass
            mapped = info.get("status")
            if mapped and apply_item_carrier_status(
                db, item["id"], mapped,
                track_number=track,
                ozon_posting_number=posting_no,
                ozon_order_number=order_no,
            ):
                moved += 1
            elif posting_no or order_no:
                db.execute(text("""
                    UPDATE shop_order_items
                    SET ozon_posting_number = COALESCE(NULLIF(:posting, ''), ozon_posting_number),
                        ozon_order_number = COALESCE(NULLIF(:order_no, ''), ozon_order_number)
                    WHERE id = :id
                """), {"id": item["id"], "posting": posting_no, "order_no": order_no})
                db.commit()
        except Exception as exc:
            logger.warning("shop ozon #%s: %s", item.get("id"), exc)
            try:
                db.rollback()
            except Exception:
                pass
            continue
    return moved

