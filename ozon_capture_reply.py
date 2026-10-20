"""Перехват ответа: хук на список + на карточку отзыва (новая страница/оверлей)."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "ozon_reply_capture.json"
LOG = ROOT / "ozon_reply_capture.log"
CDP = "http://127.0.0.1:9222"

HOOK = r"""
() => {
  if (window.__ozonReplyHook) return 'already';
  window.__ozonReplyHook = true;
  window.__ozonReplyHits = window.__ozonReplyHits || [];
  const push = (item) => {
    try {
      window.__ozonReplyHits.push(item);
      if (window.__ozonReplyHits.length > 300) window.__ozonReplyHits.shift();
    } catch (e) {}
  };
  const wrap = (method, url, body, status, respText) => {
    const u = String(url || '');
    if (!(u.includes('ozon.ru') || u.startsWith('/'))) return;
    const low = u.toLowerCase();
    const m = String(method || 'GET').toUpperCase();
    const interesting =
      ['POST','PUT','PATCH'].includes(m) ||
      low.includes('comment') || low.includes('answer') ||
      low.includes('reply') || low.includes('review');
    if (!interesting) return;
    push({
      ts: new Date().toISOString(),
      method: m,
      url: u,
      body: body == null ? null : String(body).slice(0, 5000),
      status: status == null ? null : status,
      response: respText == null ? null : String(respText).slice(0, 3000),
    });
  };
  const origFetch = window.fetch;
  window.fetch = async function(input, init) {
    const method = (init && init.method) || (input && input.method) || 'GET';
    const url = (typeof input === 'string') ? input : (input && input.url);
    const body = init && init.body;
    const resp = await origFetch.apply(this, arguments);
    let text = null;
    try { text = await resp.clone().text(); } catch (e) {}
    wrap(method, url, body, resp.status, text);
    return resp;
  };
  const OrigXHR = window.XMLHttpRequest;
  function XHR() {
    const xhr = new OrigXHR();
    let method = 'GET', url = '', body = null;
    const open = xhr.open;
    xhr.open = function(m, u) { method = m; url = u; return open.apply(xhr, arguments); };
    const send = xhr.send;
    xhr.send = function(b) {
      body = b;
      xhr.addEventListener('loadend', function() {
        wrap(method, url, body, xhr.status, xhr.responseText);
      });
      return send.apply(xhr, arguments);
    };
    return xhr;
  }
  window.XMLHttpRequest = XHR;
  return 'hooked';
}
"""


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def inject_all(ctx) -> None:
    for page in ctx.pages:
        try:
            if "seller.ozon.ru" not in (page.url or ""):
                continue
            page.add_init_script(HOOK)
            st = page.evaluate(HOOK)
            log(f"hook {st}: {page.url[:120]}")
        except Exception as e:
            log(f"inject err: {e}")


def collect_hits(ctx) -> list:
    all_hits = []
    for page in ctx.pages:
        try:
            if "seller.ozon.ru" not in (page.url or ""):
                continue
            hits = page.evaluate("() => window.__ozonReplyHits || []")
            for h in hits:
                h["_page"] = page.url[:160]
                all_hits.append(h)
        except Exception:
            pass
    return all_hits


def is_success(h: dict) -> bool:
    """Только создание ответа, не list/detail."""
    u = (h.get("url") or "").lower()
    b = (h.get("body") or "").lower()
    if h.get("method") != "POST":
        return False
    if h.get("status") not in (200, 201):
        return False
    # явный create / answer
    if any(x in u for x in ("comment/create", "comment/add", "/answer", "reply/create")):
        return bool(h.get("body"))
    # POST comment без list/detail, с текстом ответа
    if "comment" in u and "list" not in u and "detail" not in u:
        if "text" in b or "тест" in b or "test" in b or "спасибо" in b:
            return True
    return False


def main() -> None:
    LOG.write_text("", encoding="utf-8")
    log("READY — сделайте: 1) клик по тексту отзыва 2) введите ответ 3) кнопка самолётик")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        inject_all(ctx)

        seen_urls = set()
        deadline = time.time() + 25 * 60
        last_inject = 0.0
        while time.time() < deadline:
            now = time.time()
            if now - last_inject > 3:
                inject_all(ctx)
                last_inject = now

            hits = collect_hits(ctx)
            # dedupe by method+url+body+status
            uniq = []
            keys = set()
            for h in hits:
                k = (h.get("method"), h.get("url"), h.get("body"), h.get("status"))
                if k in keys:
                    continue
                keys.add(k)
                uniq.append(h)

            for h in uniq:
                key = f"{h.get('method')}|{h.get('url')}|{h.get('status')}|{(h.get('body') or '')[:80]}"
                if key not in seen_urls:
                    seen_urls.add(key)
                    log(f"→ {h.get('method')} {h.get('status')} {h.get('url')}")
                    if h.get("body"):
                        log(f"  body: {str(h.get('body'))[:500]}")

            OUT.write_text(json.dumps(uniq, ensure_ascii=False, indent=2), encoding="utf-8")

            if any(is_success(h) for h in uniq):
                log("CAPTURE OK")
                return
            time.sleep(1)
        log("TIMEOUT")


if __name__ == "__main__":
    main()
