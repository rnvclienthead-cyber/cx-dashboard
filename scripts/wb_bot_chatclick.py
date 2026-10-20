#!/usr/bin/env python3
"""Клик по иконке чата именно в строке таблицы заявки."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9224"
CLAIM = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
OUT = Path("/tmp/wb_bot_chatclick.json")


def main() -> None:
    if not CLAIM:
        raise SystemExit("claim id required")
    captured: list[dict] = []

    def on_response(resp) -> None:
        try:
            url = resp.url
            method = resp.request.method
        except Exception:
            return
        if method not in ("GET", "POST", "PUT", "PATCH"):
            return
        if any(x in url for x in ("a.wb.ru/e/", "mc.yandex", "sentry", ".png", ".svg", ".woff", "sec/api/fl")):
            return
        item = {"status": resp.status, "method": method, "url": url[:800]}
        try:
            if method != "GET" and resp.request.post_data:
                item["post"] = (resp.request.post_data or "")[:2500]
        except Exception:
            pass
        try:
            ctype = (resp.headers or {}).get("content-type", "")
            if "json" in ctype:
                item["body"] = (resp.text() or "")[:5000]
        except Exception:
            pass
        captured.append(item)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = next(
            pg for pg in ctx.pages
            if "seller.wildberries.ru" in (pg.url or "") and "chrome://" not in pg.url
        )
        page.on("response", on_response)
        try:
            page.get_by_role("button", name="I Agree").click(timeout=2000)
        except Exception:
            pass

        row = page.locator("tr").filter(has_text=CLAIM)
        print("TR", row.count())
        if row.count() == 0:
            row = page.locator("tbody tr").filter(has_text=CLAIM)
            print("TBODY_TR", row.count())
        dump = row.first.evaluate(
            """el => ({
              text: (el.innerText||'').slice(0,400),
              btns: [...el.querySelectorAll('button')].map(b => ({
                disabled: b.disabled,
                cls: (b.className||'').toString().slice(0,120),
                html: b.outerHTML.slice(0,400),
              })),
              html: el.outerHTML.slice(0, 4000),
            })"""
        )
        print("TEXT", dump.get("text"))
        print("NBTN", len(dump.get("btns") or []))
        for i, b in enumerate(dump.get("btns") or []):
            print(i, "dis" if b.get("disabled") else "ok", b.get("cls"))
        Path("/tmp/wb_bot_tr.json").write_text(json.dumps(dump, ensure_ascii=False)[:30000])

        btns = row.first.locator("button")
        # обычно: чат, затем меню. Берём первую enabled в правой части.
        enabled = row.first.locator("button:not([disabled])")
        print("ENABLED", enabled.count())
        # клик по предпоследней/первой иконке — чат
        n = enabled.count()
        target = enabled.nth(0) if n == 1 else enabled.nth(max(0, n - 2) if n >= 2 else 0)
        # если две: chat + kebab. kebab last, chat n-2.
        if n >= 2:
            target = enabled.nth(n - 2)
        target.click(timeout=8000, force=True)
        page.wait_for_timeout(5000)
        page.screenshot(path="/tmp/wb_bot_after_chat.png")
        print("URL", page.url)
        print("ALL_PAGES")
        for pg in ctx.pages:
            if "chrome://" in (pg.url or ""):
                continue
            print(" ", pg.url[:180])
        OUT.write_text(json.dumps({"claim": CLAIM, "url": page.url, "captured": captured[-80:]}, ensure_ascii=False, indent=2))
        print("captured", len(captured))
        for x in captured:
            u = x["url"]
            if any(k in u.lower() for k in ("chat", "message", "claim", "jsonrpc", "buyer")):
                print(x["status"], x["method"], u[:200])
                if x.get("post"):
                    print(" POST", x["post"][:500])
                if x.get("body") and ("chat" in u.lower() or "chatId" in x["body"] or "replySign" in x["body"]):
                    print(" BODY", x["body"][:800])


if __name__ == "__main__":
    main()
