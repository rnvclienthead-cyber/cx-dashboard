from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from ..database import get_db
from .auth import get_current_user

router = APIRouter(prefix="/api/ratings", tags=["Ratings"], dependencies=[Depends(get_current_user)])


# ── YM-запрос из официального отчёта ────────────────────────────────────────
# group_rating — официальный рейтинг витрины (один на группу товаров)
# review_count — всего оценок по этому SKU за всё время
# daily_new    — прирост оценок vs. предыдущий отчёт
_YM_REPORT_SQL = text("""
    SELECT
        d.report_date::text       AS date,
        d.supplier_article,
        d.group_rating            AS average_rating,
        d.review_count,
        GREATEST(0, d.review_count - COALESCE(prev.review_count, 0)) AS daily_new,
        d.stars_5, d.stars_4, d.stars_3, d.stars_2, d.stars_1
    FROM ym_ratings_report d
    LEFT JOIN ym_ratings_report prev
        ON prev.supplier_article = d.supplier_article
        AND prev.report_date = (d.report_date - INTERVAL '1 day')::date
    WHERE d.supplier_article IS NOT NULL
    ORDER BY d.report_date ASC
""")


def _ym_rows_to_dicts(rows):
    return [
        {
            "date": str(r.date),
            "supplier_article": r.supplier_article,
            "average_rating": float(r.average_rating or 0),
            "review_count": r.review_count,
            "daily_new": int(r.daily_new or 0),
            "stars_1": r.stars_1, "stars_2": r.stars_2, "stars_3": r.stars_3,
            "stars_4": r.stars_4, "stars_5": r.stars_5,
            "is_new": False,
        }
        for r in rows if r.supplier_article
    ]


