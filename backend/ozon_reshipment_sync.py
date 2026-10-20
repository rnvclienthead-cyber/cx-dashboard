"""Опрос статусов Ozon Доставки: автоподтверждение отправления и смена статуса доотправки."""
from __future__ import annotations

import logging
import secrets
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import ozon_delivery
from . import reshipment_notify

logger = logging.getLogger("ozon_updater")

STATUS_ORDER = ["shipped", "in_transit", "ready_for_pickup", "delivered", "returned"]
ACTIVE_STATUSES = ("approved", "packed", "shipped", "in_transit", "ready_for_pickup")


def _track(row: dict) -> str:
    return (
        row.get("ozon_posting_number")
        or row.get("track_number")
        or row.get("ozon_order_number")
        or ""
    ).strip()


def _store_ozon_meta(db: Session, req_id: int, info: dict) -> None:
    from .salebot import parse_eta_date
    posting = info.get("posting") or {}
    order_no = (info.get("order_number") or posting.get("order_number") or "").strip()
    posting_no = (info.get("posting_number") or posting.get("posting_number") or "").strip()
    eta = parse_eta_date(
        info.get("eta_raw") or posting.get("delivery_at") or posting.get("original_delivery_at")
    )
    if not order_no and not posting_no and not eta:
        return
    db.execute(text("""
        UPDATE reshipment_requests
        SET eta_date = COALESCE(:eta, eta_date),
            ozon_order_number = COALESCE(NULLIF(:order_no, ''), ozon_order_number),
            ozon_posting_number = COALESCE(NULLIF(:posting_no, ''), ozon_posting_number)
        WHERE id = :id
    """), {"id": req_id, "eta": eta, "order_no": order_no, "posting_no": posting_no})
    db.commit()


def _notify_client(db: Session, row: dict, new_status: str, track: str, eta_date="") -> None:
    pvz_addr = (row.get("ozon_pvz_address") or row.get("client_pvz_address") or "").strip()
    pvz_name = (row.get("ozon_pvz_name") or "").strip()
    reshipment_notify.notify_delivery_status(
        db, row, new_status,
        delivery_method="ozon",
        track_number=track,
        pvz_address=pvz_addr,
        pvz_name=pvz_name,
        items_to_send=row.get("items_to_send") or "",
        eta_date=eta_date,
    )


def sync_one(db: Session, row: dict) -> Optional[str]:
    """Подтверждает отправление при необходимости и двигает статус вперёд. Возвращает новый статус."""
    track = _track(row)
    if not track:
        return None
    info = ozon_delivery.get_order_status(track)
    raw_status = info.get("raw_status") or ""
    if ozon_delivery.needs_posting_approve(raw_status):
        pn = (row.get("ozon_posting_number") or track).strip()
        result = ozon_delivery.approve_posting(pn)
        if result.get("ok"):
            ozon_delivery.ensure_ready_for_shipping(pn, wait_s=8)
            logger.info("ozon_updater: approved posting #%s %s", row["id"], pn)
        else:
            logger.warning("ozon_updater: approve failed #%s %s %s", row["id"], pn, result.get("error"))
        info = ozon_delivery.get_order_status(track)
        raw_status = info.get("raw_status") or ""

    try:
        _store_ozon_meta(db, row["id"], info)
    except Exception as ex:
        logger.warning("ozon_updater: meta #%s: %s", row["id"], ex)
        try:
            db.rollback()
        except Exception:
            pass

    new_status = info.get("status")
    if not new_status or new_status == row["status"]:
        return None
    curr_idx = STATUS_ORDER.index(row["status"]) if row["status"] in STATUS_ORDER else -1
    new_idx = STATUS_ORDER.index(new_status) if new_status in STATUS_ORDER else -1
    if new_idx <= curr_idx:
        return None

    updated = db.execute(text("""
        UPDATE reshipment_requests
        SET status = :ns,
            shipped_at = CASE
              WHEN :ns IN ('shipped','in_transit','ready_for_pickup','delivered')
               AND shipped_at IS NULL THEN NOW()
              ELSE shipped_at END,
            returned_at = CASE
              WHEN :ns = 'returned' THEN COALESCE(returned_at, NOW())
              ELSE returned_at END,
            confirmed_at = CASE
              WHEN :ns IN ('delivered','returned') THEN COALESCE(confirmed_at, NOW())
              ELSE confirmed_at END,
            confirmation_token = COALESCE(confirmation_token, :tok)
        WHERE id = :id AND status = :curr
        RETURNING id, salebot_client_id, wb_reply_sign
    """), {
        "ns": new_status, "id": row["id"], "curr": row["status"],
        "tok": secrets.token_hex(32),
    }).mappings().first()
    db.commit()
    if not updated:
        return None
    merged = dict(row)
    merged["salebot_client_id"] = updated.get("salebot_client_id")
    merged["wb_reply_sign"] = updated.get("wb_reply_sign") or row.get("wb_reply_sign")
    _notify_client(
        db, merged, new_status, track,
        eta_date=info.get("eta_raw") or (info.get("posting") or {}).get("delivery_at") or "",
    )
    logger.info("ozon_updater: #%s %s → %s (%s)", row["id"], row["status"], new_status, raw_status)
    return new_status


