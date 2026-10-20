"""Средний рейтинг завода.

Оценка на карточке — одна на артикул. Если артикул шьют на одном заводе,
все его отзывы идут этому заводу. Если заводов два и больше, отзыв нельзя
разрезать между ними: такой артикул в среднюю не входит и показывается отдельно.

Средняя завода — взвешенная: каждый отзыв равен одному голосу, поэтому товар
с 1000 оценок двигает цифру сильнее, чем товар с 50.

«Надёжная оценка» — нижняя граница средней при типичном разбросе в 1 балл.
У завода с малым числом отзывов она заметно ниже средней: 4,7 на 50 отзывах
легко оказалось случайностью, а 4,5 на 1000 уже устойчиво.
"""
from __future__ import annotations

import math

# 95% нижняя граница среднего при разбросе оценок около 1 балла.
_Z = 1.96
_SD = 1.0
_PLACEHOLDERS = {"уточняется", "не указан", "не указано", "—", "-", "nan", "none"}


def normalize_rating(value) -> float:
    try:
        rating = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if rating > 5:
        rating = rating / 10.0
    if rating <= 0 or rating > 5:
        return 0.0
    return rating


def weighted_average(pairs: list[tuple[float, int]]) -> float:
    total = sum(n for _, n in pairs if n > 0)
    if total <= 0:
        return 0.0
    return round(sum(r * n for r, n in pairs if n > 0) / total, 2)


def reliable_rating(average: float, reviews: int) -> float:
    """Оценка, ниже которой завод с таким числом отзывов почти наверняка не падает."""
    if reviews <= 0 or average <= 0:
        return 0.0
    bound = average - _Z * _SD / math.sqrt(reviews)
    return round(max(1.0, min(float(average), bound)), 2)


def confidence_level(reviews: int) -> str:
    """Ступени по объёму, чтобы 1 300 и 250 000 не назывались одинаково.

    до 1 000 — мало, до 10 000 — средне, до 100 000 — много, дальше — очень много.
    """
    if reviews >= 100_000:
        return "top"
    if reviews >= 10_000:
        return "high"
    if reviews >= 1_000:
        return "mid"
    return "low"


def _clean_factory(name) -> str | None:
    raw = str(name or "").strip()
    if not raw or raw.casefold() in _PLACEHOLDERS:
        return None
    return raw


def _merge_ratings(rows: list[dict]) -> dict | None:
    usable = []
    for row in rows:
        rating = normalize_rating(row.get("rating"))
        reviews = int(row.get("reviews") or 0)
        if rating <= 0 or reviews <= 0:
            continue
        usable.append((rating, reviews, str(row.get("platform") or "")))
    if not usable:
        return None
    display = max(rows, key=lambda r: int(r.get("reviews") or 0))
    return {
        "supplier_article": str(display.get("supplier_article") or "").strip(),
        "average_rating": weighted_average([(r, n) for r, n, _ in usable]),
        "review_count": sum(n for _, n, _ in usable),
        "platforms": sorted({p for _, _, p in usable if p}),
    }


