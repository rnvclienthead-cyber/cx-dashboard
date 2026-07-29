#!/usr/bin/env python3
"""1) Backfill order_rid из WB API  2) Архивировать «ждут ответа» с одобренным возвратом по srid."""
from sqlalchemy import text
from backend.database import SessionLocal, engine
from backend import wb_chat

NOTE = "Массовое закрытие: возврат Одобрено (srid backfill)"
USER = "batch_approved_return_rid_backfill"
UUID = "e365c037-4ab8-a045-42bb-77ceb4ca7f04"

# Колонку добавляем ДО любой длинной сессии — иначе ALTER ждёт чужие транзакции.
wb_chat.ensure_chat_order_rid_column(engine)

with engine.connect() as c:
    before = c.execute(text("""
        SELECT count(*) FROM wb_chats c
        LEFT JOIN cx_dismissed_items d
          ON d.item_id=c.chat_id AND d.mp='wb' AND d.item_type='chat'
        WHERE d.item_id IS NULL AND COALESCE(c.is_closing,false)=false
          AND c.needs_answer AND c.last_sender='client'
    """)).scalar()
    print("waiting before:", before, flush=True)

db = SessionLocal()
try:
    result = wb_chat.backfill_order_rids_from_wb(db)
    print("backfill:", result, flush=True)
finally:
    db.close()

with engine.connect() as c:
    row = c.execute(text("""
        SELECT chat_id, order_rid,
               reply_sign IS NOT NULL AND reply_sign <> '' AS has_rs,
               (SELECT attachments->'goodCard'->>'rid'
                FROM wb_chat_messages m WHERE m.chat_id = c.chat_id
                ORDER BY add_ts DESC NULLS LAST LIMIT 1) AS msg_rid
        FROM wb_chats c WHERE chat_id LIKE :p
    """), {"p": f"%{UUID}%"}).mappings().first()
    print("anna after backfill:", dict(row) if row else None)

with engine.begin() as c:
    targets = c.execute(text("""
        WITH waiting AS (
          SELECT c.chat_id,
                 COALESCE(
                   NULLIF(c.order_rid, ''),
                   (SELECT m.attachments->'goodCard'->>'rid'
                    FROM wb_chat_messages m
                    WHERE m.chat_id = c.chat_id
                      AND coalesce(m.attachments->'goodCard'->>'rid','') <> ''
                    ORDER BY m.add_ts ASC NULLS LAST LIMIT 1)
                 ) AS rid
          FROM wb_chats c
          LEFT JOIN cx_dismissed_items d
            ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
          WHERE d.item_id IS NULL
            AND COALESCE(c.is_closing, false) = false
            AND c.needs_answer = true
            AND c.last_sender = 'client'
        )
        SELECT w.chat_id, w.rid
        FROM waiting w
        JOIN wb_claims cl ON cl.srid = w.rid
        WHERE w.rid IS NOT NULL AND w.rid <> '' AND cl.status = 'Одобрено'
    """)).mappings().fetchall()
    print("to close:", len(targets))

    closed = 0
    for t in targets:
        c.execute(text("""
            INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
            VALUES ('chat', :cid, 'wb', :u, :n)
            ON CONFLICT (item_type, item_id, mp) DO NOTHING
        """), {"cid": t["chat_id"], "u": USER, "n": NOTE})
        c.execute(text("""
            UPDATE wb_chats SET needs_answer = false, updated_at = now()
            WHERE chat_id = :cid
        """), {"cid": t["chat_id"]})
        closed += 1
    print("closed:", closed)

    stats = c.execute(text("""
        SELECT
          count(*) AS waiting,
          count(*) FILTER (WHERE COALESCE(NULLIF(order_rid,''), '') <> '') AS with_order_rid,
          count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM wb_claims cl
            WHERE cl.srid = COALESCE(NULLIF(c.order_rid,''),
              (SELECT m.attachments->'goodCard'->>'rid' FROM wb_chat_messages m
               WHERE m.chat_id=c.chat_id AND coalesce(m.attachments->'goodCard'->>'rid','')<>''
               ORDER BY m.add_ts ASC NULLS LAST LIMIT 1)
            ) AND cl.status='Одобрено'
          )) AS still_approved_match
        FROM wb_chats c
        LEFT JOIN cx_dismissed_items d
          ON d.item_id=c.chat_id AND d.mp='wb' AND d.item_type='chat'
        WHERE d.item_id IS NULL AND COALESCE(c.is_closing,false)=false
          AND c.needs_answer AND c.last_sender='client'
    """)).mappings().first()
    print("waiting after:", dict(stats))

    anna = c.execute(text("""
        SELECT chat_id, needs_answer, is_closing, order_rid,
               EXISTS (SELECT 1 FROM cx_dismissed_items d
                       WHERE d.item_id=c.chat_id AND d.mp='wb' AND d.item_type='chat') AS dismissed
        FROM wb_chats c WHERE chat_id LIKE :p
    """), {"p": f"%{UUID}%"}).mappings().first()
    print("anna final:", dict(anna) if anna else None)
