"""Тип отгрузки по чату: FBS / FBO / DBW / FBY.

Нужен менеджеру в шапке чата, чтобы не путать «доставку площадки» с нашей отгрузкой.
Считаем только по локальным таблицам — без живых запросов к WB/Ozon на каждое открытие.
"""
from __future__ import annotations

import time

from sqlalchemy import text

LABELS = {
    "FBS": "FBS · наш склад",
    "FBO": "FBO · склад WB",
    "DBW": "DBW · курьер WB",
    "FBY": "FBY · склад Маркета",
    "DBS": "DBS · своя доставка",
}

# «Где мой заказ»: задержка на нашей стороне vs доставка площадки.
DOSTAVKA_OUR_DELAY_KEY = "nasha_zaderzhka"
_DOSTAVKA_SELLER_CODES = frozenset({"FBS", "DBS"})
_DOSTAVKA_MARKETPLACE_CODES = frozenset({"FBO", "FBY", "DBW"})


def dostavka_branch_for_fulfillment(code: str | None) -> str | None:
    """FBS/своя доставка → «Наша задержка»; FBO/FBY/DBW → обычный шаблон площадки.

    None — тип неизвестен, ветку не угадываем.
    Пустая строка — обычный шаблон (ветка без branch_key).
    """
    key = str(code or "").strip().upper()
    if key in _DOSTAVKA_SELLER_CODES:
        return DOSTAVKA_OUR_DELAY_KEY
    if key in _DOSTAVKA_MARKETPLACE_CODES:
        return ""
    return None


# Склады WB в статистике продаж — это FBO (товар уже на стороне WB).
_FBO_WAREHOUSE_MARKERS = (
    "склад wb", "коледино", "белая дача", "электросталь", "невинномысск",
    "сынково", "крыловская", "домодедово", "шушары", "татарстан",
)

_YM_CAMPAIGN_CACHE: dict = {"ts": 0.0, "map": {}}
_YM_CAMPAIGN_TTL = 900


def fulfillment_payload(code: str | None, hint: str = "") -> dict | None:
    key = str(code or "").strip().upper()
    if key not in LABELS:
        return None
    out = {"code": key, "label": LABELS[key]}
    if hint:
        out["hint"] = hint
    return out


def classify_wb_warehouse(name: str) -> str | None:
    n = (name or "").strip().lower()
    if not n:
        return None
    if any(x in n for x in ("фбс", "fbs", "киталл")):
        return "FBS"
    if "dbw" in n:
        return "DBW"
    if n.startswith(("сц ", "ск ", "склад wb", "спб ")):
        return "FBO"
    if any(x in n for x in _FBO_WAREHOUSE_MARKERS):
        return "FBO"
    # Остальные имена в wb_logistics — региональные склады WB.
    return "FBO"


def _ozon_code(schema) -> str | None:
    raw = str(schema or "").strip().lower()
    if raw in ("fbo",):
        return "FBO"
    if raw in ("fbs", "rfbs"):
        return "FBS"
    return None


def _ym_campaign_types() -> dict[int, str]:
    now = time.time()
    cached = _YM_CAMPAIGN_CACHE.get("map") or {}
    if cached and (now - float(_YM_CAMPAIGN_CACHE.get("ts") or 0)) < _YM_CAMPAIGN_TTL:
        return cached
    out: dict[int, str] = {}
    try:
        from . import ym_client
        for camp in ym_client.available_campaigns(timeout=8) or []:
            cid = camp.get("id")
            ptype = str(camp.get("placementType") or "").upper()
            if cid and ptype in LABELS:
                out[int(cid)] = ptype
    except Exception:
        out = cached
    if out:
        _YM_CAMPAIGN_CACHE["ts"] = now
        _YM_CAMPAIGN_CACHE["map"] = out
    return out or cached


def resolve_chat_fulfillment(conn, mp: str, chat_id: str) -> dict | None:
    mp = (mp or "").strip().lower()
    cid = str(chat_id or "").strip()
    if not cid:
        return None
    try:
        if mp == "wb":
            return _resolve_wb(conn, cid)
        if mp == "ozon":
            return _resolve_ozon(conn, cid)
        if mp == "ym":
            return _resolve_ym(conn, cid)
    except Exception:
        return None
    return None


