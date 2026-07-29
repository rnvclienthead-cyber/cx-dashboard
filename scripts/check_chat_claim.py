#!/usr/bin/env python3
"""Разбор чата e365c037… и хвоста «ждут ответа» + одобренный возврат."""
from sqlalchemy import text
from backend.database import engine

UUID = "e365c037-4ab8-a045-42bb-77ceb4ca7f04"

with engine.connect() as c:
    cols = [r[0] for r in c.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='wb_chats' ORDER BY 1
    """)).fetchall()]
    print("wb_chats cols:", cols)

    chat = c.execute(text("""
        SELECT * FROM wb_chats
        WHERE chat_id LIKE :p
    """), {"p": f"%{UUID}%"}).mappings().first()
    print("\n=== chat ===")
    if not chat:
        print("NOT FOUND")
    else:
        for k, v in dict(chat).items():
            if k in ("reply_sign",):
                continue
            print(f"  {k}: {v}")

    cid = chat["chat_id"] if chat else f"1:{UUID}"

    # dismissed?
    d = c.execute(text("""
        SELECT * FROM cx_dismissed_items
        WHERE item_id = :cid AND mp='wb' AND item_type='chat'
    """), {"cid": cid}).mappings().fetchall()
    print("\ndismissed:", [dict(x) for x in d])

    # messages with rid
    print("\n=== last messages ===")
    for r in c.execute(text("""
        SELECT sender, left(text,100) t, add_ts,
               attachments->'goodCard'->>'rid' AS rid,
               attachments->'goodCard'->>'nmID' AS nm
        FROM wb_chat_messages
        WHERE chat_id = :cid
        ORDER BY add_ts DESC NULLS LAST
        LIMIT 12
    """), {"cid": cid}).mappings():
        print(dict(r))

    nm = chat.get("nm_id") if chat else None
    rids = [r[0] for r in c.execute(text("""
        SELECT DISTINCT attachments->'goodCard'->>'rid'
        FROM wb_chat_messages
        WHERE chat_id = :cid
          AND attachments->'goodCard'->>'rid' IS NOT NULL
    """), {"cid": cid}).fetchall()]
    print("\nrids from chat:", rids, "nm_id:", nm)

    # claims by rid / nm
    print("\n=== claims by rid ===")
    if rids:
        for r in c.execute(text("""
            SELECT claim_id, status, status_ex, nm_id, srid, dt, user_comment
            FROM wb_claims WHERE srid = ANY(:rids) OR claim_id::text = ANY(:rids)
            ORDER BY dt DESC NULLS LAST LIMIT 20
        """), {"rids": rids}).mappings():
            print(dict(r))

    print("\n=== claims by nm (recent approved-ish) ===")
    if nm:
        for r in c.execute(text("""
            SELECT claim_id, status, status_ex, nm_id, srid, dt,
                   left(coalesce(user_comment,''),80) uc
            FROM wb_claims
            WHERE nm_id = :nm
            ORDER BY dt DESC NULLS LAST
            LIMIT 15
        """), {"nm": int(nm)}).mappings():
            print(dict(r))

    # How many waiting still have approved claim by SAME logic as yesterday (rid)
    q = text("""
    WITH waiting AS (
      SELECT c.chat_id, c.client_name, c.nm_id, c.needs_answer, c.last_sender, c.last_ts
      FROM wb_chats c
      LEFT JOIN cx_dismissed_items d
        ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
      WHERE d.item_id IS NULL
        AND COALESCE(c.is_closing, false) = false
        AND c.needs_answer = true
        AND c.last_sender = 'client'
        AND c.last_ts > EXTRACT(EPOCH FROM now() - interval '30 days') * 1000
    ),
    chat_rid AS (
      SELECT w.chat_id, w.client_name, w.nm_id,
             (SELECT m.attachments->'goodCard'->>'rid'
              FROM wb_chat_messages m
              WHERE m.chat_id = w.chat_id
                AND m.attachments->'goodCard'->>'rid' IS NOT NULL
              ORDER BY m.add_ts DESC NULLS LAST LIMIT 1) AS rid
      FROM waiting w
    )
    SELECT count(*) FILTER (WHERE rid IS NOT NULL) AS with_rid,
           count(*) FILTER (WHERE rid IS NULL) AS no_rid,
           count(*) AS waiting_30d
    FROM chat_rid
    """)
    print("\n=== waiting 30d rid coverage ===")
    print(dict(c.execute(q).mappings().first()))

    # approved via rid still waiting
    still = c.execute(text("""
    WITH waiting AS (
      SELECT c.chat_id, c.client_name, c.nm_id
      FROM wb_chats c
      LEFT JOIN cx_dismissed_items d
        ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
      WHERE d.item_id IS NULL
        AND COALESCE(c.is_closing, false) = false
        AND c.needs_answer = true
        AND c.last_sender = 'client'
        AND c.last_ts > EXTRACT(EPOCH FROM now() - interval '30 days') * 1000
    ),
    chat_rid AS (
      SELECT w.*,
             (SELECT m.attachments->'goodCard'->>'rid'
              FROM wb_chat_messages m
              WHERE m.chat_id = w.chat_id
                AND m.attachments->'goodCard'->>'rid' IS NOT NULL
              ORDER BY m.add_ts DESC NULLS LAST LIMIT 1) AS rid
      FROM waiting w
    )
    SELECT cr.chat_id, cr.client_name, cr.nm_id, cr.rid, cl.status, cl.srid
    FROM chat_rid cr
    JOIN wb_claims cl ON cl.srid = cr.rid
    WHERE cr.rid IS NOT NULL AND cl.status = 'Одобрено'
    ORDER BY cr.chat_id
    LIMIT 30
    """)).mappings().fetchall()
    print(f"\n=== still waiting + approved by rid: {len(still)} (show up to 30) ===")
    for r in still:
        print(dict(r))

    # Is THIS chat in still?
    print("\nthis chat in rid-approved still?", any(UUID in (r["chat_id"] or "") for r in still))

    # Try link by nm_id only: waiting chats whose nm has any approved claim
    by_nm = c.execute(text("""
    WITH waiting AS (
      SELECT c.chat_id, c.client_name, c.nm_id
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
    SELECT count(DISTINCT w.chat_id)
    FROM waiting w
    WHERE EXISTS (
      SELECT 1 FROM wb_claims cl
      WHERE cl.nm_id = w.nm_id AND cl.status = 'Одобрено'
    )
    """)).scalar()
    print("waiting with ANY approved claim on same nm_id:", by_nm)

    # specifically for this chat - approved claims on nm
    if nm:
        print("\napproved claims on this nm:")
        for r in c.execute(text("""
            SELECT claim_id, status, srid, dt FROM wb_claims
            WHERE nm_id = :nm AND status = 'Одобрено'
            ORDER BY dt DESC LIMIT 10
        """), {"nm": int(nm)}).mappings():
            print(dict(r))
