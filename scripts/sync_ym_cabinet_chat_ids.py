#!/usr/bin/env python3
"""
Синхронизирует cabinet_chat_id (формат 0/43/uuid) для ym_chats.

Partner API отдаёт числовой chatId, а deep-link в кабинете требует
внутренний chatId вида 0/43/<uuid> (= innerChatId ↔ chatId в web-API).

Нужны: ym_session.json (Playwright-сессия кабинета), YM_BUSINESS_ID, YM_CAMPAIGN_ID,
DATABASE_URL_LOCAL.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests
from sqlalchemy import create_engine, text

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
SESSION = ROOT / "ym_session.json"
RESOLVE_URL = (
    "https://partner.market.yandex.ru/api/resolve/"
    "?r=partnerCustomerChat/getBusinessChats:getBusinessChats"
)
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _db_url() -> str:
    # Прод-бэкенд ходит в локальный Postgres (см. backend/database.py).
    try:
        from backend.database import DATABASE_URL as _local
        if _local:
            return _local
    except Exception:
        pass
    return "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"


def _get_sk_and_cookies(business_id: str, campaign_id: str):
    from playwright.sync_api import sync_playwright

    if not SESSION.exists():
        raise SystemExit(f"Нет сессии кабинета: {SESSION}")

    sk_holder: dict = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            storage_state=str(SESSION), user_agent=UA, locale="ru-RU"
        )
        page = context.new_page()

        def on_req(req):
            if "getBusinessChats" in req.url and req.headers.get("sk"):
                sk_holder["headers"] = {
                    k: v
                    for k, v in req.headers.items()
                    if k.lower() not in ("cookie", "content-length", "host")
                }

        page.on("request", on_req)
        page.goto(
            f"https://partner.market.yandex.ru/business/{business_id}/arbiter"
            f"?campaignId={campaign_id}&chatGroup=DIRECT",
            wait_until="networkidle",
            timeout=60000,
        )
        time.sleep(1)
        cookies = {c["name"]: c["value"] for c in context.cookies()}
        context.storage_state(path=str(SESSION))
        browser.close()

    headers = sk_holder.get("headers") or {}
    if not headers.get("sk"):
        raise SystemExit("Не удалось получить sk-токен из кабинета ЯМ")
    return headers, cookies


def _fetch_group(headers, cookies, business_id: str, campaign_id: str,
                 group: str, contexts: list[str] | None) -> list[dict]:
    filters: dict = {"pageSize": 15, "unreadOnly": False, "arbitrageOnly": False}
    if contexts:
        filters["chatContexts"] = contexts
    body = {
        "params": [{"businessId": int(business_id), "filters": filters}],
        "path": (
            f"/business/{business_id}/arbiter"
            f"?campaignId={campaign_id}&chatGroup={group}"
        ),
    }
    out: list[dict] = []
    for _ in range(10):
        r = requests.post(
            RESOLVE_URL, headers=headers, cookies=cookies, json=body, timeout=30
        )
        r.raise_for_status()
        data = ((r.json().get("results") or [{}])[0].get("data") or {})
        chats = data.get("chats") or []
        out.extend(chats)
        token = (data.get("paging") or {}).get("nextPageToken") or data.get("nextPageToken")
        if not chats or not token:
            break
        filters["pageToken"] = token
        body["params"][0]["filters"] = filters
    return out


def main() -> int:
    business_id = _env("YM_BUSINESS_ID")
    campaign_id = _env("YM_CAMPAIGN_ID")
    db_url = _db_url()
    if not business_id or not campaign_id or not db_url:
        print("Нужны YM_BUSINESS_ID, YM_CAMPAIGN_ID, DATABASE_URL_LOCAL", file=sys.stderr)
        return 1

    headers, cookies = _get_sk_and_cookies(business_id, campaign_id)
    mapping: dict[str, str] = {}
    for group, contexts in (
        ("DIRECT", ["DIRECT"]),
        ("RETURN", None),
        ("ORDER", ["ORDER", "RETURN"]),
    ):
        chats = _fetch_group(headers, cookies, business_id, campaign_id, group, contexts)
        print(f"{group}: {len(chats)} chats")
        for ch in chats:
            inner = str(ch.get("innerChatId") or "").strip()
            cab = str(ch.get("chatId") or "").strip()
            if inner and cab:
                mapping[inner] = cab

    print(f"unique mappings: {len(mapping)}")
    eng = create_engine(db_url)
    with eng.begin() as conn:
        conn.execute(text(
            "ALTER TABLE ym_chats ADD COLUMN IF NOT EXISTS cabinet_chat_id TEXT"
        ))
        updated = 0
        for inner, cab in mapping.items():
            r = conn.execute(text("""
                UPDATE ym_chats SET cabinet_chat_id = :cab
                WHERE chat_id = :cid
                  AND (cabinet_chat_id IS DISTINCT FROM :cab)
            """), {"cab": cab, "cid": inner})
            updated += r.rowcount or 0
        total = conn.execute(text(
            "SELECT count(*) FROM ym_chats WHERE cabinet_chat_id IS NOT NULL"
        )).scalar()
    print(f"updated: {updated}, with cabinet_chat_id: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
