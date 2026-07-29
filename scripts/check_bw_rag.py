#!/usr/bin/env python3
from sqlalchemy import text
from backend.database import SessionLocal

SKUS = ["BW-White-1", "BW-Black-1", "Rag-gray-2S", "Rag-grey-2S"]

db = SessionLocal()
try:
    for sku in SKUS:
        fb = db.execute(text("""
          SELECT count(*) AS n, round(avg(valuation)::numeric,2) AS rating
          FROM ym_feedbacks
          WHERE trim(supplier_article)=:s AND valuation IS NOT NULL
        """), {"s": sku}).mappings().first()
        variants = db.execute(text("""
          SELECT supplier_article, count(*)
          FROM ym_feedbacks
          WHERE lower(replace(trim(supplier_article),'grey','gray'))
              = lower(replace(:s,'grey','gray'))
          GROUP BY 1
        """), {"s": sku}).fetchall()
        report = db.execute(text("""
          SELECT report_date::text, group_rating, review_count, left(coalesce(product_name,''),80)
          FROM ym_ratings_report
          WHERE trim(supplier_article)=:s
          ORDER BY report_date DESC LIMIT 3
        """), {"s": sku}).fetchall()
        print(f"==== {sku}")
        print(f"  feedbacks valued: {dict(fb)}")
        print(f"  feedback article variants: {variants}")
        print(f"  report latest: {report}")

    print("\n=== all rag* articles ===")
    for r in db.execute(text("""
      SELECT 'fb' AS src, trim(supplier_article) AS a, count(*) n
      FROM ym_feedbacks WHERE lower(supplier_article) LIKE '%rag%' GROUP BY 2
      UNION ALL
      SELECT 'rp', trim(supplier_article), count(*)
      FROM ym_ratings_report WHERE lower(supplier_article) LIKE '%rag%'
        AND report_date >= CURRENT_DATE - INTERVAL '60 days' GROUP BY 2
      ORDER BY 1,2
    """)).fetchall():
        print(r)

    print("\n=== BW-White* ===")
    for r in db.execute(text("""
      SELECT 'fb', trim(supplier_article), count(*) FROM ym_feedbacks
      WHERE supplier_article ILIKE 'BW-White%' GROUP BY 2
      UNION ALL
      SELECT 'rp', trim(supplier_article), count(*) FROM ym_ratings_report
      WHERE supplier_article ILIKE 'BW-White%'
        AND report_date >= CURRENT_DATE - INTERVAL '60 days' GROUP BY 2
    """)).fetchall():
        print(r)
finally:
    db.close()
