#!/usr/bin/env python3
"""One-shot: sync yandex statuses after PREPARED map fix."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text
from backend import yandex_ndd

DB = os.getenv(
    "DATABASE_URL_LOCAL",
    "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard",
)
ORDER = ["packed", "shipped", "in_transit", "ready_for_pickup", "delivered", "returned"]


def main():
    eng = create_engine(DB)
    with eng.connect() as conn:
        rows = [dict(r) for r in conn.execute(text("""
            SELECT id, status, track_number, LEFT(customer_name, 22) AS name
            FROM reshipment_requests
            WHERE delivery_method = 'yandex'
              AND track_number IS NOT NULL AND track_number <> ''
              AND status IN ('approved', 'packed', 'shipped', 'in_transit', 'ready_for_pickup')
            ORDER BY id
        """)).mappings()]

    updated = []
    for r in rows:
        time.sleep(0.35)
        info = yandex_ndd.get_order_status(r["track_number"])
        raw = info.get("status") or ""
        new = yandex_ndd.STATUS_MAP.get(raw)
        print(f"#{r['id']} {r['name']}: db={r['status']} api={raw} ({info.get('description')}) -> {new}")
        if not new or new == r["status"]:
            continue
        ci = ORDER.index(r["status"]) if r["status"] in ORDER else -1
        ni = ORDER.index(new) if new in ORDER else -1
        if ni <= ci:
            continue
        with eng.begin() as conn:
            conn.execute(text("""
                UPDATE reshipment_requests
                SET status = :ns,
                    shipped_at = CASE
                      WHEN :ns IN ('shipped','in_transit','ready_for_pickup','delivered')
                       AND shipped_at IS NULL THEN NOW() ELSE shipped_at END,
                    yandex_sharing_url = COALESCE(NULLIF(:url, ''), yandex_sharing_url),
                    updated_at = NOW()
                WHERE id = :id AND status = :curr
            """), {
                "ns": new, "id": r["id"], "curr": r["status"],
                "url": info.get("sharing_url") or "",
            })
        updated.append((r["id"], r["status"], new, raw))
        print(f"  UPDATED {r['status']} -> {new}")

    print(f"\nUpdated: {len(updated)}")
    for u in updated:
        print(" ", u)

    with eng.connect() as conn:
        print("\nAfter:")
        for row in conn.execute(text("""
            SELECT status, COUNT(*) FROM reshipment_requests
            WHERE delivery_method='yandex'
              AND status IN ('approved','packed','shipped','in_transit','ready_for_pickup')
            GROUP BY 1 ORDER BY 2 DESC
        """)):
            print(" ", row[0], row[1])


if __name__ == "__main__":
    main()
