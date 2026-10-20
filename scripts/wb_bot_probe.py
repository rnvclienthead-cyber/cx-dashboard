#!/usr/bin/env python3
"""Пробный проход: поиск возврата WB в кабинете и перехват запроса «открыть чат»."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9224"
CLAIM = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
OUT = Path("/tmp/wb_bot_probe.json")


def main() -> None:
    if not CLAIM:
        print("usage: wb_bot_probe.py <claim_id>")
        sys.exit(2)
    captured: list[dict] = []

    def on_response(resp) -> None:
        try:
            url = resp.url
        except Exception:
            return
        low = url.lower()
        if not any(x in low for x in (
            "chat", "claim", "return", "message", "buyer", "commun",
            "seller", "request", "dispute",
        )):
            return
        if any(x in low for x in ("sentry", "metric", "counter", "mc.yandex", "pixel", ".png", ".svg", ".woff")):
            return
        item = {
            "status": resp.status,
            "method": resp.request.method,
            "url": url[:500],
        }
        try:
            if resp.request.method in ("POST", "PUT", "PATCH") and resp.request.post_data:
                item["post"] = (resp.request.post_data or "")[:1500]
        except Exception:
            pass
        try:
            ctype = (resp.headers or {}).get("content-type", "")
            if "json" in ctype and resp.status < 500:
                item["body"] = (resp.text() or "")[:2500]
        except Exception:
            pass
        captured.append(item)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = None
        for pg in ctx.pages:
            if "seller.wildberries.ru" in (pg.url or ""):
                page = pg
                break
        if page is None:
            page = ctx.new_page()
            page.goto("https://seller.wildberries.ru/suppliers-product-verification/customer-claims/under-consideration", timeout=60000)
        page.on("response", on_response)
        print("PAGE", page.url)
        print("TITLE", page.title())
        page.screenshot(path="/tmp/wb_bot_before.png", full_page=False)
        # видимый текст кнопок/плейсхолдеров
        info = page.evaluate(
            """() => {
              const texts = [];
              const take = (el) => (el.innerText || el.placeholder || el.ariaLabel || '').trim();
              document.querySelectorAll('input, textarea, button, [role="button"], a').forEach(el => {
                const t = take(el);
                if (t && t.length < 80) texts.push(t);
              });
              return {
                href: location.href,
                placeholders: [...document.querySelectorAll('input')].map(i => ({
                  ph: i.placeholder, type: i.type, name: i.name, aria: i.getAttribute('aria-label')
                })).slice(0, 30),
                buttons: texts.slice(0, 80),
              };
            }"""
        )
        print(json.dumps(info, ensure_ascii=False, indent=2)[:4000])

        # поиск по номеру заявки
        box = page.locator("input").first
        # предпочесть поле с поиском
        cand = page.locator(
            'input[placeholder*="оиск" i], input[placeholder*="заяв" i], input[placeholder*="номер" i], input[type="search"]'
        )
        if cand.count():
            box = cand.first
        box.click(timeout=15000)
        box.fill("")
        box.type(CLAIM, delay=30)
        page.keyboard.press("Enter")
        page.wait_for_timeout(3500)
        page.screenshot(path="/tmp/wb_bot_search.png", full_page=False)
        after = page.evaluate(
            """() => ({
              href: location.href,
              text: (document.body.innerText || '').slice(0, 2500),
            })"""
        )
        print("AFTER_URL", after.get("href"))
        print("AFTER_TEXT")
        print((after.get("text") or "")[:2000])

        # кнопка чата
        chat_btn = page.get_by_text("Чат с покупателем", exact=False)
        if chat_btn.count() == 0:
            chat_btn = page.get_by_text("Написать покупателю", exact=False)
        clicked = False
        if chat_btn.count():
            chat_btn.first.click(timeout=10000)
            clicked = True
            page.wait_for_timeout(4000)
            page.screenshot(path="/tmp/wb_bot_chat.png", full_page=False)
            print("CLICKED_CHAT", True, "URL", page.url)
        else:
            print("CLICKED_CHAT", False)

        OUT.write_text(json.dumps({
            "claim": CLAIM,
            "clicked": clicked,
            "final_url": page.url,
            "captured": captured[-80:],
        }, ensure_ascii=False, indent=2))
        print("WROTE", OUT, "n=", len(captured))

    time.sleep(0.2)


if __name__ == "__main__":
    main()
