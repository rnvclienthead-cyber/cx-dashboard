#!/usr/bin/env python3
"""Find which SKUs correspond to the two market cards and sheet presence."""
from sqlalchemy import text
from backend.database import SessionLocal

MARKET_IDS = ["4577070750", "4638431611"]
NAME_HINTS = [
    "%вешал%175%",
    "%вешал%175,5%",
    "%вешал%175.5%",
    "%насадк%швабр%",
    "%микрофибр%2%",
    "%TO-White%",
    "%TO-Black%",
    "%Rag%",
]

db = SessionLocal()
try:
    # columns on ym_feedbacks / assortment-like tables
    for table in ("ym_feedbacks", "ym_ratings_report", "ym_assortment", "ym_products", "ym_offers"):
        exists = db.execute(text(
            "SELECT to_regclass(:t)"
        ), {"t": table}).scalar()
        print(f"table {table}: {exists}")

    print("\n--- search by market id / name in ratings_report ---")
    for mid in MARKET_IDS:
        rows = db.execute(text("""
          SELECT DISTINCT supplier_article, left(product_name,80), max(report_date)::text
          FROM ym_ratings_report
          WHERE product_name ILIKE :p OR supplier_article ILIKE :p
          GROUP BY 1,2
          LIMIT 20
        """), {"p": f"%{mid}%"}).fetchall()
        print(f"mid {mid}:", rows)

    print("\n--- hanger-like in report ---")
    for r in db.execute(text("""
      SELECT DISTINCT ON (supplier_article)
        supplier_article, left(product_name,100), group_rating, review_count, report_date::text
      FROM ym_ratings_report
      WHERE product_name ILIKE '%вешал%'
        AND (product_name ILIKE '%175%' OR supplier_article ILIKE 'TO-%' OR supplier_article ILIKE '%-to%'
             OR product_name ILIKE '%стойка%')
      ORDER BY supplier_article, report_date DESC
    """)).fetchall():
        print(" ", r)

    print("\n--- mop/nasadki-like in report ---")
    for r in db.execute(text("""
      SELECT DISTINCT ON (supplier_article)
        supplier_article, left(product_name,100), group_rating, review_count, report_date::text
      FROM ym_ratings_report
      WHERE product_name ILIKE '%насад%'
         OR product_name ILIKE '%швабр%'
         OR supplier_article ILIKE 'Rag%'
         OR supplier_article ILIKE '%mop%'
      ORDER BY supplier_article, report_date DESC
    """)).fetchall():
        print(" ", r)

    print("\n--- TO-* and Rag* in feedbacks vs report ---")
    for sku_pat in ("TO-%", "Rag%", "BW-%", "BO-%"):
        fb = db.execute(text("""
          SELECT trim(supplier_article), count(*)
          FROM ym_feedbacks WHERE supplier_article ILIKE :p AND valuation IS NOT NULL
          GROUP BY 1 ORDER BY 1
        """), {"p": sku_pat}).fetchall()
        rp = db.execute(text("""
          SELECT trim(supplier_article), max(report_date)::text,
                 bool_or(coalesce(group_rating,0)>0)
          FROM ym_ratings_report WHERE supplier_article ILIKE :p
            AND report_date >= CURRENT_DATE - INTERVAL '60 days'
          GROUP BY 1 ORDER BY 1
        """), {"p": sku_pat}).fetchall()
        print(f"\n{sku_pat} feedbacks:", fb)
        print(f"{sku_pat} report 60d:", rp)

finally:
    db.close()
