#!/usr/bin/env python3
import json
from sqlalchemy import text
from backend.database import engine

UUID = "e365c037-4ab8-a045-42bb-77ceb4ca7f04"

with engine.connect() as c:
    cid = c.execute(text("SELECT chat_id FROM wb_chats WHERE chat_id LIKE :p"), {"p": f"%{UUID}%"}).scalar()
    print("cid", cid)

    rows = c.execute(text("""
        SELECT sender, add_ts, left(coalesce(text, ''), 80) AS t,
               attachments
        FROM wb_chat_messages WHERE chat_id = :cid ORDER BY add_ts
    """), {"cid": cid}).mappings().fetchall()
    print("msgs", len(rows))
    for r in rows:
        att = r["attachments"]
        print("---", r["sender"], r["add_ts"], r["t"])
        if att is None:
            print("  attachments: null")
        elif isinstance(att, dict):
            print("  keys:", list(att.keys()))
            print("  json:", json.dumps(att, ensure_ascii=False)[:800])
        else:
            print("  type:", type(att), str(att)[:300])

    # pending table?
    try:
        pcols = [r[0] for r in c.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='wb_chat_pending' ORDER BY 1
        """)).fetchall()]
        print("\nwb_chat_pending cols", pcols)
        pend = c.execute(text("SELECT * FROM wb_chat_pending WHERE chat_id=:cid"), {"cid": cid}).mappings().first()
        print("pending", dict(pend) if pend else None)
    except Exception as e:
        print("pending err", e)

    print("\ncoverage all chats:")
    print(dict(c.execute(text("""
        SELECT
          count(*) AS chats,
          count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM wb_chat_messages m
            WHERE m.chat_id = c.chat_id
              AND m.attachments->'goodCard'->>'rid' IS NOT NULL
              AND m.attachments->'goodCard'->>'rid' <> ''
          )) AS with_rid,
          count(*) FILTER (
            WHERE c.needs_answer AND c.last_sender = 'client'
              AND COALESCE(c.is_closing,false) = false
              AND NOT EXISTS (
                SELECT 1 FROM cx_dismissed_items d
                WHERE d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
              )
              AND NOT EXISTS (
                SELECT 1 FROM wb_chat_messages m
                WHERE m.chat_id = c.chat_id
                  AND m.attachments->'goodCard'->>'rid' IS NOT NULL
                  AND m.attachments->'goodCard'->>'rid' <> ''
              )
          ) AS waiting_no_rid
        FROM wb_chats c
    """)).mappings().first()))

    # alternate attachment paths for rid
    print("\nother rid-like paths in waiting chats sample:")
    samples = c.execute(text("""
        SELECT m.chat_id, jsonb_object_keys(m.attachments) AS k
        FROM wb_chat_messages m
        JOIN wb_chats c ON c.chat_id = m.chat_id
        WHERE m.attachments IS NOT NULL
          AND c.needs_answer AND c.last_sender='client'
        LIMIT 1
    """)).fetchall()
    # better: count how often goodCard exists without rid
    print(dict(c.execute(text("""
        SELECT
          count(*) FILTER (WHERE attachments ? 'goodCard') AS has_goodcard,
          count(*) FILTER (WHERE attachments->'goodCard'->>'rid' IS NOT NULL
                            AND attachments->'goodCard'->>'rid' <> '') AS has_rid,
          count(*) FILTER (WHERE attachments ? 'goodCard'
                            AND (attachments->'goodCard'->>'rid' IS NULL
                                 OR attachments->'goodCard'->>'rid' = '')) AS goodcard_no_rid
        FROM wb_chat_messages
        WHERE attachments IS NOT NULL
    """)).mappings().first()))