def sync_open_ozon_reshipments(db: Session) -> dict:
    """Один проход по открытым Ozon-заявкам: подтвердить + обновить статусы."""
    healed = recover_missing_ozon_orders(db)
    rows = db.execute(text("""
        SELECT id, track_number, ozon_order_number, ozon_posting_number, status, salebot_client_id,
               wb_reply_sign, ozon_pvz_address, ozon_pvz_name, client_pvz_address,
               items_to_send, ym_chat_id, ozon_chat_id, marketplace, wb_chat_id
        FROM reshipment_requests
        WHERE delivery_method = 'ozon'
          AND COALESCE(NULLIF(track_number, ''), NULLIF(ozon_order_number, ''), NULLIF(ozon_posting_number, '')) IS NOT NULL
          AND status = ANY(:statuses)
        ORDER BY id
    """), {"statuses": list(ACTIVE_STATUSES)}).mappings().all()
    moved = 0
    for row in rows:
        try:
            if sync_one(db, dict(row)):
                moved += 1
        except Exception as e:
            logger.warning("ozon_updater: ошибка #%s: %s", row["id"], e)
            try:
                db.rollback()
            except Exception:
                pass
    return {"checked": len(rows), "moved": moved, "healed": healed}


def recover_missing_ozon_orders(db: Session) -> int:
    """Одобрили Ozon, а заказ не записался (таймаут API) — найти или создать."""
    if not ozon_delivery.is_enabled():
        return 0
    rows = db.execute(text("""
        SELECT id, customer_name, customer_phone, items_to_send,
               ozon_pvz_code, client_pvz_code, ozon_shipment_method_id
        FROM reshipment_requests
        WHERE delivery_method = 'ozon'
          AND status IN ('approved', 'packed')
          AND COALESCE(NULLIF(track_number, ''), NULLIF(ozon_order_number, ''), NULLIF(ozon_posting_number, '')) IS NULL
        ORDER BY id
        LIMIT 20
    """)).mappings().all()
    healed = 0
    for row in rows:
        req_id = row["id"]
        try:
            found = ozon_delivery.find_posting(req_id=req_id)
            order_no = str((found or {}).get("order_number") or "").strip()
            posting_no = str((found or {}).get("posting_number") or "").strip()
            if not order_no and not posting_no:
                pvz = row.get("ozon_pvz_code") or row.get("client_pvz_code") or ""
                if not pvz:
                    logger.warning("ozon_updater: #%s без ПВЗ — заказ не создать", req_id)
                    continue
                created = ozon_delivery.create_order(
                    req_id=req_id,
                    customer_name=row["customer_name"],
                    customer_phone=row["customer_phone"],
                    destination_pvz_id=pvz,
                    items_description=row.get("items_to_send") or "Комплектующие",
                    shipment_method_id=row.get("ozon_shipment_method_id"),
                )
                order_no = created.get("order_number") or ""
                posting_no = created.get("posting_number") or ""
            if not order_no and not posting_no:
                continue
            track = order_no or posting_no
            db.execute(text("""
                UPDATE reshipment_requests
                SET track_number = COALESCE(NULLIF(track_number, ''), :track),
                    ozon_order_number = COALESCE(NULLIF(ozon_order_number, ''), :order_no),
                    ozon_posting_number = COALESCE(NULLIF(ozon_posting_number, ''), :posting_no)
                WHERE id = :id
                  AND COALESCE(NULLIF(track_number, ''), NULLIF(ozon_order_number, ''), NULLIF(ozon_posting_number, '')) IS NULL
            """), {
                "id": req_id,
                "track": track,
                "order_no": order_no or track,
                "posting_no": posting_no,
            })
            db.commit()
            healed += 1
            logger.info("ozon_updater: healed missing order #%s %s", req_id, track)
        except Exception as e:
            logger.warning("ozon_updater: heal #%s: %s", req_id, e)
            try:
                db.rollback()
            except Exception:
                pass
    return healed
