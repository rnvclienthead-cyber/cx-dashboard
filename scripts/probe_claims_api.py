#!/usr/bin/env python3
"""Probe WB claims API: how to fetch by id, what status codes look like."""
import os, json, requests
from dotenv import load_dotenv
load_dotenv("/root/cx-dashboard/.env")

KEY = os.getenv("WB_API_KEY") or os.getenv("WB_RETURNS_TOKEN") or ""
BASE = "https://returns-api.wildberries.ru"
H = {"Authorization": KEY}

# 1) sample archive page
r = requests.get(f"{BASE}/api/v1/claims", params={"is_archive": "true", "limit": 5, "offset": 0},
                 headers=H, timeout=40)
print("archive page", r.status_code)
data = r.json() if r.ok else {}
claims = data.get("claims") or data.get("data") or data
if isinstance(claims, dict):
    print("top keys", claims.keys())
    claims = claims.get("claims") or claims.get("data") or []
print("n=", len(claims) if isinstance(claims, list) else type(claims))
if isinstance(claims, list) and claims:
    print("sample keys", sorted(claims[0].keys()))
    for c in claims[:5]:
        print({
            "id": c.get("id"),
            "status": c.get("status"),
            "status_ex": c.get("status_ex"),
            "nm_id": c.get("nm_id") or c.get("nmId"),
            "srid": (c.get("srid") or "")[:40],
            "wb_comment": (c.get("wb_comment") or c.get("wbComment") or "")[:80],
        })

# 2) try fetch by id with different param names
CID = "442f40d1-7a2a-4f04-b820-4d42e1e44a61"
for params in (
    {"id": CID, "is_archive": "true", "limit": 1},
    {"id": CID, "is_archive": "false", "limit": 1},
    {"claim_id": CID, "is_archive": "true", "limit": 1},
    {"claimId": CID, "is_archive": "true", "limit": 1},
):
    rr = requests.get(f"{BASE}/api/v1/claims", params=params, headers=H, timeout=40)
    body = rr.json() if rr.ok else {"err": rr.text[:200]}
    cl = body.get("claims") if isinstance(body, dict) else None
    if cl is None and isinstance(body, dict):
        cl = body.get("data")
    n = len(cl) if isinstance(cl, list) else body
    print("params", params, "->", rr.status_code, "n=", n if not isinstance(n, list) else len(n))

# 3) stuck claims with status_ex numeric
from sqlalchemy import text
from backend.database import SessionLocal
db = SessionLocal()
print("\n=== stuck На рассмотрении with numeric status_ex ===")
for r in db.execute(text("""
  SELECT status, status_ex, count(*)
  FROM wb_claims
  WHERE status IN ('На рассмотрении','0','0.0')
  GROUP BY 1,2
  ORDER BY 3 DESC
  LIMIT 20
""")).fetchall():
    print(r)

# link claim to chat Anastasia nm 425421147
print("\n=== chat by srid of June29 claim ===")
SRID = "eAx.i4aaeffb74713c54b057648ea68b8be2e.0.0"
for r in db.execute(text("""
  SELECT chat_id, client_name, nm_id::text, order_rid, needs_answer
  FROM wb_chats
  WHERE order_rid = :r
     OR chat_id IN (
       SELECT chat_id FROM wb_chat_messages
       WHERE attachments->'goodCard'->>'rid' = :r
       LIMIT 5
     )
"""), {"r": SRID}).mappings().all():
    print(dict(r))
db.close()
