#!/usr/bin/env python3
"""Разовый прогон: обновить статусы возвратов + архив чатов с одобренным возвратом."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from backend.database import SessionLocal
from backend import wb_mini_sync


def _stats(db):
    claims = db.execute(text("""
      SELECT
        count(*) FILTER (WHERE status = 'На рассмотрении') AS review,
        count(*) FILTER (WHERE status = 'Одобрено') AS ok,
        count(*) FILTER (WHERE status = 'Отказ') AS rej
      FROM wb_claims
    """)).mappings().first()
    waiting = db.execute(text("""
      SELECT count(*) FROM wb_chats c
      LEFT JOIN cx_dismissed_items d
        ON d.item_id=c.chat_id AND d.mp='wb' AND d.item_type='chat'
      WHERE d.item_id IS NULL AND COALESCE(c.is_closing,false)=false
        AND c.needs_answer AND c.last_sender='client'
    """)).scalar()
    approved_waiting = db.execute(text("""
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
      SELECT count(*) FROM waiting w
      JOIN wb_claims cl ON cl.srid = w.rid
      WHERE cl.status = 'Одобрено'
    """)).scalar()
    return dict(claims), waiting, approved_waiting


def main():
    db = SessionLocal()
    try:
        before_c, before_w, before_aw = _stats(db)
        print("BEFORE claims:", before_c, "waiting:", before_w, "approved+waiting:", before_aw, flush=True)
        n = wb_mini_sync.sync_recent_claims(db)
        print("sync_recent_claims:", n, flush=True)
        after_c, after_w, after_aw = _stats(db)
        print("AFTER  claims:", after_c, "waiting:", after_w, "approved+waiting:", after_aw, flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
