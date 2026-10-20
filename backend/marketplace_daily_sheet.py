"""Ежедневная выгрузка заказов и продаж WB / Ozon / Яндекс в Google-таблицу.

Таблица: «Заказы/Продажи по дням».
Жёлтые ячейки — цифра есть, но ей нельзя верить как точной:
комиссия и логистика в день заказа ещё не подтверждены, реклама не разложена
по артикулам, остаток — это снимок «сейчас», а не конец того дня.
"""
from __future__ import annotations

import os
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

SPREADSHEET_ID = "1P_vyS7I7kWsAvyfpAdm7MhOtpHEHdSjNzYQ6NSeo27A"
SHEET_ORDERS = "По заказам"
SHEET_SALES = "По продажам"
DATE_FROM = date(2026, 8, 1)
MSK = ZoneInfo("Europe/Moscow")

ORDER_HEADERS = [
    "Дата заказа", "Маркетплейс", "Бренд", "Категория", "Наименование товара",
    "Артикул продавца", "Артикул маркетплейса", "Схема отгрузки",
    "Итого заказано, шт. ( с вычетом отмен)", "Итого заказано, руб. ( с вычетом отмен)",
    "Отменено шт.", "Возвраты, руб.", "Комиссия, руб.", "Логистика, руб.",
    "Реклама, руб.", "Внутренние расходы, руб.", "К выплате, руб.",
    "Себестоимость, руб.", "Прибыль, руб.",
    "Средняя цена продавца, руб.", "Средняя цена покупателя, руб.",
    "Остаток на конец периода, шт. (FBO)", "Остаток на конец периода, шт. (FBS)",
]
SALES_HEADERS = [
    "Дата заказа", "Маркетплейс", "Бренд", "Категория", "Наименование товара",
    "Артикул продавца", "Артикул маркетплейса", "Схема отгрузки",
    "Итого продано шт. (с вычетом возвратов)", "Итого продано, руб. (с вычетом возвратов)",
    "Возвраты, руб.", "Возвраты, шт.",
    "Комиссия за продажи, руб.", "Комиссия за возвраты, руб.",
    "Логистика за продажи, руб.", "Логистика за возвраты, руб.", "Логистика за отмены, руб.",
    "Реклама, руб.", "Внутренние расходы, руб.", "Компенсации руб.", "Штрафы руб.",
    "К перечислению, руб.", "Себестоимость, руб.", "Прибыль, руб.",
]

NOTE_HEADERS = ["Почему жёлтое", "Как сделать точным"]

# Код сомнения → столбцы (с нуля) и текст для двух колонок справа.
REASONS = {
    "commission": (
        "Комиссия — доля «цена продавца минус к выплате» по недавним продажам этого артикула. Сам заказ в отчёте ещё не закрыт, цифра оценочная.",
        "Когда заказ попадёт в финансовый отчёт площадки, скрипт подставит фактическую комиссию по этому заказу.",
    ),
    "commission_wb": (
        "Комиссия WB — это цена продавца минус сумма к выплате из отчёта. Внутри и процент площадки, и скидка, которую WB даёт покупателю.",
        "Если нужна только комиссия по договору, её надо вычесть отдельно от скидки покупателю. Сейчас такого столбца в таблице нет.",
    ),
    "logistics_wb": (
        "Логистика WB в отчёте часто приходит без артикула, поэтому по товару стоит 0 или только то, что удалось привязать.",
        "Нужны строки отчёта WB с видом операции «Логистика» и артикулом. Сейчас эти строки в базу не сохраняются.",
    ),
    "logistics_split": (
        "Логистика Ozon разложена по типам услуг (доставка, возврат, отмена). Это наша раскладка, не готовая колонка кабинета.",
        "Сверять с отчётом реализации Ozon за день: если типы услуг названы иначе, раскладку надо поправить.",
    ),
    "logistics_part": (
        "Логистика есть не по всем заказам: часть отправлений ещё без начисления. Недостающее добрано средней доставкой этого артикула.",
        "Подождать, пока Ozon закроет отправления, и обновить таблицу.",
    ),
    "ads": (
        "Реклама стоит 0: расход рекламного кабинета не приходит в финансовом отчёте по артикулу.",
        "Подключить рекламные кабинеты WB, Ozon и Яндекса и разложить расход на артикул за день.",
    ),
    "internal": (
        "Внутренние расходы (хранение, приёмка, подписки) приходят общей суммой кабинета, без товара. По артикулу стоит 0.",
        "Нужен отчёт площадки, где хранение и приёмка уже разложены по артикулу.",
    ),
    "cogs": (
        "Себестоимость — цена последней партии артикула, а не той поставки, из которой ушёл именно этот товар.",
        "В таблице партий у артикула несколько цен. Чтобы было точно, нужна связь заказа с конкретной поставкой.",
    ),
    "cogs_missing": (
        "Себестоимости этого артикула нет в таблице партий, стоит 0.",
        "Добавить артикул и цену партии в таблицу себестоимости.",
    ),
    "stock": (
        "Остаток — это снимок на сейчас, а не остаток на конец того дня. На прошлые дни история остатков у площадки не хранится.",
        "Снимать остаток каждый день и хранить у нас. Задним числом восстановить нельзя.",
    ),
    "buyer": (
        "Цены покупателя в заказе нет.",
        "Брать цену покупателя из начисления Ozon или из заказа Яндекса, когда площадка её отдаёт.",
    ),
    "ym_qty": (
        "У Яндекса в сохранённом заказе нет количества штук, считаем 1 штуку на строку.",
        "Сохранять количество из заказа Яндекса. Сейчас оно при загрузке отбрасывается.",
    ),
    "ym_scheme": (
        "Схему отгрузки Яндекс в заказе не пишет. Стоит fbs, как у нашей витрины.",
        "Уточнить в кабинете Яндекса модель работы и записать её в выгрузку.",
    ),
    "ym_money": (
        "У Яндекса нет дневного финансового отчёта по товару. Комиссия, логистика и выплата по строке не из кабинета.",
        "Сохранять строки отчёта о реализации Яндекса по каждому товару, а не только общую сумму за месяц.",
    ),
    "date_accrual": (
        "Дата — день начисления или доставки, не день заказа. В заголовке столбца написано «Дата заказа».",
        "Связать начисление с номером заказа и ставить дату создания заказа.",
    ),
    "scheme_mix": (
        "В этот день у артикула были и FBO, и FBS. Деньги отчёта не делятся по схеме, стоит та схема, где заказов больше.",
        "Делить финансовый отчёт по схеме отгрузки. Сейчас в отчёте WB схемы нет.",
    ),
    "brand": (
        "Бренд в карточке не нашёлся, стоит Vidovito.",
        "Проставить бренд в карточке товара.",
    ),
    "mp_sku": (
        "Артикула маркетплейса в данных нет.",
        "Догрузить карточку товара: nmId WB, SKU Ozon или SKU Яндекса.",
    ),
}

