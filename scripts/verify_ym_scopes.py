#!/usr/bin/env python3
"""Проверка YM scopes после обновления токена + пробная отправка в чат."""
from __future__ import annotations

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

key = os.getenv("YM_API_KEY", "")
biz = os.getenv("YM_BUSINESS_ID", "")
camp = os.getenv("YM_CAMPAIGN_ID", "")
h = {"Api-Key": key, "Content-Type": "application/json"}
base = "https://api.partner.market.yandex.ru"
ok_list: list = []
fail_list: list = []


def check(name: str, method: str, url: str, **kw):
    try:
        r = requests.request(method, url, headers=h, timeout=30, **kw)
        body = (r.text or "").replace("\n", " ")[:220]
        if r.status_code in (401, 403):
            fail_list.append((name, r.status_code, body))
            print(f"FAIL {name}: {r.status_code} {body}")
        else:
            ok_list.append((name, r.status_code))
            print(f"OK   {name}: {r.status_code} {body[:140]}")
        return r
    except Exception as e:
        fail_list.append((name, "exc", str(e)))
        print(f"FAIL {name}: {e}")
        return None


def main():
    print("=== read / worker paths ===")
    check("auth/token", "POST", f"{base}/v2/auth/token", json={})
    check("orders", "GET", f"{base}/v2/campaigns/{camp}/orders", params={"page": 1, "pageSize": 1})
    check("returns", "GET", f"{base}/v2/campaigns/{camp}/returns", params={"limit": 1})
    check(
        "goods-feedback",
        "POST",
        f"{base}/v2/businesses/{biz}/goods-feedback",
        json={"pageSize": 1, "reactionStatus": "ALL"},
    )
    check(
        "goods-questions",
        "POST",
        f"{base}/v1/businesses/{biz}/goods-questions",
        json={"pageSize": 1, "needAnswer": True},
    )

    r = check(
        "chats waiting",
        "POST",
        f"{base}/v2/businesses/{biz}/chats",
        json={"statuses": ["WAITING_FOR_PARTNER"], "limit": 3},
    )
    chat_id = None
    customer = None
    if r is not None and r.status_code == 200:
        chats = ((r.json().get("result") or {}).get("chats") or [])
        if chats:
            chat_id = chats[0].get("chatId")
            customer = ((chats[0].get("context") or {}).get("customer") or {}).get("name")
            print(f"  sample chat={chat_id} customer={customer}")

    if chat_id:
        check(
            "chats history",
            "POST",
            f"{base}/v2/businesses/{biz}/chats/history",
            params={"chatId": chat_id},
            json={},
        )

    print("\n=== send scope (empty -> expect 400, not 403) ===")
    if not chat_id:
        print("no waiting chat")
    else:
        r = requests.post(
            f"{base}/v2/businesses/{biz}/chats/message",
            headers=h,
            params={"chatId": chat_id},
            json={"message": ""},
            timeout=20,
        )
        body = (r.text or "").replace("\n", " ")[:250]
        print(f"send empty: {r.status_code} {body}")
        if r.status_code == 403:
            fail_list.append(("send-scope", 403, body))
        else:
            ok_list.append(("send-scope", r.status_code))
            print("  -> communication scope OK")

        # Реальная короткая отправка в чат, который ждёт ответа
        do_send = "--send" in sys.argv
        if do_send and r.status_code != 403:
            text = (
                "Здравствуйте! Проверяем работу чатов из нашей системы КС — "
                "ответ скоро дополним по вашему вопросу."
            )
            sr = requests.post(
                f"{base}/v2/businesses/{biz}/chats/message",
                headers=h,
                params={"chatId": chat_id},
                json={"message": text},
                timeout=25,
            )
            print(f"send real chat={chat_id}: {sr.status_code} {(sr.text or '')[:200].replace(chr(10), ' ')}")
            ok_status = sr.status_code in (200, 201, 204)
            if ok_status or (
                sr.status_code == 200 and (sr.json() or {}).get("status") == "OK"
            ):
                ok_list.append(("send-real", sr.status_code))
                try:
                    from backend import ym_chat_sync

                    db = __import__("backend.database", fromlist=["SessionLocal"]).SessionLocal()
                    try:
                        ym_chat_sync.sync_chats(db)
                    finally:
                        db.close()
                    print("  local ym_chat_sync refreshed")
                except Exception as e:
                    print(f"  local sync after send: {e}")
            else:
                fail_list.append(("send-real", sr.status_code, (sr.text or "")[:200]))

    print("\n=== summary ===")
    print(f"ok={len(ok_list)} fail={len(fail_list)}")
    for f in fail_list:
        print(" FAIL", f)
    sys.exit(1 if fail_list else 0)


if __name__ == "__main__":
    main()
