#!/usr/bin/env python3
from sqlalchemy import text
from backend.database import SessionLocal

db = SessionLocal()
try:
    print("=== rag* feedbacks ===")
    for r in db.execute(text("""
      SELECT supplier_article, valuation, created_date::text
      FROM ym_feedbacks WHERE lower(trim(supplier_article)) LIKE '%rag%'
      ORDER BY created_date DESC NULLS LAST LIMIT 15
    """)).fetchall():
        print(r)

    print("\n=== summary candidates ===")
    for r in db.execute(text("""
      SELECT TRIM(supplier_article) AS sku, COUNT(*) AS total,
             ROUND(AVG(valuation)::numeric, 2) AS rating
      FROM ym_feedbacks
      WHERE (supplier_article ILIKE '%rag%' OR supplier_article ILIKE 'BW-%')
        AND valuation IS NOT NULL AND TRIM(supplier_article) != ''
      GROUP BY 1 ORDER BY 1
    """)).fetchall():
        print(r)

    print("\n=== report rag* today ===")
    for r in db.execute(text("""
      SELECT supplier_article, group_rating, review_count, left(product_name,90)
      FROM ym_ratings_report
      WHERE lower(supplier_article) LIKE '%rag%'
        AND report_date = (SELECT max(report_date) FROM ym_ratings_report)
      ORDER BY 1
    """)).fetchall():
        print(r)

    print("\n=== BW report today ===")
    for r in db.execute(text("""
      SELECT supplier_article, group_rating, review_count, left(product_name,70)
      FROM ym_ratings_report
      WHERE report_date=(SELECT max(report_date) FROM ym_ratings_report)
        AND supplier_article ILIKE 'BW-%'
      ORDER BY 1
    """)).fetchall():
        print(r)
finally:
    db.close()
