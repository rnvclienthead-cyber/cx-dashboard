"""Критические товары по рейтингу для утренней сводки Telegram."""
from __future__ import annotations

import html

from sqlalchemy import text

# На витрине зелёный от 4.5. Порог 4.4, чтобы 4.49 у флагманов ЯМ не шумел каждый день.
CRITICAL_BELOW = 4.4
# Для «чуть ниже 4.4» нужны несколько оценок. Рейтинг < 4.0 показываем с первой оценки.
MIN_REVIEWS = 5
LOW_RATING = 4.0
MAX_PER_MP = 8
MP_ORDER = ("WB", "ЯМ", "Ozon")


def is_critical(rating, reviews, *, below: float = CRITICAL_BELOW, min_reviews: int = MIN_REVIEWS) -> bool:
    try:
        r = float(rating or 0)
        n = int(reviews or 0)
    except (TypeError, ValueError):
        return False
    if r <= 0 or r >= below:
        return False
    if r < LOW_RATING:
        return n >= 1
    return n >= min_reviews


def pick_critical(rows, *, below: float = CRITICAL_BELOW, min_reviews: int = MIN_REVIEWS, limit: int = MAX_PER_MP) -> list[dict]:
    picked = []
    for row in rows or []:
        sku = str(row.get("sku") or "").strip()
        if not sku or not is_critical(row.get("rating"), row.get("reviews"), below=below, min_reviews=min_reviews):
            continue
        picked.append({
            "sku": sku,
            "rating": round(float(row["rating"]), 2),
            "reviews": int(row["reviews"] or 0),
        })
    picked.sort(key=lambda x: (x["rating"], -x["reviews"], x["sku"]))
    return picked[:limit]


def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute(text("""
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = :n
    """), {"n": name}).scalar())


def _wb_rows(conn) -> list[dict]:
    if not _table_exists(conn, "wb_ratings_totals"):
        return []
    rows = conn.execute(text("""
        SELECT TRIM(supplier_article) AS sku,
               average_rating AS rating,
               COALESCE(review_count, 0) AS reviews
        FROM wb_ratings_totals
        WHERE supplier_article IS NOT NULL
          AND TRIM(supplier_article) <> ''
    """)).mappings().all()
    return [dict(r) for r in rows]


def _ym_rows(conn) -> list[dict]:
    if _table_exists(conn, "ym_ratings_report"):
        rows = conn.execute(text("""
            WITH last AS (
                SELECT TRIM(supplier_article) AS sku,
                       group_rating AS rating,
                       COALESCE(review_count, 0) AS reviews,
                       ROW_NUMBER() OVER (
                           PARTITION BY TRIM(supplier_article)
                           ORDER BY report_date DESC
                       ) AS rn
                FROM ym_ratings_report
                WHERE supplier_article IS NOT NULL
                  AND TRIM(supplier_article) <> ''
            )
            SELECT sku, rating, reviews FROM last WHERE rn = 1
        """)).mappings().all()
        return [dict(r) for r in rows]
    if not _table_exists(conn, "ym_feedbacks"):
        return []
    rows = conn.execute(text("""
        SELECT TRIM(supplier_article) AS sku,
               AVG(valuation) AS rating,
               COUNT(*) AS reviews
        FROM ym_feedbacks
        WHERE supplier_article IS NOT NULL
          AND TRIM(supplier_article) <> ''
          AND valuation IS NOT NULL
        GROUP BY TRIM(supplier_article)
    """)).mappings().all()
    return [dict(r) for r in rows]


def _ozon_rows(conn) -> list[dict]:
    if not _table_exists(conn, "ozon_ratings_report"):
        return []
    sku_expr = "COALESCE(NULLIF(TRIM(supplier_article), ''), sku::text)"
    rows = conn.execute(text(f"""
        SELECT TRIM({sku_expr}) AS sku,
               average_rating AS rating,
               COALESCE(review_count, 0) AS reviews
        FROM ozon_ratings_report
        WHERE report_date = (SELECT MAX(report_date) FROM ozon_ratings_report)
    """)).mappings().all()
    return [dict(r) for r in rows]


def collect_critical(conn) -> dict[str, list[dict]]:
    groups = {}
    fetchers = (("WB", _wb_rows), ("ЯМ", _ym_rows), ("Ozon", _ozon_rows))
    for label, fetch in fetchers:
        try:
            groups[label] = pick_critical(fetch(conn))
        except Exception:
            groups[label] = []
    return groups


def has_critical(groups: dict | None) -> bool:
    return any(groups.get(mp) for mp in (groups or {}))


def format_critical(groups: dict | None) -> str:
    groups = groups or {}
    lines = [
        f"⭐ <b>Критические товары по рейтингу</b> (ниже {CRITICAL_BELOW:g})",
    ]
    if not has_critical(groups):
        lines.append("Нет.")
        return "\n".join(lines)
    for mp in MP_ORDER:
        items = groups.get(mp) or []
        if not items:
            lines.append(f"{mp}: нет")
            continue
        lines.append(f"{mp}:")
        for it in items:
            sku = html.escape(it["sku"])
            lines.append(f"• {sku} — {it['rating']:.2f} ({it['reviews']} оценок)")
    lines.append("https://cxvo.ru/ratings")
    return "\n".join(lines)
