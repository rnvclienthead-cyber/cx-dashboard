"""Карточка заказа в шапке чата: статус, дата, сумма.

Только локальные таблицы и точечный поиск по ключу заказа.
wb_logistics (~миллион строк) — только `WHERE srid = :r`, без скана.
"""
from __future__ import annotations

from sqlalchemy import text

from . import chat_fulfillment
from .ozon_posting import STATUS_LABELS as OZON_STATUS_LABELS

_RU_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)

YM_STATUS_RU = {
    "PROCESSING": "В сборке",
    "DELIVERY": "В доставке",
    "PICKUP": "В пункте выдачи",
    "DELIVERED": "Доставлен",
    "CANCELLED": "Отменён",
    "PENDING": "Ожидает",
    "UNPAID": "Не оплачен",
    "RESERVED": "Зарезервирован",
    "RETURNED": "Возврат",
    "PARTIALLY_RETURNED": "Частичный возврат",
    "REJECTED": "Отклонён",
    "UNKNOWN": "Неизвестно",
}

OZON_STATUS_EXTRA = {
    "posting_in_carriage": "В перевозке",
    "posting_on_way_to_city": "Едет в город",
    "posting_on_way_to_pickup_point": "Едет в ПВЗ",
    "posting_delivered_to_pickup_point": "В пункте выдачи",
    "customer_not_in_pickup_point": "Истёк срок хранения",
}


def _date_label(value) -> str:
    if not value:
        return ""
    try:
        if hasattr(value, "day") and hasattr(value, "month"):
            return f"{value.day} {_RU_MONTHS[value.month - 1]}"
        raw = str(value)[:10]
        year, month, day = raw.split("-")
        return f"{int(day)} {_RU_MONTHS[int(month) - 1]}"
    except Exception:
        return str(value)[:10]


