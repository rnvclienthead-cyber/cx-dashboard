#!/usr/bin/env python3
from sqlalchemy import text
from backend.database import engine

UUID = "e365c037-4ab8-a045-42bb-77ceb4ca7f04"
NM = 425375043

with engine.connect() as c:
    cols = [r[0] for r in c.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'wb_claims' ORDER BY 1
    """)).fetchall()]
    print("wb_claims cols:", cols)

    chat = c.execute(text("""
        SELECT chat_id, client_name, nm_id, needs_answer, last_sender, last_message, last_ts
        FROM wb_chats WHERE chat_id LIKE :p
    """), {"p": f"%{UUID}%"}).mappings().first()
    print("chat:", dict(chat) if chat else None)
    cid = chat["chat_id"]

    rids = [r[0] for r in c.execute(text("""
        SELECT DISTINCT attachments->'goodCard'->>'rid'
        FROM wb_chat_messages
        WHERE chat_id = :cid AND attachments->'goodCard'->>'rid' IS NOT NULL
    """), {"cid": cid}).fetchall()]
    print("rids in messages:", rids)

    # pick date-like columns
    date_cols = [x for x in cols if any(k in x for k in ("date", "dt", "creat", "updat", "time"))]
    print("date-ish cols:", date_cols)

    order_by = "updated_at" if "updated_at" in cols else ("created_date" if "created_date" in cols else cols[0])
    sel = [x for x in ("claim_id", "id", "status", "status_ex", "nm_id", "srid", "order_id",
                       "user_name", "client_name", "wb_user_id", "created_at", "updated_at",
                       "created_date", "dt", "supplier_article") if x in cols]
    sql = f"SELECT {', '.join(sel)} FROM wb_claims WHERE nm_id = :nm ORDER BY {order_by} DESC NULLS LAST LIMIT 25"
    print("sql:", sql)
    rows = c.execute(text(sql), {"nm": NM}).mappings().fetchall()
    print(f"claims on nm={NM}: {len(rows)}")
    for r in rows:
        print(dict(r))

    approved = [dict(r) for r in rows if (r.get("status") or "") == "Одобрено"]
    print("approved on this nm:", len(approved))

    # Count waiting without rid but with approved claim on same nm
    n = c.execute(text("""
        WITH waiting AS (
          SELECT c.chat_id, c.nm_id, c.client_name
          FROM wb_chats c
          LEFT JOIN cx_dismissed_items d
            ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
          WHERE d.item_id IS NULL
            AND COALESCE(c.is_closing, false) = false
            AND c.needs_answer = true
            AND c.last_sender = 'client'
            AND c.last_ts > EXTRACT(EPOCH FROM now() - interval '30 days') * 1000
            AND c.nm_id IS NOT NULL
        ),
        with_rid AS (
          SELECT w.chat_id,
                 EXISTS (
                   SELECT 1 FROM wb_chat_messages m
                   WHERE m.chat_id = w.chat_id
                     AND m.attachments->'goodCard'->>'rid' IS NOT NULL
                 ) AS has_rid
          FROM waiting w
        )
        SELECT
          count(*) FILTER (
            WHERE NOT wr.has_rid AND EXISTS (
              SELECT 1 FROM wb_claims cl
              WHERE cl.nm_id = w.nm_id AND cl.status = 'Одобрено'
            )
          ) AS no_rid_but_approved_nm,
          count(*) FILTER (
            WHERE wr.has_rid AND EXISTS (
              SELECT 1 FROM wb_claims cl
              WHERE cl.srid = (
                SELECT m.attachments->'goodCard'->>'rid'
                FROM wb_chat_messages m
                WHERE m.chat_id = w.chat_id
                  AND m.attachments->'goodCard'->>'rid' IS NOT NULL
                ORDER BY m.add_ts DESC NULLS LAST LIMIT 1
              ) AND cl.status = 'Одобрено'
            )
          ) AS rid_approved_still,
          count(*) AS waiting_total
        FROM waiting w
        JOIN with_rid wr ON wr.chat_id = w.chat_id
    """)).mappings().first()
    print("\nwaiting 30d stats:", dict(n))

    # sample of no-rid but approved nm
    samples = c.execute(text("""
        WITH waiting AS (
          SELECT c.chat_id, c.nm_id, c.client_name, left(c.last_message,80) lm
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
              WHERE cl.nm_id = c.nm_id AND cl.status = 'Одобрено'
            )
        )
        SELECT * FROM waiting ORDER BY chat_id LIMIT 20
    """)).mappings().fetchall()
    print(f"\nsample no-rid + approved-on-nm ({len(samples)} shown):")
    for r in samples:
        print(dict(r))
