"""Снимок вкладки WB DBW «В доставке» в Google Sheets.

Важно про API WB: отдельных методов «все на сборке» / «все в доставке» нет.
Есть только:
  - GET /dbw/orders/new  — новые
  - GET /dbw/orders      — уже завершённые (проданы / отменены) за период
  - POST /status,/courier,/delivery-date — по известным ID

Поэтому каждый новый заказ мы сразу берём на учёт (до записи в Sheets).
Заказы, которые уже ушли на сборку (confirm) до того, как мы их увидели
в /new, API не отдаст — их можно добрать только выгрузкой из кабинета.

Таблица «В доставке» каждый запуск обновляется.
Заказы, которые ушли из доставки, не удаляются — переносятся в лист «Архив».
Лист «История курьеров» копит замены курьеров с датами.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from backend import cron_lock, wb_client

load_dotenv()
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

MARKETPLACE_BASE = "https://marketplace-api.wildberries.ru"
SPREADSHEET_ID = os.getenv(
    "WB_DBW_SHEET_ID",
    "1aLeFFmXHgXzf4pp2JobSNzP9gf_er3U33xFuU7_2xUo",
)
GOOGLE_CREDS_PATH = os.getenv("GOOGLE_CREDS_PATH", "/root/cx-dashboard/bot_api_key.json")
DB_URL = os.getenv(
    "DATABASE_URL_LOCAL",
    "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard",
)
LOOKBACK_DAYS = 30
REQUEST_PAUSE_SEC = 0.2
BATCH_SIZE = 1000
IN_DELIVERY_STATUS = "complete"
# Во вкладке кабинета «В доставке» не показывают отменённые клиентом,
# даже если supplierStatus ещё complete. 551 waiting + 1 postponed = 552.
ACTIVE_DELIVERY_WB_STATUSES = {"waiting", "postponed_delivery"}
TERMINAL_STATUSES = {
    "receive",
    "reject",
    "cancel",
    "cancel_missed_call",
}
ARCHIVE_STATUS_RU = {
    "receive": "Получен покупателем",
    "reject": "Отказ при получении",
    "cancel": "Отменён продавцом",
    "cancel_missed_call": "Отмена: не дозвон",
}
WB_ARCHIVE_STATUS_RU = {
    "sold": "Получен покупателем",
    "canceled": "Отменён",
    "canceled_by_client": "Отменён покупателем",
    "declined_by_client": "Отказ покупателя",
}
HEADER = [
    "Заказ",
    "Стикер",
    "Артикул",
    "Наименование",
    "Доставим покупателю",
    "Статус",
    "Данные курьера",
    "Смена курьера",
]
ARCHIVE_HEADER = HEADER + ["Дата в архиве"]
HISTORY_HEADER = [
    "Заказ",
    "ФИО",
    "Телефон",
    "Машина",
    "Актуален с",
    "Актуален до",
    "Примечание",
]
ARCHIVE_SHEET_NAMES = ("Архив", "архив")
MSK = ZoneInfo("Europe/Moscow")
COURIER_REPLACED_LABEL = "Заменили и данные изменились"
DELIVERY_TABLE_ID = "dbwInDelivery"
DELIVERY_TABLE_NAME = "ВДоставке"
HISTORY_TABLE_ID = "dbwCourierHistory"
ARCHIVE_TABLE_ID = "dbwArchive"
COURIER_LOOKUP_SHEET = "Поиск курьера"
COURIER_COL_IDX = HEADER.index("Данные курьера")
STATUS_COL_IDX = HEADER.index("Статус")
STICKER_COL_IDX = HEADER.index("Стикер")
DELIVERY_COL_WIDTHS = [120, 130, 140, 280, 170, 160, 360, 260]
ARCHIVE_COL_WIDTHS = DELIVERY_COL_WIDTHS + [140]
# Формула: мгновенный список при вводе ФИО в B1 (локаль RU — разделитель ;).
# Курьер — колонка G после добавления «Стикер».
COURIER_LOOKUP_FORMULA = (
    '=IF($B$1="";"";IFERROR('
    "FILTER('В доставке'!A2:H;ISNUMBER(SEARCH($B$1;'В доставке'!G2:G)));"
    '"Нет заказов по этому курьеру"))'
)
COURIER_LOOKUP_COUNT_FORMULA = (
    '=IF($B$1="";0;IFERROR('
    "ROWS(FILTER('В доставке'!A2:A;ISNUMBER(SEARCH($B$1;'В доставке'!G2:G))));"
    "0))"
)


def _chunks(items: list, size: int = BATCH_SIZE):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _request(method: str, path: str, *, json_body=None, params=None, timeout: int = 40):
    url = f"{MARKETPLACE_BASE}{path}"
    last_error = None
    for attempt in range(4):
        try:
            resp = requests.request(
                method,
                url,
                headers=wb_client.auth_headers(),
                json=json_body,
                params=params,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(REQUEST_PAUSE_SEC * (attempt + 1))
            continue
        if resp.status_code == 429:
            time.sleep(5 * (attempt + 1))
            continue
        resp.raise_for_status()
        time.sleep(REQUEST_PAUSE_SEC)
        return resp.json() if resp.content else {}
    if last_error:
        raise last_error
    raise RuntimeError(f"WB {method} {path}: слишком много 429")


def fetch_new_orders() -> list[dict]:
    data = _request("GET", "/api/v3/dbw/orders/new")
    return list(data.get("orders") or [])


def fetch_period_orders(date_from: int, date_to: int) -> list[dict]:
    orders: list[dict] = []
    next_v = 0
    while True:
        data = _request(
            "GET",
            "/api/v3/dbw/orders",
            params={"limit": 1000, "next": next_v, "dateFrom": date_from, "dateTo": date_to},
        )
        chunk = list(data.get("orders") or [])
        orders.extend(chunk)
        next_v = int(data.get("next") or 0)
        if len(chunk) < 1000 or not next_v:
            break
    return orders


def fetch_statuses(order_ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for chunk in _chunks(order_ids):
        data = _request("POST", "/api/v3/dbw/orders/status", json_body={"orders": chunk})
        for row in data.get("orders") or []:
            oid = row.get("id")
            if oid is not None:
                out[int(oid)] = row
    return out


def fetch_couriers(order_ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    if not order_ids:
        return out
    for chunk in _chunks(order_ids):
        data = _request("POST", "/api/v3/dbw/orders/courier", json_body={"orders": chunk})
        for row in data.get("orders") or []:
            oid = row.get("orderID") or row.get("id")
            if oid is not None:
                out[int(oid)] = row.get("courierInfo") or {}
    return out


def fetch_delivery_dates(order_ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    if not order_ids:
        return out
    for chunk in _chunks(order_ids):
        data = _request("POST", "/api/v3/dbw/orders/delivery-date", json_body={"orders": chunk})
        for row in data.get("orders") or []:
            oid = row.get("id")
            if oid is not None:
                out[int(oid)] = row
    return out


def format_sticker_number(part_a, part_b) -> str:
    a = str(part_a or "").strip()
    b = str(part_b or "").strip()
    return f"{a}{b}" if (a or b) else ""


def fetch_stickers(order_ids: list[int]) -> dict[int, str]:
    """Номера стикеров (partA+partB). API: максимум 100 ID за запрос.

    Обязательны query-параметры type/width/height — без них WB отвечает 400.
    """
    out: dict[int, str] = {}
    if not order_ids:
        return out
    params = {"type": "png", "width": 40, "height": 30}
    for chunk in _chunks(order_ids, 100):
        try:
            data = _request(
                "POST",
                "/api/v3/dbw/orders/stickers",
                json_body={"orders": chunk},
                params=params,
            )
        except Exception as exc:
            print(f"stickers batch({len(chunk)}): {exc}")
            continue
        for row in data.get("stickers") or []:
            oid = row.get("orderId") or row.get("id")
            if oid is None:
                continue
            num = format_sticker_number(row.get("partA"), row.get("partB"))
            if num:
                out[int(oid)] = num
    return out


def merge_order_meta(*groups: list[dict]) -> dict[int, dict]:
    by_id: dict[int, dict] = {}
    for group in groups:
        for row in group:
            oid = row.get("id")
            if oid is None:
                continue
            by_id[int(oid)] = row
    return by_id


def format_phone(raw) -> str:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not digits:
        return ""
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits.startswith("7"):
        return f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return "+" + digits if not str(raw).startswith("+") else str(raw)


def format_delivery(row: dict | None) -> str:
    if not row:
        return ""
    date_raw = str(row.get("dDate") or "").strip()
    date_txt = ""
    if date_raw:
        try:
            date_txt = datetime.strptime(date_raw[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError:
            date_txt = date_raw
    t_from = str(row.get("dTimeFrom") or "").strip()
    t_to = str(row.get("dTimeTo") or "").strip()
    if t_from and t_to:
        time_txt = f"{t_from}–{t_to}"
    else:
        time_txt = t_from or t_to
    if date_txt and time_txt:
        return f"{date_txt}, {time_txt}"
    return date_txt or time_txt or ""


def courier_contacts(info: dict | None) -> dict:
    info = info or {}
    contacts = info.get("contacts") or {}
    return {
        "fullName": str(contacts.get("fullName") or "").strip(),
        "phone": "".join(ch for ch in str(contacts.get("phone") or "") if ch.isdigit()),
        "carNumber": str(contacts.get("carNumber") or "").strip(),
        "updatedAt": str(info.get("updatedAt") or "").strip(),
        "mustBeAssigned": bool(info.get("mustBeAssigned")),
    }


def courier_fingerprint(info: dict | None) -> str:
    c = courier_contacts(info)
    if not (c["fullName"] or c["phone"] or c["carNumber"]):
        return ""
    return f"{c['fullName']}|{c['phone']}|{c['carNumber']}"


def format_courier(info: dict | None) -> str:
    c = courier_contacts(info)
    parts = []
    if c["fullName"]:
        parts.append(c["fullName"])
    phone = format_phone(c["phone"])
    if phone:
        parts.append(phone)
    if c["carNumber"]:
        parts.append(c["carNumber"])
    if parts:
        return ", ".join(parts)
    if c["mustBeAssigned"]:
        return "Ждём данные курьера"
    return ""


def format_msk(dt_raw: str | None) -> str:
    raw = str(dt_raw or "").strip()
    if not raw:
        return ""
    try:
        if raw.endswith("Z"):
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=MSK)
        return dt.astimezone(MSK).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return raw


def is_active_in_delivery(supplier_status: str, wb_status: str) -> bool:
    if (supplier_status or "").strip() != IN_DELIVERY_STATUS:
        return False
    wb = (wb_status or "").strip()
    if not wb:
        return True
    return wb in ACTIVE_DELIVERY_WB_STATUSES


def format_status(
    supplier_status: str,
    wb_status: str,
    courier_info: dict | None,
    previous_ui_status: str | None = None,
) -> str:
    """Статусы как во вкладке кабинета.

    previous_ui_status больше не «залипает» на «Ожидает курьера», если курьер
    уже есть: иначе после первого ожидания статус не переходит во «В доставке».
    """
    if not is_active_in_delivery(supplier_status, wb_status):
        return ""
    info = courier_info or {}
    contacts = info.get("contacts") or {}
    has_contacts = bool(
        str(contacts.get("fullName") or "").strip()
        or str(contacts.get("phone") or "").strip()
        or str(contacts.get("carNumber") or "").strip()
    )
    if (wb_status or "").strip() == "postponed_delivery":
        return "Дата вручения изменилась, товар у курьера"
    if not has_contacts:
        return "Ожидает курьера"
    return "В доставке"


def format_courier_change(replaced: bool, updated_at: str | None) -> str:
    stamp = format_msk(updated_at)
    if replaced:
        return f"{COURIER_REPLACED_LABEL}" + (f" · {stamp}" if stamp else "")
    if stamp:
        return f"Обновлено {stamp}"
    return ""


def _delivery_sort_key(row: dict) -> tuple:
    date_raw = str((row.get("delivery") or {}).get("dDate") or "")
    status_rank = 0 if row.get("ui_status") == "Ожидает курьера" else 1
    return (date_raw or "9999-99-99", status_rank, int(row["order_id"]))


def build_sheet_rows(in_delivery: list[dict]) -> list[list]:
    rows = []
    for item in sorted(in_delivery, key=_delivery_sort_key):
        rows.append([
            str(item["order_id"]),
            item.get("sticker") or "",
            item.get("article") or "",
            item.get("name") or item.get("article") or "",
            format_delivery(item.get("delivery")),
            item.get("ui_status") or "",
            format_courier(item.get("courier")),
            item.get("courier_change") or "",
        ])
    return rows


def build_history_sheet_rows(history: list[dict]) -> list[list]:
    rows = []
    for item in history:
        rows.append([
            str(item["order_id"]),
            item.get("full_name") or "",
            format_phone(item.get("phone")),
            item.get("car_number") or "",
            format_msk(item.get("started_at")),
            format_msk(item.get("ended_at")) if item.get("ended_at") else "сейчас",
            item.get("note") or "",
        ])
    return rows


def find_header_row(values: list[list]) -> int:
    """Индекс строки с заголовком «Заказ» (после строки актуальности, если есть)."""
    for i, row in enumerate(values or []):
        if not row:
            continue
        if str(row[0] or "").strip().lower() in {"заказ", "№ задания"}:
            return i
    return 0


def migrate_delivery_cells(cells: list, header_cells: list[str]) -> list:
    """Подгоняет старые строки листа под актуальный HEADER (стикер / артикул)."""
    cells = list(cells)
    has_article = "артикул" in header_cells
    has_sticker = "стикер" in header_cells
    if has_sticker and has_article:
        return cells
    if not has_sticker and has_article:
        return [cells[0], ""] + cells[1:]
    if not has_sticker and not has_article:
        return [cells[0], "", ""] + cells[1:]
    # есть стикер, нет артикула
    sticker = cells[1] if len(cells) > 1 else ""
    return [cells[0], sticker, ""] + cells[2:]


def sheet_rows_to_map(values: list[list]) -> dict[str, list]:
    """order_id → row values without header (и без старой строки актуальности)."""
    out: dict[str, list] = {}
    if not values:
        return out
    header_idx = find_header_row(values)
    header_cells = [str(c or "").strip().lower() for c in (values[header_idx] or [])]
    for row in values[header_idx + 1:]:
        if not row:
            continue
        oid = "".join(ch for ch in str(row[0] or "") if ch.isdigit())
        if not oid:
            continue
        cells = migrate_delivery_cells(row, header_cells)
        padded = list(cells) + [""] * (len(HEADER) - len(cells))
        out[oid] = padded[:len(HEADER)]
    return out


def archive_status_label(supplier_status: str, wb_status: str = "") -> str:
    supplier_status = (supplier_status or "").strip()
    wb_status = (wb_status or "").strip()
    if supplier_status in ARCHIVE_STATUS_RU:
        return ARCHIVE_STATUS_RU[supplier_status]
    if wb_status in WB_ARCHIVE_STATUS_RU:
        return WB_ARCHIVE_STATUS_RU[wb_status]
    return supplier_status or wb_status or "Архив"


def is_terminal_status(supplier_status: str) -> bool:
    return (supplier_status or "").strip() in TERMINAL_STATUSES


# Кабинет убирает из «В доставке», хотя supplierStatus ещё complete.
_LEFT_DELIVERY_WB = {"canceled_by_client", "canceled", "declined_by_client", "sold"}


def should_archive_order(supplier_status: str, wb_status: str) -> bool:
    if is_terminal_status(supplier_status):
        return True
    supplier = (supplier_status or "").strip()
    wb = (wb_status or "").strip()
    return supplier == IN_DELIVERY_STATUS and wb in _LEFT_DELIVERY_WB


def _col_letter(n: int) -> str:
    """1 → A, 26 → Z, 27 → AA."""
    n = max(int(n), 1)
    letters = []
    while n:
        n, rem = divmod(n - 1, 26)
        letters.append(chr(65 + rem))
    return "".join(reversed(letters))


def pick_rows_to_archive(
    previous_rows: dict[str, list],
    current_ids: set[str],
    already_archived: set[str],
    statuses: dict[int, dict] | None = None,
) -> list[list]:
    """В архив: финальный supplierStatus ИЛИ кабинет уже убрал заказ из доставки.

    Нельзя архивировать просто потому, что заказа не оказалось в текущем
    снимке (импорт/пробелы API) — иначе живые «В доставке» улетают зря.
    """
    statuses = statuses or {}
    stamp = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    out = []
    for oid, row in previous_rows.items():
        if oid in current_ids or oid in already_archived:
            continue
        try:
            oid_int = int(oid)
        except ValueError:
            continue
        st = statuses.get(oid_int) or {}
        supplier = str(st.get("supplierStatus") or "")
        wb = str(st.get("wbStatus") or "")
        if not should_archive_order(supplier, wb):
            continue
        archived = list(row[:len(HEADER)]) + [stamp]
        while len(archived) < len(ARCHIVE_HEADER):
            archived.append("")
        archived[STATUS_COL_IDX] = archive_status_label(supplier, wb)
        out.append(archived[:len(ARCHIVE_HEADER)])
    return out


def collect_in_delivery(
    *,
    new_orders: list[dict],
    period_orders: list[dict],
    tracked: list[dict],
    statuses: dict[int, dict],
    couriers: dict[int, dict],
    delivery_dates: dict[int, dict],
    names: dict[str, str],
    courier_meta: dict[int, dict] | None = None,
    previous_ui_statuses: dict[int, str] | None = None,
    stickers: dict[int, str] | None = None,
) -> list[dict]:
    meta = merge_order_meta(tracked, period_orders, new_orders)
    courier_meta = courier_meta or {}
    previous_ui_statuses = previous_ui_statuses or {}
    stickers = stickers or {}
    result = []
    for oid, status_row in statuses.items():
        supplier = status_row.get("supplierStatus") or ""
        wb = status_row.get("wbStatus") or ""
        if not is_active_in_delivery(supplier, wb):
            continue
        order = meta.get(oid) or {}
        article = str(order.get("article") or "").strip()
        courier = couriers.get(oid) or {}
        prev_ui = previous_ui_statuses.get(oid) or str(order.get("ui_status") or "").strip()
        ui_status = format_status(
            supplier,
            wb,
            courier,
            previous_ui_status=prev_ui,
        )
        cm = courier_meta.get(oid) or {}
        sticker = (
            stickers.get(oid)
            or str(order.get("sticker") or "").strip()
            or ""
        )
        result.append({
            "order_id": oid,
            "sticker": sticker,
            "article": article,
            "name": names.get(article) or order.get("name") or article,
            "supplier_status": supplier,
            "wb_status": wb,
            "courier": courier,
            "delivery": delivery_dates.get(oid) or order.get("delivery") or {},
            "ui_status": ui_status,
            "courier_change": format_courier_change(
                bool(cm.get("replaced")),
                cm.get("updated_at") or courier_contacts(courier).get("updatedAt"),
            ),
        })
    return result


def next_tracked_ids(statuses: dict[int, dict], known_ids: list[int]) -> list[int]:
    """Оставляем живые задания, чтобы не потерять их между /new и завершением."""
    keep = []
    for oid in known_ids:
        status = (statuses.get(oid) or {}).get("supplierStatus") or ""
        if status in TERMINAL_STATUSES:
            continue
        keep.append(oid)
    return keep


def ensure_tables(conn) -> None:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS wb_dbw_tracked_orders (
            order_id BIGINT PRIMARY KEY,
            article TEXT,
            nm_id BIGINT,
            created_at TIMESTAMPTZ,
            supplier_status TEXT,
            wb_status TEXT,
            ui_status TEXT,
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(text("""
        ALTER TABLE wb_dbw_tracked_orders
        ADD COLUMN IF NOT EXISTS ui_status TEXT
    """))
    conn.execute(text("""
        ALTER TABLE wb_dbw_tracked_orders
        ADD COLUMN IF NOT EXISTS sticker TEXT
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS wb_dbw_seen_new (
            order_id BIGINT PRIMARY KEY,
            article TEXT,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS wb_dbw_courier_history (
            id BIGSERIAL PRIMARY KEY,
            order_id BIGINT NOT NULL,
            fingerprint TEXT NOT NULL,
            full_name TEXT,
            phone TEXT,
            car_number TEXT,
            started_at TIMESTAMPTZ NOT NULL,
            ended_at TIMESTAMPTZ,
            note TEXT,
            source_updated_at TEXT
        )
    """))
    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_wb_dbw_courier_history_order
        ON wb_dbw_courier_history (order_id, started_at DESC)
    """))
    conn.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_wb_dbw_courier_history_active
        ON wb_dbw_courier_history (order_id)
        WHERE ended_at IS NULL
    """))


def ingest_new_orders_early(conn, new_orders: list[dict]) -> int:
    """Сразу фиксируем ID из /new в БД — до любых запросов к Google Sheets.

    Дыра, из‑за которой терялись заказы: задание уходит из /new в confirm
    (сборка), а API не отдаёт список «на сборке». Если ID не успели записать,
    после перевода в доставку заказ есть в ЛК, но не у нас.
    """
    ensure_tables(conn)
    added = 0
    for row in new_orders:
        oid = row.get("id")
        if oid is None:
            continue
        oid = int(oid)
        article = str(row.get("article") or "").strip() or None
        created = row.get("createdAt") or row.get("created_at")
        seen = conn.execute(text(
            "SELECT 1 FROM wb_dbw_seen_new WHERE order_id = :oid"
        ), {"oid": oid}).first()
        conn.execute(text("""
            INSERT INTO wb_dbw_seen_new (order_id, article, first_seen_at, last_seen_at)
            VALUES (:order_id, :article, NOW(), NOW())
            ON CONFLICT (order_id) DO UPDATE SET
                article = COALESCE(EXCLUDED.article, wb_dbw_seen_new.article),
                last_seen_at = NOW()
        """), {"order_id": oid, "article": article})
        conn.execute(text("""
            INSERT INTO wb_dbw_tracked_orders
                (order_id, article, nm_id, created_at, supplier_status, wb_status, last_seen_at)
            VALUES
                (:order_id, :article, :nm_id, :created_at, 'new', :wb_status, NOW())
            ON CONFLICT (order_id) DO UPDATE SET
                article = COALESCE(EXCLUDED.article, wb_dbw_tracked_orders.article),
                nm_id = COALESCE(EXCLUDED.nm_id, wb_dbw_tracked_orders.nm_id),
                created_at = COALESCE(wb_dbw_tracked_orders.created_at, EXCLUDED.created_at),
                last_seen_at = NOW()
        """), {
            "order_id": oid,
            "article": article,
            "nm_id": row.get("nmId") or row.get("nm_id"),
            "created_at": created,
            "wb_status": "waiting",
        })
        if not seen:
            added += 1
    return added


def load_tracked(conn) -> list[dict]:
    rows = conn.execute(text(
        "SELECT order_id AS id, article, nm_id, created_at, ui_status, sticker "
        "FROM wb_dbw_tracked_orders"
    )).mappings().all()
    return [dict(row) for row in rows]


def load_seen_new(conn) -> list[dict]:
    """Все ID, которые хоть раз мелькали в /new — страховка на этапе сборки."""
    rows = conn.execute(text(
        "SELECT order_id AS id, article FROM wb_dbw_seen_new"
    )).mappings().all()
    return [dict(row) for row in rows]


def load_seed_ids_from_sheet(
    *,
    creds_path: str = GOOGLE_CREDS_PATH,
    spreadsheet_id: str = SPREADSHEET_ID,
) -> list[dict]:
    """Лист «Сиды»: в колонке A — ID сборочных заданий из кабинета."""
    import gspread

    try:
        sh = gspread.service_account(filename=creds_path).open_by_key(spreadsheet_id)
        ws = sh.worksheet("Сиды")
    except Exception:
        return []
    values = ws.col_values(1)
    out = []
    seen = set()
    for raw in values:
        digits = "".join(ch for ch in str(raw) if ch.isdigit())
        if not digits:
            continue
        oid = int(digits)
        if oid in seen:
            continue
        seen.add(oid)
        out.append({"id": oid})
    return out


def _blankish(val) -> bool:
    s = str(val or "").strip()
    return s in ("", "-", "None", "null")


def parse_delivery_xlsx(path: str | Path) -> list[dict]:
    """Разбор выгрузки кабинета WB «Сборочные задания» / delivery.xlsx."""
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    out: list[dict] = []
    for row in rows:
        if not row or row[0] is None:
            continue
        try:
            oid = int(str(row[0]).strip())
        except ValueError:
            continue
        name = str(row[6] or "").strip()
        sticker = str(row[2] or "").strip()
        article = str(row[13] or "").strip()
        date_client = str(row[15] or "").strip()
        if _blankish(date_client):
            date_client = ""
        status = str(row[16] or "").strip() or "В доставке"
        fio = "" if _blankish(row[23]) else str(row[23]).strip()
        phone = "" if _blankish(row[24]) else str(row[24]).strip()
        car = "" if _blankish(row[25]) else str(row[25]).strip()
        t_from = "" if _blankish(row[26]) else str(row[26]).strip()
        t_to = "" if _blankish(row[27]) else str(row[27]).strip()
        # В выгрузке «время прибытия» — окно курьера на склад; для колонки
        # «Доставим покупателю» берём дату клиенту + 06:00–20:00 как в кабинете,
        # а если даты нет — только то, что есть.
        delivery = {"dDate": "", "dTimeFrom": "06:00", "dTimeTo": "20:00"}
        if date_client:
            # 20.08.2026 → 2026-08-20
            try:
                delivery["dDate"] = datetime.strptime(date_client, "%d.%m.%Y").strftime("%Y-%m-%d")
            except ValueError:
                delivery["dDate"] = date_client
        courier = {
            "contacts": {
                "fullName": fio,
                "phone": phone,
                "carNumber": car,
            } if (fio or phone or car) else None,
            "mustBeAssigned": True,
            "updatedAt": None,
        }
        out.append({
            "id": oid,
            "sticker": sticker,
            "article": article,
            "name": name,
            "ui_status": status,
            "delivery": delivery,
            "courier": courier,
            # окно приезда курьера на склад — в смене/данных не дублируем отдельно
            "courier_arrival": {"from": t_from, "to": t_to},
        })
    return out


def import_delivery_rows_as_snapshot(rows: list[dict]) -> list[dict]:
    """Превращает строки Excel в формат snapshot для листа."""
    snapshot = []
    for row in rows:
        snapshot.append({
            "order_id": int(row["id"]),
            "sticker": row.get("sticker") or "",
            "article": row.get("article") or "",
            "name": row.get("name") or row.get("article") or "",
            "supplier_status": IN_DELIVERY_STATUS,
            "wb_status": "waiting",
            "courier": row.get("courier") or {},
            "delivery": row.get("delivery") or {},
            "ui_status": row.get("ui_status") or "В доставке",
            "courier_change": "",
        })
    return snapshot


def save_tracked(
    conn,
    orders_by_id: dict[int, dict],
    statuses: dict[int, dict],
    keep_ids: list[int],
    ui_statuses: dict[int, str] | None = None,
) -> None:
    """Обновляет учёт без полной очистки таблицы.

    Раньше был DELETE всех строк и INSERT заново: при сбое посередине
    или неполном keep_ids можно было потерять живые ID.
    """
    ui_statuses = ui_statuses or {}
    keep_set = {int(oid) for oid in keep_ids}
    for oid in keep_set:
        order = orders_by_id.get(oid) or {}
        status = statuses.get(oid) or {}
        created = order.get("createdAt") or order.get("created_at")
        conn.execute(text("""
            INSERT INTO wb_dbw_tracked_orders
                (order_id, article, nm_id, created_at, supplier_status, wb_status, ui_status, sticker, last_seen_at)
            VALUES
                (:order_id, :article, :nm_id, :created_at, :supplier_status, :wb_status, :ui_status, :sticker, NOW())
            ON CONFLICT (order_id) DO UPDATE SET
                article = COALESCE(EXCLUDED.article, wb_dbw_tracked_orders.article),
                nm_id = COALESCE(EXCLUDED.nm_id, wb_dbw_tracked_orders.nm_id),
                created_at = COALESCE(wb_dbw_tracked_orders.created_at, EXCLUDED.created_at),
                supplier_status = COALESCE(EXCLUDED.supplier_status, wb_dbw_tracked_orders.supplier_status),
                wb_status = COALESCE(EXCLUDED.wb_status, wb_dbw_tracked_orders.wb_status),
                ui_status = COALESCE(EXCLUDED.ui_status, wb_dbw_tracked_orders.ui_status),
                sticker = COALESCE(EXCLUDED.sticker, wb_dbw_tracked_orders.sticker),
                last_seen_at = NOW()
        """), {
            "order_id": oid,
            "article": order.get("article"),
            "nm_id": order.get("nmId") or order.get("nm_id"),
            "created_at": created,
            "supplier_status": status.get("supplierStatus"),
            "wb_status": status.get("wbStatus"),
            "ui_status": ui_statuses.get(oid) or order.get("ui_status"),
            "sticker": str(order.get("sticker") or "").strip() or None,
        })

    # снимаем с учёта: финальные + всё, чего нет в keep (после UPSERT живых)
    terminal_ids = {
        int(oid) for oid, st in statuses.items()
        if (st.get("supplierStatus") or "") in TERMINAL_STATUSES
    }
    existing = {
        int(r[0]) for r in conn.execute(text(
            "SELECT order_id FROM wb_dbw_tracked_orders"
        )).all()
    }
    # пустой keep_set не трогаем чужие строки — иначе риск стереть всё при сбое API
    stale = (existing - keep_set) if keep_set else set()
    drop_ids = sorted(terminal_ids | stale)
    if drop_ids:
        conn.execute(text("""
            DELETE FROM wb_dbw_tracked_orders
            WHERE order_id = ANY(:ids)
        """), {"ids": drop_ids})


def sync_courier_history(conn, couriers: dict[int, dict], now: datetime | None = None) -> dict[int, dict]:
    """Фиксирует смены курьера. Возвращает meta для колонки «Смена курьера»."""
    now = now or datetime.now(timezone.utc)
    meta: dict[int, dict] = {}
    for oid, info in couriers.items():
        fp = courier_fingerprint(info)
        c = courier_contacts(info)
        active = conn.execute(text("""
            SELECT id, fingerprint, started_at, source_updated_at
            FROM wb_dbw_courier_history
            WHERE order_id = :oid AND ended_at IS NULL
            ORDER BY started_at DESC
            LIMIT 1
        """), {"oid": oid}).mappings().first()

        replaced = False
        if fp and active and active["fingerprint"] != fp:
            conn.execute(text("""
                UPDATE wb_dbw_courier_history
                SET ended_at = :ended_at, note = :note
                WHERE id = :id
            """), {
                "ended_at": now,
                "note": COURIER_REPLACED_LABEL,
                "id": active["id"],
            })
            replaced = True
            active = None

        if fp and not active:
            conn.execute(text("""
                INSERT INTO wb_dbw_courier_history
                    (order_id, fingerprint, full_name, phone, car_number, started_at, note, source_updated_at)
                VALUES
                    (:order_id, :fingerprint, :full_name, :phone, :car_number, :started_at, :note, :source_updated_at)
            """), {
                "order_id": oid,
                "fingerprint": fp,
                "full_name": c["fullName"] or None,
                "phone": c["phone"] or None,
                "car_number": c["carNumber"] or None,
                "started_at": now,
                "note": COURIER_REPLACED_LABEL if replaced else "Назначен",
                "source_updated_at": c["updatedAt"] or None,
            })

        prior_count = conn.execute(text("""
            SELECT COUNT(*) FROM wb_dbw_courier_history WHERE order_id = :oid
        """), {"oid": oid}).scalar() or 0
        meta[oid] = {
            "replaced": prior_count > 1 or replaced,
            "updated_at": c["updatedAt"] or None,
        }
    return meta


def load_courier_history(conn) -> list[dict]:
    rows = conn.execute(text("""
        SELECT order_id, full_name, phone, car_number,
               started_at::text AS started_at,
               ended_at::text AS ended_at,
               note
        FROM wb_dbw_courier_history
        ORDER BY order_id, started_at
    """)).mappings().all()
    return [dict(r) for r in rows]


def load_names(conn, articles: list[str]) -> dict[str, str]:
    arts = sorted({a for a in articles if a})
    if not arts:
        return {}
    params = {f"a{i}": art for i, art in enumerate(arts)}
    placeholders = ", ".join(f":a{i}" for i in range(len(arts)))
    rows = conn.execute(text(
        f"SELECT supplier_article, name_ru FROM wb_assortment WHERE supplier_article IN ({placeholders})"
    ), params).mappings().all()
    return {str(r["supplier_article"]): str(r["name_ru"] or "").strip() for r in rows if r["name_ru"]}


def normalize_courier_query(query: str) -> str:
    return (query or "").strip().casefold().replace("ё", "е")


def courier_query_match(courier_cell: str, query: str) -> bool:
    """Частичное совпадение: все слова из запроса есть в «Данные курьера»."""
    hay = normalize_courier_query(courier_cell)
    q = normalize_courier_query(query)
    if not q or not hay:
        return False
    tokens = [tok for tok in q.split() if tok]
    return all(tok in hay for tok in tokens)


def filter_delivery_rows_by_courier(delivery_rows: list[list], query: str) -> list[list]:
    q = (query or "").strip()
    if not q:
        return []
    out = []
    for row in delivery_rows:
        courier = row[COURIER_COL_IDX] if len(row) > COURIER_COL_IDX else ""
        if courier_query_match(str(courier), q):
            out.append(list(row[:len(HEADER)]))
    return out


def _ensure_worksheet(sh, title: str):
    for ws in sh.worksheets():
        if ws.title == title:
            return ws
    return sh.add_worksheet(title=title, rows=5000, cols=12)


def ensure_courier_lookup_sheet(sh, delivery_rows: list[list] | None = None) -> None:
    """Лист «Поиск курьера»: в B1 — ФИО/часть имени, ниже — актуальные заказы.

    Основной путь — формула FILTER по листу «В доставке» (список обновляется сразу).
    Если формула недоступна — пишем совпадения с текущего синка.
    Лист настраивается один раз; повторные синки его не перетирают.
    В C1 — количество заказов выбранного курьера.
    """
    titles = {ws.title for ws in sh.worksheets()}
    created = COURIER_LOOKUP_SHEET not in titles
    ws = _ensure_worksheet(sh, COURIER_LOOKUP_SHEET)
    try:
        a4 = str(ws.acell("A4", value_render_option="FORMULA").value or "")
    except Exception:
        a4 = ""

    def _write_count_cell() -> None:
        ws.update(
            [[COURIER_LOOKUP_COUNT_FORMULA]],
            "C1",
            value_input_option="USER_ENTERED",
        )
        try:
            sh.batch_update({"requests": [{
                "repeatCell": {
                    "range": {
                        "sheetId": ws.id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                        "startColumnIndex": 2,
                        "endColumnIndex": 3,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.9, "green": 0.95, "blue": 0.9},
                            "textFormat": {"bold": True, "fontSize": 14},
                            "horizontalAlignment": "CENTER",
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
                }
            }]})
        except Exception:
            pass

    if not created and a4.startswith("="):
        # обновить формулы при смене колонок (Артикул сдвинул курьера в F)
        needs_formula = ("E2:E" in a4) or ("A2:F" in a4) or ("F2:F" not in a4)
        try:
            c1 = str(ws.acell("C1", value_render_option="FORMULA").value or "")
        except Exception:
            c1 = ""
        if needs_formula or "F2:F" not in c1:
            ws.update([[COURIER_LOOKUP_FORMULA]], "A4", value_input_option="USER_ENTERED")
            _write_count_cell()
        elif "FILTER" not in c1.upper():
            _write_count_cell()
        try:
            if str(ws.acell("A1").value or "").strip() != "ФИО":
                ws.update([["ФИО"]], "A1", value_input_option="USER_ENTERED")
        except Exception:
            pass
        # шапка списка на листе поиска тоже должна включать Артикул
        try:
            row3 = [str(c or "") for c in (ws.row_values(3) or [])]
            if row3[:len(HEADER)] != HEADER:
                ws.update([HEADER], "A3", value_input_option="USER_ENTERED")
        except Exception:
            pass
        return

    try:
        query = str(ws.acell("B1").value or "").strip()
    except Exception:
        query = ""

    tip = (
        "Введите фамилию или имя в B1 — список ниже обновится сразу. В C1 — число заказов."
        if not query
        else "Меняйте текст в B1 — список ниже обновится сразу. В C1 — число заказов."
    )
    ws.update(
        [["ФИО", query, COURIER_LOOKUP_COUNT_FORMULA], [tip, "", ""], HEADER],
        "A1",
        value_input_option="USER_ENTERED",
    )
    try:
        ws.update([[COURIER_LOOKUP_FORMULA]], "A4", value_input_option="USER_ENTERED")
    except Exception as exc:
        matched = filter_delivery_rows_by_courier(delivery_rows or [], query)
        block = matched or [["Нет заказов по этому курьеру"] + [""] * (len(HEADER) - 1)]
        ws.update(block, "A4", value_input_option="USER_ENTERED")
        print(f"courier lookup formula fallback: {exc}")

    sheet_id = ws.id
    try:
        sh.batch_update({"requests": [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 3},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 2,
                        "endRowIndex": 3,
                        "startColumnIndex": 0,
                        "endColumnIndex": len(HEADER),
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True},
                            "backgroundColor": {"red": 0.85, "green": 0.92, "blue": 1.0},
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,backgroundColor)",
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                        "startColumnIndex": 1,
                        "endColumnIndex": 2,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 1, "green": 0.97, "blue": 0.85},
                            "textFormat": {"bold": True, "fontSize": 12},
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                        "startColumnIndex": 2,
                        "endColumnIndex": 3,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.9, "green": 0.95, "blue": 0.9},
                            "textFormat": {"bold": True, "fontSize": 14},
                            "horizontalAlignment": "CENTER",
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
                }
            },
        ]})
        width_reqs = []
        for idx, width in enumerate(DELIVERY_COL_WIDTHS):
            width_reqs.append({
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": idx,
                        "endIndex": idx + 1,
                    },
                    "properties": {"pixelSize": width},
                    "fields": "pixelSize",
                }
            })
        if width_reqs:
            sh.batch_update({"requests": width_reqs})
    except Exception as exc:
        print(f"format courier lookup sheet: {exc}")


def _find_archive_worksheet(sh):
    for ws in sh.worksheets():
        if ws.title in ARCHIVE_SHEET_NAMES:
            return ws
    return _ensure_worksheet(sh, "Архив")


def read_sheet_maps(
    *,
    creds_path: str = GOOGLE_CREDS_PATH,
    spreadsheet_id: str = SPREADSHEET_ID,
) -> tuple[dict[str, list], set[str]]:
    import gspread

    sh = gspread.service_account(filename=creds_path).open_by_key(spreadsheet_id)
    delivery_ws = sh.get_worksheet(0)
    previous = sheet_rows_to_map(delivery_ws.get_all_values())
    archive_ws = _find_archive_worksheet(sh)
    archived = set(sheet_rows_to_map(archive_ws.get_all_values()).keys())
    return previous, archived


def _delete_sheet_bandings(sh, sheet_id: int) -> list[dict]:
    requests = []
    meta = sh.fetch_sheet_metadata()
    for s in meta.get("sheets", []):
        if s.get("properties", {}).get("sheetId") != sheet_id:
            continue
        for br in s.get("bandedRanges") or []:
            brid = br.get("bandedRangeId")
            if brid is not None:
                requests.append({"deleteBanding": {"bandedRangeId": brid}})
        if s.get("basicFilter"):
            requests.append({"clearBasicFilter": {"sheetId": sheet_id}})
    return requests


def format_as_table(
    sh,
    ws,
    *,
    n_rows: int,
    n_cols: int,
    header_row: int = 0,
    frozen_rows: int | None = None,
    header_rgb=(0.85, 0.92, 1.0),
    col_widths: list[int] | None = None,
    with_basic_filter: bool = False,
) -> None:
    """Табличный вид без общего фильтра (чтобы читатели делали временные фильтры).

    with_basic_filter=False по умолчанию: общий фильтр меняют только редакторы
    и он ломает картину для всех. Читателям/комментаторам достаточно
    «Создать временный фильтр».
    """
    sheet_id = ws.id
    header_row = max(int(header_row), 0)
    end_row = max(int(n_rows), header_row + 2)
    end_col = max(int(n_cols), 1)
    freeze_n = frozen_rows if frozen_rows is not None else header_row + 1
    hr, hg, hb = header_rgb
    requests = _delete_sheet_bandings(sh, sheet_id)
    requests.extend([
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": end_row,
                    "startColumnIndex": 0,
                    "endColumnIndex": end_col,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 1, "green": 1, "blue": 1},
                        "textFormat": {"bold": False, "fontFamily": "Arial", "fontSize": 10},
                        "horizontalAlignment": "LEFT",
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": header_row,
                    "endRowIndex": header_row + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": end_col,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": hr, "green": hg, "blue": hb},
                        "textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 10},
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": int(freeze_n)},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "addBanding": {
                "bandedRange": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": header_row,
                        "endRowIndex": end_row,
                        "startColumnIndex": 0,
                        "endColumnIndex": end_col,
                    },
                    "rowProperties": {
                        "headerColor": {"red": hr, "green": hg, "blue": hb},
                        "firstBandColor": {"red": 1, "green": 1, "blue": 1},
                        "secondBandColor": {"red": 0.96, "green": 0.97, "blue": 0.98},
                    },
                }
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": header_row,
                    "endIndex": header_row + 1,
                },
                "properties": {"pixelSize": 32},
                "fields": "pixelSize",
            }
        },
    ])
    if header_row > 0:
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": header_row,
                    "startColumnIndex": 0,
                    "endColumnIndex": end_col,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.98, "green": 0.99, "blue": 1.0},
                        "textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 11},
                        "horizontalAlignment": "LEFT",
                        "verticalAlignment": "MIDDLE",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
            }
        })
        requests.append({
            "mergeCells": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": end_col,
                },
                "mergeType": "MERGE_ALL",
            }
        })
        requests.append({
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
                "properties": {"pixelSize": 36},
                "fields": "pixelSize",
            }
        })
    if with_basic_filter:
        requests.append({
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": header_row,
                        "endRowIndex": end_row,
                        "startColumnIndex": 0,
                        "endColumnIndex": end_col,
                    }
                }
            }
        })
    widths = col_widths or [120] * end_col
    for idx, width in enumerate(widths[:end_col]):
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": idx,
                    "endIndex": idx + 1,
                },
                "properties": {"pixelSize": int(width)},
                "fields": "pixelSize",
            }
        })
    sh.batch_update({"requests": requests})


def _sheet_meta(sh, fields: str = "sheets(properties,merges,bandedRanges,tables,basicFilter)"):
    """Метаданные через Sheets API (у gspread не всегда видны tables)."""
    import requests
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(GOOGLE_CREDS_PATH, scopes=scopes)
    creds.refresh(Request())
    resp = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sh.id}",
        params={"fields": fields},
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _find_table(meta: dict, sheet_id: int, table_id: str) -> dict | None:
    for s in meta.get("sheets") or []:
        if s.get("properties", {}).get("sheetId") != sheet_id:
            continue
        for t in s.get("tables") or []:
            if t.get("tableId") == table_id:
                return t
    return None


def soft_write_table(
    sh,
    ws,
    values: list[list],
    *,
    table_id: str,
    table_name: str,
    n_cols: int,
    col_widths: list[int] | None = None,
) -> None:
    """Пишет данные без clear() и держит нативную Table Sheets.

    clear()/пересоздание таблицы заставляет браузер полностью перерисовывать
    лист и сбрасывает временные фильтры. Мягкая перезапись + updateTable
    этого избегает.
    """
    sheet_id = ws.id
    n_rows = max(len(values), 1)
    n_cols = max(n_cols, 1)
    meta = _sheet_meta(sh)
    sheet = next(
        (s for s in meta.get("sheets") or [] if s.get("properties", {}).get("sheetId") == sheet_id),
        {},
    )
    prep = []
    for merge in sheet.get("merges") or []:
        prep.append({"unmergeCells": {"range": merge}})
    for br in sheet.get("bandedRanges") or []:
        brid = br.get("bandedRangeId")
        if brid is not None:
            prep.append({"deleteBanding": {"bandedRangeId": brid}})
    if sheet.get("basicFilter"):
        prep.append({"clearBasicFilter": {"sheetId": sheet_id}})
    for t in sheet.get("tables") or []:
        tid = t.get("tableId")
        if tid and tid != table_id:
            prep.append({"deleteTable": {"tableId": tid}})
    # чужие banding/merge/таблицы мешают; нашу Table не удаляем
    if prep:
        sh.batch_update({"requests": prep})

    existing = _find_table(meta, sheet_id, table_id)
    old_end = 0
    old_cols = 0
    if existing:
        rng = existing.get("range") or {}
        old_end = int(rng.get("endRowIndex") or 0)
        old_cols = int(rng.get("endColumnIndex") or 0)
    try:
        prev_len = len(ws.get_all_values() or [])
    except Exception:
        prev_len = old_end

    def _table_range_req() -> dict:
        return {
            "updateTable": {
                "table": {
                    "tableId": table_id,
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": n_rows,
                        "startColumnIndex": 0,
                        "endColumnIndex": n_cols,
                    },
                },
                "fields": "range",
            }
        }

    # расширяем table до записи, если строк/колонок стало больше
    if existing and (old_end < n_rows or old_cols < n_cols):
        sh.batch_update({"requests": [_table_range_req()]})

    # мягкая запись значений (без clear всего листа)
    ws.update(values, "A1")

    # подчистить хвост (старая актуальность / лишние строки после уменьшения)
    clear_to = max(old_end, prev_len)
    if clear_to > n_rows:
        start = n_rows + 1
        ws.batch_clear([f"A{start}:{_col_letter(n_cols)}{clear_to}"])

    if existing:
        if old_end != n_rows or old_cols != n_cols:
            sh.batch_update({"requests": [_table_range_req()]})
    else:
        sh.batch_update({"requests": [{
            "addTable": {
                "table": {
                    "tableId": table_id,
                    "name": table_name,
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": n_rows,
                        "startColumnIndex": 0,
                        "endColumnIndex": n_cols,
                    },
                }
            }
        }]})

    width_reqs = [{
        "updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount",
        }
    }]
    for idx, width in enumerate((col_widths or [])[:n_cols]):
        width_reqs.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": idx,
                    "endIndex": idx + 1,
                },
                "properties": {"pixelSize": int(width)},
                "fields": "pixelSize",
            }
        })
    if width_reqs:
        sh.batch_update({"requests": width_reqs})


def write_sheets(
    delivery_rows: list[list],
    history_rows: list[list],
    archive_new_rows: list[list] | None = None,
    *,
    creds_path: str = GOOGLE_CREDS_PATH,
    spreadsheet_id: str = SPREADSHEET_ID,
) -> None:
    import gspread

    sh = gspread.service_account(filename=creds_path).open_by_key(spreadsheet_id)
    ws = sh.get_worksheet(0)
    try:
        if ws.title != "В доставке":
            ws.update_title("В доставке")
    except Exception:
        pass

    delivery_values = [HEADER] + delivery_rows
    try:
        soft_write_table(
            sh,
            ws,
            delivery_values,
            table_id=DELIVERY_TABLE_ID,
            table_name=DELIVERY_TABLE_NAME,
            n_cols=len(HEADER),
            col_widths=DELIVERY_COL_WIDTHS,
        )
    except Exception as exc:
        print(f"format delivery sheet: {exc}")

    hist = _ensure_worksheet(sh, "История курьеров")
    hist_values = [HISTORY_HEADER] + history_rows
    try:
        soft_write_table(
            sh,
            hist,
            hist_values,
            table_id=HISTORY_TABLE_ID,
            table_name="ИсторияКурьеров",
            n_cols=len(HISTORY_HEADER),
            col_widths=[120, 220, 140, 120, 140, 140, 220],
        )
    except Exception as exc:
        print(f"format history sheet: {exc}")

    archive = _find_archive_worksheet(sh)
    existing = archive.get_all_values()
    hdr = find_header_row(existing) if existing else 0
    header_cells = [str(c or "").strip().lower() for c in ((existing[hdr] if existing else []) or [])]
    archive_needs_migrate = existing and (
        "артикул" not in header_cells or "стикер" not in header_cells
    )
    if archive_new_rows or archive_needs_migrate:
        by_id: dict[str, list] = {}
        if existing:
            for r in existing[hdr + 1:]:
                oid = "".join(ch for ch in str(r[0] or "") if ch.isdigit())
                if not oid:
                    continue
                cells = list(r)
                date_arch = ""
                if len(cells) > len(HEADER):
                    date_arch = str(cells[len(cells) - 1] or "").strip()
                # сначала вытащим дату архива со старых форматов
                if "стикер" not in header_cells and "артикул" in header_cells and len(cells) >= 8:
                    date_arch = str(cells[7] or "").strip() or date_arch
                    cells = cells[:7]
                elif "стикер" not in header_cells and "артикул" not in header_cells and len(cells) >= 7:
                    date_arch = str(cells[6] or "").strip() or date_arch
                    cells = cells[:6]
                core = migrate_delivery_cells(cells, header_cells)
                padded = list(core) + [""] * (len(HEADER) - len(core))
                by_id[oid] = padded[:len(HEADER)] + [date_arch]
        for r in (archive_new_rows or []):
            oid = str(r[0])
            by_id[oid] = list(r[:len(ARCHIVE_HEADER)])
        arch_values = [ARCHIVE_HEADER] + list(by_id.values())
        try:
            soft_write_table(
                sh,
                archive,
                arch_values,
                table_id=ARCHIVE_TABLE_ID,
                table_name="АрхивDBW",
                n_cols=len(ARCHIVE_HEADER),
                col_widths=ARCHIVE_COL_WIDTHS,
            )
        except Exception as exc:
            print(f"format archive sheet: {exc}")

    try:
        ensure_courier_lookup_sheet(sh, delivery_rows)
    except Exception as exc:
        print(f"courier lookup sheet: {exc}")


def log_to_system(engine, action: str, status: str, details: str = "") -> None:
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO system_logs (action, status, details) VALUES (:action, :status, :details)"),
                {"action": action, "status": status, "details": str(details)[:2000]},
            )
    except Exception as exc:
        print(f"Не удалось записать лог: {exc}")


def collect_snapshot(
    conn,
    new_orders: list[dict] | None = None,
) -> tuple[list[dict], list[dict], dict[int, dict]]:
    now = datetime.now(timezone.utc)
    date_to = int(now.timestamp())
    date_from = int((now - timedelta(days=LOOKBACK_DAYS)).timestamp())
    if new_orders is None:
        new_orders = fetch_new_orders()
    period_orders = fetch_period_orders(date_from, date_to)
    tracked = load_tracked(conn)
    seen_new = load_seen_new(conn)
    seeded = load_seed_ids_from_sheet()
    meta = merge_order_meta(tracked, seen_new, period_orders, new_orders, seeded)
    known_ids = list(meta.keys())
    statuses = fetch_statuses(known_ids) if known_ids else {}
    in_delivery_ids = [
        oid for oid, row in statuses.items()
        if is_active_in_delivery(row.get("supplierStatus") or "", row.get("wbStatus") or "")
    ]
    couriers = fetch_couriers(in_delivery_ids)
    delivery_dates = fetch_delivery_dates(in_delivery_ids)
    stickers = fetch_stickers(in_delivery_ids)
    for oid, num in stickers.items():
        row = meta.get(oid) or {"id": oid}
        row["sticker"] = num
        meta[oid] = row
    courier_meta = sync_courier_history(conn, couriers, now=now)
    articles = [str((meta.get(oid) or {}).get("article") or "") for oid in in_delivery_ids]
    names = load_names(conn, articles)
    previous_ui = {
        int(r["id"]): str(r.get("ui_status") or "")
        for r in tracked
        if r.get("id") is not None
    }
    snapshot = collect_in_delivery(
        new_orders=new_orders,
        period_orders=period_orders,
        tracked=tracked,
        statuses=statuses,
        couriers=couriers,
        delivery_dates=delivery_dates,
        names=names,
        courier_meta=courier_meta,
        previous_ui_statuses=previous_ui,
        stickers=stickers,
    )
    keep_ids = next_tracked_ids(statuses, known_ids)
    for row in seeded:
        oid = int(row["id"])
        if oid not in keep_ids and (statuses.get(oid) or {}).get("supplierStatus") not in TERMINAL_STATUSES:
            keep_ids.append(oid)
    # всё, что видели в /new (сейчас или раньше) — держим до финала
    for row in list(new_orders) + list(seen_new):
        oid = int(row["id"])
        if oid not in keep_ids and (statuses.get(oid) or {}).get("supplierStatus") not in TERMINAL_STATUSES:
            keep_ids.append(oid)
    for item in snapshot:
        oid = int(item["order_id"])
        if item.get("sticker"):
            meta.setdefault(oid, {"id": oid})
            meta[oid]["sticker"] = item["sticker"]
    ui_statuses = {int(item["order_id"]): item.get("ui_status") or "" for item in snapshot}
    save_tracked(conn, meta, statuses, keep_ids, ui_statuses=ui_statuses)
    history = load_courier_history(conn)
    return snapshot, history, statuses


def rewrite_archive_sheet(
    rows: list[list],
    *,
    creds_path: str = GOOGLE_CREDS_PATH,
    spreadsheet_id: str = SPREADSHEET_ID,
) -> None:
    import gspread

    sh = gspread.service_account(filename=creds_path).open_by_key(spreadsheet_id)
    archive = _find_archive_worksheet(sh)
    values = [ARCHIVE_HEADER] + rows
    soft_write_table(
        sh,
        archive,
        values,
        table_id=ARCHIVE_TABLE_ID,
        table_name="АрхивDBW",
        n_cols=len(ARCHIVE_HEADER),
        col_widths=ARCHIVE_COL_WIDTHS,
    )


def repair_archive_against_api(
    *,
    creds_path: str = GOOGLE_CREDS_PATH,
    spreadsheet_id: str = SPREADSHEET_ID,
) -> dict:
    """Вернуть в «В доставке» то, что API ещё считает complete; в архиве оставить только финал."""
    import gspread

    sh = gspread.service_account(filename=creds_path).open_by_key(spreadsheet_id)
    delivery_ws = sh.get_worksheet(0)
    archive_ws = _find_archive_worksheet(sh)
    delivery_map = sheet_rows_to_map(delivery_ws.get_all_values())
    archive_vals = archive_ws.get_all_values()
    archive_map = sheet_rows_to_map(archive_vals)

    ids = [int(oid) for oid in archive_map.keys()]
    statuses = fetch_statuses(ids) if ids else {}
    keep_archive: list[list] = []
    restore: list[list] = []
    stamp = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    for oid, row in archive_map.items():
        st = statuses.get(int(oid)) or {}
        supplier = str(st.get("supplierStatus") or "")
        wb = str(st.get("wbStatus") or "")
        if should_archive_order(supplier, wb):
            fixed = list(row[:len(HEADER)])
            date_arch = ""
            hdr = find_header_row(archive_vals)
            raw_rows = [
                r for r in archive_vals[hdr + 1:]
                if "".join(ch for ch in str(r[0] or "") if ch.isdigit()) == oid
            ]
            if raw_rows and len(raw_rows[0]) > len(HEADER):
                date_arch = str(raw_rows[0][len(HEADER)] or "").strip()
            fixed[STATUS_COL_IDX] = archive_status_label(supplier, wb)
            keep_archive.append(fixed + [date_arch or stamp])
        elif is_active_in_delivery(supplier, wb):
            # ещё в доставке по API — возвращаем
            if oid not in delivery_map:
                restore.append(list(row[:len(HEADER)]))

    # переписать архив
    rewrite_archive_sheet(keep_archive, creds_path=creds_path, spreadsheet_id=spreadsheet_id)

    # дописать возвращённые в «В доставке»
    if restore:
        current = delivery_ws.get_all_values()
        by_id = sheet_rows_to_map(current)
        for row in restore:
            by_id[str(row[0])] = list(row[:len(HEADER)])
        values = [HEADER] + list(by_id.values())
        soft_write_table(
            sh,
            delivery_ws,
            values,
            table_id=DELIVERY_TABLE_ID,
            table_name=DELIVERY_TABLE_NAME,
            n_cols=len(HEADER),
            col_widths=DELIVERY_COL_WIDTHS,
        )

    return {
        "restored": len(restore),
        "kept_archive": len(keep_archive),
        "checked": len(ids),
    }


def run_import_xlsx(path: str | Path) -> int:
    """Разовая загрузка выгрузки кабинета в таблицу + учёт/историю курьеров."""
    path = Path(path)
    rows = parse_delivery_xlsx(path)
    if not rows:
        raise RuntimeError(f"В файле нет заказов: {path}")

    engine = create_engine(DB_URL, pool_pre_ping=True)
    _lock_conn = cron_lock.acquire_with_wait(
        engine,
        "wb_dbw_delivery",
        timeout_sec=180,
        poll_sec=5,
    )
    try:
        snapshot = import_delivery_rows_as_snapshot(rows)
        previous_rows, archived_ids = read_sheet_maps()
        current_ids = {str(item["order_id"]) for item in snapshot}
        # статусы только для кандидатов в архив — чтобы не утащить живые заказы
        candidate_ids = [
            int(oid) for oid in previous_rows.keys()
            if oid not in current_ids and oid not in archived_ids
        ]
        statuses = fetch_statuses(candidate_ids) if candidate_ids else {}
        archive_new = pick_rows_to_archive(
            previous_rows, current_ids, archived_ids, statuses=statuses,
        )

        with engine.begin() as conn:
            ensure_tables(conn)
            couriers = {int(r["id"]): r.get("courier") or {} for r in rows}
            sync_courier_history(conn, couriers)
            meta = {int(r["id"]): r for r in rows}
            statuses_tracked = {
                int(r["id"]): {"supplierStatus": IN_DELIVERY_STATUS, "wbStatus": "waiting"}
                for r in rows
            }
            ui_statuses = {int(r["id"]): r.get("ui_status") or "" for r in rows}
            save_tracked(conn, meta, statuses_tracked, list(meta.keys()), ui_statuses=ui_statuses)
            history = load_courier_history(conn)

        write_sheets(
            build_sheet_rows(snapshot),
            build_history_sheet_rows(history),
            archive_new_rows=archive_new,
        )
        details = (
            f"импорт {path.name}: {len(snapshot)} в доставке, "
            f"в архив +{len(archive_new)}, "
            f"история {len(history)}"
        )
        print(f"✅ {details}")
        log_to_system(engine, "WB DBW доставка", "SUCCESS", details)
        return len(snapshot)
    except Exception as exc:
        print(f"❌ Импорт DBW: {exc}")
        log_to_system(engine, "WB DBW доставка", "FAIL", f"import: {exc}")
        raise
    finally:
        _lock_conn.close()


def run_ingest_only() -> int:
    """Только захват /new в БД. Быстро, без Google Sheets.

    Нужен отдельный cron: полная синхронизация иногда дольше минуты и
    пропускает тик из‑за lock — а заказ из /new за это время может уйти
    на сборку и пропасть из API навсегда.
    """
    engine = create_engine(DB_URL, pool_pre_ping=True)
    _lock_conn = cron_lock.acquire_or_exit(
        engine,
        "wb_dbw_ingest",
        log_db=lambda action, status, details="": log_to_system(engine, action, status, details),
    )
    try:
        new_orders = fetch_new_orders()
        with engine.begin() as conn:
            added = ingest_new_orders_early(conn, new_orders)
        msg = f"ingest /new: +{added} новых (сейчас в /new {len(new_orders)})"
        print(f"📥 {msg}")
        if added:
            log_to_system(engine, "WB DBW ingest", "SUCCESS", msg)
        return added
    except Exception as exc:
        log_to_system(engine, "WB DBW ingest", "FAIL", str(exc)[:500])
        raise
    finally:
        _lock_conn.close()


def run_ingest_loop(interval_sec: float = 0.5) -> None:
    """Демон: каждые N секунд забирает /new. Чем чаще — тем меньше дыра на сборке.

    Lock берём один раз на весь процесс — иначе при частом тике сами себе мешаем.
    """
    import time

    interval = max(0.2, float(interval_sec))  # не быстрее лимита WB (200 мс)
    engine = create_engine(DB_URL, pool_pre_ping=True)
    _lock_conn = cron_lock.acquire_or_exit(
        engine,
        "wb_dbw_ingest",
        log_db=lambda action, status, details="": log_to_system(engine, action, status, details),
    )
    print(f"🔁 WB DBW ingest loop каждые {interval}с")
    try:
        while True:
            started = time.monotonic()
            try:
                new_orders = fetch_new_orders()
                with engine.begin() as conn:
                    added = ingest_new_orders_early(conn, new_orders)
                msg = f"ingest /new: +{added} новых (сейчас в /new {len(new_orders)})"
                print(f"📥 {msg}")
                if added:
                    log_to_system(engine, "WB DBW ingest", "SUCCESS", msg)
            except Exception as exc:
                print(f"❌ ingest loop: {exc}")
                log_to_system(engine, "WB DBW ingest", "FAIL", str(exc)[:500])
            elapsed = time.monotonic() - started
            time.sleep(max(0.0, interval - elapsed))
    finally:
        _lock_conn.close()


def run_sync() -> int:
    engine = create_engine(DB_URL, pool_pre_ping=True)
    _lock_conn = cron_lock.acquire_or_exit(
        engine,
        "wb_dbw_delivery",
        log_db=lambda action, status, details="": log_to_system(engine, action, status, details),
    )
    log_to_system(engine, "WB DBW доставка", "INFO", "Старт выгрузки «В доставке»")
    try:
        # 1) Сначала фиксируем /new в БД — даже если Google Sheets ляжет.
        new_orders = fetch_new_orders()
        with engine.begin() as conn:
            added = ingest_new_orders_early(conn, new_orders)
        if added:
            msg = f"новых ID из /new на учёт: +{added} (всего в /new {len(new_orders)})"
            print(f"📥 {msg}")
            log_to_system(engine, "WB DBW доставка", "INFO", msg)

        # 2) Потом читаем/пишем Google и собираем полный снимок.
        previous_rows, archived_ids = read_sheet_maps()
        with engine.begin() as conn:
            ensure_tables(conn)
            snapshot, history, statuses = collect_snapshot(conn, new_orders=new_orders)
        delivery_rows = build_sheet_rows(snapshot)
        current_ids = {row[0] for row in delivery_rows}
        # добираем статусы для строк, которые пропали с листа, но ещё не в statuses
        missing = [
            int(oid) for oid in previous_rows.keys()
            if oid not in current_ids and oid not in archived_ids and int(oid) not in statuses
        ]
        if missing:
            statuses.update(fetch_statuses(missing))
        archive_new = pick_rows_to_archive(
            previous_rows, current_ids, archived_ids, statuses=statuses,
        )
        write_sheets(
            delivery_rows,
            build_history_sheet_rows(history),
            archive_new_rows=archive_new,
        )
        stamp = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
        details = (
            f"{len(delivery_rows)} в доставке, "
            f"в архив +{len(archive_new)}, "
            f"{len(history)} записей истории курьеров, "
            f"обновлено {stamp} МСК"
        )
        print(f"✅ {details}")
        log_to_system(engine, "WB DBW доставка", "SUCCESS", details)
        return len(delivery_rows)
    except Exception as exc:
        print(f"❌ WB DBW доставка: {exc}")
        log_to_system(engine, "WB DBW доставка", "FAIL", str(exc))
        raise
    finally:
        _lock_conn.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "import":
        src = sys.argv[2] if len(sys.argv) > 2 else "/tmp/delivery.xlsx"
        run_import_xlsx(src)
    elif len(sys.argv) > 1 and sys.argv[1] == "repair-archive":
        result = repair_archive_against_api()
        print(
            f"✅ repair-archive: проверено {result['checked']}, "
            f"вернул в доставку {result['restored']}, "
            f"оставил в архиве {result['kept_archive']}"
        )
    elif len(sys.argv) > 1 and sys.argv[1] == "ingest":
        run_ingest_only()
    elif len(sys.argv) > 1 and sys.argv[1] == "ingest-loop":
        interval = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5
        run_ingest_loop(interval)
    else:
        run_sync()
