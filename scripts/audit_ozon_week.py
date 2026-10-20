"""Автоответы Ozon за 7 дней: текст клиента и что ушло."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from backend.database import engine

SQL = """
    SELECT created_at, chat_id, theme, branch_key, zone, reason,
           left(client_message, 350) AS msg,
           left(reply_text, 350) AS reply
    FROM cx_chat_bot_log
    WHERE coalesce(mp, 'wb') = 'ozon'
      AND sent = true
      AND created_at >= now() - interval '7 days'
    ORDER BY created_at DESC
"""


def main() -> None:
    with engine.connect() as conn:
        rows = conn.execute(text(SQL)).mappings().all()
    print(f"n={len(rows)}")
    for r in rows:
        print("=" * 72)
        print(r["created_at"], "theme=", r["theme"], "branch=", r["branch_key"])
        print("chat", r["chat_id"])
        print("reason", (r["reason"] or "")[:120])
        print("CLIENT:", (r["msg"] or "").replace("\n", " | "))
        print("REPLY:", (r["reply"] or "").replace("\n", " | "))


if __name__ == "__main__":
    main()
