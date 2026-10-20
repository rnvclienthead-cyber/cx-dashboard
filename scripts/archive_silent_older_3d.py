#!/usr/bin/env python3
"""Разово убрать в архив чаты без переписки больше 3 дней (все площадки).

Автологику закрытия не меняет. Критерий: последнее сообщение/активность
старше 3 дней. Закрытие = INSERT в cx_dismissed_items + needs_answer=false.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from backend.database import engine

NOTE = "Массовое закрытие: нет переписки >3 дней"
USER = "batch_archive_silent_3d"

WB_OPEN = """
  SELECT c.chat_id, c.needs_answer,
         to_timestamp(COALESCE(c.last_ts, 0) / 1000.0) AS last_act
  FROM wb_chats c
  LEFT JOIN cx_dismissed_items d
    ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
  WHERE d.item_id IS NULL
"""

OZON_OPEN = """
  SELECT c.chat_id, c.needs_answer,
    COALESCE(
      (SELECT MAX(m.created_at) FROM ozon_chat_messages m WHERE m.chat_id = c.chat_id),
      c.waiting_since, c.created_at, c.updated_at
    ) AS last_act
  FROM ozon_chats c
  LEFT JOIN cx_dismissed_items d
    ON d.item_id = c.chat_id AND d.mp = 'ozon' AND d.item_type = 'chat'
  WHERE d.item_id IS NULL
"""

YM_OPEN = """
  SELECT c.chat_id, c.needs_answer,
    COALESCE(
      (SELECT MAX(m.created_at) FROM ym_chat_messages m WHERE m.chat_id = c.chat_id),
      c.waiting_since, c.updated_at, c.created_at
    ) AS last_act
  FROM ym_chats c
  LEFT JOIN cx_dismissed_items d
    ON d.item_id = c.chat_id AND d.mp = 'ym' AND d.item_type = 'chat'
  WHERE d.item_id IS NULL
"""

CLEAR = {
    "wb": """
        UPDATE wb_chats
        SET needs_answer = false, waiting_since = NULL, updated_at = now()
        WHERE chat_id = ANY(:ids)
    """,
    "ozon": """
        UPDATE ozon_chats
        SET needs_answer = false, waiting_since = NULL, updated_at = now()
        WHERE chat_id = ANY(:ids)
    """,
    "ym": """
        UPDATE ym_chats
        SET needs_answer = false, waiting_since = NULL, updated_at = now()
        WHERE chat_id = ANY(:ids)
    """,
}


def _archive_mp(conn, *, mp: str, select_sql: str) -> dict:
    before = conn.execute(text(f"""
        WITH open_chats AS ({select_sql})
        SELECT
          count(*) AS open_total,
          count(*) FILTER (WHERE last_act IS NULL OR last_act < now() - interval '3 days') AS silent_3d,
          count(*) FILTER (
            WHERE (last_act IS NULL OR last_act < now() - interval '3 days') AND needs_answer
          ) AS silent_3d_waiting
        FROM open_chats
    """)).mappings().first()
    print(f"[{mp}] before:", dict(before))

    targets = conn.execute(text(f"""
        SELECT chat_id FROM ({select_sql}) o
        WHERE last_act IS NULL OR last_act < now() - interval '3 days'
    """)).fetchall()
    ids = [r[0] for r in targets]
    print(f"[{mp}] to_archive:", len(ids))

    closed = 0
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        res = conn.execute(text("""
            INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
            SELECT 'chat', x, :mp, :u, :n
            FROM unnest(CAST(:ids AS text[])) AS x
            ON CONFLICT (item_type, item_id, mp) DO NOTHING
            RETURNING item_id
        """), {"ids": chunk, "mp": mp, "u": USER, "n": NOTE})
        closed += len(res.fetchall())
        conn.execute(text(CLEAR[mp]), {"ids": chunk})
        conn.execute(text("""
            INSERT INTO cx_chat_bot_state (chat_id, stage, bot_disabled, updated_at)
            SELECT x, 'new', true, now()
            FROM unnest(CAST(:ids AS text[])) AS x
            ON CONFLICT (chat_id) DO UPDATE SET
                bot_disabled = true,
                updated_at = now()
        """), {"ids": chunk})

    print(f"[{mp}] archived_new:", closed)

    after = conn.execute(text(f"""
        WITH open_chats AS ({select_sql})
        SELECT
          count(*) AS open_total,
          count(*) FILTER (WHERE needs_answer) AS waiting,
          count(*) FILTER (WHERE last_act IS NULL OR last_act < now() - interval '3 days') AS still_silent_3d
        FROM open_chats
    """)).mappings().first()
    print(f"[{mp}] after:", dict(after))
    return {"mp": mp, "archived": closed, "before": dict(before), "after": dict(after)}


def main():
    with engine.begin() as conn:
        _archive_mp(conn, mp="wb", select_sql=WB_OPEN)
    with engine.begin() as conn:
        _archive_mp(conn, mp="ozon", select_sql=OZON_OPEN)
    with engine.begin() as conn:
        _archive_mp(conn, mp="ym", select_sql=YM_OPEN)


if __name__ == "__main__":
    main()
