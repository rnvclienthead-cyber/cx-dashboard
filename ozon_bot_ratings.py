"""
Витринные рейтинги Ozon из списка товаров в кабинете продавца.

Браузер бота уже открыт в seller.ozon.ru. Читаем колонки «Рейтинг» и «Отзывы»
со страницы товаров, без запроса к карточке на ozon.ru.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VPS = os.getenv("OZON_BOT_VPS", "root@72.56.240.22")
OUT = ROOT / "ozon_bot_ratings.json"

_LIST_URL = "https://seller.ozon.ru/app/products?filter=all"

JS_READ_PAGE = """
() => {
  const headers = [...document.querySelectorAll('th')].map(th =>
    (th.innerText || '').replace(/\\s+/g, ' ').trim()
  );
  const find = (name) => headers.findIndex(h => h.startsWith(name));
  const iArt = find('Артикул');
  const iName = find('Название');
  const iRev = find('Отзывы');
  const iRate = find('Рейтинг');
  if (iArt < 0 || iRate < 0) return { error: 'no-headers', headers, rows: [], pages: [] };
  const table = [...document.querySelectorAll('table')]
    .sort((a, b) => b.querySelectorAll('tr').length - a.querySelectorAll('tr').length)[0];
  const rows = [];
  for (const tr of (table ? table.querySelectorAll('tr') : [])) {
    const cells = [...tr.children];
    const article = (cells[iArt] && cells[iArt].innerText || '').trim();
    if (!article || article.startsWith('Артикул')) continue;
    rows.push({
      article,
      name: (cells[iName] && cells[iName].innerText || '').trim(),
      reviews: (cells[iRev] && cells[iRev].innerText || '').trim(),
      rating: (cells[iRate] && cells[iRate].innerText || '').trim(),
    });
  }
  const labels = [...document.querySelectorAll('button')]
    .map(b => (b.innerText || '').trim());
  return { rows, pages: labels, headers };
}
"""


def parse_rating(text) -> float | None:
    raw = (text or "").strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not raw or raw in {"0", "0.0"}:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if value <= 0 or value > 5:
        return None
    return value


def parse_review_count(text) -> int:
    match = re.search(r"(\d[\d\s\u00a0\u202f]*)", text or "")
    if not match:
        return 0
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits or 0)


def parse_sku_and_article(text) -> tuple[int | None, str]:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    article = lines[0] if lines else ""
    match = re.search(r"SKU\s+(\d+)", text or "")
    sku = int(match.group(1)) if match else None
    return sku, article


def pager_pages(labels) -> list[int]:
    """Номера страниц списка: 1, 2, 3. Чип размера страницы (50) не берём."""
    nums = sorted({int(x) for x in labels if str(x).isdigit()})
    pages = []
    expect = 1
    for num in nums:
        if num == expect:
            pages.append(num)
            expect += 1
        elif pages:
            break
    return pages or [1]


def row_from_cabinet(raw: dict, report_date: str) -> dict | None:
    sku, article = parse_sku_and_article(raw.get("article") or "")
    rating = parse_rating(raw.get("rating") or "")
    if not sku or not article or rating is None:
        return None
    name_lines = [ln.strip() for ln in (raw.get("name") or "").splitlines() if ln.strip()]
    return {
        "report_date": report_date,
        "sku": sku,
        "supplier_article": article,
        "product_name": name_lines[0] if name_lines else "",
        "average_rating": rating,
        "review_count": parse_review_count(raw.get("reviews") or ""),
    }


def _snapshot(page) -> dict:
    return page.evaluate(JS_READ_PAGE)


def _wait_ready(page) -> dict:
    """Ждём, пока появятся строки и кнопки страниц. Иначе читается недогруженный список."""
    page.mouse.move(700, 450)
    last: dict = {}
    for _ in range(30):
        last = _snapshot(page)
        if len(last.get("rows") or []) >= 8 and len(pager_pages(last.get("pages") or [])) >= 2:
            return last
        page.wait_for_timeout(500)
    return last


def _harvest(page) -> dict:
    """Прокрутка окна дорисовывает строки. Копим их, пока не дойдём до конца страницы."""
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(300)
    page.mouse.move(700, 450)
    merged: dict[str, dict] = {}
    payload: dict = {}
    last_y = None
    for step in range(16):
        payload = _snapshot(page)
        for raw in payload.get("rows") or []:
            key = raw.get("article") or ""
            if key:
                merged[key] = raw
        y = page.evaluate("() => Math.round(window.scrollY)")
        limit = page.evaluate(
            "() => Math.max(0, document.documentElement.scrollHeight - window.innerHeight)"
        )
        at_end = limit > 200 and y >= limit - 8
        stuck = last_y is not None and y == last_y
        if len(merged) >= 48 or (step > 0 and (stuck or at_end)):
            break
        last_y = y
        page.mouse.wheel(0, 1100)
        page.wait_for_timeout(450)
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(400)
    top = _snapshot(page)
    return {
        "rows": list(merged.values()),
        "pages": top.get("pages") or payload.get("pages") or [],
        "error": None if merged else (top.get("error") or payload.get("error")),
    }


def _click_page(page, number: int) -> None:
    page.evaluate(
        """(n) => {
          const btn = [...document.querySelectorAll('button')]
            .find(b => (b.innerText || '').trim() === String(n));
          if (btn) btn.click();
        }""",
        number,
    )


def collect_ratings(page, skus: list[dict] | None = None) -> list[dict]:
    """Читает рейтинг из списка товаров кабинета. Отдельную вкладку закрывает."""
    del skus  # список берём со страницы кабинета, не из базы
    report_date = date.today().isoformat()
    tab = page.context.new_page()
    found: dict[int, dict] = {}
    try:
        tab.goto(_LIST_URL, wait_until="domcontentloaded", timeout=45000)
        ready = _wait_ready(tab)
        if ready.get("error") and not ready.get("rows"):
            print(f"ratings: нет таблицы ({ready.get('error')})", flush=True)
            return []
        first = _harvest(tab)
        pages = pager_pages(first.get("pages") or ready.get("pages") or [])
        print(f"ratings pages {pages}", flush=True)
        for number in pages:
            if number != 1:
                before = (first.get("rows") or [{}])[0].get("article")
                _click_page(tab, number)
                switched = False
                for _ in range(25):
                    tab.wait_for_timeout(400)
                    head = ((_snapshot(tab).get("rows") or [{}])[0]).get("article")
                    if head and head != before:
                        switched = True
                        break
                if not switched:
                    print(f"ratings page {number} не открылась", flush=True)
                    continue
                first = _harvest(tab)
            for raw in first.get("rows") or []:
                row = row_from_cabinet(raw, report_date)
                if row:
                    found[row["sku"]] = row
            print(
                f"ratings page {number} rows={len(first.get('rows') or [])} kept={len(found)}",
                flush=True,
            )
    finally:
        tab.close()
    return list(found.values())


def upload_ratings(rows: list[dict]) -> None:
    if not rows:
        return
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    remote = "/tmp/ozon_bot_ratings.json"
    subprocess.check_call(["scp", str(OUT), f"{VPS}:{remote}"])
    script = r'''
import json, os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
load_dotenv("/root/cx-dashboard/.env")
url = os.getenv("DATABASE_URL_LOCAL") or "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"
engine = create_engine(url, pool_pre_ping=True)
rows = json.load(open("/tmp/ozon_bot_ratings.json"))
with engine.begin() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS ozon_ratings_report (
            id SERIAL PRIMARY KEY,
            report_date DATE NOT NULL,
            sku BIGINT NOT NULL,
            supplier_article TEXT,
            product_name TEXT,
            average_rating DOUBLE PRECISION,
            review_count INTEGER,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (report_date, sku)
        )
    """))
    n = 0
    for r in rows:
        conn.execute(text("""
            INSERT INTO ozon_ratings_report (
                report_date, sku, supplier_article, product_name,
                average_rating, review_count, synced_at
            ) VALUES (
                :report_date, :sku, :supplier_article, :product_name,
                :average_rating, :review_count, NOW()
            )
            ON CONFLICT (report_date, sku) DO UPDATE SET
                supplier_article = COALESCE(EXCLUDED.supplier_article, ozon_ratings_report.supplier_article),
                product_name = COALESCE(EXCLUDED.product_name, ozon_ratings_report.product_name),
                average_rating = EXCLUDED.average_rating,
                review_count = EXCLUDED.review_count,
                synced_at = NOW()
        """), r)
        n += 1
print("ratings_upserted", n)
'''
    subprocess.check_call(
        [
            "ssh",
            VPS,
            "PYTHONPATH=/root/cx-dashboard /root/cx-dashboard/venv/bin/python - <<'PY'\n"
            + script
            + "\nPY",
        ]
    )


def collect_and_upload_ratings(page) -> int:
    rows = collect_ratings(page)
    upload_ratings(rows)
    return len(rows)


if __name__ == "__main__":
    from playwright.sync_api import sync_playwright

    cdp = os.getenv("OZON_BOT_CDP", "http://127.0.0.1:9222")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp)
        ctx = browser.contexts[0]
        anchor = ctx.pages[0] if ctx.pages else ctx.new_page()
        n = collect_and_upload_ratings(anchor)
        print("done", n)
