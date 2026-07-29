#!/usr/bin/env python3
"""Сверка статусов packed vs перевозчик."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from sqlalchemy import text
from backend.database import SessionLocal
from backend import yandex_ndd

db = SessionLocal()
try:
    rows = db.execute(text("""
      SELECT id, status, delivery_method, track_number, cdek_number, cdek_uuid,
             customer_name, shipped_at, updated_at
      FROM reshipment_requests
      WHERE status = 'packed'
      ORDER BY id
    """)).mappings().fetchall()
    print(f"packed count: {len(rows)}")
    stuck = []
    for r in rows:
        method = r["delivery_method"] or ""
        track = (r["track_number"] or r["cdek_number"] or "").strip()
        print(f"\n#{r['id']} {r['customer_name']} method={method} track={track}")
        if method == "yandex" and track:
            try:
                info = yandex_ndd.get_order_status(track)
                raw = info.get("status") or ""
                mapped = yandex_ndd.STATUS_MAP.get(raw, "(не маппится)")
                print(f"  YD: {raw} -> {mapped} | {info.get('description')}")
                if mapped in ("shipped", "in_transit", "ready_for_pickup", "delivered", "returned"):
                    stuck.append((r["id"], "packed", mapped, raw, track))
            except Exception as e:
                print(f"  YD err: {e}")
        elif method == "cdek":
            print("  CDEK: skip live check (need webhook/history)")
        else:
            print("  no carrier/track")

    print("\n=== NEED FIX (packed but carrier ahead) ===")
    for s in stuck:
        print(s)
finally:
    db.close()
