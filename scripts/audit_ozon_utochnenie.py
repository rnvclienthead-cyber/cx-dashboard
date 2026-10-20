"""Что ушло клиенту в Ozon-автоответах с темой utochnenie и шапкой карточки."""
from sqlalchemy import text

from backend.database import engine

SQL = """
    SELECT created_at, chat_id, theme, branch_key, sent, zone,
           left(client_message, 280) AS msg,
           left(reply_text, 500) AS reply
    FROM cx_chat_bot_log
    WHERE coalesce(mp, 'wb') = 'ozon'
      AND sent = true
      AND theme = 'utochnenie'
      AND client_message ILIKE :pat
    ORDER BY created_at DESC
"""


def main() -> None:
    with engine.connect() as conn:
        rows = conn.execute(
            text(SQL), {"pat": "%у меня вопрос по вашему товару%"}
        ).mappings().all()
    print(f"n={len(rows)}")
    for r in rows:
        print("=" * 60)
        print(r["created_at"], r["chat_id"], "zone", r["zone"], "branch", r["branch_key"])
        print("CLIENT:", (r["msg"] or "").replace("\n", " | "))
        print("REPLY:", (r["reply"] or "").replace("\n", " | "))


if __name__ == "__main__":
    main()
