#!/usr/bin/env python3
from backend.database import engine, SessionLocal
from backend.routers import chat_playbook
from backend import chat_bot
from sqlalchemy import text

chat_playbook.ensure_new_themes(engine)
chat_bot.init_bot_tables(engine)

db = SessionLocal()
themes = [r[0] for r in db.execute(text(
    "SELECT theme_key FROM cx_chat_themes ORDER BY sort_order"
)).fetchall()]
print("themes:", themes)
for k in ("utochnenie", "cheque", "parametry", "dengi", "srok_hraneniya", "teh_sboi"):
    n = db.execute(text(
        "SELECT count(*) FROM cx_chat_variants WHERE theme_key=:k"
    ), {"k": k}).scalar()
    print(f"  variants {k}: {n}")

for msg, stage in [
    ("здравствуйте", "new"),
    ("1", "awaiting_menu"),
    ("где чек", "new"),
    ("не хватает болтов", "new"),
    ("когда деньги вернутся", "new"),
    ("какая высота", "new"),
]:
    t, c, n, r = chat_bot.classify(db, msg, stage, [], "")
    print(f"classify[{stage}] {msg!r} -> {t} conf={c:.2f} human={n} ({r})")

body, action = chat_bot.assemble_reply(
    db, "utochnenie", "", "test-chat", {"stage": "new"}, None, None
)
print("menu action:", action)
print("menu body:\n", body)
db.close()
