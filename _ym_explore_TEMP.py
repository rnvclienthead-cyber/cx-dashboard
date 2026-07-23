"""
╔══════════════════════════════════════════════════════════════════╗
║  ВРЕМЕННЫЙ РАЗВЕДОЧНЫЙ СКРИПТ — УДАЛИТЬ ПОСЛЕ ИССЛЕДОВАНИЯ      ║
║  Цель: найти идентификаторы, связывающие возвраты ЯМ с           ║
║  поставками/инвойсами. Бьём все доступные методы API,            ║
║  собираем сэмплы, строим матрицу пересечений.                    ║
╚══════════════════════════════════════════════════════════════════╝

Запуск:
    PYTHONPATH=/root/cx-dashboard python3 _ym_explore_TEMP.py > /tmp/ym_explore.txt 2>&1
    cat /tmp/ym_explore.txt | grep -A5 "MATCH\|FOUND\|пересечен"

Что делает:
    1. Вызывает ~15 методов API, пишет полный JSON сэмпла
    2. Для конкретной пары orderId/returnId — все order-specific методы
    3. Строит плоский набор всех строковых значений из каждого метода
    4. Ищет ПЕРЕСЕЧЕНИЯ: какие ID из одного метода встречаются в другом
    5. Ищет инвойсы (RNV26-Pxx) в любом ответе
"""

import json
import os
import sys
import time
from collections import defaultdict

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

YM_API_KEY     = os.getenv("YM_API_KEY", "").strip()
YM_CAMPAIGN_ID = os.getenv("YM_CAMPAIGN_ID", "").strip()
YM_BUSINESS_ID = os.getenv("YM_BUSINESS_ID", "").strip()
DATABASE_URL   = os.getenv(
    "DATABASE_URL_LOCAL",
    "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard",
)
BASE = "https://api.partner.market.yandex.ru"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def hdr():
    return {"Api-Key": YM_API_KEY, "Content-Type": "application/json"}


def get(path, params=None):
    r = requests.get(f"{BASE}{path}", headers=hdr(), params=params or {}, timeout=30)
    if r.status_code not in (200, 201):
        return {"__ERROR__": r.status_code, "__TEXT__": r.text[:300]}
    ct = r.headers.get("content-type", "")
    if "json" not in ct:
        return {"__BINARY__": ct, "__SIZE__": len(r.content), "__PREVIEW__": r.content[:80].hex()}
    try:
        return r.json()
    except Exception as e:
        return {"__JSON_ERR__": str(e), "__TEXT__": r.text[:300]}


def post(path, body=None):
    r = requests.post(f"{BASE}{path}", headers=hdr(), json=body or {}, timeout=30)
    if r.status_code not in (200, 201):
        return {"__ERROR__": r.status_code, "__TEXT__": r.text[:300]}
    ct = r.headers.get("content-type", "")
    if "json" not in ct:
        return {"__BINARY__": ct, "__SIZE__": len(r.content), "__PREVIEW__": r.content[:80].hex()}
    try:
        return r.json()
    except Exception as e:
        return {"__JSON_ERR__": str(e), "__TEXT__": r.text[:300]}


def sep(title):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def pp(data, truncate=4000):
    s = json.dumps(data, ensure_ascii=False, indent=2)
    print(s[:truncate] + (" ...[обрезано]" if len(s) > truncate else ""))


