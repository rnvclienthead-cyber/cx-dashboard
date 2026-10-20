"""Лист InputAPIRustamWB: что сейчас надо принять на нашем ПВЗ.

Склад оформлен как пункт выдачи, возвраты везут туда.
В отчёте WB «Возвраты и перемещение товаров» это статус «Готов к выдаче».
«В пути в ПВЗ» и «Выдано» в лист не пишем.

Строка 10 — названия колонок, её не меняем. Данные пишем с 11-й строки.
Каждый запуск заново ставит текущий список: дата заказа, артикул, количество.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

from sqlalchemy import create_engine, text

SPREADSHEET_ID = "1I0Xcngh7IfuJuZ-3R4koYUnJSRbtZLvf5gYsymhDFYo"
INPUT_SHEET = "InputAPIRustamWB"
CATALOG_SHEET = "База_данных"
DATA_START = 11

ARRIVED_STATUSES = {
    "готов к выдаче",
    "прибыл на пвз",
}
ISSUED_STATUS = "выдано"


def normalize_status(value) -> str:
    text = str(value or "").replace("ё", "е").replace("Ё", "Е")
    return " ".join(text.lower().split())


def is_arrived_at_pvz(status) -> bool:
    return normalize_status(status) in ARRIVED_STATUSES


def order_day(value) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    if len(text) >= 10 and text[2] == "." and text[5] == ".":
        day, month, year = text[:2], text[3:5], text[6:10]
        if year.isdigit() and month.isdigit() and day.isdigit():
            return f"{year}-{month}-{day}"
    return ""


def nm_key(value) -> str:
    if value in (None, ""):
        return ""
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value).strip()


def load_seller_articles(values: list[list]) -> dict[str, str]:
    """Артикул WB (nmId) → артикул продавца."""
    if not values:
        return {}
    header = [str(cell).strip() for cell in values[0]]
    try:
        art_col = header.index("Артикул")
        nm_col = header.index("Артикул ВБ")
    except ValueError as exc:
        raise RuntimeError("На листе «База_данных» нет колонок «Артикул» и «Артикул ВБ»") from exc
    catalog = {}
    for row in values[1:]:
        if len(row) <= max(art_col, nm_col):
            continue
        article = str(row[art_col]).strip()
        nm_id = nm_key(row[nm_col])
        if article and nm_id and nm_id not in catalog:
            catalog[nm_id] = article
    return catalog


def seller_article(row: dict, catalog: dict[str, str]) -> str:
    code = str(row.get("vendor_code") or "").strip()
    if code:
        return code
    return catalog.get(nm_key(row.get("nm_id")), "")


def _group(pairs: list[tuple[str, str]]) -> list[tuple[str, str, int]]:
    counts: dict[tuple[str, str], int] = {}
    for day, article in pairs:
        counts[(day, article)] = counts.get((day, article), 0) + 1
    return [(day, article, qty) for (day, article), qty in sorted(counts.items())]


def aggregate_arrived(rows: list[dict], catalog: dict[str, str]) -> tuple[list[tuple[str, str, int]], list[str]]:
    """Сколько штук сейчас в статусе «прибыл на ПВЗ», без учёта памяти."""
    pairs = []
    skipped = []
    for row in rows:
        if not is_arrived_at_pvz(row.get("status")):
            continue
        day = order_day(row.get("order_dt"))
        article = seller_article(row, catalog)
        if not day or not article:
            key = nm_key(row.get("nm_id"))
            if key:
                skipped.append(key)
            continue
        pairs.append((day, article))
    return _group(pairs), skipped


def parse_sheet_rows(values: list[list]) -> list[tuple[str, str, int]]:
    """Строки листа начиная с 11-й: дата, артикул, количество."""
    parsed = []
    for row in values:
        if not row or not str(row[0]).strip():
            continue
        day = order_day(row[0])
        article = str(row[1]).strip() if len(row) > 1 else ""
        raw_qty = row[2] if len(row) > 2 else ""
        try:
            qty = int(float(str(raw_qty).replace(",", ".").strip()))
        except (TypeError, ValueError):
            continue
        if day and article and qty > 0:
            parsed.append((day, article, qty))
    return parsed


def merge_groups(existing: list[tuple[str, str, int]], deltas: list[tuple[str, str, int]]) -> list[tuple[str, str, int]]:
    """Старые строки остаются. Новые штуки прибавляются к той же дате и артикулу."""
    counts: dict[tuple[str, str], int] = {}
    for day, article, qty in list(existing) + list(deltas):
        if qty:
            key = (day, article)
            counts[key] = counts.get(key, 0) + int(qty)
    return [(day, article, qty) for (day, article), qty in sorted(counts.items()) if qty > 0]


def surplus_quantities(report_groups, sheet_groups) -> list[tuple[str, str, int]]:
    """Сколько штук из текущего «прибыл на ПВЗ» ещё нет на листе."""
    on_sheet: dict[tuple[str, str], int] = {}
    for day, article, qty in sheet_groups:
        on_sheet[(day, article)] = on_sheet.get((day, article), 0) + qty
    extra = []
    for day, article, qty in report_groups:
        add = qty - on_sheet.get((day, article), 0)
        if add > 0:
            extra.append((day, article, add))
    return extra


def _should_count(row: dict, prev: dict | None) -> bool:
    if prev and prev.get("counted"):
        return False
    if is_arrived_at_pvz(row.get("status")):
        return True
    # Успели забрать с ПВЗ между запусками: раньше товар ещё не прибыл, теперь «Выдано».
    if prev and normalize_status(row.get("status")) == ISSUED_STATUS:
        return not is_arrived_at_pvz(prev.get("status"))
    return False


def plan_updates(rows: list[dict], catalog: dict[str, str], known: dict[str, dict]):
    """Новые штуки к добавлению и полная память по srid из этого отчёта."""
    deltas_pairs = []
    skipped = []
    records = []
    seen = set()
    for row in rows:
        srid = str(row.get("srid") or "").strip()
        if not srid or srid in seen:
            continue
        seen.add(srid)
        prev = known.get(srid)
        status = str(row.get("status") or "").strip() or None
        day = order_day(row.get("order_dt"))
        article = seller_article(row, catalog)
        counted = bool(prev and prev.get("counted"))
        if _should_count(row, prev):
            if day and article:
                deltas_pairs.append((day, article))
                counted = True
            else:
                nm_id = nm_key(row.get("nm_id"))
                if nm_id:
                    skipped.append(nm_id)
        records.append({
            "srid": srid,
            "status": status,
            "counted": counted,
            "order_dt": day or None,
            "nm_id": nm_key(row.get("nm_id")) or None,
            "article": article or None,
        })
    return _group(deltas_pairs), records, skipped


def bootstrap_records(rows: list[dict], catalog: dict[str, str]) -> list[dict]:
    """Первый запуск: текущие «прибыл на ПВЗ» уже лежат на листе, помечаем их учтёнными."""
    records = []
    seen = set()
    for row in rows:
        srid = str(row.get("srid") or "").strip()
        if not srid or srid in seen:
            continue
        seen.add(srid)
        day = order_day(row.get("order_dt"))
        article = seller_article(row, catalog)
        arrived = is_arrived_at_pvz(row.get("status"))
        records.append({
            "srid": srid,
            "status": str(row.get("status") or "").strip() or None,
            "counted": bool(arrived and day and article),
            "order_dt": day or None,
            "nm_id": nm_key(row.get("nm_id")) or None,
            "article": article or None,
        })
    return records


def report_window(today: date | None = None) -> tuple[str, str]:
    end = today or date.today()
    start = end - timedelta(days=30)
    return start.isoformat(), end.isoformat()


def database_url() -> str:
    return (
        os.getenv("DATABASE_URL_LOCAL")
        or os.getenv("DATABASE_URL")
        or "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"
    )


def ensure_table(conn) -> None:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS wb_pvz_input_seen (
            srid TEXT PRIMARY KEY,
            status TEXT,
            counted BOOLEAN NOT NULL DEFAULT FALSE,
            order_dt DATE,
            nm_id BIGINT,
            article TEXT,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.commit()


def load_known(conn) -> dict[str, dict]:
    found = conn.execute(text("SELECT srid, status, counted FROM wb_pvz_input_seen")).fetchall()
    return {
        str(row.srid): {"status": row.status, "counted": bool(row.counted)}
        for row in found
    }


def save_seen(conn, records: list[dict]) -> None:
    if not records:
        return
    sql = text("""
        INSERT INTO wb_pvz_input_seen (srid, status, counted, order_dt, nm_id, article, updated_at)
        VALUES (:srid, :status, :counted, :order_dt, :nm_id, :article, NOW())
        ON CONFLICT (srid) DO UPDATE SET
            status = EXCLUDED.status,
            counted = wb_pvz_input_seen.counted OR EXCLUDED.counted,
            order_dt = COALESCE(EXCLUDED.order_dt, wb_pvz_input_seen.order_dt),
            nm_id = COALESCE(EXCLUDED.nm_id, wb_pvz_input_seen.nm_id),
            article = COALESCE(EXCLUDED.article, wb_pvz_input_seen.article),
            updated_at = NOW()
    """)
    for record in records:
        nm_id = record.get("nm_id") or None
        conn.execute(sql, {
            "srid": record["srid"],
            "status": record.get("status"),
            "counted": bool(record.get("counted")),
            "order_dt": record.get("order_dt") or None,
            "nm_id": int(nm_id) if nm_id else None,
            "article": record.get("article"),
        })
    conn.commit()


def write_data_rows(worksheet, groups: list[tuple[str, str, int]]) -> None:
    """Пишем только с 11-й строки. Строки 1–10 не читаем и не меняем."""
    clear_until = max(worksheet.row_count, DATA_START)
    worksheet.batch_clear([f"A{DATA_START}:C{clear_until}"])
    if not groups:
        return
    worksheet.update(
        [[day, article, qty] for day, article, qty in groups],
        f"A{DATA_START}",
        raw=False,
    )
    last = DATA_START + len(groups) - 1
    worksheet.batch_format([
        {
            "range": f"A{DATA_START}:A{last}",
            "format": {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}},
        },
        {
            "range": f"C{DATA_START}:C{last}",
            "format": {"numberFormat": {"type": "NUMBER", "pattern": "0"}},
        },
    ])


def read_data_rows(worksheet) -> list[tuple[str, str, int]]:
    values = worksheet.get(f"A{DATA_START}:C")
    return parse_sheet_rows(values)


def sync_sheet(rows: list[dict], *, worksheet, catalog: dict[str, str]) -> dict:
    """Переписывает данные с 11-й строки текущим списком «Готов к выдаче». Строки 1–10 не трогает."""
    groups, skipped = aggregate_arrived(rows, catalog)
    write_data_rows(worksheet, groups)
    return {
        "rows": len(groups),
        "units": sum(qty for _day, _article, qty in groups),
        "skipped": sorted(set(skipped)),
    }


def creds_path() -> str:
    path = os.getenv("GOOGLE_CREDS_PATH", "").strip()
    if path and os.path.exists(path):
        return path
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot_api_key.json")


def main() -> None:
    from dotenv import load_dotenv

    from backend import cron_lock
    from backend.wb_goods_return import fetch_report

    load_dotenv()
    engine = create_engine(database_url(), pool_pre_ping=True)
    lock_conn = cron_lock.acquire_or_exit(engine, "wb_pvz_input")
    try:
        import gspread

        date_from, date_to = report_window()
        print(f"Отчёт WB {date_from} … {date_to}")
        rows = fetch_report(date_from, date_to)
        sheet = gspread.service_account(filename=creds_path()).open_by_key(SPREADSHEET_ID)
        catalog = load_seller_articles(sheet.worksheet(CATALOG_SHEET).get("A:Z"))
        result = sync_sheet(
            rows,
            worksheet=sheet.worksheet(INPUT_SHEET),
            catalog=catalog,
        )
        print(
            f"Лист «{INPUT_SHEET}»: {result['rows']} строк с 11-й, "
            f"{result['units']} штук к приёмке, без артикула {len(result['skipped'])}"
        )
        if result["skipped"]:
            print("Нет артикула продавца для WB:", ", ".join(result["skipped"][:20]))
    finally:
        lock_conn.close()


if __name__ == "__main__":
    main()
