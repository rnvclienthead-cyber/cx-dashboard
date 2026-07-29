#!/usr/bin/env python3
"""Fetch specific claim from WB and find Anastasia chat."""
from dotenv import load_dotenv
load_dotenv("/root/cx-dashboard/.env")
from sqlalchemy import text
from backend.database import SessionLocal
from backend import wb_mini_sync
import json

CLAIM_IDS = [
    "442f40d1-7a2a-4f04-b820-4d42e1e44a61",  # screenshot comment June 29
    "30a20d4f-bbfc-46b1-997d-be9569ccf565",  # status_ex=1 stuck
]

db = SessionLocal()
try:
    print("=== Anastasia chats ===")
    for r in db.execute(text("""
      SELECT chat_id, client_name, nm_id::text, order_rid,
             left(coalesce(last_message,''),100) AS msg, needs_answer
      FROM wb_chats
      WHERE client_name ILIKE '%Анастас%'
         OR client_name ILIKE '%Anastas%'
      ORDER BY last_ts DESC NULLS LAST
      LIMIT 20
    """)).mappings().all():
        print(dict(r))

    for cid in CLAIM_IDS:
        print(f"\n=== WB fetch claim {cid} ===")
        for is_arch in (True, False):
            try:
                items = wb_mini_sync._fetch_claims_page(
                    is_archive=is_arch, limit=1, offset=0, claim_id=cid
                )
            except Exception as e:
                print(f"  archive={is_arch} ERR {e}")
                continue
            print(f"  archive={is_arch} n={len(items)}")
            if items:
                it = items[0]
                interesting = {
                    k: it.get(k) for k in (
                        "id", "srid", "status", "status_ex", "status_description",
                        "wb_comment", "wbComment", "claim_type", "nm_id", "nmId",
                        "user_comment", "userComment", "dt",
                    ) if k in it or True
                }
                # dump all keys briefly
                print("  keys:", sorted(it.keys()))
                print("  status=", it.get("status"), "status_ex=", it.get("status_ex"),
                      "status_description=", it.get("status_description"),
                      "wb_comment=", (it.get("wb_comment") or it.get("wbComment") or "")[:120])
                mapped = wb_mini_sync._map_claim_status(
                    it.get("status", ""),
                    str(it.get("status_description") or it.get("status_ex") or ""),
                    str(it.get("wb_comment") or it.get("wbComment") or ""),
                )
                print("  mapped ->", mapped)
                # upsert
                ok = wb_mini_sync._upsert_claim_row(db, it, is_archive=is_arch)
                db.commit()
                print("  upserted", ok)
                break
finally:
    db.close()