YELLOW = {"backgroundColor": {"red": 1, "green": 0.949, "blue": 0.8}}
WHITE = {"backgroundColor": {"red": 1, "green": 1, "blue": 1}}
THIN_BORDER = {"style": "SOLID", "width": 1, "color": {"red": 0.6, "green": 0.6, "blue": 0.6}}
MP_SORT = {"WB": 0, "Ozon": 1, "Яндекс": 2}


def rub(value) -> int:
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def art_key(value) -> str:
    return str(value or "").strip().casefold()


def payout_orders(ordered_rub, commission, logistics, ads, internal) -> int:
    return rub(ordered_rub) + rub(commission) + rub(logistics) + rub(ads) + rub(internal)


def payout_sales(sold, comm_sales, comm_returns, log_sales, log_returns, log_cancels,
                 ads, internal, compensations, penalties) -> int:
    return (
        rub(sold) + rub(comm_sales) + rub(comm_returns)
        + rub(log_sales) + rub(log_returns) + rub(log_cancels)
        + rub(ads) + rub(internal) + rub(compensations) + rub(penalties)
    )


def profit(payout, cogs) -> int:
    return rub(payout) + rub(cogs)


def wb_scheme(warehouse_type: str) -> str:
    text_value = (warehouse_type or "").casefold()
    if "продав" in text_value:
        return "FBS"
    if "wb" in text_value or "вб" in text_value or "вайлд" in text_value:
        return "FBO"
    return ""