# ─── Flatten all string/int/float leaf values from nested dict/list ──────────
def flatten_values(obj, prefix=""):
    """Рекурсивно извлекаем все строковые и числовые значения как (path, value)."""
    result = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            result.extend(flatten_values(v, f"{prefix}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5]):  # сэмплируем max 5 элементов
            result.extend(flatten_values(v, f"{prefix}[{i}]"))
    elif isinstance(obj, (str, int, float)) and obj not in (None, "", 0):
        result.append((prefix, str(obj)))
    return result


# ─── 0. Загружаем известные ID из БД ─────────────────────────────────────────
sep("0. ИЗВЕСТНЫЕ ID ИЗ БД")

known = {}
with engine.connect() as conn:
    # Инвойсы (внутренние номера типа RNV26-Pxx)
    inv_rows = conn.execute(text(
        "SELECT DISTINCT invoice_num, supply_id FROM wb_invoices "
        "WHERE supply_id IS NOT NULL AND supply_id != '' LIMIT 30"
    )).fetchall()
    known["invoices_internal"] = [r[0] for r in inv_rows if r[0] and r[0] != "0"]
    known["invoices_wb_supply"] = [r[1] for r in inv_rows if r[1]]

    # Несколько последних возвратов ЯМ
    ret_rows = conn.execute(text(
        "SELECT return_id, order_id, supplier_article FROM ym_returns "
        "ORDER BY created_at DESC LIMIT 5"
    )).fetchall()
    known["ym_return_ids"]  = [str(r[0]) for r in ret_rows]
    known["ym_order_ids"]   = [str(r[1]) for r in ret_rows]
    known["ym_articles"]    = list(set(r[2] for r in ret_rows if r[2]))

# Целевая пара (первый возврат из списка)
TARGET_RETURN_ID = int(known["ym_return_ids"][0]) if known["ym_return_ids"] else 26522031
TARGET_ORDER_ID  = int(known["ym_order_ids"][0])  if known["ym_order_ids"]  else 56873805891
TARGET_ARTICLE   = known["ym_articles"][0] if known["ym_articles"] else "POLKO-BW-White-2"

print(f"Целевой возврат:  {TARGET_RETURN_ID}")
print(f"Целевой заказ:    {TARGET_ORDER_ID}")
print(f"Целевой артикул:  {TARGET_ARTICLE}")
print(f"Внутренних инвойсов: {len(known['invoices_internal'])} — {known['invoices_internal'][:5]}")
print(f"WB supply_id:        {len(known['invoices_wb_supply'])} — {known['invoices_wb_supply'][:3]}")

# Общий сборник всех ответов: { метка_метода: [flat_values] }
all_responses: dict[str, list] = {}


def record(label, data):
    """Сохранить ответ и сразу вывести."""
    print(f"\n--- {label} ---")
    pp(data)
    all_responses[label] = flatten_values(data)
    time.sleep(0.4)
    return data

# ─── 1. ЗАКАЗЫ ────────────────────────────────────────────────────────────────
sep("1. ЗАКАЗЫ")

record("orders_list",
    get(f"/v2/campaigns/{YM_CAMPAIGN_ID}/orders", {"limit": 2, "fromDate": "01-01-2026"}))

record("order_single",
    get(f"/v2/campaigns/{YM_CAMPAIGN_ID}/orders/{TARGET_ORDER_ID}"))

record("order_identifiers",
    get(f"/v2/campaigns/{YM_CAMPAIGN_ID}/orders/{TARGET_ORDER_ID}/identifiers"))

record("order_items",
    get(f"/v2/campaigns/{YM_CAMPAIGN_ID}/orders/{TARGET_ORDER_ID}/items"))

# бизнес-уровневый список заказов (v1)
record("orders_business",
    post(f"/v1/businesses/{YM_BUSINESS_ID}/orders",
         {"orderIds": [TARGET_ORDER_ID]}))

# ─── 2. ВОЗВРАТЫ ─────────────────────────────────────────────────────────────
sep("2. ВОЗВРАТЫ")

record("returns_list",
    get(f"/v2/campaigns/{YM_CAMPAIGN_ID}/returns", {"limit": 2}))

record("return_single",
    get(f"/v2/campaigns/{YM_CAMPAIGN_ID}/orders/{TARGET_ORDER_ID}/returns/{TARGET_RETURN_ID}"))

# Заявление на возврат (PDF-мета)
record("return_application",
    get(f"/v2/campaigns/{YM_CAMPAIGN_ID}/orders/{TARGET_ORDER_ID}/returns/{TARGET_RETURN_ID}/application"))

# ─── 3. ПОСТАВКИ (SUPPLY REQUESTS) ──────────────────────────────────────────
sep("3. ПОСТАВКИ — supply-requests")

# Все поставки — максимум полей
sup_data = record("supply_requests",
    post(f"/v2/campaigns/{YM_CAMPAIGN_ID}/supply-requests", {
        "limit": 10,
        "dateFrom": "2025-01-01",
        "dateTo":   "2026-12-31",
    }))

# Собираем все requestId из ответа
supply_req_ids = []
for req in (sup_data.get("result", {}).get("requests") or []):
    rid = req.get("id", {}).get("id")
    if rid:
        supply_req_ids.append(rid)
    known.setdefault("supply_marketplace_ids", []).append(
        req.get("id", {}).get("marketplaceRequestId", ""))
    known.setdefault("supply_warehouse_ids", []).append(
        req.get("id", {}).get("warehouseRequestId", ""))

print(f"\n  Найдено requestId: {supply_req_ids[:6]}")
print(f"  marketplaceRequestId (номер акта): {known.get('supply_marketplace_ids', [])[:6]}")
print(f"  warehouseRequestId:                {known.get('supply_warehouse_ids', [])[:6]}")

# Для каждой поставки — товарный состав
for sid in supply_req_ids[:5]:
    record(f"supply_items_{sid}",
        post(f"/v2/campaigns/{YM_CAMPAIGN_ID}/supply-requests/items",
             {"requestId": sid, "limit": 20}))

# Документы поставки
for sid in supply_req_ids[:3]:
    record(f"supply_docs_{sid}",
        post(f"/v2/campaigns/{YM_CAMPAIGN_ID}/supply-requests/documents",
             {"requestId": sid}))

# ─── 4. ПЕРВАЯ МИЛЯ / ОТГРУЗКИ FBS ──────────────────────────────────────────
sep("4. FIRST-MILE SHIPMENTS (FBS)")

record("first_mile_shipments",
    get(f"/v2/campaigns/{YM_CAMPAIGN_ID}/first-mile/shipments",
        {"limit": 3, "dateFrom": "2026-01-01", "dateTo": "2026-12-31"}))

# ─── 5. СТОКИ / АССОРТИМЕНТ ──────────────────────────────────────────────────
sep("5. СТОКИ И АССОРТИМЕНТ")

record("stocks",
    post(f"/v2/businesses/{YM_BUSINESS_ID}/offers/stocks",
         {"offerIds": [TARGET_ARTICLE], "withTurnover": True}))

# Детальная карточка оффера
record("offer_detail",
    post(f"/v2/businesses/{YM_BUSINESS_ID}/offer-mappings",
         {"offerIds": [TARGET_ARTICLE], "limit": 1}))

# ─── 6. ФИНАНСЫ ──────────────────────────────────────────────────────────────
sep("6. ФИНАНСЫ — сводки и отчёты")

record("finance_reports_list",
    get(f"/v2/campaigns/{YM_CAMPAIGN_ID}/stats/orders",
        {"dateFrom": "2026-05-01", "dateTo": "2026-05-31",
         "orders": [TARGET_ORDER_ID]}))

# Транзакции
record("transactions",
    post(f"/v2/campaigns/{YM_CAMPAIGN_ID}/billing/transactions/services",
         {"dateFrom": "2026-05-01", "dateTo": "2026-05-31", "limit": 3}))

# ─── 7. ДРУГИЕ МЕТОДЫ ────────────────────────────────────────────────────────
sep("7. РАЗНЫЕ МЕТОДЫ")

# Рейтинг магазина
record("campaign_stats",
    get(f"/v2/campaigns/{YM_CAMPAIGN_ID}"))

# Склады
record("warehouses",
    get(f"/v1/campaigns/{YM_CAMPAIGN_ID}/warehouses"))

# Товарные сборки / комплекты
record("order_delivery_shipments",
    get(f"/v2/campaigns/{YM_CAMPAIGN_ID}/orders/{TARGET_ORDER_ID}/delivery/shipments"))

# stat orders — разбивка по заказу
record("stats_orders",
    post(f"/v2/campaigns/{YM_CAMPAIGN_ID}/stats/orders",
         {"orders": [TARGET_ORDER_ID]}))

# ─── 8. МАТРИЦА ПЕРЕСЕЧЕНИЙ ──────────────────────────────────────────────────
sep("8. ═══ МАТРИЦА ПЕРЕСЕЧЕНИЙ ИДЕНТИФИКАТОРОВ ═══")

print("""
Для каждой пары методов смотрим: есть ли значения из метода A в ответе метода B.
Ищем особо: orderId, returnId, marketplaceRequestId (номер поставки), invoice.
""")

# Плоские наборы значений по методам
flat_sets: dict[str, set] = {
    label: set(v for _, v in pairs)
    for label, pairs in all_responses.items()
}

# Конкретные ID, которые ищем
search_ids = {
    "TARGET_ORDER_ID":      str(TARGET_ORDER_ID),
    "TARGET_RETURN_ID":     str(TARGET_RETURN_ID),
}
for i, mp in enumerate(known.get("supply_marketplace_ids", [])[:5]):
    if mp: search_ids[f"supply_marketplace_{i}"] = mp
for i, wh in enumerate(known.get("supply_warehouse_ids", [])[:5]):
    if wh: search_ids[f"supply_warehouse_{i}"] = wh
for inv in known.get("invoices_internal", [])[:10]:
    search_ids[f"invoice_{inv}"] = inv

print(f"\n{'ID':45} {'Найден в методах':}")
print("-" * 80)
for label, val in search_ids.items():
    found_in = [m for m, s in flat_sets.items() if val in s]
    status = ", ".join(found_in) if found_in else "❌  НЕ НАЙДЕН ни в одном методе"
    print(f"  {label:43} {status}")

# Парные пересечения (самые интересные: supply ↔ orders ↔ returns)
sep("9. ПАРНЫЕ ПЕРЕСЕЧЕНИЯ (overlap между методами)")

key_methods = [k for k in flat_sets if any(x in k for x in
    ["order", "return", "supply", "shipment", "transaction"])]

for i, m1 in enumerate(key_methods):
    for m2 in key_methods[i+1:]:
        overlap = flat_sets[m1] & flat_sets[m2]
        # Убираем слишком короткие и числа до 3 знаков (шум)
        overlap = {v for v in overlap if len(v) > 3 and not (v.isdigit() and len(v) <= 4)}
        if overlap:
            print(f"\n  [{m1}] ∩ [{m2}]")
            print(f"    Общих значений: {len(overlap)}")
            print(f"    Примеры: {list(overlap)[:10]}")

# ─── 10. ПОИСК ИНВОЙСОВ В ОТВЕТАХ ────────────────────────────────────────────
sep("10. ПОИСК ВНУТРЕННИХ ИНВОЙСОВ (RNV-...) В ОТВЕТАХ API")

invoices_set = set(known.get("invoices_internal", []))
found_any = False
for method, pairs in all_responses.items():
    hits = [(path, val) for path, val in pairs if val in invoices_set]
    if hits:
        print(f"\n  ✅ НАЙДЕНО в [{method}]:")
        for path, val in hits:
            print(f"      {path} = {val}")
        found_any = True
if not found_any:
    print("\n  ❌ Внутренние инвойсы (RNV-...) не найдены ни в одном ответе ЯМ API.")
    print("     Это ожидаемо — они существуют только у нас в БД, не в ЯМ.")
    print("     Нужно строить связь через supply_marketplaceRequestId (номер акта ЯМ).")

sep("ИТОГ")
print("""
Смотри разделы выше:
 - Раздел 8: какой ID в каком методе найден
 - Раздел 9: пересечения между методами
 - Раздел 10: инвойсы в ответах ЯМ

Если orderId встретился в supply_items/supply_docs — связь есть напрямую.
Если нет — ищем цепочку через shipmentId или warehouseRequestId.
""")