def _price(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return round(n, 2)


def _tone(status: str, label: str) -> str:
    key = f"{status} {label}".lower()
    if any(x in key for x in ("отмен", "cancel", "отказ", "reject", "спор", "arbitration")):
        return "bad"
    if any(x in key for x in ("доставлен", "получен", "delivered", "sold", "выкуплен")):
        return "ok"
    if any(x in key for x in ("пути", "доставк", "сборк", "ожид", "пункт", "курьер", "deliver")):
        return "info"
    if any(x in key for x in ("возврат", "return")):
        return "warn"
    return "muted"


def _card(
    *,
    mp: str,
    status: str = "",
    status_label: str = "",
    order_id: str = "",
    article: str = "",
    date=None,
    price=None,
    warehouse: str = "",
    region: str = "",
    posting_number: str = "",
) -> dict | None:
    label = (status_label or status or "").strip()
    oid = str(order_id or posting_number or "").strip()
    article = str(article or "").strip()
    warehouse = str(warehouse or "").strip()
    region = str(region or "").strip()
    posting_number = str(posting_number or "").strip()
    date_label = _date_label(date)
    price_n = _price(price)
    if not any((label, oid, article, date_label, price_n, warehouse, region)):
        return None
    return {
        "mp": mp,
        "status": str(status or "").strip(),
        "status_label": label or "—",
        "tone": _tone(status, label),
        "order_id": oid,
        "posting_number": posting_number,
        "article": article,
        "date_label": date_label,
        "price": price_n,
        "warehouse": warehouse,
        "region": region,
    }


def resolve_chat_order(conn, mp: str, chat_id: str, *, rid: str = "", order_n: str = "") -> dict | None:
    mp = (mp or "").strip().lower()
    cid = str(chat_id or "").strip()
    if not cid and not rid and not order_n:
        return None
    try:
        if mp == "wb":
            return _resolve_wb(conn, cid, rid=rid)
        if mp == "ozon":
            return _resolve_ozon(conn, cid, order_n=order_n)
        if mp == "ym":
            return _resolve_ym(conn, cid, order_n=order_n)
    except Exception:
        return None
    return None


def _ozon_status_label(status: str) -> str:
    key = str(status or "").strip()
    return OZON_STATUS_LABELS.get(key) or OZON_STATUS_EXTRA.get(key) or key or "—"


def _ym_status_label(status: str) -> str:
    key = str(status or "").strip()
    return YM_STATUS_RU.get(key.upper(), key or "—")


def _wb_status_label(row: dict, dbw: dict | None) -> tuple[str, str]:
    if dbw:
        ui = str(dbw.get("ui_status") or "").strip()
        if ui:
            return ui, ui
        supplier = str(dbw.get("supplier_status") or "").strip()
        wb = str(dbw.get("wb_status") or "").strip()
        from .wb_dbw_delivery import archive_status_label, is_active_in_delivery
        if is_active_in_delivery(supplier, wb):
            return supplier or wb, "В доставке"
        return supplier or wb, archive_status_label(supplier, wb)
    if row.get("is_cancel"):
        return "cancel", "Отменён"
    doc = str(row.get("doc_type") or "").strip().upper()
    if doc in {"RETURN", "RETURNS", "ВОЗВРАТ"}:
        return doc, "Возврат"
    if doc in {"SALE", "ПРОДАЖА"}:
        return doc, "Продажа"
    return doc, doc or "Заказ"


def _resolve_wb(conn, chat_id: str, *, rid: str = "") -> dict | None:
    rid = str(rid or "").strip()
    if not rid:
        if not chat_id:
            return None
        chat = conn.execute(text("""
            SELECT order_rid FROM wb_chats WHERE chat_id = :cid LIMIT 1
        """), {"cid": chat_id}).mappings().first()
        if not chat:
            return None
        rid = chat_fulfillment._wb_order_rid(conn, chat_id, chat.get("order_rid") or "")
    if not rid:
        return None
    log = conn.execute(text("""
        SELECT srid, dt, supplier_article, nm_id, finish_price,
               warehouse_name, doc_type, is_cancel
        FROM wb_logistics
        WHERE srid = :r
        LIMIT 1
    """), {"r": rid}).mappings().first()
    row = dict(log) if log else None
    if not row:
        order = conn.execute(text("""
            SELECT srid, dt, supplier_article, shk_id,
                   CASE WHEN cancel_dt IS NOT NULL THEN true ELSE false END AS is_cancel
            FROM wb_orders
            WHERE srid = :r
            LIMIT 1
        """), {"r": rid}).mappings().first()
        row = dict(order) if order else {}
    dbw = conn.execute(text("""
        SELECT d.supplier_status, d.wb_status, d.ui_status, d.article
        FROM wb_orders o
        JOIN wb_dbw_tracked_orders d ON d.sticker = o.shk_id::text
        WHERE o.srid = :r
        LIMIT 1
    """), {"r": rid}).mappings().first()
    dbw_d = dict(dbw) if dbw else None
    if not row and not dbw_d:
        return None
    status, label = _wb_status_label(row or {}, dbw_d)
    article = str((row or {}).get("supplier_article") or (dbw_d or {}).get("article") or "").strip()
    return _card(
        mp="wb",
        status=status,
        status_label=label,
        order_id=str((row or {}).get("srid") or rid),
        article=article,
        date=(row or {}).get("dt"),
        price=(row or {}).get("finish_price"),
        warehouse=str((row or {}).get("warehouse_name") or ""),
    )


def _resolve_ozon(conn, chat_id: str, *, order_n: str = "") -> dict | None:
    order_n = str(order_n or "").strip()
    if not order_n:
        chat = conn.execute(text("""
            SELECT order_number FROM ozon_chats
            WHERE chat_id::text = :cid
            LIMIT 1
        """), {"cid": chat_id}).mappings().first()
        order_n = str((chat or {}).get("order_number") or "").strip()
    if not order_n:
        return None
    row = conn.execute(text("""
        SELECT posting_number, order_number, status, supplier_article,
               sku_name, created_at, price
        FROM ozon_orders
        WHERE posting_number = :ord
           OR order_number = :ord
           OR posting_number LIKE :ord || '-%'
        ORDER BY
            CASE
              WHEN posting_number = :ord OR order_number = :ord THEN 0
              ELSE 1
            END,
            created_at DESC NULLS LAST
        LIMIT 1
    """), {"ord": order_n}).mappings().first()
    if not row:
        return None
    d = dict(row)
    status = str(d.get("status") or "").strip()
    return _card(
        mp="ozon",
        status=status,
        status_label=_ozon_status_label(status),
        order_id=str(d.get("order_number") or order_n),
        posting_number=str(d.get("posting_number") or ""),
        article=str(d.get("supplier_article") or ""),
        date=d.get("created_at"),
        price=d.get("price"),
    )


def _resolve_ym(conn, chat_id: str, *, order_n: str = "") -> dict | None:
    order_n = str(order_n or "").strip()
    if not order_n:
        chat = conn.execute(text("""
            SELECT order_id FROM ym_chats
            WHERE chat_id::text = :cid
            LIMIT 1
        """), {"cid": chat_id}).mappings().first()
        order_n = str((chat or {}).get("order_id") or "").strip()
    if not order_n:
        return None
    row = conn.execute(text("""
        SELECT order_id, status, supplier_article, sku_name,
               created_at, price, buyer_total, region_name
        FROM ym_orders
        WHERE order_id::text = :oid
        LIMIT 1
    """), {"oid": order_n}).mappings().first()
    if not row:
        return None
    d = dict(row)
    status = str(d.get("status") or "").strip()
    return _card(
        mp="ym",
        status=status,
        status_label=_ym_status_label(status),
        order_id=str(d.get("order_id") or order_n),
        article=str(d.get("supplier_article") or ""),
        date=d.get("created_at"),
        price=d.get("buyer_total") or d.get("price"),
        region=str(d.get("region_name") or ""),
    )