def parse_wb_date(value) -> date | None:
    raw = str(value or "")[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def fmt_date(day: date) -> str:
    return day.strftime("%d.%m.%Y")


def show_scheme(scheme: str, *, orders: bool) -> str:
    text_value = (scheme or "").strip()
    if not text_value:
        return ""
    return text_value.lower() if orders else text_value.upper()


ORDER_REASON_COLS = {
    "commission": {12}, "logistics_wb": {13}, "logistics_part": {13}, "ads": {14}, "internal": {15},
    "cogs": {17}, "cogs_missing": {17}, "stock": {21, 22}, "buyer": {20},
    "ym_qty": {8, 9}, "ym_scheme": {7}, "ym_money": {12, 13, 14, 15},
    "brand": {2}, "mp_sku": {6}, "scheme_mix": {7},
}
SALES_REASON_COLS = {
    "commission": {12, 13}, "commission_wb": {12, 13},
    "logistics_wb": {14, 15, 16}, "logistics_split": {14, 15, 16},
    "ads": {17}, "internal": {18}, "cogs": {22}, "cogs_missing": {22},
    "ym_qty": {8, 9}, "ym_scheme": {7}, "ym_money": {12, 13, 14, 15, 16, 17, 18, 19, 20},
    "date_accrual": {0}, "scheme_mix": {7}, "brand": {2}, "mp_sku": {6},
}
ORDER_PAYOUT_IF = {"commission", "logistics_wb", "logistics_part", "ads", "internal", "cogs", "cogs_missing", "ym_money"}
SALES_PAYOUT_IF = ORDER_PAYOUT_IF | {"commission_wb", "logistics_split"}


def explain(codes, *, kind: str):
    cols = set()
    table = ORDER_REASON_COLS if kind == "order" else SALES_REASON_COLS
    why, how = [], []
    seen = set()
    for code in codes:
        if code in seen:
            continue
        seen.add(code)
        cols |= table.get(code, set())
        text = REASONS.get(code)
        if text:
            why.append(text[0])
            how.append(text[1])
    payout_if = ORDER_PAYOUT_IF if kind == "order" else SALES_PAYOUT_IF
    if seen & payout_if:
        if kind == "order":
            cols.update({16, 18})
        else:
            cols.update({21, 23})
    return cols, " ".join(why), " ".join(how)


def col_letter(index: int) -> str:
    n = index + 1
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def yellow_ranges(flags_by_row: list[set[int]], ncols: int, start_row: int = 2) -> list[str]:
    ranges = []
    for col in range(ncols):
        run = None
        for i, flags in enumerate(flags_by_row):
            on = col in flags
            if on and run is None:
                run = i
            elif not on and run is not None:
                ranges.append(_span(col, start_row + run, start_row + i - 1))
                run = None
        if run is not None:
            ranges.append(_span(col, start_row + run, start_row + len(flags_by_row) - 1))
    return ranges


def _span(col: int, row_a: int, row_b: int) -> str:
    letter = col_letter(col)
    if row_a == row_b:
        return f"{letter}{row_a}"
    return f"{letter}{row_a}:{letter}{row_b}"


def _avg(total, qty):
    if not qty:
        return ""
    return rub(total / qty)


def aggregate_wb_orders(rows: list[dict], date_from: date, date_to: date) -> list[dict]:
    grouped = {}
    for row in rows:
        day = parse_wb_date(row.get("date"))
        if day is None or day < date_from or day >= date_to:
            continue
        article = str(row.get("supplierArticle") or "").strip()
        if not article:
            continue
        scheme = wb_scheme(row.get("warehouseType"))
        key = (day, art_key(article), str(row.get("nmId") or ""), scheme)
        bucket = grouped.get(key)
        if bucket is None:
            bucket = {
                "date": day, "article": article, "nm_id": str(row.get("nmId") or ""),
                "scheme": scheme, "brand": str(row.get("brand") or "").strip(),
                "category": str(row.get("subject") or "").strip(),
                "qty": 0, "cancelled": 0, "rub": 0.0, "buyer": 0.0,
            }
            grouped[key] = bucket
        if row.get("isCancel"):
            bucket["cancelled"] += 1
            continue
        bucket["qty"] += 1
        bucket["rub"] += float(row.get("priceWithDisc") or 0)
        bucket["buyer"] += float(row.get("finishedPrice") or 0)
    return list(grouped.values())


_OZON_LOG_RETURN = {2, 9, 45, 53, 59}
_OZON_LOG_CANCEL = {6}


def oz_amount(node) -> float:
    if not isinstance(node, dict):
        return 0.0
    try:
        return float(node.get("amount") or 0)
    except (TypeError, ValueError):
        return 0.0


def aggregate_ozon_accruals(day: date, accruals: list[dict], postings: dict) -> list[dict]:
    """Начисления Ozon за день → строки продаж. postings: номер отправления → (артикул, название)."""
    grouped = {}
    for accrual in accruals:
        posting = accrual.get("posting") or {}
        scheme = "FBO" if str(posting.get("delivery_schema") or "").lower() == "fbo" else (
            "FBS" if str(posting.get("delivery_schema") or "").lower() == "fbs" else ""
        )
        article, name = postings.get(str(accrual.get("unit_number") or ""), ("", ""))
        article = str(article or "").strip()
        if not article:
            continue
        key = (art_key(article), scheme)
        bucket = grouped.get(key)
        if bucket is None:
            bucket = {
                "date": day, "article": article, "name": name, "sku": "",
                "scheme": scheme,
                "sale_qty": 0, "return_qty": 0,
                "sale_rub": 0.0, "return_rub": 0.0,
                "comm_sales": 0.0, "comm_returns": 0.0,
                "compensation": 0.0,
                "log_sales": 0.0, "log_returns": 0.0, "log_cancels": 0.0,
            }
            grouped[key] = bucket
        if name and not bucket["name"]:
            bucket["name"] = name
        for product in posting.get("products") or []:
            qty = int(product.get("quantity") or 0) or 1
            sku = str(product.get("sku") or "")
            if sku:
                bucket["sku"] = sku
            commission = product.get("commission") or {}
            if commission:
                line = oz_amount(commission.get("sale_amount"))
                if line == 0:
                    line = oz_amount(commission.get("seller_price")) * qty
                fee = oz_amount(commission.get("sale_commission"))
                if line < 0 or fee > 0:
                    bucket["return_qty"] += qty
                    bucket["return_rub"] += abs(line)
                    bucket["comm_returns"] += abs(fee)
                else:
                    bucket["sale_qty"] += qty
                    bucket["sale_rub"] += abs(line)
                    bucket["comm_sales"] += abs(fee)
            for service in (product.get("delivery") or {}).get("services") or []:
                amount = oz_amount(service.get("accrued"))
                type_id = int(service.get("type_id") or 0)
                if type_id in _OZON_LOG_CANCEL:
                    bucket["log_cancels"] += amount
                elif type_id in _OZON_LOG_RETURN:
                    bucket["log_returns"] += amount
                else:
                    bucket["log_sales"] += amount
    return list(grouped.values())


def aggregate_ozon_realization(day: date, rows: list[dict]) -> list[dict]:
    grouped = {}
    for row in rows:
        item = row.get("item") or {}
        article = str(item.get("offer_id") or "").strip()
        if not article:
            continue
        key = art_key(article)
        bucket = grouped.get(key)
        if bucket is None:
            bucket = {
                "date": day, "article": article,
                "name": str(item.get("name") or "").strip(),
                "sku": str(item.get("sku") or ""),
                "sale_qty": 0, "return_qty": 0,
                "sale_rub": 0.0, "return_rub": 0.0,
                "comm_sales": 0.0, "comm_returns": 0.0,
                "compensation": 0.0,
            }
            grouped[key] = bucket
        seller = float(row.get("seller_price_per_instance") or 0)
        for side, prefix in (("delivery_commission", "sale"), ("return_commission", "return")):
            block = row.get(side) or {}
            if not block:
                continue
            qty = int(block.get("quantity") or 0)
            bucket[f"{prefix}_qty"] += qty
            bucket[f"{prefix}_rub"] += seller * qty
            fee = float(block.get("standard_fee") or 0)
            if prefix == "sale":
                bucket["comm_sales"] += fee
            else:
                bucket["comm_returns"] += fee
            bucket["compensation"] += float(block.get("compensation") or 0)
    return list(grouped.values())


def order_record(day, mp, brand, category, name, article, mp_sku, scheme,
                 qty, ordered_rub, cancelled, buyer_total, cogs_unit, reasons,
                 returns_rub=0, commission=0, logistics=0, ads=0, internal=0):
    ordered_rub = rub(ordered_rub)
    codes = list(reasons)
    if cogs_unit:
        codes.append("cogs")
        cogs = -rub(cogs_unit * qty) if qty else 0
    else:
        codes.append("cogs_missing")
        cogs = 0
    pay = payout_orders(ordered_rub, commission, logistics, ads, internal)
    yellow, why, how = explain(codes, kind="order")
    if qty == 0:
        yellow.add(19)
    if not category:
        yellow.add(3)
    if not name:
        yellow.add(4)
    if not scheme:
        yellow.add(7)
    buyer = _avg(buyer_total, qty) if buyer_total is not None else ""
    if buyer == "":
        yellow.add(20)
        if "buyer" not in codes:
            extra_why, extra_how = REASONS["buyer"]
            why = (why + " " + extra_why).strip()
            how = (how + " " + extra_how).strip()
    values = [
        fmt_date(day), mp, brand, category, name, article, str(mp_sku or ""),
        show_scheme(scheme, orders=True),
        qty, ordered_rub, cancelled, rub(returns_rub), rub(commission), rub(logistics),
        rub(ads), rub(internal), pay, cogs, profit(pay, cogs),
        _avg(ordered_rub, qty), buyer, "", "",
    ]
    return values, yellow


def sales_record(day, mp, brand, category, name, article, mp_sku, scheme,
                 sold_qty, sold_rub, returns_rub, returns_qty,
                 comm_sales, comm_returns, compensations, penalties, cogs_unit,
                 reasons, log_sales=0, log_returns=0, log_cancels=0, ads=0, internal=0):
    codes = list(reasons)
    log_sales, log_returns, log_cancels = rub(log_sales), rub(log_returns), rub(log_cancels)
    sold_rub = rub(sold_rub)
    if cogs_unit:
        codes.append("cogs")
        cogs = -rub(cogs_unit * sold_qty) if sold_qty else 0
    else:
        codes.append("cogs_missing")
        cogs = 0
    pay = payout_sales(
        sold_rub, comm_sales, comm_returns, log_sales, log_returns, log_cancels,
        ads, internal, compensations, penalties,
    )
    yellow, why, how = explain(codes, kind="sales")
    if not category:
        yellow.add(3)
    if not name:
        yellow.add(4)
    if not scheme:
        yellow.add(7)
    values = [
        fmt_date(day), mp, brand, category, name, article, str(mp_sku or ""),
        show_scheme(scheme, orders=False),
        sold_qty, sold_rub, rub(returns_rub), returns_qty,
        rub(comm_sales), rub(comm_returns),
        log_sales, log_returns, log_cancels, rub(ads), rub(internal),
        rub(compensations), rub(penalties), pay, cogs, profit(pay, cogs),
    ]
    return values, yellow


def apply_stock(values, yellow, stock, fbo_col, fbs_col):
    if not stock:
        yellow.add(fbo_col)
        yellow.add(fbs_col)
        return
    fbo, fbs = stock
    values[fbo_col] = fbo
    values[fbs_col] = fbs
    yellow.add(fbo_col)
    yellow.add(fbs_col)


def sort_rows(rows):
    rows.sort(key=lambda item: (item[0][0][6:10] + item[0][0][3:5] + item[0][0][0:2], MP_SORT.get(item[0][1], 9), item[0][5]))
    return rows


def missing_header_tail(current, expected):
    """Пустой хвост шапки можно дописать. Чужое название — нельзя."""
    cells = [str(cell or "").strip() for cell in current]
    while cells and not cells[-1]:
        cells.pop()
    if cells == list(expected):
        return []
    if cells == list(expected[:len(cells)]):
        return list(expected[len(cells):])
    return None


def repair_headers(worksheet, expected):
    tail = missing_header_tail(worksheet.row_values(1), expected)
    if tail is None:
        raise SystemExit(f"На вкладке «{worksheet.title}» другие заголовки — запись остановлена")
    if tail:
        worksheet.update([tail], f"{col_letter(len(expected) - len(tail))}1", raw=False)


def apply_borders(worksheet, last_row: int, ncols: int):
    if last_row < 2 or ncols < 1:
        return
    border = THIN_BORDER
    body = {"requests": [{
        "updateBorders": {
            "range": {
                "sheetId": worksheet.id,
                "startRowIndex": 0,
                "endRowIndex": last_row,
                "startColumnIndex": 0,
                "endColumnIndex": ncols,
            },
            "top": border,
            "bottom": border,
            "left": border,
            "right": border,
            "innerHorizontal": border,
            "innerVertical": border,
        }
    }]}
    for attempt in range(4):
        try:
            worksheet.spreadsheet.batch_update(body)
            return
        except Exception as exc:
            if "429" not in str(exc) or attempt == 3:
                raise
            time.sleep(65)


def clear_row_groups(worksheet):
    spreadsheet = worksheet.spreadsheet
    sheet_id = worksheet.id
    meta = spreadsheet.fetch_sheet_metadata()
    existing = []
    for sheet in meta.get("sheets", []):
        if sheet.get("properties", {}).get("sheetId") == sheet_id:
            existing = list(sheet.get("rowGroups") or [])
            break
    if not existing:
        return
    existing.sort(key=lambda group: (group.get("depth", 1), group["range"]["startIndex"]), reverse=True)
    requests = []
    for group in existing:
        requests.append({"deleteDimensionGroup": {"range": {
            "sheetId": sheet_id,
            "dimension": "ROWS",
            "startIndex": group["range"]["startIndex"],
            "endIndex": group["range"]["endIndex"],
        }}})
    spreadsheet.batch_update({"requests": requests})


def database_url() -> str:
    return (
        os.getenv("DATABASE_URL_LOCAL")
        or os.getenv("DATABASE_URL")
        or "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"
    )


def _get_json(url, headers, params=None, timeout=120):
    for attempt in range(6):
        response = requests.get(url, headers=headers, params=params, timeout=timeout)
        if response.status_code == 429:
            time.sleep(65)
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"WB не ответил: {url}")


