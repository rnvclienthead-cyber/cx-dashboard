#!/usr/bin/env python3
"""Диагностика чата WB: сообщения, токены, бот."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from backend.database import SessionLocal

CHAT_UUID = sys.argv[1] if len(sys.argv) > 1 else "59515e7e-4bc1-2e40-5b6c-c4db49baf010"


def main():
    db = SessionLocal()
    try:
        chats = db.execute(text("""
          SELECT chat_id, client_name, nm_id, needs_answer, last_sender,
                 left(last_message, 300) AS last_message,
                 to_timestamp(last_ts/1000.0) AS last_at, order_rid,
                 reply_sign IS NOT NULL AND reply_sign <> '' AS has_rs,
                 is_closing
          FROM wb_chats
          WHERE chat_id LIKE :p OR chat_id = :c
          ORDER BY last_ts DESC NULLS LAST LIMIT 5
        """), {"p": f"%{CHAT_UUID}%", "c": CHAT_UUID}).mappings().fetchall()
        print("CHATS", len(chats))
        if not chats:
            return
        for r in chats:
            print(json.dumps({k: str(v) for k, v in dict(r).items()}, ensure_ascii=False))

        cid = chats[0]["chat_id"]
        msgs = db.execute(text("""
          SELECT sender, add_ts, to_timestamp(add_ts/1000.0) AS at,
                 text,
                 CASE WHEN attachments IS NULL THEN NULL ELSE attachments::text END AS att
          FROM wb_chat_messages
          WHERE chat_id = :c
          ORDER BY add_ts ASC NULLS LAST
        """), {"c": cid}).mappings().fetchall()
        print("MSGS", len(msgs))
        for m in msgs:
            print("---")
            print(m["at"], m["sender"])
            print(m["text"] or "")
            if m["att"]:
                print("ATT", (m["att"] or "")[:400])

        # bot state
        try:
            st = db.execute(text("""
              SELECT * FROM cx_chat_bot_state WHERE chat_id = :c
            """), {"c": cid}).mappings().first()
            print("BOT_STATE", {k: str(v)[:300] for k, v in dict(st).items()} if st else None)
        except Exception as e:
            print("BOT_STATE err", e)

        try:
            sug = db.execute(text("""
              SELECT client_last_ts, left(payload::text, 3000) AS payload, updated_at
              FROM cx_chat_bot_suggest_cache WHERE chat_id = :c
            """), {"c": cid}).mappings().first()
            print("SUGGEST", {k: str(v)[:2000] for k, v in dict(sug).items()} if sug else None)
        except Exception as e:
            print("SUGGEST err", e)

        # moderation / drafts
        for q in [
            "SELECT * FROM cx_moderation_queue WHERE item_id LIKE :p OR payload::text ILIKE :p2 ORDER BY created_at DESC LIMIT 5",
        ]:
            try:
                rows = db.execute(text(q), {"p": f"%{CHAT_UUID}%", "p2": f"%{CHAT_UUID}%"}).mappings().fetchall()
                print("MOD", len(rows))
                for r in rows:
                    print({k: str(v)[:200] for k, v in dict(r).items()})
            except Exception as e:
                print("MOD err", e)

        # chat bot send log if any
        for table in ("cx_chat_bot_actions", "cx_chat_bot_log", "cx_response_log", "cx_chat_send_log"):
            try:
                exists = db.execute(text("""
                  SELECT 1 FROM information_schema.tables WHERE table_name=:t
                """), {"t": table}).scalar()
                if not exists:
                    continue
                cols = [c[0] for c in db.execute(text("""
                  SELECT column_name FROM information_schema.columns WHERE table_name=:t
                """), {"t": table}).fetchall()]
                print("TABLE", table, cols)
                rows = db.execute(text(f"""
                  SELECT * FROM {table}
                  WHERE chat_id = :c OR cast(payload as text) ILIKE :p
                  ORDER BY 1 DESC LIMIT 10
                """), {"c": cid, "p": f"%{CHAT_UUID}%"}).mappings().fetchall()
                for r in rows:
                    print({k: str(v)[:250] for k, v in dict(r).items()})
            except Exception as e:
                print(table, "err", e)

        toks = db.execute(text("""
          SELECT token, chat_id, created_at, expires_at, created_by
          FROM reshipment_chat_tokens
          WHERE chat_id = :c OR chat_id LIKE :p
          ORDER BY created_at DESC LIMIT 20
        """), {"c": cid, "p": f"%{CHAT_UUID}%"}).mappings().fetchall()
        print("CHAT_TOKENS", len(toks))
        for t in toks:
            print({k: str(v) for k, v in dict(t).items()})

        import re
        found_toks = set()
        found_urls = set()
        for m in msgs:
            t = m["text"] or ""
            found_toks.update(re.findall(r"[?&]t=([A-Za-z0-9_-]+)", t))
            found_urls.update(re.findall(r"https?://\S+", t))
            if "box.vidovito" in t or "cxvo.ru" in t or "?t=" in t:
                print("LINK_MSG", m["at"], m["sender"], t)
        print("FOUND_TOKS", list(found_toks))
        print("FOUND_URLS", list(found_urls))
        for tok in found_toks:
            row = db.execute(text("""
              SELECT token, chat_id, created_at, created_by, expires_at
              FROM reshipment_chat_tokens WHERE token = :t
            """), {"t": tok}).mappings().first()
            print("TOK_LOOKUP", tok, dict(row) if row else "NOT IN reshipment_chat_tokens")
            row2 = db.execute(text("""
              SELECT token, chat_id FROM reshipment_tokens WHERE token = :t LIMIT 1
            """), {"t": tok}).mappings().first() if False else None
            try:
                row2 = db.execute(text("""
                  SELECT token, chat_id, created_at FROM reshipment_tokens WHERE token = :t LIMIT 1
                """), {"t": tok}).mappings().first()
                print("TOK2", tok, dict(row2) if row2 else "NOT IN reshipment_tokens")
            except Exception as e:
                print("TOK2 err", e)
    finally:
        db.close()


if __name__ == "__main__":
    main()
