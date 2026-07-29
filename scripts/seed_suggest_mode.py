#!/usr/bin/env python3
from sqlalchemy import text
from backend.database import SessionLocal, engine
from backend import chat_bot

chat_bot.init_bot_tables(engine)
db = SessionLocal()
force = {"bot_shadow": "false", "bot_enabled": "false", "bot_suggest_on_open": "true"}
for k, v, d in chat_bot.EXTRA_SETTINGS:
    val = force.get(k, v)
    db.execute(text("""
        INSERT INTO cx_chat_settings (key, value, descr, updated_by)
        VALUES (:k, :v, :d, 'system')
        ON CONFLICT (key) DO UPDATE SET
          descr = EXCLUDED.descr,
          value = CASE WHEN :force THEN EXCLUDED.value ELSE cx_chat_settings.value END
    """), {"k": k, "v": val, "d": d, "force": k in force})
db.commit()
print("settings:", {r[0]: r[1] for r in db.execute(text(
    "SELECT key, value FROM cx_chat_settings WHERE key LIKE 'bot_%' ORDER BY 1"
)).fetchall()})
row = db.execute(text("""
    SELECT chat_id FROM wb_chats
    WHERE needs_answer AND last_sender='client'
    ORDER BY last_ts DESC LIMIT 1
""")).fetchone()
if row:
    s = chat_bot.suggest_for_chat(db, row[0])
    print("suggest:", s.get("theme"), s.get("confidence"), len(s.get("reply_text") or ""))
db.close()