def fetch_wb_orders(api_key: str, date_from: date) -> list[dict]:
    headers = {"Authorization": api_key}
    cursor = f"{date_from.isoformat()}T00:00:00"
    seen = {}
    pages = 0
    while pages < 40:
        pages += 1
        payload = _get_json(
            "https://statistics-api.wildberries.ru/api/v1/supplier/orders",
            headers, {"dateFrom": cursor, "flag": 0},
        )
        rows = payload if isinstance(payload, list) else []
        print(f"  WB заказы, страница {pages}: {len(rows)}")
        if not rows:
            break
        before = len(seen)
        for row in rows:
            srid = str(row.get("srid") or "")
            if srid:
                seen[srid] = row
        new_cursor = str(rows[-1].get("lastChangeDate") or "")
        if not new_cursor or new_cursor == cursor or len(seen) == before:
            break
        cursor = new_cursor
        time.sleep(61)
    return list(seen.values())


def fetch_wb_stocks(api_key: str) -> dict[str, tuple[int, int]]:
    """Текущие остатки на складах WB (FBO). Старый statistics/stocks отключён."""
    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    totals = defaultdict(lambda: [0, 0])
    offset = 0
    while offset < 500000:
        response = None
        for _attempt in range(5):
            response = requests.post(
                "https://seller-analytics-api.wildberries.ru/api/analytics/v1/stocks-report/wb-warehouses",
                headers=headers, json={"limit": 100000, "offset": offset}, timeout=90,
            )
            if response.status_code == 429:
                time.sleep(25)
                continue
            break
        response.raise_for_status()
        items = ((response.json() or {}).get("data") or {}).get("items") or []
        if not items:
            break
        for row in items:
            nm = str(row.get("nmId") or "")
            if nm:
                totals[nm][0] += int(row.get("quantity") or 0)
        if len(items) < 100000:
            break
        offset += len(items)
        time.sleep(21)
    return {nm: (pair[0], pair[1]) for nm, pair in totals.items()}


def fetch_ozon_accruals(client_id: str, api_key: str, day: date) -> list[dict]:
    from backend.ozon_client import headers as ozon_headers, BASE
    last = ""
    seen = set()
    rows = []
    while len(seen) < 40:
        body = {"date": day.isoformat()}
        if last:
            body["last_id"] = last
        response = None
        for _attempt in range(4):
            response = requests.post(
                f"{BASE}/v1/finance/accrual/by-day",
                headers=ozon_headers(client_id, api_key),
                json=body, timeout=90,
            )
            if response.status_code == 429:
                time.sleep(3)
                continue
            break
        if response is None or response.status_code >= 400:
            print(f"  Ozon начисления {day}: {getattr(response, 'status_code', 'нет ответа')}")
            break
        data = response.json() or {}
        chunk = data.get("accruals") or []
        rows.extend(chunk)
        new_last = str(data.get("last_id") or "")
        if not chunk or not new_last or new_last in seen:
            break
        seen.add(new_last)
        last = new_last
    return rows


def fetch_ozon_realization(client_id: str, api_key: str, day: date) -> list[dict]:
    from backend.ozon_client import headers as ozon_headers, BASE
    for _attempt in range(4):
        response = requests.post(
            f"{BASE}/v1/finance/realization/by-day",
            headers=ozon_headers(client_id, api_key),
            json={"day": day.day, "month": day.month, "year": day.year},
            timeout=90,
        )
        if response.status_code == 429:
            time.sleep(3)
            continue
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return (response.json() or {}).get("rows") or []
    return []


