#!/usr/bin/env python3
from datetime import datetime, timezone
from sqlalchemy import text
from backend.database import engine

TOKEN = "3faVpvRw5uNrlniy"

with engine.connect() as c:
    tok = c.execute(text(
        "SELECT * FROM reshipment_chat_tokens WHERE token = :t"
    ), {"t": TOKEN}).mappings().first()
    if not tok:
        print("NOT_FOUND")
        raise SystemExit(0)

    d = dict(tok)
    print("token:", d["token"])
    print("chat_id:", d.get("chat_id"))
    print("created_by:", d.get("created_by"))
    print("created_at:", d.get("created_at"))
    print("expires_at:", d.get("expires_at"))
    print("used_at:", d.get("used_at"))
    print("request_id:", d.get("request_id"))
    print("expired_flag:", d.get("expired"))

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    exp = d.get("expires_at")
    used = d.get("used_at")
    expired_flag = bool(d.get("expired"))
    past_expiry = bool(exp and exp < now)
    active = (not used) and (not expired_flag) and (not past_expiry)
    print("ACTIVE:", active)
    if used:
        print("inactive_reason: already_used")
    elif expired_flag:
        print("inactive_reason: expired_flag")
    elif past_expiry:
        print("inactive_reason: past_expiry")

    if d.get("chat_id"):
        chat = c.execute(text("""
            SELECT chat_id, client_name, nm_id, needs_answer, last_sender,
                   left(last_message, 140) AS lm
            FROM wb_chats WHERE chat_id = :c
        """), {"c": d["chat_id"]}).mappings().first()
        print("chat:", dict(chat) if chat else None)

    reqs = c.execute(text("""
        SELECT id, status, customer_name, created_at
        FROM reshipment_requests
        WHERE chat_token = :t OR id = :rid
    """), {"t": TOKEN, "rid": d.get("request_id") or -1}).mappings().fetchall()
    print("requests:", [dict(r) for r in reqs])