def _wb_order_rid(conn, chat_id: str, order_rid: str = "") -> str:
    rid = str(order_rid or "").strip()
    if rid:
        return rid
    row = conn.execute(text("""
        SELECT attachments->'goodCard'->>'rid'
        FROM wb_chat_messages
        WHERE chat_id = :cid
          AND coalesce(attachments->'goodCard'->>'rid', '') <> ''
        ORDER BY add_ts ASC NULLS LAST
        LIMIT 1
    """), {"cid": chat_id}).first()
    if row and row[0]:
        return str(row[0]).strip()
    row = conn.execute(text("""
        SELECT rid FROM wb_chat_pending
        WHERE chat_id = :cid AND coalesce(rid, '') <> ''
        LIMIT 1
    """), {"cid": chat_id}).first()
    if row and row[0]:
        return str(row[0]).strip()
    return ""


def _resolve_wb(conn, chat_id: str) -> dict | None:
    chat = conn.execute(text("""
        SELECT order_rid, nm_id FROM wb_chats WHERE chat_id = :cid LIMIT 1
    """), {"cid": chat_id}).mappings().first()
    if not chat:
        return None
    rid = _wb_order_rid(conn, chat_id, chat.get("order_rid") or "")
    nm_id = chat.get("nm_id")

    warehouse = ""
    if rid:
        row = conn.execute(text("""
            SELECT warehouse_name FROM wb_logistics WHERE srid = :r LIMIT 1
        """), {"r": rid}).first()
        warehouse = (row[0] or "").strip() if row else ""
        by_wh = classify_wb_warehouse(warehouse)
        if by_wh == "FBS":
            return fulfillment_payload("FBS", warehouse)
        if by_wh == "DBW":
            return fulfillment_payload("DBW", warehouse)
        if by_wh == "FBO":
            return fulfillment_payload("FBO", warehouse)
        dbw = conn.execute(text("""
            SELECT 1
            FROM wb_orders o
            JOIN wb_dbw_tracked_orders d ON d.sticker = o.shk_id::text
            WHERE o.srid = :r
            LIMIT 1
        """), {"r": rid}).scalar()
        if dbw:
            return fulfillment_payload("DBW")
        return fulfillment_payload("FBS")

    if nm_id is not None:
        n = conn.execute(text("""
            SELECT count(*) FROM wb_dbw_tracked_orders
            WHERE nm_id = :n AND last_seen_at > now() - interval '21 days'
        """), {"n": int(nm_id)}).scalar() or 0
        if n == 1:
            return fulfillment_payload("DBW")
    return None


def _resolve_ozon(conn, chat_id: str) -> dict | None:
    row = conn.execute(text("""
        SELECT o.schema_code
        FROM ozon_chats c
        JOIN ozon_orders o ON (
            o.posting_number = c.order_number
            OR o.order_number = c.order_number
        )
        WHERE c.chat_id = :cid
          AND coalesce(c.order_number, '') <> ''
        ORDER BY o.created_at DESC NULLS LAST
        LIMIT 1
    """), {"cid": chat_id}).first()
    if not row:
        return None
    return fulfillment_payload(_ozon_code(row[0]))


def _resolve_ym(conn, chat_id: str) -> dict | None:
    row = conn.execute(text("""
        SELECT r.campaign_id
        FROM ym_chats c
        JOIN ym_returns r ON r.order_id::text = c.order_id::text
        WHERE c.chat_id::text = :cid
          AND coalesce(c.order_id, '') <> ''
        ORDER BY r.created_at DESC NULLS LAST
        LIMIT 1
    """), {"cid": chat_id}).first()
    if not row or row[0] is None:
        return None
    try:
        camp = int(row[0])
    except (TypeError, ValueError):
        return None
    ptype = _ym_campaign_types().get(camp)
    if ptype == "FBS":
        return fulfillment_payload("FBS")
    if ptype == "FBY":
        return fulfillment_payload("FBY")
    if ptype == "DBS":
        return fulfillment_payload("DBS")
    return None
