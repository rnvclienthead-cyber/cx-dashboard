#!/usr/bin/env python3
"""
Архивировать чаты Ozon и ЯМ старше 30 дней — та же логика, что archive_older_30d.py для WB.

Критерий возраста:
  Ozon — MAX(ozon_chat_messages.created_at) или waiting_since/created_at/updated_at
  ЯМ   — MAX(ym_chat_messages.created_at) или waiting_since/updated_at/created_at

Закрытие = INSERT в cx_dismissed_items + needs_answer=false.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from backend.database import engine

NOTE = "Массовое закрытие: старше 30 дней"
USER = "batch_archive_older_30d"


def _archive_mp(conn, *, mp: str, select_sql: str, clear_sql: str) -> dict:
    before = conn.execute(text(f"""
        WITH open_chats AS ({select_sql})
        SELECT
          count(*) AS open_total,
          count(*) FILTER (WHERE last_act < now() - interval '30 days') AS older_30d,
          count(*) FILTER (
            WHERE last_act < now() - interval '30 days' AND needs_answer
          ) AS older_30d_waiting,
          count(*) FILTER (WHERE last_act >= now() - interval '30 days') AS newer_30d
        FROM open_chats
    """)).mappings().first()
    print(f"[{mp}] before:", dict(before))

    targets = conn.execute(text(f"""
        SELECT chat_id FROM ({select_sql}) o
        WHERE last_act < now() - interval '30 days'
    """)).fetchall()
    ids = [r[0] for r in targets]
    print(f"[{mp}] to_archive:", len(ids))

    closed = 0
    for cid in ids:
        res = conn.execute(text("""
            INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
            VALUES ('chat', :id, :mp, :u, :n)
            ON CONFLICT (item_type, item_id, mp) DO NOTHING
            RETURNING item_id
        """), {"id": cid, "mp": mp, "u": USER, "n": NOTE})
        if res.fetchone():
            closed += 1

    cleared = 0
    if ids:
        # clear needs_answer пачками
        for i in range(0, len(ids), 200):
            chunk = ids[i:i + 200]
            upd = conn.execute(text(clear_sql), {"ids": chunk})
            cleared += upd.rowcount or 0

    print(f"[{mp}] archived_new:", closed, "needs_answer_cleared:", cleared)

    after = conn.execute(text(f"""
        WITH open_chats AS ({select_sql})
        SELECT
          count(*) AS open_total,
          count(*) FILTER (WHERE needs_answer) AS waiting,
          count(*) FILTER (WHERE last_act < now() - interval '30 days') AS still_older_30d
        FROM open_chats
    """)).mappings().first()
    print(f"[{mp}] after:", dict(after))
    return {"mp": mp, "archived": closed, "cleared": cleared, "before": dict(before), "after": dict(after)}


OZON_OPEN = """
  SELECT c.chat_id, c.needs_answer,
    COALESCE(
      (SELECT MAX(m.created_at) FROM ozon_chat_messages m WHERE m.chat_id = c.chat_id),
      c.waiting_since, c.created_at, c.updated_at, now()
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
      c.waiting_since, c.updated_at, c.created_at, now()
    ) AS last_act
  FROM ym_chats c
  LEFT JOIN cx_dismissed_items d
    ON d.item_id = c.chat_id AND d.mp = 'ym' AND d.item_type = 'chat'
  WHERE d.item_id IS NULL
"""

OZON_CLEAR = """
  UPDATE ozon_chats
  SET needs_answer = false, waiting_since = NULL, updated_at = now()
  WHERE chat_id = ANY(:ids)
"""

YM_CLEAR = """
  UPDATE ym_chats
  SET needs_answer = false, waiting_since = NULL, updated_at = now()
  WHERE chat_id = ANY(:ids)
"""

# Безопасное закрытие как в chat_triage: thanks / empty (только ждущие ответа)
THANKS_RE = r"(спасибо|благодар|ок|хорошо|понятно|принято|извините|поняла|понял)"


def _archive_safe_close(conn, *, mp: str, table: str) -> int:
    """Закрыть thanks/empty по last_message — как SAFE_TO_CLOSE на WB."""
    rows = conn.execute(text(f"""
        SELECT c.chat_id
        FROM {table} c
        LEFT JOIN cx_dismissed_items d
          ON d.item_id = c.chat_id AND d.mp = :mp AND d.item_type = 'chat'
        WHERE d.item_id IS NULL
          AND c.needs_answer = true
          AND (
            COALESCE(TRIM(c.last_message), '') = ''
            OR (
              length(TRIM(c.last_message)) <= 60
              AND TRIM(c.last_message) ~* :pat
            )
          )
    """), {"mp": mp, "pat": f"^({THANKS_RE})"}).fetchall()
    ids = [r[0] for r in rows]
    note = "Массовое закрытие: thanks/empty (как triage WB)"
    closed = 0
    for cid in ids:
        res = conn.execute(text("""
            INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
            VALUES ('chat', :id, :mp, :u, :n)
            ON CONFLICT (item_type, item_id, mp) DO NOTHING
            RETURNING item_id
        """), {"id": cid, "mp": mp, "u": USER, "n": note})
        if res.fetchone():
            closed += 1
    if ids:
        if mp == "ozon":
            conn.execute(text(OZON_CLEAR), {"ids": ids})
        else:
            conn.execute(text(YM_CLEAR), {"ids": ids})
    print(f"[{mp}] safe_close thanks/empty:", closed)
    return closed


def main():
    # Отдельные транзакции: длинный batch не держит лок против ozon_chat_sync
    with engine.begin() as conn:
        _archive_mp(conn, mp="ozon", select_sql=OZON_OPEN, clear_sql=OZON_CLEAR)
    with engine.begin() as conn:
        _archive_mp(conn, mp="ym", select_sql=YM_OPEN, clear_sql=YM_CLEAR)
    with engine.begin() as conn:
        _archive_safe_close(conn, mp="ozon", table="ozon_chats")
    with engine.begin() as conn:
        _archive_safe_close(conn, mp="ym", table="ym_chats")


if __name__ == "__main__":
    main()
