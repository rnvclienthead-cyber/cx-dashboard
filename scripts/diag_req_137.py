#!/usr/bin/env python3
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
from sqlalchemy import text
from backend.database import SessionLocal

db = SessionLocal()
try:
    row = db.execute(text("SELECT * FROM reshipment_requests WHERE id = 137")).mappings().first()
    if not row:
        print("NOT FOUND")
        sys.exit(1)
    d = {k: (str(v)[:300] if v is not None else None) for k, v in dict(row).items()}
    print(json.dumps(d, ensure_ascii=False, indent=2, default=str))

    track = row.get("track_number") or ""
    print("\n--- Yandex status ---")
    if track and (row.get("delivery_method") == "yandex" or "-udp" in str(track)):
        from backend import yandex_ndd
        try:
            info = yandex_ndd.get_order_status(track)
            print(json.dumps(info, ensure_ascii=False, indent=2, default=str))
            # more full info
            sc, sd = yandex_ndd._get("/request/info", {"request_id": track})
            print("HTTP", sc)
            # trim large
            print(json.dumps(sd, ensure_ascii=False, indent=2, default=str)[:4000])
        except Exception as e:
            print("YD error", e)
finally:
    db.close()
