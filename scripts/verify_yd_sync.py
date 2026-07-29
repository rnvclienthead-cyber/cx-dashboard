#!/usr/bin/env python3
import os, sys, pathlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
from sqlalchemy import text
from backend.database import SessionLocal
from backend import yandex_ndd

KNOWN_OK_UNMAPPED = {
    "SORTING_CENTER_LOADED", "SORTING_CENTER_CREATED", "CREATED", "DRAFT",
    "SORTING_CENTER_PREPARED", "SORTING_CENTER_CANCELED", "CANCELLED", "CANCELED",
}

db = SessionLocal()
try:
    rows = db.execute(text("""
      SELECT id, status, track_number, customer_name
      FROM reshipment_requests
      WHERE delivery_method = 'yandex'
        AND track_number IS NOT NULL AND track_number != ''
        AND status IN ('approved','packed','shipped','in_transit','ready_for_pickup')
      ORDER BY id
    """)).mappings().fetchall()
    print(f"active yandex: {len(rows)}")
    mismatches, unknown = [], []
    order = ["approved","packed","shipped","in_transit","ready_for_pickup","delivered","returned"]
    for r in rows:
        info = yandex_ndd.get_order_status(r["track_number"])
        raw = info.get("status") or ""
        mapped = yandex_ndd.STATUS_MAP.get(raw)
        our = r["status"]
        print(f"#{r['id']} ours={our} yd={raw}->{mapped}")
        if mapped and mapped != our:
            oi = order.index(our) if our in order else -1
            mi = order.index(mapped) if mapped in order else -1
            if mi > oi:
                mismatches.append((r["id"], our, mapped, raw))
        elif raw and not mapped and raw not in KNOWN_OK_UNMAPPED:
            unknown.append((r["id"], our, raw, info.get("description")))
    print("MISMATCHES:", mismatches)
    print("UNKNOWN:", unknown)
    main = pathlib.Path("/root/cx-dashboard/backend/main.py").read_text()
    ndd = pathlib.Path("/root/cx-dashboard/backend/yandex_ndd.py").read_text()
    idx = main.find("AND status IN")
    print("updater filter snippet:", main[idx:idx+120].replace("\n"," "))
    print("has DELIVERY_TRANSPORTATION:", '"DELIVERY_TRANSPORTATION"' in ndd)
finally:
    db.close()
