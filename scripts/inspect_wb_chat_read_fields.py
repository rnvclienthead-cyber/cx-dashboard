#!/usr/bin/env python3
import json
from dotenv import load_dotenv
load_dotenv("/root/cx-dashboard/.env")
from backend import wb_chat

chats = wb_chat.get_chats()
print("n=", len(chats))
keys_union = set()
for c in chats:
    keys_union |= set(c.keys())
print("all keys:", sorted(keys_union))
if chats:
    sample = chats[0]
    lm = sample.get("lastMessage")
    print("lastMessage keys:", sorted(lm.keys()) if isinstance(lm, dict) else lm)
    # dump one chat without replySign
    dump = {k: v for k, v in sample.items() if k != "replySign"}
    print(json.dumps(dump, ensure_ascii=False, default=str)[:2000])

# also peek events for read-related fields
ev = wb_chat.get_events(0)
print("\nevents top keys:", sorted((ev or {}).keys()))
events = (ev or {}).get("events") or []
print("events n=", len(events))
if events:
    ek = set()
    for e in events[:30]:
        ek |= set(e.keys())
        if isinstance(e.get("message"), dict):
            ek |= {f"message.{k}" for k in e["message"].keys()}
    print("event field union:", sorted(ek))
    print(json.dumps(events[0], ensure_ascii=False, default=str)[:1500])