def fetch_ozon_stocks(client_id: str, api_key: str) -> dict[str, tuple[int, int]]:
    from backend.ozon_client import headers as ozon_headers, BASE
    cursor = ""
    out = {}
    for _ in range(30):
        body = {"filter": {"visibility": "ALL"}, "limit": 1000}
        if cursor:
            body["cursor"] = cursor
        response = requests.post(
            f"{BASE}/v4/product/info/stocks",
            headers=ozon_headers(client_id, api_key),
            json=body, timeout=60,
        )
        if response.status_code >= 400:
            print(f"  остатки Ozon недоступны: {response.status_code}")
            return out
        data = response.json() or {}
        items = data.get("items") or []
        for item in items:
            offer = art_key(item.get("offer_id"))
            fbo = fbs = 0
            for stock in item.get("stocks") or []:
                kind = str(stock.get("type") or "").lower()
                present = int(stock.get("present") or 0)
                if kind == "fbs":
                    fbs += present
                else:
                    fbo += present
            if offer:
                out[offer] = (fbo, fbs)
        cursor = str(data.get("cursor") or "")
        if not cursor or not items:
            break
    return out


def load_catalogs(conn):
    cogs = {}
    for article, cost in conn.execute(text("""
        SELECT DISTINCT ON (lower(trim(supplier_article)))
               supplier_article, cost_value
        FROM wb_cogs
        WHERE cost_value > 0
        ORDER BY lower(trim(supplier_article)), shipment_date DESC NULLS LAST, id DESC
    """)):
        cogs[art_key(article)] = float(cost or 0)

    names = {}
    for article, name, category in conn.execute(text("""
        SELECT supplier_article, name_ru, category_1 FROM wb_assortment
    """)):
        names[art_key(article)] = {
            "name": str(name or "").strip(),
            "category": str(category or "").strip(),
        }
    ym = {}
    for article, name, category, market_sku in conn.execute(text("""
        SELECT supplier_article, name, category, market_sku FROM ym_assortment
    """)):
        ym[art_key(article)] = {
            "name": str(name or "").strip(),
            "category": str(category or "").strip(),
            "sku": str(market_sku or "").strip(),
        }
    return cogs, names, ym


def load_ozon_postings(conn):
    rows = conn.execute(text("""
        SELECT posting_number, supplier_article, sku_name
        FROM ozon_orders
        WHERE created_at >= '2026-06-01'
    """)).fetchall()
    return {
        str(posting): (str(article or "").strip(), str(name or "").strip())
        for posting, article, name in rows
        if posting and article
    }


def load_ozon_orders(conn, date_from: date, date_to: date):
    return conn.execute(text("""
        SELECT (created_at AT TIME ZONE 'Europe/Moscow')::date AS day,
               posting_number, supplier_article, sku_name, schema_code, status,
               COALESCE(quantity, 1), COALESCE(price, 0)
        FROM ozon_orders
        WHERE (created_at AT TIME ZONE 'Europe/Moscow')::date >= :start
          AND (created_at AT TIME ZONE 'Europe/Moscow')::date < :end
    """), {"start": date_from, "end": date_to}).fetchall()


def load_ym_orders(conn, date_from: date, date_to: date):
    return conn.execute(text("""
        SELECT created_at::date, supplier_article, sku_name, status,
               COALESCE(price, 0), order_id
        FROM ym_orders
        WHERE created_at >= :start AND created_at < :end
    """), {"start": date_from, "end": date_to}).fetchall()


def load_wb_sales(conn, date_from: date, date_to: date):
    return conn.execute(text("""
        SELECT COALESCE(o.dt::date, r.rr_dt) AS day,
               bool_or(o.srid IS NULL) AS date_gap,
               min(r.sa_name) AS article,
               r.nm_id::text,
               r.doc_type_name,
               SUM(CASE WHEN r.quantity > 0 THEN r.quantity ELSE 1 END) AS qty,
               SUM(r.retail_price * CASE WHEN r.quantity > 0 THEN r.quantity ELSE 1 END) AS retail,
               SUM(r.ppvz_for_pay) AS for_pay,
               SUM(r.penalty) AS penalty,
               SUM(r.additional_payment) AS extra
        FROM wb_report_detail r
        LEFT JOIN wb_orders o ON o.srid = r.srid
        WHERE r.doc_type_name IN ('Продажа', 'Возврат')
          AND COALESCE(r.sa_name, '') <> ''
          AND COALESCE(o.dt::date, r.rr_dt) >= :start
          AND COALESCE(o.dt::date, r.rr_dt) < :end
        GROUP BY 1, lower(trim(r.sa_name)), r.nm_id::text, r.doc_type_name
    """), {"start": date_from, "end": date_to}).fetchall()


def load_wb_rates(conn):
    rows = conn.execute(text("""
        SELECT lower(trim(sa_name)) AS article,
               SUM(retail_price - ppvz_for_pay)
                   / NULLIF(SUM(retail_price), 0) AS comm_rate,
               SUM(delivery_rub + rebill_logistic_cost)
                   / NULLIF(SUM(CASE WHEN quantity > 0 THEN quantity ELSE 1 END), 0) AS log_unit
        FROM wb_report_detail
        WHERE doc_type_name = 'Продажа' AND COALESCE(sa_name, '') <> '' AND retail_price > 0
        GROUP BY 1
    """)).fetchall()
    return {
        article: {"comm_rate": float(comm or 0), "log_unit": float(log or 0)}
        for article, comm, log in rows
    }


def load_wb_returns(conn, date_from: date, date_to: date):
    rows = conn.execute(text("""
        SELECT o.dt::date AS day, lower(trim(o.supplier_article)) AS article,
               SUM(r.retail_price * CASE WHEN r.quantity > 0 THEN r.quantity ELSE 1 END) AS rub
        FROM wb_report_detail r
        JOIN wb_orders o ON o.srid = r.srid
        WHERE r.doc_type_name = 'Возврат'
          AND o.dt::date >= :start AND o.dt::date < :end
        GROUP BY 1, 2
    """), {"start": date_from, "end": date_to}).fetchall()
    return {(day, article): float(amount or 0) for day, article, amount in rows}


def fold_ozon_posting_fees(accruals, into):
    """Комиссия и логистика отправления — чтобы поставить их на дату заказа."""
    for accrual in accruals:
        unit = str(accrual.get("unit_number") or "")
        if not unit:
            continue
        slot = into.setdefault(unit, {"comm": 0.0, "log": 0.0, "buyer": 0.0})
        for product in (accrual.get("posting") or {}).get("products") or []:
            commission = product.get("commission") or {}
            if commission:
                slot["comm"] += oz_amount(commission.get("sale_commission"))
                buyer = oz_amount(commission.get("sale_price"))
                if buyer > 0:
                    slot["buyer"] += buyer
            for service in (product.get("delivery") or {}).get("services") or []:
                slot["log"] += oz_amount(service.get("accrued"))


