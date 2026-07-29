#!/usr/bin/env python3
"""Smoke: YM chats list + history + DB sync (send requires communication scope)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from backend.database import SessionLocal
from backend import ym_chat_sync as ym


def main():
    assert ym._key() and ym._business_id(), "YM keys missing"
    waiting = ym._list_chats(["WAITING_FOR_PARTNER"], limit=5)
    print("waiting:", len(waiting))
    if waiting:
        cid = waiting[0].get("chatId")
        print("sample", cid, waiting[0].get("status"))
        hist = ym._fetch_history(cid)
        print("history msgs", len(hist.get("messages") or []))
        # check send scope without sending real text to customer
        try:
            ym.send_message(str(cid), "")
        except PermissionError as e:
            print("send scope:", e)
        except Exception as e:
            print("send probe:", type(e).__name__, str(e)[:200])
    db = SessionLocal()
    try:
        n = ym.sync_chats(db)
        print("sync_chats:", n)
        from sqlalchemy import text
        row = db.execute(text(
            "SELECT count(*) AS c, count(*) FILTER (WHERE needs_answer) AS w FROM ym_chats"
        )).mappings().first()
        print("db ym_chats", dict(row))
        m = db.execute(text("SELECT count(*) FROM ym_chat_messages")).scalar()
        print("db messages", m)
    finally:
        db.close()


if __name__ == "__main__":
    main()
