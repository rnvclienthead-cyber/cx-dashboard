#!/usr/bin/env python3
from sqlalchemy import text
from backend.database import SessionLocal

db = SessionLocal()
try:
    print("=== claims by nm_id 425421147 ===")
    rows = db.execute(text("""
      SELECT claim_id, srid, nm_id, supplier_article, status, status_ex,
             claim_type, is_archive, claim_date::text, last_sync::text,
             left(coalesce(user_comment,''), 160) AS comment
      FROM wb_claims
      WHERE nm_id::text = '425421147'
      ORDER BY claim_date DESC NULLS LAST
      LIMIT 20
    """)).mappings().all()
    for r in rows:
        print(dict(r))

    print("\n=== chats for nm ===")
    for r in db.execute(text("""
      SELECT chat_id, client_name, nm_id::text AS nm,
             left(coalesce(last_message,''),80) AS last_message,
             needs_answer, order_rid
      FROM wb_chats WHERE nm_id::text = '425421147'
      LIMIT 10
    """)).mappings().all():
        print(dict(r))

    print("\n=== status distribution ===")
    for r in db.execute(text(
        "SELECT status, count(*) FROM wb_claims GROUP BY 1 ORDER BY 2 DESC"
    )).fetchall():
        print(r)

    print("\n=== На рассмотрении by archive ===")
    for r in db.execute(text("""
      SELECT is_archive, count(*) FROM wb_claims
      WHERE status IN ('На рассмотрении','0','0.0')
      GROUP BY 1
    """)).fetchall():
        print(r)

    print("\n=== last_sync age for На рассмотрении ===")
    for r in db.execute(text("""
      SELECT
        count(*) FILTER (WHERE last_sync IS NULL) AS never,
        count(*) FILTER (WHERE last_sync < now() - interval '1 day') AS gt1d,
        count(*) FILTER (WHERE last_sync < now() - interval '7 days') AS gt7d,
        count(*) AS total
      FROM wb_claims
      WHERE status IN ('На рассмотрении','0','0.0')
    """)).mappings().all():
        print(dict(r))
finally:
    db.close()
