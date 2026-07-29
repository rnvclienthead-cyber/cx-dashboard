#!/usr/bin/env python3
"""Попытка найти goodCard/rid для чата через WB API + оценка backfill."""
import json
import os
import sys

sys.path.insert(0, "/root/cx-dashboard")
os.chdir("/root/cx-dashboard")

from sqlalchemy import text
from backend.database import engine
from backend import wb_chat

UUID = "e365c037-4ab8-a045-42bb-77ceb4ca7f04"
TARGET = f"1:{UUID}"

# 1) list chats from WB
chats = []
try:
    chats = wb_chat.get_chats() if hasattr(wb_chat, "get_chats") else None
except Exception as e:
    print("get_chats err", e)

if chats is None:
    # try common function names
    for name in dir(wb_chat):
        if "chat" in name.lower() and callable(getattr(wb_chat, name)):
            print("fn", name)

# Inspect source for fetch
import inspect
src = open("/root/cx-dashboard/backend/wb_chat.py").read()
if "buyer-chat-api" in src:
    print("has buyer-chat-api")

# Call internal list if exists
from backend.wb_chat import _key
import requests

key = _key()
print("has key", bool(key))

# WB chats list
url = "https://buyer-chat-api.wildberries.ru/api/v1/seller/chats"
r = requests.get(url, headers={"Authorization": key}, timeout=60)
print("chats status", r.status_code)
data = r.json() if r.status_code == 200 else None
if isinstance(data, dict):
    print("keys", data.keys())
    items = data.get("result") or data.get("chats") or data.get("data") or []
elif isinstance(data, list):
    items = data
else:
    items = []
print("chats count", len(items) if isinstance(items, list) else type(items))

found = None
if isinstance(items, list):
    for ch in items:
        cid = str(ch.get("chatID") or ch.get("chatId") or "")
        if UUID in cid:
            found = ch
            break

print("\n=== found in WB list ===")
if found:
    print(json.dumps(found, ensure_ascii=False, indent=2)[:2000])
else:
    print("NOT in first page / list payload")
    # show one sample structure
    if isinstance(items, list) and items:
        print("sample keys", items[0].keys())
        print("sample goodCard", items[0].get("goodCard"))

# Events search for this chat (may be heavy) - try recent events filter if API supports
# Also try matching claim by text similarity for this chat only
with engine.connect() as c:
    chat = c.execute(text("""
        SELECT chat_id, nm_id::text nm, last_message, last_ts
        FROM wb_chats WHERE chat_id = :cid
    """), {"cid": TARGET}).mappings().first()
    print("\nlocal chat nm", chat["nm"])

    # claims with similar text on same nm around same dates
    claims = c.execute(text("""
        SELECT claim_id, srid, status, claim_date,
               left(user_comment, 200) uc
        FROM wb_claims
        WHERE nm_id = :nm
          AND user_comment ILIKE '%отверст%'
        ORDER BY claim_date DESC NULLS LAST
        LIMIT 10
    """), {"nm": chat["nm"]}).mappings().fetchall()
    print("claims with отверстие:", len(claims))
    for row in claims:
        print(dict(row))