def build_orders(wb_rows, ozon_rows, ym_rows, catalogs, ozon_sku, stocks, yesterday,
                 wb_rates=None, wb_returns=None, ozon_fees=None):
    wb_rates = wb_rates or {}
    wb_returns = wb_returns or {}
    ozon_fees = ozon_fees or {}
    cogs, names, ym_catalog = catalogs
    brand_by_article = {}
    subject_by_article = {}
    for row in wb_rows:
        key = art_key(row["article"])
        if row.get("brand"):
            brand_by_article[key] = row["brand"]
        if row.get("category"):
            subject_by_article[key] = row["category"]

    result = []
    schemes = defaultdict(Counter)
    article_info = {}

    def meta(article, fallback_name=""):
        key = art_key(article)
        card = names.get(key) or {}
        ym_card = ym_catalog.get(key) or {}
        brand = brand_by_article.get(key) or ""
        category = subject_by_article.get(key) or card.get("category") or ym_card.get("category") or ""
        name = card.get("name") or fallback_name or ym_card.get("name") or category
        brand_yellow = not brand
        if not brand:
            brand = "Vidovito"
        return brand, category, name, brand_yellow

    for row in wb_rows:
        if row["qty"] == 0 and row["cancelled"] == 0:
            continue
        brand, category, name, brand_yellow = meta(row["article"])
        rate = wb_rates.get(art_key(row["article"])) or {}
        reasons = ["commission", "logistics_wb", "ads", "internal"]
        if brand_yellow:
            reasons.append("brand")
        if not row["nm_id"]:
            reasons.append("mp_sku")
        if row["date"] == yesterday:
            reasons.append("stock")
        values, yellow = order_record(
            row["date"], "WB", brand, category, name, row["article"], row["nm_id"], row["scheme"],
            row["qty"], row["rub"], row["cancelled"], row["buyer"],
            cogs.get(art_key(row["article"]), 0), reasons,
            returns_rub=-wb_returns.get((row["date"], art_key(row["article"])), 0),
            commission=-(rate.get("comm_rate") or 0) * row["rub"],
            logistics=-(rate.get("log_unit") or 0) * row["qty"],
        )
        if row["date"] == yesterday:
            apply_stock(values, yellow, stocks.get(("WB", row["nm_id"])), 21, 22)
        result.append((values, yellow))
        schemes[(row["date"], art_key(row["article"]))][row["scheme"] or "?"] += row["qty"] or row["cancelled"]
        article_info.setdefault(art_key(row["article"]), {
            "brand": row.get("brand") or "",
            "category": row.get("category") or "",
            "article": row["article"],
        })

    ozon_grouped = {}
    for day, posting, article, sku_name, schema, status, quantity, price in ozon_rows:
        article = str(article or "").strip()
        if not article or day is None:
            continue
        scheme = "FBO" if str(schema or "").lower() == "fbo" else ("FBS" if str(schema or "").lower() == "fbs" else "")
        key = (day, art_key(article), scheme)
        bucket = ozon_grouped.setdefault(key, {
            "date": day, "article": article, "name": str(sku_name or "").strip(),
            "scheme": scheme, "qty": 0, "cancelled": 0, "rub": 0.0,
            "comm": 0.0, "log": 0.0, "buyer": 0.0, "fee_qty": 0, "bare_rub": 0.0,
        })
        qty = int(quantity or 1)
        fee = ozon_fees.get(str(posting or ""))
        if str(status or "").lower() == "cancelled":
            bucket["cancelled"] += qty
        else:
            bucket["qty"] += qty
            bucket["rub"] += float(price or 0) * qty
            if fee:
                bucket["comm"] += fee["comm"]
                bucket["log"] += fee["log"]
                bucket["buyer"] += fee["buyer"]
                bucket["fee_qty"] += qty
            else:
                bucket["bare_rub"] += float(price or 0) * qty
        if sku_name and not bucket["name"]:
            bucket["name"] = str(sku_name)
    ozon_rate = {}
    for bucket in ozon_grouped.values():
        key = art_key(bucket["article"])
        slot = ozon_rate.setdefault(key, {"comm": 0.0, "log": 0.0, "rub": 0.0, "qty": 0})
        covered = bucket["rub"] - bucket["bare_rub"]
        slot["comm"] += bucket["comm"]
        slot["log"] += bucket["log"]
        slot["rub"] += covered
        slot["qty"] += bucket["fee_qty"]
    for bucket in ozon_grouped.values():
        brand, category, name, brand_yellow = meta(bucket["article"], bucket["name"])
        rate = ozon_rate.get(art_key(bucket["article"])) or {}
        reasons = ["ads", "internal"]
        commission = bucket["comm"]
        logistics = bucket["log"]
        if bucket["bare_rub"] and rate.get("rub"):
            commission += bucket["bare_rub"] * (rate["comm"] / rate["rub"])
            reasons.append("commission")
        elif bucket["bare_rub"]:
            reasons.append("commission")
        if bucket["qty"] and bucket["fee_qty"] < bucket["qty"]:
            missing = bucket["qty"] - bucket["fee_qty"]
            if rate.get("qty"):
                logistics += missing * (rate["log"] / rate["qty"])
            reasons.append("logistics_part")
        if brand_yellow:
            reasons.append("brand")
        mp_sku = ozon_sku.get(art_key(bucket["article"]), "")
        if not mp_sku:
            reasons.append("mp_sku")
        buyer = bucket["buyer"] if bucket["buyer"] else None
        if buyer is None:
            reasons.append("buyer")
        if bucket["date"] == yesterday:
            reasons.append("stock")
        values, yellow = order_record(
            bucket["date"], "Ozon", brand, category, name or bucket["name"],
            bucket["article"], mp_sku, bucket["scheme"],
            bucket["qty"], bucket["rub"], bucket["cancelled"], buyer,
            cogs.get(art_key(bucket["article"]), 0), reasons,
            commission=commission, logistics=logistics,
        )
        if bucket["date"] == yesterday:
            apply_stock(values, yellow, stocks.get(("Ozon", art_key(bucket["article"]))), 21, 22)
        result.append((values, yellow))

    ym_grouped = {}
    for day, article, sku_name, status, price, _order_id in ym_rows:
        article = str(article or "").strip()
        if not article or day is None:
            continue
        key = (day, art_key(article))
        bucket = ym_grouped.setdefault(key, {
            "date": day, "article": article, "name": str(sku_name or "").strip(),
            "qty": 0, "cancelled": 0, "rub": 0.0,
        })
        if str(status or "").upper() == "CANCELLED":
            bucket["cancelled"] += 1
        else:
            bucket["qty"] += 1
            bucket["rub"] += float(price or 0)
    for bucket in ym_grouped.values():
        brand, category, name, brand_yellow = meta(bucket["article"], bucket["name"])
        ym_card = ym_catalog.get(art_key(bucket["article"])) or {}
        reasons = ["ym_qty", "ym_scheme", "ym_money", "ads", "buyer"]
        if brand_yellow:
            reasons.append("brand")
        if not ym_card.get("sku"):
            reasons.append("mp_sku")
        if bucket["date"] == yesterday:
            reasons.append("stock")
        values, yellow = order_record(
            bucket["date"], "Яндекс", brand, category, name or bucket["name"],
            bucket["article"], ym_card.get("sku", ""), "FBS",
            bucket["qty"], bucket["rub"], bucket["cancelled"], None,
            cogs.get(art_key(bucket["article"]), 0), reasons,
        )
        if bucket["date"] == yesterday:
            apply_stock(values, yellow, None, 21, 22)
        result.append((values, yellow))
    return result, schemes, article_info


