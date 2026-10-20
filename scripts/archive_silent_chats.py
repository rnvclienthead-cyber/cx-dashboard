"""Разово закрыть чаты по правилу: мы уже писали, тишина >3 дней."""
from __future__ import annotations

import sys

from sqlalchemy import text

from backend.chat_bot import set_bot_disabled
from backend.database import engine

NOTE = "Авто: нет общения >3 дн после нашего ответа"
DRY = "--apply" not in sys.argv

WB_TARGETS = """
SELECT c.chat_id
FROM wb_chats c
LEFT JOIN cx_dismissed_items d
  ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
WHERE d.item_id IS NULL
  AND to_timestamp(COALESCE(c.last_ts, 0) / 1000.0) < now() - interval '3 days'
  AND lower(COALESCE(c.last_sender, '')) = 'seller'
  AND EXISTS (
    SELECT 1 FROM wb_chat_messages s
    WHERE s.chat_id = c.chat_id AND s.sender = 'seller'
  )
"""

OZON_TARGETS = """
SELECT c.chat_id
FROM ozon_chats c
LEFT JOIN cx_dismissed_items d
  ON d.item_id = c.chat_id AND d.mp = 'ozon' AND d.item_type = 'chat'
WHERE d.item_id IS NULL
  AND COALESCE(
    (SELECT MAX(m.created_at) FROM ozon_chat_messages m WHERE m.chat_id = c.chat_id),
    c.waiting_since, c.updated_at, c.created_at
  ) < now() - interval '3 days'
  AND lower(COALESCE(c.last_sender, '')) IN ('seller', 'courier')
  AND EXISTS (
    SELECT 1 FROM ozon_chat_messages s
    WHERE s.chat_id = c.chat_id
      AND lower(COALESCE(s.sender_type, '')) IN ('seller', 'courier')
  )
"""

YM_TARGETS = """
SELECT c.chat_id
FROM ym_chats c
LEFT JOIN cx_dismissed_items d
  ON d.item_id = c.chat_id AND d.mp = 'ym' AND d.item_type = 'chat'
WHERE d.item_id IS NULL
  AND COALESCE(
    (SELECT MAX(m.created_at) FROM ym_chat_messages m WHERE m.chat_id = c.chat_id),
    c.waiting_since, c.updated_at, c.created_at
  ) < now() - interval '3 days'
  AND lower(COALESCE(c.last_sender, '')) IN ('seller', 'partner', 'shop', 'merchant')
  AND EXISTS (
    SELECT 1 FROM ym_chat_messages s
    WHERE s.chat_id = c.chat_id
      AND lower(COALESCE(s.sender_type, '')) IN ('seller', 'partner', 'shop', 'merchant')
  )
"""


def _ids(conn, sql: str) -> list[str]:
    return [str(r[0]) for r in conn.execute(text(sql)).fetchall()]


def archive(conn, mp: str, ids: list[str], dismissed_by: str) -> int:
    if not ids:
        return 0
    conn.execute(text("""
        INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
        SELECT 'chat', x, :mp, :by, :note
        FROM unnest(CAST(:ids AS text[])) AS x
        ON CONFLICT (item_type, item_id, mp) DO NOTHING
    """), {"mp": mp, "by": dismissed_by, "note": NOTE, "ids": ids})
    if mp == "wb":
        conn.execute(text("""
            UPDATE wb_chats SET needs_answer = false, waiting_since = NULL, updated_at = now()
            WHERE chat_id = ANY(:ids) AND needs_answer = true
        """), {"ids": ids})
    elif mp == "ozon":
        conn.execute(text("""
            UPDATE ozon_chats SET needs_answer = false, waiting_since = NULL, updated_at = now()
            WHERE chat_id = ANY(:ids) AND needs_answer = true
        """), {"ids": ids})
    elif mp == "ym":
        conn.execute(text("""
            UPDATE ym_chats SET needs_answer = false, waiting_since = NULL, updated_at = now()
            WHERE chat_id = ANY(:ids) AND needs_answer = true
        """), {"ids": ids})
    for cid in ids:
        set_bot_disabled(conn, cid)
    return len(ids)


