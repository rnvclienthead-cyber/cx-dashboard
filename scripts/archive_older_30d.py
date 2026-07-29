#!/usr/bin/env python3
"""Архивировать все открытые чаты старше 30 дней (по last_ts)."""
from sqlalchemy import text
from backend.database import engine

NOTE = "Массовое закрытие: старше 30 дней"
USER = "batch_archive_older_30d"

with engine.begin() as c:
    stats = c.execute(text("""
        WITH open_chats AS (
          SELECT c.chat_id, c.last_ts, c.needs_answer, c.last_sender
          FROM wb_chats c
          LEFT JOIN cx_dismissed_items d
            ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
          WHERE d.item_id IS NULL
            AND COALESCE(c.is_closing, false) = false
        )
        SELECT
          count(*) AS open_total,
          count(*) FILTER (
            WHERE last_ts < EXTRACT(EPOCH FROM now() - interval '30 days') * 1000
          ) AS older_30d,
          count(*) FILTER (
            WHERE last_ts < EXTRACT(EPOCH FROM now() - interval '30 days') * 1000
              AND needs_answer AND last_sender = 'client'
          ) AS older_30d_waiting,
          count(*) FILTER (
            WHERE last_ts >= EXTRACT(EPOCH FROM now() - interval '30 days') * 1000
          ) AS newer_30d
        FROM open_chats
    """)).mappings().first()
    print("before:", dict(stats))

    targets = c.execute(text("""
        SELECT c.chat_id
        FROM wb_chats c
        LEFT JOIN cx_dismissed_items d
          ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
        WHERE d.item_id IS NULL
          AND COALESCE(c.is_closing, false) = false
          AND c.last_ts < EXTRACT(EPOCH FROM now() - interval '30 days') * 1000
    """)).fetchall()
    ids = [r[0] for r in targets]
    print("to_archive:", len(ids))

    closed = 0
    for cid in ids:
        res = c.execute(text("""
            INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
            VALUES ('chat', :id, 'wb', :u, :n)
            ON CONFLICT (item_type, item_id, mp) DO NOTHING
            RETURNING item_id
        """), {"id": cid, "u": USER, "n": NOTE})
        if res.fetchone():
            closed += 1

    if ids:
        upd = c.execute(text("""
            UPDATE wb_chats
            SET needs_answer = false, updated_at = now()
            WHERE chat_id = ANY(:ids)
        """), {"ids": ids})
        print("needs_answer_cleared:", upd.rowcount)
    print("archived_new:", closed)

    after = c.execute(text("""
        WITH open_chats AS (
          SELECT c.chat_id, c.last_ts, c.needs_answer, c.last_sender
          FROM wb_chats c
          LEFT JOIN cx_dismissed_items d
            ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
          WHERE d.item_id IS NULL
            AND COALESCE(c.is_closing, false) = false
        )
        SELECT
          count(*) AS open_total,
          count(*) FILTER (
            WHERE needs_answer AND last_sender = 'client'
          ) AS waiting,
          count(*) FILTER (
            WHERE last_ts < EXTRACT(EPOCH FROM now() - interval '30 days') * 1000
          ) AS still_older_30d
        FROM open_chats
    """)).mappings().first()
    print("after:", dict(after))