@router.get("/ym/summary")
def get_ym_ratings_summary(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Последний отчёт рейтингов ЯМ: официальный рейтинг, всего оценок, прирост, звёзды."""
    try:
        rows = db.execute(text("""
            SELECT
                d.supplier_article,
                d.product_name,
                d.group_rating     AS average_rating,
                d.weekly_delta,
                d.review_count     AS total_reviews,
                GREATEST(0, d.review_count - COALESCE(prev.review_count, 0)) AS new_today,
                d.stars_5, d.stars_4, d.stars_3, d.stars_2, d.stars_1,
                d.report_date::text AS snapshot_date
            FROM ym_ratings_report d
            LEFT JOIN ym_ratings_report prev
                ON prev.supplier_article = d.supplier_article
                AND prev.report_date = (d.report_date - INTERVAL '1 day')::date
            WHERE d.report_date = (SELECT MAX(report_date) FROM ym_ratings_report)
                AND d.supplier_article IS NOT NULL
            ORDER BY d.review_count DESC
        """)).fetchall()
        return [
            {
                "supplier_article": r.supplier_article,
                "product_name":     r.product_name,
                "average_rating":   float(r.average_rating or 0),
                "weekly_delta":     float(r.weekly_delta or 0),
                "total_reviews":    r.total_reviews,
                "new_today":        int(r.new_today or 0),
                "stars_5": r.stars_5, "stars_4": r.stars_4, "stars_3": r.stars_3,
                "stars_2": r.stars_2, "stars_1": r.stars_1,
                "snapshot_date":    str(r.snapshot_date),
            }
            for r in rows
        ]
    except Exception as e:
        print(f"Ошибка YM summary: {e}")
        return []


@router.get("/")
def get_ratings(
    platform: str = Query("wb", pattern="^(wb|ym|all)$"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    columns_query = text("SELECT column_name FROM information_schema.columns WHERE table_name = 'wb_ratings_raw'")
    tables_query = text("SELECT table_name FROM information_schema.tables WHERE table_name = 'wb_assortment'")

    try:
        if platform == "ym":
            has_report = db.execute(text("SELECT COUNT(*) FROM ym_ratings_report")).scalar() or 0
            if has_report > 0:
                rows = db.execute(_YM_REPORT_SQL).fetchall()
                return _ym_rows_to_dicts(rows)
            # Fallback: если отчётов ещё нет — агрегируем из feedbacks
            fallback = db.execute(text("""
                SELECT
                    CURRENT_DATE::text AS date,
                    supplier_article,
                    ROUND(AVG(valuation::numeric), 2) AS average_rating,
                    COUNT(*)::int AS review_count,
                    0 AS daily_new,
                    COUNT(CASE WHEN valuation = 5 THEN 1 END)::int AS stars_5,
                    COUNT(CASE WHEN valuation = 4 THEN 1 END)::int AS stars_4,
                    COUNT(CASE WHEN valuation = 3 THEN 1 END)::int AS stars_3,
                    COUNT(CASE WHEN valuation = 2 THEN 1 END)::int AS stars_2,
                    COUNT(CASE WHEN valuation = 1 THEN 1 END)::int AS stars_1
                FROM ym_feedbacks
                WHERE supplier_article IS NOT NULL AND valuation IS NOT NULL
                GROUP BY supplier_article
            """)).fetchall()
            return _ym_rows_to_dicts(fallback)

        # ── WB ────────────────────────────────────────────────────────────────
        existing_cols = [r[0] for r in db.execute(columns_query).fetchall()]
        if not existing_cols:
            return []
        has_assortment = db.execute(tables_query).scalar() is not None

        def find_col(names):
            lower_map = {c.lower(): c for c in existing_cols}
            for n in names:
                if n.lower() in lower_map:
                    return lower_map[n.lower()]
            return "0"

        rating_col = find_col(["feedbackRating_current", "statistics_valuation", "valuation", "rating_current"])
        count_col  = find_col(["feedbackCount_current", "statistics_feedbacksCount", "feedbacksCount_current"])
        s1 = find_col(["oneStar_current", "statistics_star1", "star1", "onestar"])
        s2 = find_col(["twoStar_current", "statistics_star2", "star2", "twostar"])
        s3 = find_col(["threeStar_current", "statistics_star3", "star3", "threestar"])
        s4 = find_col(["fourStar_current", "statistics_star4", "star4", "fourstar"])
        s5 = find_col(["fiveStar_current", "statistics_star5", "star5", "fivestar"])

        join_clause   = "LEFT JOIN wb_assortment a ON r.sys_article = a.supplier_article" if has_assortment else ""
        is_new_select = "COALESCE(a.is_new, FALSE) as is_new" if has_assortment else "FALSE as is_new"

        wb_query = text(f"""
            SELECT
                r.sys_date as date,
                r.sys_article as supplier_article,
                CAST(COALESCE(r."{rating_col}", 0) AS FLOAT)   as average_rating,
                CAST(COALESCE(r."{count_col}",  0) AS INTEGER) as review_count,
                CAST(COALESCE(r."{s1}", 0) AS INTEGER) as stars_1,
                CAST(COALESCE(r."{s2}", 0) AS INTEGER) as stars_2,
                CAST(COALESCE(r."{s3}", 0) AS INTEGER) as stars_3,
                CAST(COALESCE(r."{s4}", 0) AS INTEGER) as stars_4,
                CAST(COALESCE(r."{s5}", 0) AS INTEGER) as stars_5,
                {is_new_select}
            FROM wb_ratings_raw r
            {join_clause}
            WHERE r.sys_article IS NOT NULL
                AND r.sys_article NOT IN ('Unknown', 'nan', '')
            ORDER BY r.sys_date ASC
        """)

        result = db.execute(wb_query).fetchall()
        wb_result = [
            {
                "date": str(r.date),
                "supplier_article": r.supplier_article,
                "average_rating": round(r.average_rating, 2) if r.average_rating <= 5.0 else round(r.average_rating / 10.0, 2),
                "review_count": r.review_count,
                "daily_new": 0,
                "stars_1": r.stars_1, "stars_2": r.stars_2, "stars_3": r.stars_3,
                "stars_4": r.stars_4, "stars_5": r.stars_5,
                "is_new": r.is_new,
            }
            for r in result
        ]

        if platform == "wb":
            return wb_result

        # platform == "all": WB + YM из официального отчёта
        ym_rows = db.execute(_YM_REPORT_SQL).fetchall()
        return wb_result + _ym_rows_to_dicts(ym_rows)

    except Exception as e:
        print(f"Ошибка чтения рейтингов: {e}")
        return []