def build_factory_ratings(factory_pairs, rating_rows) -> dict:
    """Сводит артикулы в рейтинг заводов.

    factory_pairs: пары (ключ артикула, название завода).
    rating_rows: словари art, supplier_article, rating, reviews, platform.
    """
    factories_by_art: dict[str, set[str]] = {}
    for art, name in factory_pairs:
        key = str(art or "").strip()
        factory = _clean_factory(name)
        if not key or not factory:
            continue
        factories_by_art.setdefault(key, set()).add(factory)

    by_art: dict[str, list[dict]] = {}
    for row in rating_rows:
        key = str(row.get("art") or "").strip()
        if not key:
            continue
        by_art.setdefault(key, []).append(row)

    grouped: dict[str, list[dict]] = {}
    shared: list[dict] = []
    unassigned_skus = 0
    unassigned_reviews = 0

    for art, rows in by_art.items():
        merged = _merge_ratings(rows)
        if not merged:
            continue
        names = factories_by_art.get(art) or set()
        if len(names) == 0:
            unassigned_skus += 1
            unassigned_reviews += merged["review_count"]
            continue
        if len(names) > 1:
            shared.append({**merged, "factories": sorted(names)})
            continue
        factory = next(iter(names))
        grouped.setdefault(factory, []).append(merged)

    factories = []
    for name, products in grouped.items():
        products.sort(key=lambda p: (-p["review_count"], p["supplier_article"]))
        reviews = sum(p["review_count"] for p in products)
        average = weighted_average([(p["average_rating"], p["review_count"]) for p in products])
        factories.append({
            "factory_name": name,
            "average_rating": average,
            "reliable_rating": reliable_rating(average, reviews),
            "review_count": reviews,
            "sku_count": len(products),
            "confidence": confidence_level(reviews),
            "products": products,
        })
    factories.sort(key=lambda f: (-f["reliable_rating"], -f["review_count"], f["factory_name"]))
    shared.sort(key=lambda p: (-p["review_count"], p["supplier_article"]))
    own = {f["factory_name"] for f in factories}
    mentioned = {name for item in shared for name in item["factories"]}

    return {
        "factories": factories,
        "shared": shared,
        "shared_only_factories": sorted(mentioned - own),
        "unassigned_sku_count": unassigned_skus,
        "unassigned_review_count": unassigned_reviews,
    }


def _table_exists(db, name: str) -> bool:
    from sqlalchemy import text
    return db.execute(
        text("SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = :n"),
        {"n": name},
    ).scalar() is not None


def _fetch_ratings(db, platform: str) -> list[dict]:
    from sqlalchemy import text
    from .invoice_match import article_key_sql

    sources = ["wb", "ym", "ozon"] if platform == "all" else [platform]
    rows: list[dict] = []

    if "wb" in sources and _table_exists(db, "wb_ratings_totals"):
        art = article_key_sql("supplier_article")
        found = db.execute(text(f"""
            SELECT {art} AS art,
                   TRIM(supplier_article) AS supplier_article,
                   average_rating AS rating,
                   COALESCE(review_count, 0) AS reviews
            FROM wb_ratings_totals
            WHERE supplier_article IS NOT NULL
              AND TRIM(supplier_article) <> ''
        """)).mappings().all()
        rows.extend({**dict(r), "platform": "wb"} for r in found)

    if "ym" in sources and _table_exists(db, "ym_feedbacks"):
        art = article_key_sql("supplier_article")
        found = db.execute(text(f"""
            SELECT {art} AS art,
                   TRIM(supplier_article) AS supplier_article,
                   AVG(valuation) AS rating,
                   COUNT(*) AS reviews
            FROM ym_feedbacks
            WHERE supplier_article IS NOT NULL
              AND TRIM(supplier_article) <> ''
              AND valuation IS NOT NULL
            GROUP BY 1, 2
        """)).mappings().all()
        rows.extend({**dict(r), "platform": "ym"} for r in found)

    if "ozon" in sources and _table_exists(db, "ozon_ratings_report"):
        sku_expr = "COALESCE(NULLIF(TRIM(supplier_article), ''), sku::text)"
        art = article_key_sql(sku_expr)
        found = db.execute(text(f"""
            SELECT {art} AS art,
                   TRIM({sku_expr}) AS supplier_article,
                   average_rating AS rating,
                   COALESCE(review_count, 0) AS reviews
            FROM ozon_ratings_report
            WHERE report_date = (SELECT MAX(report_date) FROM ozon_ratings_report)
        """)).mappings().all()
        rows.extend({**dict(r), "platform": "ozon"} for r in found)

    return rows


def _fetch_factories(db) -> list[tuple[str, str]]:
    from sqlalchemy import text
    from .invoice_match import article_key_sql

    if not _table_exists(db, "wb_factories"):
        return []
    art = article_key_sql("supplier_article")
    found = db.execute(text(f"""
        SELECT DISTINCT {art} AS art, TRIM(factory_name) AS factory_name
        FROM wb_factories
        WHERE supplier_article IS NOT NULL
          AND TRIM(supplier_article) <> ''
          AND factory_name IS NOT NULL
          AND TRIM(factory_name) <> ''
    """)).mappings().all()
    return [(r["art"], r["factory_name"]) for r in found]


