#!/usr/bin/env python3
from dotenv import load_dotenv
load_dotenv("/root/cx-dashboard/.env")
from sqlalchemy import text
from backend.database import SessionLocal

tok = "7c52e194a396e46550490a35488ec468"
cid = "1:59515e7e-4bc1-2e40-5b6c-c4db49baf010"
db = SessionLocal()
try:
    rows = db.execute(text("""
      SELECT token, chat_id, created_at, created_by, expires_at
      FROM reshipment_chat_tokens
      WHERE token=:t OR chat_id=:c
      ORDER BY created_at DESC LIMIT 10
    """), {"t": tok, "c": cid}).mappings().fetchall()
    print("TOKENS")
    for r in rows:
        print(dict(r))

    dels = db.execute(text("""
      SELECT chat_id, client_name, to_timestamp(last_ts/1000.0) AS at,
             left(last_message, 140) AS lm
      FROM wb_chats
      WHERE last_message ILIKE '%удалили это сообщение%'
      ORDER BY last_ts DESC LIMIT 20
    """)).mappings().fetchall()
    print("DELETIONS", len(dels))
    for r in dels:
        print(dict(r))

    # messages with form link that have a following delete notice in same chat within 60s
    linked = db.execute(text("""
      SELECT count(*) FROM wb_chat_messages
      WHERE sender='seller' AND text ILIKE '%box.vidovito.com%'
        AND add_ts > extract(epoch from now() - interval '30 days') * 1000
    """)).scalar()
    print("form_links_30d", linked)

    # playbook body for soglasie
    pb = db.execute(text("""
      SELECT theme_key, branch_key, left(body, 400) FROM cx_chat_variants
      WHERE theme_key='soglasie' ORDER BY sort_order
    """)).fetchall()
    print("PLAYBOOK soglasie")
    for r in pb:
        print(r)
finally:
    db.close()