def build_sales(wb_sales, ozon_sales, ym_rows, catalogs, schemes, article_info, yesterday):
    cogs, names, ym_catalog = catalogs
    result = []

    def meta(article, fallback_name=""):
        key = art_key(article)
        card = names.get(key) or {}
        ym_card = ym_catalog.get(key) or {}
        known = article_info.get(key) or {}
        brand = known.get("brand") or ""
        brand_fallback = not brand
        if not brand:
            brand = "Vidovito"
        category = known.get("category") or card.get("category") or ym_card.get("category") or ""
        name = card.get("name") or fallback_name or ym_card.get("name") or category
        return brand, category, name, ym_card, brand_fallback

    wb_grouped = {}
    for day, date_gap, article, nm_id, doc_type, qty, retail, for_pay, penalty, extra in wb_sales:
        article = str(article or "").strip()
        key = (day, art_key(article), str(nm_id or ""))
        bucket = wb_grouped.setdefault(key, {
            "date": day, "article": article, "nm_id": str(nm_id or ""),
            "date_gap": False,
            "sale_qty": 0, "return_qty": 0,
            "sale_retail": 0.0, "return_retail": 0.0,
            "sale_pay": 0.0, "return_pay": 0.0,
            "penalty": 0.0, "extra": 0.0,
        })
        bucket["date_gap"] = bucket["date_gap"] or bool(date_gap)
        qty = int(qty or 0)
        if doc_type == "Возврат":
            bucket["return_qty"] += qty
            bucket["return_retail"] += float(retail or 0)
            bucket["return_pay"] += float(for_pay or 0)
        else:
            bucket["sale_qty"] += qty
            bucket["sale_retail"] += float(retail or 0)
            bucket["sale_pay"] += float(for_pay or 0)
        bucket["penalty"] += float(penalty or 0)
        bucket["extra"] += float(extra or 0)

    for bucket in wb_grouped.values():
        sold_qty = bucket["sale_qty"] - bucket["return_qty"]
        sold_rub = bucket["sale_retail"] - bucket["return_retail"]
        if sold_qty == 0 and bucket["return_qty"] == 0 and sold_rub == 0:
            continue
        scheme_counter = schemes.get((bucket["date"], art_key(bucket["article"]))) or Counter()
        scheme, scheme_yellow = ("", True)
        if scheme_counter:
            scheme = scheme_counter.most_common(1)[0][0]
            if scheme == "?":
                scheme = ""
            scheme_yellow = len([k for k in scheme_counter if k and k != "?"]) != 1
        brand, category, name, _ym, brand_fallback = meta(bucket["article"])
        shown = (article_info.get(art_key(bucket["article"])) or {}).get("article") or bucket["article"]
        comm_sales = -(bucket["sale_retail"] - bucket["sale_pay"])
        comm_returns = bucket["return_retail"] - bucket["return_pay"]
        reasons = ["commission_wb", "logistics_wb", "ads", "internal"]
        if brand_fallback:
            reasons.append("brand")
        if bucket["date_gap"]:
            reasons.append("date_accrual")
        if scheme_yellow:
            reasons.append("scheme_mix")
        if not bucket["nm_id"]:
            reasons.append("mp_sku")
        values, yellow = sales_record(
            bucket["date"], "WB", brand, category, name, shown, bucket["nm_id"], scheme,
            sold_qty, sold_rub, -bucket["return_retail"], bucket["return_qty"],
            comm_sales, comm_returns, bucket["extra"], -bucket["penalty"],
            cogs.get(art_key(bucket["article"]), 0), reasons,
        )
        result.append((values, yellow))

    for bucket in ozon_sales:
        sold_qty = bucket["sale_qty"] - bucket["return_qty"]
        sold_rub = bucket["sale_rub"] - bucket["return_rub"]
        if sold_qty == 0 and bucket["return_qty"] == 0:
            continue
        brand, category, name, _ym, brand_fallback = meta(bucket["article"], bucket["name"])
        reasons = ["date_accrual", "logistics_split", "ads", "internal"]
        if brand_fallback:
            reasons.append("brand")
        if not bucket.get("scheme"):
            reasons.append("scheme_mix")
        if not bucket.get("sku"):
            reasons.append("mp_sku")
        values, yellow = sales_record(
            bucket["date"], "Ozon", brand, category, name, bucket["article"], bucket["sku"],
            bucket.get("scheme") or "FBO",
            sold_qty, sold_rub, -bucket["return_rub"], bucket["return_qty"],
            -bucket["comm_sales"], bucket["comm_returns"], bucket["compensation"],
            bucket.get("penalties", 0),
            cogs.get(art_key(bucket["article"]), 0),
            reasons,
            log_sales=bucket.get("log_sales", 0),
            log_returns=bucket.get("log_returns", 0),
            log_cancels=bucket.get("log_cancels", 0),
            ads=bucket.get("ads", 0),
            internal=bucket.get("internal", 0),
        )
        result.append((values, yellow))

    delivered = {}
    for day, article, sku_name, status, price, _order_id in ym_rows:
        if str(status or "").upper() != "DELIVERED":
            continue
        article = str(article or "").strip()
        if not article:
            continue
        key = (day, art_key(article))
        bucket = delivered.setdefault(key, {
            "date": day, "article": article, "name": str(sku_name or "").strip(),
            "qty": 0, "rub": 0.0,
        })
        bucket["qty"] += 1
        bucket["rub"] += float(price or 0)
    for bucket in delivered.values():
        brand, category, name, ym_card, brand_fallback = meta(bucket["article"], bucket["name"])
        reasons = ["date_accrual", "ym_qty", "ym_scheme", "ym_money", "ads"]
        if brand_fallback:
            reasons.append("brand")
        if not ym_card.get("sku"):
            reasons.append("mp_sku")
        values, yellow = sales_record(
            bucket["date"], "Яндекс", brand, category, name, bucket["article"],
            ym_card.get("sku", ""), "FBS",
            bucket["qty"], bucket["rub"], 0, 0,
            0, 0, 0, 0,
            cogs.get(art_key(bucket["article"]), 0),
            reasons,
        )
        result.append((values, yellow))
    return result


