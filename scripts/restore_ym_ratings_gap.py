"""Заполнить пропущенные дни ym_ratings_report.

Пример (дыра 7–19 августа 2026):
  PYTHONPATH=/root/cx-dashboard python3 scripts/restore_ym_ratings_gap.py \\
      --base 2026-08-06 --from 2026-08-07 --to 2026-08-19
"""
import argparse
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.ym_ratings_restore import add_stars, daterange, rating_from_stars

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
DATABASE_URL = os.getenv(
    "DATABASE_URL_LOCAL",
    "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard",
)


def parse_day(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True, help="Последний официальный день YYYY-MM-DD")
    p.add_argument("--from", dest="gap_from", required=True)
    p.add_argument("--to", dest="gap_to", required=True)
    args = p.parse_args()
    base_day = parse_day(args.base)
    gap_from = parse_day(args.gap_from)
    gap_to = parse_day(args.gap_to)
    if not (base_day < gap_from <= gap_to):
        raise SystemExit("Нужно: --base < --from <= --to")

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    inserted = 0
    with engine.begin() as conn:
        base_rows = conn.execute(text("""
            SELECT supplier_article, product_name, group_rating, weekly_delta,
                   review_count, stars_5, stars_4, stars_3, stars_2, stars_1
            FROM ym_ratings_report
            WHERE report_date = :d
        """), {"d": base_day}).mappings().all()
        if not base_rows:
            raise SystemExit(f"Нет официального снимка за {base_day}")

        fb = conn.execute(text("""
            SELECT trim(supplier_article) AS sku,
                   created_date::date AS dt,
                   count(*) FILTER (WHERE valuation = 5) AS n5,
                   count(*) FILTER (WHERE valuation = 4) AS n4,
                   count(*) FILTER (WHERE valuation = 3) AS n3,
                   count(*) FILTER (WHERE valuation = 2) AS n2,
                   count(*) FILTER (WHERE valuation = 1) AS n1
            FROM ym_feedbacks
            WHERE created_date::date >= :a AND created_date::date <= :b
              AND valuation BETWEEN 1 AND 5
            GROUP BY 1, 2
        """), {"a": gap_from, "b": gap_to}).mappings().all()
        adds = {}
        for r in fb:
            adds.setdefault(r["sku"], {})[r["dt"]] = r

        state = {}
        for r in base_rows:
            sku = str(r["supplier_article"]).strip()
            state[sku] = {
                "product_name": r["product_name"],
                "group_rating": r["group_rating"],
                "weekly_delta": r["weekly_delta"],
                "stars": (
                    r["stars_5"] or 0, r["stars_4"] or 0, r["stars_3"] or 0,
                    r["stars_2"] or 0, r["stars_1"] or 0,
                ),
            }

        for day in daterange(gap_from, gap_to):
            for sku, st in state.items():
                extra = adds.get(sku, {}).get(day)
                if extra:
                    st["stars"] = add_stars(*st["stars"], extra["n5"], extra["n4"], extra["n3"], extra["n2"], extra["n1"])
                s5, s4, s3, s2, s1 = st["stars"]
                count = s5 + s4 + s3 + s2 + s1
                computed = rating_from_stars(s5, s4, s3, s2, s1)
                # Витрина — официальная с базового дня; звёзды идут по отзывам.
                group_rating = st["group_rating"] if st["group_rating"] is not None else computed
                conn.execute(text("""
                    INSERT INTO ym_ratings_report
                        (report_date, supplier_article, product_name, group_rating,
                         weekly_delta, review_count, stars_5, stars_4, stars_3, stars_2, stars_1)
                    VALUES
                        (:report_date, :supplier_article, :product_name, :group_rating,
                         :weekly_delta, :review_count, :stars_5, :stars_4, :stars_3, :stars_2, :stars_1)
                    ON CONFLICT (report_date, supplier_article) DO UPDATE SET
                        product_name = EXCLUDED.product_name,
                        group_rating = EXCLUDED.group_rating,
                        weekly_delta = EXCLUDED.weekly_delta,
                        review_count = EXCLUDED.review_count,
                        stars_5 = EXCLUDED.stars_5, stars_4 = EXCLUDED.stars_4,
                        stars_3 = EXCLUDED.stars_3, stars_2 = EXCLUDED.stars_2,
                        stars_1 = EXCLUDED.stars_1,
                        synced_at = NOW()
                """), {
                    "report_date": day,
                    "supplier_article": sku,
                    "product_name": st["product_name"],
                    "group_rating": group_rating,
                    "weekly_delta": st["weekly_delta"],
                    "review_count": count,
                    "stars_5": s5, "stars_4": s4, "stars_3": s3, "stars_2": s2, "stars_1": s1,
                })
                inserted += 1

    print(f"Готово: {inserted} строк за {gap_from}…{gap_to}, база {len(state)} артикулов")


if __name__ == "__main__":
    main()
