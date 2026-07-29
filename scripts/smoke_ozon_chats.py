#!/usr/bin/env python3
"""Smoke: Ozon chat list v3 + history + DB sync."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from backend.database import SessionLocal
from backend import ozon_chat_sync as oz


def main():
    assert oz._cid() and oz._key(), "OZON keys missing"
    chats = oz._list_chats(unread_only=True, chat_status="Opened")
    print("unread opened:", len(chats))
    if chats:
        cid = (chats[0].get("chat") or {}).get("chat_id")
        print("sample", cid, "unread", chats[0].get("unread_count"))
        hist = oz._post("/v3/chat/history", {"chat_id": cid, "limit": 5})
        print("history msgs", len(hist.get("messages") or []))
    db = SessionLocal()
    try:
        n = oz.sync_chats(db)
        print("sync_chats:", n)
        from sqlalchemy import text
        row = db.execute(text(
            "SELECT count(*) AS c, count(*) FILTER (WHERE needs_answer) AS w FROM ozon_chats"
        )).mappings().first()
        print("db ozon_chats", dict(row))
        m = db.execute(text("SELECT count(*) FROM ozon_chat_messages")).scalar()
        print("db messages", m)
    finally:
        db.close()


if __name__ == "__main__":
    main()
