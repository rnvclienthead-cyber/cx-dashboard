#!/usr/bin/env python3
"""Probe claim + live WB actions for a chat."""
import os
import sys
import requests
from sqlalchemy import text
from dotenv import load_dotenv

load_dotenv("/root/cx-dashboard/.env")
load_dotenv()

sys.path.insert(0, "/root/cx-dashboard")
from backend.database import engine  # noqa: E402

CHAT = sys.argv[1] if len(sys.argv) > 1 else "6494b499-6767-f2c8-b600-0efa7983f40a"
KEY = os.getenv("WB_API_KEY") or os.getenv("WB_RETURNS_TOKEN") or ""
BASE = "https://returns-api.wildberries.ru"
H = {"Authorization": KEY}


def fetch_claim(cid: str):
    for arch in ("false", "true"):
        rr = requests.get(
            f"{BASE}/api/v1/claims",
            params={"id": cid, "is_archive": arch, "limit": 1},
            headers=H,
            timeout=40,
        )
        print(f"WB GET id={cid} archive={arch} ->", rr.status_code)
        data = rr.json() if rr.ok else {"err": rr.text[:300]}
        cl = (data.get("claims") or []) if isinstance(data, dict) else []
        if cl:
            item = cl[0]
            print("keys", sorted(item.keys()))
            print("status", item.get("status"), "status_ex", item.get("status_ex"))
            print("actions", item.get("actions"))
            print("wb_comment", (item.get("wb_comment") or "")[:200])
            print("user_comment", (item.get("user_comment") or "")[:200])
            return item
        print("no claims", str(data)[:400])
    return None


with engine.connect() as c:
    chats = c.execute(
        text(
            """
      SELECT chat_id, client_name, nm_id, order_rid, good_name, needs_answer,
             left(coalesce(last_message, ''), 120) lm
      FROM wb_chats WHERE chat_id LIKE :p
    """
        ),
        {"p": f"%{CHAT}%"},
    ).mappings().all()
    print("=== chats ===")
    for r in chats:
        print(dict(r))

    rid = None
    if chats:
        rid = chats[0].get("order_rid")
        m = c.execute(
            text(
                """
          SELECT attachments->'goodCard'->>'rid' AS rid
          FROM wb_chat_messages
          WHERE chat_id LIKE :p AND attachments->'goodCard'->>'rid' IS NOT NULL
          ORDER BY ts DESC NULLS LAST LIMIT 5
        """
            ),
            {"p": f"%{CHAT}%"},
        ).mappings().all()
        print("=== msg rids ===", [dict(x) for x in m])
        if m and m[0].get("rid"):
            rid = m[0]["rid"] or rid

    print("rid=", rid)
    claims = []
    if rid:
        claims = c.execute(
            text(
                """
          SELECT claim_id::text, srid, status, status_ex, claim_type, user_comment,
                 is_archive, claim_date, nm_id, supplier_article
          FROM wb_claims WHERE srid = :r OR LOWER(srid)=LOWER(:r)
        """
            ),
            {"r": rid},
        ).mappings().all()
    if not claims and chats and chats[0].get("nm_id"):
        claims = c.execute(
            text(
                """
          SELECT claim_id::text, srid, status, status_ex, claim_type, user_comment,
                 is_archive, claim_date, nm_id, supplier_article
          FROM wb_claims WHERE nm_id::text = :nm
          ORDER BY claim_date DESC NULLS LAST LIMIT 5
        """
            ),
            {"nm": str(chats[0]["nm_id"])},
        ).mappings().all()
    print("=== claims ===")
    for r in claims:
        print(dict(r))

    if claims:
        fetch_claim(claims[0]["claim_id"])
    else:
        rr = requests.get(
            f"{BASE}/api/v1/claims",
            params={"is_archive": "false", "limit": 8},
            headers=H,
            timeout=40,
        )
        print("sample active", rr.status_code)
        data = rr.json() if rr.ok else {}
        for item in (data.get("claims") or [])[:8]:
            print(
                {
                    "id": item.get("id"),
                    "status": item.get("status"),
                    "status_ex": item.get("status_ex"),
                    "actions": item.get("actions"),
                    "user_comment": (item.get("user_comment") or "")[:80],
                }
            )