def main() -> None:
    with engine.connect() as conn:
        wb = _ids(conn, WB_TARGETS)
        oz = _ids(conn, OZON_TARGETS)
        ym = _ids(conn, YM_TARGETS)
        print(f"preview wb={len(wb)} ozon={len(oz)} ym={len(ym)} total={len(wb)+len(oz)+len(ym)}")
        extra = conn.execute(text("""
            SELECT 'wb' AS mp, count(*) FROM wb_chats c
            LEFT JOIN cx_dismissed_items d ON d.item_id=c.chat_id AND d.mp='wb' AND d.item_type='chat'
            WHERE d.item_id IS NULL
              AND to_timestamp(COALESCE(c.last_ts,0)/1000.0) < now() - interval '3 days'
            UNION ALL
            SELECT 'ozon', count(*) FROM ozon_chats c
            LEFT JOIN cx_dismissed_items d ON d.item_id=c.chat_id AND d.mp='ozon' AND d.item_type='chat'
            WHERE d.item_id IS NULL
              AND COALESCE((SELECT MAX(m.created_at) FROM ozon_chat_messages m WHERE m.chat_id=c.chat_id),
                           c.waiting_since, c.updated_at, c.created_at) < now() - interval '3 days'
            UNION ALL
            SELECT 'ym', count(*) FROM ym_chats c
            LEFT JOIN cx_dismissed_items d ON d.item_id=c.chat_id AND d.mp='ym' AND d.item_type='chat'
            WHERE d.item_id IS NULL
              AND COALESCE((SELECT MAX(m.created_at) FROM ym_chat_messages m WHERE m.chat_id=c.chat_id),
                           c.waiting_since, c.updated_at, c.created_at) < now() - interval '3 days'
        """)).fetchall()
        print("open_older_3d_any", {r[0]: int(r[1]) for r in extra})
        for mp, sql in (
            ("wb", """
                SELECT coalesce(lower(c.last_sender), '∅') s,
                       count(*) n,
                       count(*) FILTER (
                         WHERE EXISTS (SELECT 1 FROM wb_chat_messages m
                                       WHERE m.chat_id=c.chat_id AND m.sender='seller')
                       ) with_seller_msg
                FROM wb_chats c
                LEFT JOIN cx_dismissed_items d ON d.item_id=c.chat_id AND d.mp='wb' AND d.item_type='chat'
                WHERE d.item_id IS NULL
                  AND to_timestamp(COALESCE(c.last_ts,0)/1000.0) < now() - interval '3 days'
                GROUP BY 1 ORDER BY n DESC
            """),
            ("ozon", """
                SELECT coalesce(lower(c.last_sender), '∅') s,
                       count(*) n,
                       count(*) FILTER (
                         WHERE EXISTS (SELECT 1 FROM ozon_chat_messages m
                                       WHERE m.chat_id=c.chat_id
                                         AND lower(coalesce(m.sender_type,'')) IN ('seller','courier'))
                       ) with_seller_msg
                FROM ozon_chats c
                LEFT JOIN cx_dismissed_items d ON d.item_id=c.chat_id AND d.mp='ozon' AND d.item_type='chat'
                WHERE d.item_id IS NULL
                  AND COALESCE((SELECT MAX(m.created_at) FROM ozon_chat_messages m WHERE m.chat_id=c.chat_id),
                               c.waiting_since, c.updated_at, c.created_at) < now() - interval '3 days'
                GROUP BY 1 ORDER BY n DESC
            """),
            ("ym", """
                SELECT coalesce(lower(c.last_sender), '∅') s,
                       count(*) n,
                       count(*) FILTER (
                         WHERE EXISTS (SELECT 1 FROM ym_chat_messages m
                                       WHERE m.chat_id=c.chat_id
                                         AND lower(coalesce(m.sender_type,'')) IN ('seller','partner','shop','merchant'))
                       ) with_seller_msg
                FROM ym_chats c
                LEFT JOIN cx_dismissed_items d ON d.item_id=c.chat_id AND d.mp='ym' AND d.item_type='chat'
                WHERE d.item_id IS NULL
                  AND COALESCE((SELECT MAX(m.created_at) FROM ym_chat_messages m WHERE m.chat_id=c.chat_id),
                               c.waiting_since, c.updated_at, c.created_at) < now() - interval '3 days'
                GROUP BY 1 ORDER BY n DESC
            """),
        ):
            rows = conn.execute(text(sql)).fetchall()
            print(mp, [(r[0], int(r[1]), int(r[2])) for r in rows])
        if DRY:
            print("dry-run; pass --apply to archive")
            print("=== samples ===")
            for r in conn.execute(text("""
                SELECT c.chat_id, c.last_sender, c.needs_answer,
                       to_timestamp(COALESCE(c.last_ts,0)/1000.0) AS last_at,
                       left(coalesce(c.last_message,''), 90) AS lm,
                       (SELECT count(*) FROM wb_chat_messages m WHERE m.chat_id=c.chat_id) AS msgs
                FROM wb_chats c
                LEFT JOIN cx_dismissed_items d
                  ON d.item_id=c.chat_id AND d.mp='wb' AND d.item_type='chat'
                WHERE d.item_id IS NULL
                  AND to_timestamp(COALESCE(c.last_ts,0)/1000.0) < now() - interval '3 days'
                ORDER BY to_timestamp(COALESCE(c.last_ts,0)/1000.0)
                LIMIT 6
            """)).mappings():
                print("wb", dict(r))
            print("wb msg senders", conn.execute(text("""
                SELECT m.sender, count(*)
                FROM wb_chat_messages m
                JOIN wb_chats c ON c.chat_id=m.chat_id
                LEFT JOIN cx_dismissed_items d
                  ON d.item_id=c.chat_id AND d.mp='wb' AND d.item_type='chat'
                WHERE d.item_id IS NULL
                  AND to_timestamp(COALESCE(c.last_ts,0)/1000.0) < now() - interval '3 days'
                GROUP BY 1
            """)).fetchall())
            print("ozon msg senders", conn.execute(text("""
                SELECT m.sender_type, count(*)
                FROM ozon_chat_messages m
                JOIN ozon_chats c ON c.chat_id=m.chat_id
                LEFT JOIN cx_dismissed_items d
                  ON d.item_id=c.chat_id AND d.mp='ozon' AND d.item_type='chat'
                WHERE d.item_id IS NULL
                  AND COALESCE((SELECT MAX(x.created_at) FROM ozon_chat_messages x WHERE x.chat_id=c.chat_id),
                               c.waiting_since, c.updated_at, c.created_at) < now() - interval '3 days'
                GROUP BY 1
            """)).fetchall())
            print("ym msg senders", conn.execute(text("""
                SELECT m.sender_type, count(*)
                FROM ym_chat_messages m
                JOIN ym_chats c ON c.chat_id=m.chat_id
                LEFT JOIN cx_dismissed_items d
                  ON d.item_id=c.chat_id AND d.mp='ym' AND d.item_type='chat'
                WHERE d.item_id IS NULL
                  AND COALESCE((SELECT MAX(x.created_at) FROM ym_chat_messages x WHERE x.chat_id=c.chat_id),
                               c.waiting_since, c.updated_at, c.created_at) < now() - interval '3 days'
                GROUP BY 1
            """)).fetchall())
            return
        n = 0
        n += archive(conn, "wb", wb, "wb_chat_sync")
        n += archive(conn, "ozon", oz, "ozon_chat_sync")
        n += archive(conn, "ym", ym, "ym_chat_sync")
        conn.commit()
        print(f"archived {n}")
        try:
            from backend import chat_realtime
            for cid in wb:
                chat_realtime.publish_chat_removed(cid, "wb")
            for cid in oz:
                chat_realtime.publish_chat_removed(cid, "ozon")
            for cid in ym:
                chat_realtime.publish_chat_removed(cid, "ym")
        except Exception as e:
            print("realtime skip:", e)


if __name__ == "__main__":
    main()