def write_sheet(worksheet, headers, packed_rows, numeric_until):
    packed_rows = sort_rows(list(packed_rows))
    values = [row for row, _flags in packed_rows]
    flags = [flag for _row, flag in packed_rows]
    ncols = numeric_until + 1
    need = max(len(values) + 5, 50)
    if worksheet.row_count < need or worksheet.col_count < ncols:
        worksheet.resize(rows=max(worksheet.row_count, need), cols=max(worksheet.col_count, ncols))
    end_letter = col_letter(ncols - 1)
    clear_until = max(worksheet.row_count, len(values) + 1)
    worksheet.batch_clear([f"A2:{end_letter}{clear_until}"])
    if values:
        for offset in range(0, len(values), 2000):
            chunk = values[offset:offset + 2000]
            worksheet.update(chunk, f"A{2 + offset}", raw=False)
    paint_until = max(2, len(values) + 1)
    num_end = col_letter(numeric_until)
    worksheet.batch_format([{
        "range": f"A2:{end_letter}{paint_until}",
        "format": WHITE,
    }])
    ranges = yellow_ranges(flags, ncols) if flags else []
    for offset in range(0, len(ranges), 80):
        worksheet.batch_format([
            {"range": item, "format": YELLOW} for item in ranges[offset:offset + 80]
        ])
    worksheet.batch_format([
        {"range": f"A2:A{paint_until}", "format": {"numberFormat": {"type": "DATE", "pattern": "dd.mm.yyyy"}}},
        {"range": f"I2:{num_end}{paint_until}", "format": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
        {"range": f"G2:G{paint_until}", "format": {"numberFormat": {"type": "TEXT"}}},
    ])
    apply_borders(worksheet, paint_until, ncols)
    clear_row_groups(worksheet)
    print(f"  {worksheet.title}: {len(values)} строк, жёлтых диапазонов {len(ranges)}")


def build_and_write(engine, *, date_from: date, date_to: date, yesterday: date,
                    wb_key: str, ozon_id: str, ozon_key: str, creds_path: str):
    import gspread

    print("Каталоги и заказы из базы…")
    with engine.connect() as conn:
        catalogs = load_catalogs(conn)
        postings = load_ozon_postings(conn)
        ozon_rows = load_ozon_orders(conn, date_from, date_to)
        ym_rows = load_ym_orders(conn, date_from, date_to)
        wb_sales = load_wb_sales(conn, date_from, date_to)
        wb_rates = load_wb_rates(conn)
        wb_returns = load_wb_returns(conn, date_from, date_to)
    print(f"  Ozon заказы {len(ozon_rows)}, отправления {len(postings)}, Яндекс {len(ym_rows)}, WB продажи-группы {len(wb_sales)}")

    print("Продажи Ozon по начислениям…")
    ozon_sales = []
    ozon_sku = {}
    ozon_fees = {}
    day = date_from
    while day < date_to:
        try:
            raw = fetch_ozon_accruals(ozon_id, ozon_key, day)
        except Exception as exc:
            print(f"  Ozon {day}: {exc}")
            raw = []
        fold_ozon_posting_fees(raw, ozon_fees)
        for bucket in aggregate_ozon_accruals(day, raw, postings):
            ozon_sales.append(bucket)
            if bucket["sku"]:
                ozon_sku[art_key(bucket["article"])] = bucket["sku"]
        day += timedelta(days=1)
    print(f"  строк Ozon-продаж {len(ozon_sales)}")

    print("Заказы WB…")
    wb_api_rows = fetch_wb_orders(wb_key, date_from)
    wb_orders = aggregate_wb_orders(wb_api_rows, date_from, date_to)
    print(f"  строк WB-заказов {len(wb_orders)}")

    print("Остатки…")
    stocks = {}
    try:
        for nm, pair in fetch_wb_stocks(wb_key).items():
            stocks[("WB", nm)] = pair
    except Exception as exc:
        print(f"  остатки WB: {exc}")
    try:
        for article, pair in fetch_ozon_stocks(ozon_id, ozon_key).items():
            stocks[("Ozon", article)] = pair
    except Exception as exc:
        print(f"  остатки Ozon: {exc}")

    orders, schemes, article_info = build_orders(
        wb_orders, ozon_rows, ym_rows, catalogs, ozon_sku, stocks, yesterday,
        wb_rates=wb_rates, wb_returns=wb_returns, ozon_fees=ozon_fees,
    )
    sales = build_sales(
        wb_sales, ozon_sales, ym_rows, catalogs, schemes, article_info, yesterday,
    )

    print("Запись в таблицу…")
    sheet = gspread.service_account(filename=creds_path).open_by_key(SPREADSHEET_ID)
    orders_ws = sheet.worksheet(SHEET_ORDERS)
    sales_ws = sheet.worksheet(SHEET_SALES)
    repair_headers(orders_ws, ORDER_HEADERS)
    repair_headers(sales_ws, SALES_HEADERS)
    write_sheet(orders_ws, ORDER_HEADERS, orders, 22)
    write_sheet(sales_ws, SALES_HEADERS, sales, 23)
    return len(orders), len(sales)


def main():
    load_dotenv()
    from backend.cron_lock import acquire_or_exit

    engine = create_engine(database_url(), pool_pre_ping=True)
    lock_conn = acquire_or_exit(engine, "marketplace_sheet")
    today = datetime.now(MSK).date()
    date_from = DATE_FROM
    raw_from = os.getenv("SHEET_DATE_FROM", "").strip()
    if raw_from:
        date_from = datetime.strptime(raw_from, "%Y-%m-%d").date()
    date_to = today
    yesterday = today - timedelta(days=1)
    wb_key = os.getenv("WB_API_KEY", "").strip().strip('"')
    ozon_id = os.getenv("OZON_CLIENT_ID", "").strip().strip('"')
    ozon_key = os.getenv("OZON_API_KEY", "").strip().strip('"')
    creds = os.getenv("GOOGLE_CREDS_PATH", "")
    if not creds or not os.path.exists(creds):
        creds = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot_api_key.json")
    print(f"Период {date_from} … {date_to} (не включая сегодня)")
    try:
        orders_n, sales_n = build_and_write(
            engine, date_from=date_from, date_to=date_to, yesterday=yesterday,
            wb_key=wb_key, ozon_id=ozon_id, ozon_key=ozon_key, creds_path=creds,
        )
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO system_logs (action, status, details) VALUES (:a, :s, :d)"
            ), {
                "a": "Таблица заказов и продаж",
                "s": "SUCCESS",
                "d": f"Заказы {orders_n}, продажи {sales_n}, период {date_from}–{yesterday}",
            })
        print(f"Готово: заказы {orders_n}, продажи {sales_n}")
    finally:
        lock_conn.close()


if __name__ == "__main__":
    main()