def _as_of(db, platform: str) -> str:
    from sqlalchemy import text
    queries = {
        "wb": "SELECT MAX(refreshed_at)::date FROM wb_ratings_totals",
        "ym": "SELECT MAX(created_date)::date FROM ym_feedbacks",
        "ozon": "SELECT MAX(report_date) FROM ozon_ratings_report",
    }
    sql = queries.get(platform)
    if not sql:
        return ""
    try:
        value = db.execute(text(sql)).scalar()
    except Exception:
        db.rollback()
        return ""
    return str(value or "")


def build_attributed_ratings(review_rows, source_review_count: int = 0) -> dict:
    """Отзывы спорных товаров, каждый из которых сел ровно на один завод.

    Повтор строки из-за нескольких поставок не удваивает отзыв.
    Если один отзыв пришёл на два завода, он выбрасывается.
    """
    by_id: dict = {}
    for row in review_rows:
        rid = row.get("id")
        factory = _clean_factory(row.get("factory_name"))
        rating = normalize_rating(row.get("rating"))
        if rid is None or not factory or rating <= 0:
            continue
        bucket = by_id.setdefault(str(rid), {
            "factories": set(),
            "rating": rating,
            "art": str(row.get("art") or "").strip(),
            "supplier_article": str(row.get("supplier_article") or "").strip(),
        })
        bucket["factories"].add(factory)

    kept = [b for b in by_id.values() if len(b["factories"]) == 1 and b["supplier_article"]]
    tree: dict[str, dict] = {}
    for bucket in kept:
        factory = next(iter(bucket["factories"]))
        art = bucket["art"] or bucket["supplier_article"]
        slot = tree.setdefault(factory, {}).setdefault(art, {
            "supplier_article": bucket["supplier_article"],
            "ratings": [],
        })
        slot["ratings"].append(bucket["rating"])

    factories = []
    for name, products_map in tree.items():
        products = []
        for slot in products_map.values():
            reviews = len(slot["ratings"])
            products.append({
                "supplier_article": slot["supplier_article"],
                "average_rating": weighted_average([(r, 1) for r in slot["ratings"]]),
                "review_count": reviews,
                "platforms": ["wb"],
            })
        products.sort(key=lambda p: (-p["review_count"], p["supplier_article"]))
        reviews = sum(p["review_count"] for p in products)
        average = weighted_average([(p["average_rating"], p["review_count"]) for p in products])
        factories.append({
            "factory_name": name,
            "average_rating": average,
            "reliable_rating": reliable_rating(average, reviews),
            "review_count": reviews,
            "sku_count": len(products),
            "confidence": confidence_level(reviews),
            "products": products,
        })
    factories.sort(key=lambda f: (-f["review_count"], -f["reliable_rating"], f["factory_name"]))
    return {
        "review_count": len(kept),
        "source_review_count": int(source_review_count or 0),
        "factories": factories,
    }


