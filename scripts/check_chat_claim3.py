#!/usr/bin/env python3
from sqlalchemy import text
from backend.database import engine

UUID = "e365c037-4ab8-a045-42bb-77ceb4ca7f04"
NM = "425375043"

with engine.connect() as c:
    chat = c.execute(text("""
        SELECT chat_id, client_name, nm_id::text AS nm, needs_answer, left(last_message, 120) AS lm
        FROM wb_chats WHERE chat_id LIKE :p
    """), {"p": f"%{UUID}%"}).mappings().first()
    print("chat", dict(chat))

    rows = c.execute(text("""
        SELECT claim_id, status, status_ex, nm_id, srid, claim_date, created_dt,
               left(coalesce(user_comment, ''), 120) AS uc
        FROM wb_claims
        WHERE nm_id = :nm
        ORDER BY coalesce(claim_date, created_dt) DESC NULLS LAST
        LIMIT 25
    """), {"nm": NM}).mappings().fetchall()
    print("claims on nm", len(rows))
    for r in rows:
        print(dict(r))
    print("approved", sum(1 for r in rows if r["status"] == "Одобрено"))

    stats = c.execute(text("""
        WITH waiting AS (
          SELECT c.chat_id, c.nm_id::text AS nm, c.client_name
          FROM wb_chats c
          LEFT JOIN cx_dismissed_items d
            ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
          WHERE d.item_id IS NULL
            AND COALESCE(c.is_closing, false) = false
            AND c.needs_answer = true
            AND c.last_sender = 'client'
            AND c.last_ts > EXTRACT(EPOCH FROM now() - interval '30 days') * 1000
            AND c.nm_id IS NOT NULL
        )
        SELECT
          count(*) AS waiting,
          count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM wb_chat_messages m
            WHERE m.chat_id = w.chat_id
              AND m.attachments->'goodCard'->>'rid' IS NOT NULL
          )) AS with_rid,
          count(*) FILTER (WHERE NOT EXISTS (
            SELECT 1 FROM wb_chat_messages m
            WHERE m.chat_id = w.chat_id
              AND m.attachments->'goodCard'->>'rid' IS NOT NULL
          ) AND EXISTS (
            SELECT 1 FROM wb_claims cl
            WHERE cl.nm_id = w.nm AND cl.status = 'Одобрено'
          )) AS no_rid_but_approved_on_nm,
          count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM wb_claims cl
            WHERE cl.srid = (
              SELECT m.attachments->'goodCard'->>'rid'
              FROM wb_chat_messages m
              WHERE m.chat_id = w.chat_id
                AND m.attachments->'goodCard'->>'rid' IS NOT NULL
              ORDER BY m.add_ts DESC NULLS LAST LIMIT 1
            ) AND cl.status = 'Одобрено'
          )) AS rid_match_approved_still
        FROM waiting w
    """)).mappings().first()
    print("stats30d", dict(stats))

    samples = c.execute(text("""
        SELECT c.chat_id, c.client_name, c.nm_id::text AS nm, left(c.last_message, 90) AS lm
        FROM wb_chats c
        LEFT JOIN cx_dismissed_items d
          ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
        WHERE d.item_id IS NULL
          AND COALESCE(c.is_closing, false) = false
          AND c.needs_answer = true
          AND c.last_sender = 'client'
          AND c.last_ts > EXTRACT(EPOCH FROM now() - interval '30 days') * 1000
          AND c.nm_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM wb_chat_messages m
            WHERE m.chat_id = c.chat_id
              AND m.attachments->'goodCard'->>'rid' IS NOT NULL
          )
          AND EXISTS (
            SELECT 1 FROM wb_claims cl
            WHERE cl.nm_id = c.nm_id::text AND cl.status = 'Одобрено'
          )
        ORDER BY c.last_ts DESC
        LIMIT 15
    """)).mappings().fetchall()
    print("samples no-rid + approved nm:")
    for r in samples:
        print(dict(r))
