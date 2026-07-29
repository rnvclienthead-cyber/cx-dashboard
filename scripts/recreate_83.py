#!/usr/bin/env python3
"""Разовая повторная отправка заявки #83."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from backend.database import SessionLocal
from backend.routers.reshipment import recreate_shipment, RecreateShipmentRequest

db = SessionLocal()
try:
    result = recreate_shipment(
        83,
        RecreateShipmentRequest(processed_by="mursalimov@vidovito.team", notify_client=True),
        db,
        current_user={"username": "mursalimov@vidovito.team", "role": "admin"},
    )
    print(result)
finally:
    db.close()
