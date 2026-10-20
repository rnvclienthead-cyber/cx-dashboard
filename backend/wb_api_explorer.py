"""
WB API Explorer — собирает по 5 примеров данных с каждого доступного эндпоинта WB.
Сохраняет в таблицу wb_api_samples для поиска по полям.

Запуск: python3 wb_api_explorer.py
"""

import os
import json
import time
import requests
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

WB_API_KEY = os.getenv("WB_API_KEY", "").strip()
DB_URL = "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"
engine = create_engine(DB_URL)

H = {"Authorization": WB_API_KEY, "Content-Type": "application/json"}

DATE_FROM_30 = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")
DATE_FROM_90 = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%dT00:00:00")
DATE_FROM_SHORT = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
DATE_TO = datetime.now().strftime("%Y-%m-%dT23:59:59")


def create_table():
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS wb_api_samples"))
        conn.execute(text("""
            CREATE TABLE wb_api_samples (
                id           SERIAL PRIMARY KEY,
                source       VARCHAR(100),
                endpoint     VARCHAR(500),
                description  TEXT,
                sample_index INTEGER,
                data         JSONB,
                field_names  TEXT[],
                fetched_at   TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("CREATE INDEX ON wb_api_samples USING GIN (data)"))
    print("✅ Таблица wb_api_samples создана")


def save_samples(source, endpoint, description, rows):
    if not rows:
        print(f"  ⚠️  {endpoint} — пустой ответ")
        return 0
    if not isinstance(rows, list):
        rows = [rows]
    samples = rows[:5]
    with engine.begin() as conn:
        for i, row in enumerate(samples, 1):
            fields = sorted(row.keys()) if isinstance(row, dict) else []
            conn.execute(text("""
                INSERT INTO wb_api_samples
                    (source, endpoint, description, sample_index, data, field_names)
                VALUES
                    (:source, :endpoint, :description, :idx, CAST(:data AS JSONB), :fields)
            """), {
                "source": source,
                "endpoint": endpoint,
                "description": description,
                "idx": i,
                "data": json.dumps(row, ensure_ascii=False, default=str),
                "fields": fields,
            })
    field_preview = ", ".join(list(samples[0].keys())[:10]) if isinstance(samples[0], dict) else ""
    print(f"  ✅ {endpoint} — {len(samples)} шт | {field_preview}")
    return len(samples)


def get(url, params=None, pause=3):
    try:
        r = requests.get(url, headers=H, params=params, timeout=90)
        time.sleep(pause)
        if r.status_code == 429:
            print(f"    ⏳ rate limit, жду 65с")
            time.sleep(65)
            return get(url, params, pause)
        if r.status_code != 200:
            print(f"    ❌ HTTP {r.status_code}: {r.text[:120]}")
            return None
        return r.json()
    except Exception as e:
        print(f"    ❌ {e}")
        return None


def post(url, body, params=None, pause=3):
    try:
        r = requests.post(url, headers=H, json=body, params=params, timeout=90)
        time.sleep(pause)
        if r.status_code == 204:
            return []
        if r.status_code not in (200, 201):
            print(f"    ❌ HTTP {r.status_code}: {r.text[:120]}")
            return None
        return r.json()
    except Exception as e:
        print(f"    ❌ {e}")
        return None


def run():
    create_table()
    total = 0

    # =========================================================
    print("\n📦 STATISTICS API — продажи, заказы, остатки")
    # =========================================================

    r = get("https://statistics-api.wildberries.ru/api/v1/supplier/sales",
            {"dateFrom": DATE_FROM_30, "flag": 0})
    if r:
        total += save_samples(
            "statistics-api", "/api/v1/supplier/sales",
            "Продажи/возвраты v1. Поля: srid, incomeID, nmId, supplierArticle, barcode, isCancel, finishedPrice, warehouseName",
            r
        )

    r = get("https://statistics-api.wildberries.ru/api/v1/supplier/orders",
            {"dateFrom": DATE_FROM_30, "flag": 0})
    if r:
        total += save_samples(
            "statistics-api", "/api/v1/supplier/orders",
            "Заказы v1. Поля: srid, orderId, nmId, supplierArticle, barcode, warehouseName, incomeID",
            r
        )

    r = get("https://statistics-api.wildberries.ru/api/v1/supplier/stocks",
            {"dateFrom": DATE_FROM_SHORT})
    if r:
        total += save_samples(
            "statistics-api", "/api/v1/supplier/stocks",
            "Остатки на складе. Поля: nmId, supplierArticle, barcode, quantity, warehouseName",
            r
        )

    # sales-reports/detailed v1 — 90 дней, чтобы поймать Возвраты
    print("  ⏳ sales-reports/detailed (90 дней, может быть медленно)...")
    r = post(
        "https://finance-api.wildberries.ru/api/finance/v1/sales-reports/detailed",
        {
            "dateFrom": DATE_FROM_90,
            "dateTo": DATE_TO,
            "rrdId": 0,
            "limit": 1000,
            "period": "weekly",
        },
        pause=5,
    )
    if r:
        types = {}
        for row in r:
            t = row.get("docTypeName") or row.get("doc_type_name") or "?"
            types[t] = types.get(t, 0) + 1
        print(f"    ℹ️  Типы строк: {types}")

        sale_rows = [x for x in r if (x.get("docTypeName") or x.get("doc_type_name")) == "Продажа"]
        total += save_samples(
            "finance-api", "/api/finance/v1/sales-reports/detailed [Продажа]",
            "Детальный отчёт — Продажа. Поля: rrdId, giId, shkId, srid, sku, nmId, vendorCode, trbxId, orderUid, stickerId",
            sale_rows
        )

        ret_rows = [x for x in r if (x.get("docTypeName") or x.get("doc_type_name")) == "Возврат"]
        if ret_rows:
            total += save_samples(
                "finance-api", "/api/finance/v1/sales-reports/detailed [Возврат]",
                "Детальный отчёт — Возврат. КЛЮЧЕВЫЕ ПОЛЯ ДЛЯ ПРИВЯЗКИ: giId, shkId, srid, sku, trbxId, orderUid, stickerId",
                ret_rows
            )
        else:
            print("  ⚠️  Возвратов в sales-reports/detailed за 90 дней не найдено")

    # =========================================================
    print("\n📋 RETURNS/CLAIMS API — претензии покупателей")
    # =========================================================

    for archive, label in [("false", "активные"), ("true", "архивные")]:
        r = get("https://returns-api.wildberries.ru/api/v1/claims",
                {"limit": 10, "offset": 0, "is_archive": archive})
        if r:
            items = r.get("claims", [])
            total += save_samples(
                "returns-api", f"/api/v1/claims [{label}]",
                f"Претензии {label}. Поля: id, srid, nm_id, claim_type, status, user_comment, photos, dt, delivery_dt, order_dt, price",
                items
            )

    # =========================================================
    print("\n💬 FEEDBACKS API — отзывы покупателей")
    # =========================================================

    r = get("https://feedbacks-api.wildberries.ru/api/v1/feedbacks",
            {"isAnswered": False, "take": 10, "skip": 0, "order": "dateDesc"})
    if r:
        items = r.get("data", {}).get("feedbacks", [])
        total += save_samples(
            "feedbacks-api", "/api/v1/feedbacks [unanswered]",
            "Отзывы неотвеченные. Поля: id, productDetails(nmId, nmName, supplierArticle), text, createdDate, valuation",
            items
        )

    r = get("https://feedbacks-api.wildberries.ru/api/v1/feedbacks/archive",
            {"take": 10, "skip": 0})
    if r:
        items = r.get("data", {}).get("feedbacks", []) if isinstance(r, dict) else r
        if items:
            total += save_samples(
                "feedbacks-api", "/api/v1/feedbacks/archive",
                "Архив отзывов. Те же поля что и активные.",
                items
            )

    # =========================================================
    print("\n🏪 MARKETPLACE API — FBW заказы и поставки")
    # =========================================================

    r = get("https://marketplace-api.wildberries.ru/api/v3/orders",
            {"limit": 5, "next": 0})
    if r:
        items = r.get("orders", [])
        total += save_samples(
            "marketplace-api", "/api/v3/orders",
            "Заказы FBW (сборка). Поля: id, article, barcode, skus, warehouseId, address, createdAt",
            items
        )

    r = get("https://marketplace-api.wildberries.ru/api/v3/supplies",
            {"limit": 5, "next": 0})
    if r:
        items = r.get("supplies", [])
        total += save_samples(
            "marketplace-api", "/api/v3/supplies",
            "Поставки FBW. Поля: id, name, closedAt, scanDt, isLargeCargo",
            items
        )

    r = get("https://marketplace-api.wildberries.ru/api/v3/warehouses")
    if r:
        items = r if isinstance(r, list) else r.get("warehouses", [])
        total += save_samples(
            "marketplace-api", "/api/v3/warehouses",
            "Склады WB для поставок. Поля: id, name, workTime, acceptsQR",
            items
        )

    # =========================================================
    print("\n📊 ИТОГ")
    # =========================================================

    print(f"\n🏁 Сохранено {total} примеров. Сводка:\n")
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT source, endpoint, COUNT(*) as cnt,
                   array_agg(DISTINCT f ORDER BY f) as fields
            FROM wb_api_samples,
                 UNNEST(field_names) as f
            GROUP BY source, endpoint
            ORDER BY source, endpoint
        """)).mappings().all()
        for row in rows:
            print(f"[{row['source']}] {row['endpoint']} ({row['cnt']} шт)")
            print(f"   Поля: {', '.join(row['fields'])}")
            print()


if __name__ == "__main__":
    run()
