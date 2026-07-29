#!/usr/bin/env python3
"""Разовая синхронизация packed → актуальный статус Яндекса + уведомление."""
import os, sys, time, secrets
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from sqlalchemy import text
from backend.database import SessionLocal
from backend import yandex_ndd, reshipment_notify
from backend.salebot import (
    notify_in_transit, notify_ready_for_pickup, notify_delivered, is_enabled,
    format_delivery_status_message,
)

db = SessionLocal()
try:
    rows = db.execute(text("""
      SELECT id, track_number, status, salebot_client_id, wb_reply_sign,
             yandex_sharing_url, yandex_pvz_address, yandex_pvz_name,
             client_pvz_address, items_to_send
      FROM reshipment_requests
      WHERE status = 'packed' AND delivery_method = 'yandex'
        AND track_number IS NOT NULL AND track_number != ''
      ORDER BY id
    """)).mappings().fetchall()

    for row in rows:
        info = yandex_ndd.get_order_status(row["track_number"])
        raw = info.get("status") or ""
        new_status = yandex_ndd.STATUS_MAP.get(raw)
        print(f"#{row['id']}: packed | YD={raw} -> {new_status}")
        if not new_status or new_status == "packed":
            continue

        sharing_url = (info.get("sharing_url") or row.get("yandex_sharing_url") or "").strip()
        updated = db.execute(text("""
            UPDATE reshipment_requests
            SET status = :ns,
                shipped_at = CASE WHEN shipped_at IS NULL THEN NOW() ELSE shipped_at END,
                confirmed_at = CASE WHEN :ns IN ('delivered','returned')
                                    THEN COALESCE(confirmed_at, NOW()) ELSE confirmed_at END,
                confirmation_token = COALESCE(confirmation_token, :tok),
                yandex_sharing_url = COALESCE(NULLIF(:sharing_url, ''), yandex_sharing_url),
                updated_at = NOW()
            WHERE id = :id AND status = 'packed'
            RETURNING id
        """), {
            "ns": new_status, "id": row["id"],
            "tok": secrets.token_hex(32), "sharing_url": sharing_url,
        }).mappings().first()
        db.commit()
        if not updated:
            print("  skip update")
            continue
        print(f"  updated -> {new_status}")

        if not reshipment_notify.claim_notify(db, row["id"], new_status):
            print("  notify already sent")
            continue

        num = row["track_number"]
        sid = row.get("salebot_client_id")
        pvz_addr = (row.get("yandex_pvz_address") or row.get("client_pvz_address") or "").strip()
        pvz_name = (row.get("yandex_pvz_name") or "").strip()
        items = row.get("items_to_send") or ""
        salebot_sent = False
        if sid and is_enabled():
            try:
                if new_status == "in_transit":
                    salebot_sent = notify_in_transit(
                        sid, row["id"], track_number=num, delivery_method="yandex",
                        sharing_url=sharing_url, items_to_send=items,
                        pvz_address=pvz_addr, pvz_name=pvz_name,
                    )
                elif new_status == "ready_for_pickup":
                    salebot_sent = notify_ready_for_pickup(
                        sid, row["id"], track_number=num, delivery_method="yandex",
                        sharing_url=sharing_url, pvz_address=pvz_addr,
                        pvz_name=pvz_name, items_to_send=items,
                    )
                elif new_status == "delivered":
                    salebot_sent = notify_delivered(sid, row["id"])
            except Exception as e:
                print("  salebot err", e)

        if not salebot_sent and row.get("wb_reply_sign"):
            try:
                from backend import wb_chat
                msg = format_delivery_status_message(
                    new_status, track_number=num, delivery_method="yandex",
                    sharing_url=sharing_url, pvz_address=pvz_addr,
                    pvz_name=pvz_name, items_to_send=items,
                )
                if msg:
                    wb_chat.send_message(row["wb_reply_sign"], msg)
                    print("  wb notified")
            except Exception as e:
                print("  wb err", e)
        time.sleep(0.5)
finally:
    db.close()