def _fetch_attributed(db) -> dict:
    """Отзыв WB → заказ → поставка → инвойс → завод. Только товары с двумя заводами."""
    from sqlalchemy import text
    from .invoice_match import article_key_sql, invoice_key_sql, is_real_supply_sql, supply_key_sql

    empty = {"review_count": 0, "source_review_count": 0, "factories": []}
    needed = ("wb_feedbacks", "wb_orders", "wb_logistics", "wb_invoices", "wb_factories")
    if not all(_table_exists(db, name) for name in needed):
        return empty

    art_fb = article_key_sql("f.supplier_article")
    art_inv = article_key_sql("inv.supplier_article")
    art_fac = article_key_sql("fac.supplier_article")
    art_multi = article_key_sql("supplier_article")
    multi_sql = f"""
        multi AS (
            SELECT {art_multi} AS art
            FROM wb_factories
            WHERE factory_name IS NOT NULL
              AND btrim(factory_name) <> ''
              AND factory_name <> 'Уточняется'
            GROUP BY 1
            HAVING COUNT(DISTINCT factory_name) > 1
        )
    """
    source_count = db.execute(text(f"""
        WITH {multi_sql}
        SELECT COUNT(*)
        FROM wb_feedbacks f
        JOIN multi m ON m.art = {art_fb}
        WHERE f.valuation IS NOT NULL
    """)).scalar()
    found = db.execute(text(f"""
        WITH {multi_sql}
        SELECT f.id AS id,
               {art_fb} AS art,
               TRIM(f.supplier_article) AS supplier_article,
               f.valuation AS rating,
               TRIM(fac.factory_name) AS factory_name
        FROM wb_feedbacks f
        JOIN multi m ON m.art = {art_fb}
        JOIN wb_orders o ON o.shk_id = f.last_order_shk_id
        JOIN wb_logistics l ON l.srid = o.srid
        JOIN wb_invoices inv
          ON {is_real_supply_sql("l.income_id")}
         AND {is_real_supply_sql("inv.supply_id")}
         AND {supply_key_sql("l.income_id")} = {supply_key_sql("inv.supply_id")}
         AND {art_fb} = {art_inv}
        JOIN wb_factories fac
          ON {invoice_key_sql("fac.invoice_num")} = {invoice_key_sql("inv.invoice_num")}
         AND {invoice_key_sql("fac.invoice_num")} <> ''
         AND {art_fac} = {art_fb}
         AND fac.factory_name IS NOT NULL
         AND btrim(fac.factory_name) <> ''
         AND fac.factory_name <> 'Уточняется'
        WHERE f.valuation IS NOT NULL
          AND f.last_order_shk_id IS NOT NULL
    """)).mappings().all()
    return build_attributed_ratings(found, int(source_count or 0))


def _row_from_products(name: str, products: list[dict]) -> dict:
    ordered = sorted(products, key=lambda p: (-p["review_count"], p["supplier_article"]))
    reviews = sum(p["review_count"] for p in ordered)
    average = weighted_average([(p["average_rating"], p["review_count"]) for p in ordered])
    return {
        "factory_name": name,
        "average_rating": average,
        "reliable_rating": reliable_rating(average, reviews),
        "review_count": reviews,
        "sku_count": len(ordered),
        "confidence": confidence_level(reviews),
        "products": ordered,
    }


def merge_attributed_into_factories(report: dict, attributed: dict | None) -> dict:
    """Привязанные отзывы спорных товаров входят в среднюю завода наравне с остальными."""
    by_name: dict[str, list] = {f["factory_name"]: list(f["products"]) for f in report.get("factories") or []}
    for factory in (attributed or {}).get("factories") or []:
        bucket = by_name.setdefault(factory["factory_name"], [])
        for product in factory.get("products") or []:
            bucket.append({**product, "partial": True})
    factories = [_row_from_products(name, products) for name, products in by_name.items()]
    factories.sort(key=lambda f: (-f["reliable_rating"], -f["review_count"], f["factory_name"]))
    own = {f["factory_name"] for f in factories}
    mentioned = {name for item in report.get("shared") or [] for name in item["factories"]}
    updated = dict(report)
    updated["factories"] = factories
    updated["shared_only_factories"] = sorted(mentioned - own)
    return updated


def _safe_attributed(db) -> dict:
    try:
        return _fetch_attributed(db)
    except Exception as e:
        print(f"Ошибка привязки отзывов к заводу: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return {"review_count": 0, "source_review_count": 0, "factories": []}


def factory_ratings_report(db, platform: str) -> dict:
    """«Все площадки» — одна средняя: отзывы WB, ЯМ и Ozon складываются по числу оценок."""
    factory_pairs = _fetch_factories(db)
    if platform == "all":
        rows = []
        for source in ("wb", "ym", "ozon"):
            rows.extend(_fetch_ratings(db, source))
        report = build_factory_ratings(factory_pairs, rows)
        report["platform"] = "all"
        report["as_of"] = ""
        report["attributed"] = _safe_attributed(db)
        report = merge_attributed_into_factories(report, report["attributed"])
        return {"platform": platform, "groups": [report]}

    report = build_factory_ratings(factory_pairs, _fetch_ratings(db, platform))
    report["platform"] = platform
    report["as_of"] = _as_of(db, platform)
    if platform == "wb":
        report["attributed"] = _safe_attributed(db)
        report = merge_attributed_into_factories(report, report["attributed"])
    else:
        report["attributed"] = None
    return {"platform": platform, "groups": [report]}
