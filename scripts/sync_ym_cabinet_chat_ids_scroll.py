#!/usr/bin/env python3
"""Scroll YM arbiter lists in Playwright and collect all cabinet_chat_id mappings."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, text

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
SESSION = ROOT / "ym_session.json"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def main() -> int:
    from playwright.sync_api import sync_playwright

    business_id = (os.getenv("YM_BUSINESS_ID") or "").strip()
    campaign_id = (os.getenv("YM_CAMPAIGN_ID") or "").strip()
    if not business_id or not campaign_id:
        print("Need YM_BUSINESS_ID / YM_CAMPAIGN_ID", file=sys.stderr)
        return 1
    if not SESSION.exists():
        print(f"No session: {SESSION}", file=sys.stderr)
        return 1

    try:
        from backend.database import DATABASE_URL as db_url
    except Exception:
        db_url = "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"

    mapping: dict[str, str] = {}
    captured: list[dict] = []

    def on_response(resp):
        if "getBusinessChats" not in resp.url:
            return
        try:
            body = resp.text()
            d = json.loads(body)
            chats = ((d.get("results") or [{}])[0].get("data") or {}).get("chats") or []
            captured.append(chats)
            for ch in chats:
                inner = str(ch.get("innerChatId") or "").strip()
                cab = str(ch.get("chatId") or "").strip()
                if inner and cab:
                    mapping[inner] = cab
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            storage_state=str(SESSION), user_agent=UA, locale="ru-RU"
        )
        page = context.new_page()
        page.on("response", on_response)

        for group in ("DIRECT", "RETURN", "ORDER"):
            url = (
                f"https://partner.market.yandex.ru/business/{business_id}/arbiter"
                f"?campaignId={campaign_id}&chatGroup={group}"
            )
            print("goto", group)
            page.goto(url, wait_until="networkidle", timeout=60000)
            time.sleep(2)
            # scroll chat list to force pagination
            for i in range(25):
                page.mouse.wheel(0, 2400)
                page.evaluate(
                    """() => {
                      const nodes = Array.from(document.querySelectorAll('*'))
                        .filter(el => el.scrollHeight > el.clientHeight + 40);
                      for (const el of nodes) {
                        el.scrollTop = el.scrollHeight;
                      }
                    }"""
                )
                time.sleep(0.7)
            print(f"  mapped so far: {len(mapping)}")

        context.storage_state(path=str(SESSION))
        browser.close()

    print(f"responses: {len(captured)}, unique: {len(mapping)}")

    eng = create_engine(db_url)
    with eng.begin() as conn:
        conn.execute(text(
            "ALTER TABLE ym_chats ADD COLUMN IF NOT EXISTS cabinet_chat_id TEXT"
        ))
        # known from user for Olga K. DIRECT chat
        mapping.setdefault(
            "15651975", "0/43/d6b35f7f-58ee-4a57-ab93-aa0db884180f"
        )
        updated = 0
        for inner, cab in mapping.items():
            r = conn.execute(text("""
                UPDATE ym_chats SET cabinet_chat_id = :cab
                WHERE chat_id = :cid
            """), {"cab": cab, "cid": inner})
            updated += r.rowcount or 0
        total = conn.execute(text(
            "SELECT count(*) FROM ym_chats WHERE cabinet_chat_id IS NOT NULL"
        )).scalar()
    print(f"updated rows: {updated}, with cabinet_chat_id: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
