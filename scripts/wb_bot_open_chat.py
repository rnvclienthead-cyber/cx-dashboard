#!/usr/bin/env python3
"""Открыть чат WB по номеру заявки в залогиненном Chrome кабинета (CDP :9224).

Пишет одну JSON-строку в stdout: {"ok": true, "chat_id": "1:uuid"}
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.wb_cabinet_bot import chat_id_from_cabinet_url  # noqa: E402

CDP = os.getenv("WB_BOT_CDP", "http://127.0.0.1:9224")
LOCK = Path(os.getenv("WB_BOT_LOCK", "/tmp/wb_bot_open_chat.lock"))
CLAIMS_URL = (
    "https://seller.wildberries.ru/suppliers-product-verification"
    "/customer-claims/under-consideration"
)
ARCHIVE_URL = (
    "https://seller.wildberries.ru/suppliers-product-verification"
    "/customer-claims/archive"
)


def _out(payload: dict, code: int = 0) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    raise SystemExit(code)


def _seller_page(ctx):
    for pg in ctx.pages:
        url = pg.url or ""
        if "seller.wildberries.ru" in url and "chrome://" not in url and "sw.js" not in url:
            return pg
    page = ctx.new_page()
    page.goto(CLAIMS_URL, timeout=60000)
    return page


def _dismiss(page) -> None:
    for name in ("Согласен", "Хорошо", "I Agree", "OK"):
        try:
            page.get_by_role("button", name=name).click(timeout=800)
            return
        except Exception:
            continue


def _force_russian(page) -> None:
    page.evaluate(
        """() => {
          const exp = "max-age=31536000; path=/";
          for (const name of ["locale", "external-locale"]) {
            document.cookie = name + "=ru; " + exp;
            document.cookie = name + "=ru; domain=.wildberries.ru; " + exp;
            document.cookie = name + "=ru; domain=seller.wildberries.ru; " + exp;
          }
        }"""
    )


def _search_box(page):
    """Поле «Номер заявки»: у инпута нет placeholder, подпись рядом."""
    loc = page.locator("input[type='text']")
    if loc.count():
        return loc.first
    return page.locator("input").first


def _fill_search(page, claim: str) -> None:
    box = _search_box(page)
    box.click(timeout=10000)
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    box.press_sequentially(claim, delay=8)
    page.keyboard.press("Enter")


def _wait_row(page, claim: str, timeout_ms: int = 8000):
    loc = page.locator("tr").filter(has_text=claim)
    try:
        loc.first.wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        return None
    return loc if loc.count() else None


def _close_extra(ctx) -> None:
    for pg in list(ctx.pages):
        url = pg.url or ""
        if "chat-with-clients" in url or "omnibox" in url:
            try:
                pg.close()
            except Exception:
                pass


def _park(ctx) -> None:
    """После работы бота не оставлять живой кабинет — он жрёт CPU 24/7."""
    pages = list(ctx.pages)
    kept = None
    for pg in pages:
        try:
            if kept is None:
                kept = pg
                pg.goto("about:blank", timeout=15000, wait_until="domcontentloaded")
            else:
                pg.close()
        except Exception:
            pass


def _chat_id_from_pages(ctx) -> str:
    for pg in ctx.pages:
        cid = chat_id_from_cabinet_url(pg.url or "")
        if cid:
            return cid
    return ""


def _open_chat_from_row(ctx, page, row) -> str:
    """Клик по иконке чата. Новая вкладка часто рождается с пустым URL — ждём chatId."""
    btn = row.first.locator("button").first
    btn.wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(250)
    btn.click(timeout=6000)
    t0 = time.time()
    js_tried = False
    while time.time() - t0 < 12:
        chat_id = _chat_id_from_pages(ctx)
        if chat_id:
            return chat_id
        if not js_tried and time.time() - t0 >= 1.6:
            try:
                btn.evaluate("el => el.click()")
            except Exception:
                pass
            js_tried = True
        time.sleep(0.15)
    return chat_id_from_cabinet_url(page.url or "")


def run(claim: str, *, archive: bool = False) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP)
        except Exception as e:
            return {"ok": False, "error": f"Chrome кабинета WB не запущен: {e}"}
        if not browser.contexts:
            return {"ok": False, "error": "в Chrome кабинета WB нет окна — войдите в ЛК"}
        ctx = browser.contexts[0]
        try:
            _close_extra(ctx)
            page = _seller_page(ctx)
            _force_russian(page)
            target = ARCHIVE_URL if archive else CLAIMS_URL
            need = "archive" if archive else "under-consideration"
            if need not in (page.url or ""):
                page.goto(target, timeout=60000, wait_until="domcontentloaded")
            _dismiss(page)

            _fill_search(page, claim)
            row = _wait_row(page, claim, 7000)
            if row is None:
                where = "архиве" if archive else "активных"
                return {"ok": False, "error": f"заявка не нашлась в {where} WB по номеру"}

            chat_id = _open_chat_from_row(ctx, page, row)
            if not chat_id:
                return {"ok": False, "error": "кабинет не открыл чат по этой заявке"}
            _close_extra(ctx)
            return {"ok": True, "chat_id": chat_id}
        finally:
            _park(ctx)


def main() -> None:
    args = [a for a in sys.argv[1:] if a]
    claim = ""
    archive = False
    wait_lock = False
    for a in args:
        if a in ("--archive", "archive"):
            archive = True
        elif a in ("--active", "active"):
            archive = False
        elif a == "--wait":
            wait_lock = True
        elif not a.startswith("-"):
            claim = a
    if not claim:
        _out({"ok": False, "error": "нет номера заявки"}, 2)
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as fh:
        deadline = time.time() + 90 if wait_lock else 0
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not wait_lock or time.time() >= deadline:
                    _out({
                        "ok": False,
                        "busy": True,
                        "error": "бот WB уже открывает другой чат — подождите пару секунд",
                    }, 1)
                time.sleep(0.4)
        result = run(claim, archive=archive)
        _out(result, 0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
