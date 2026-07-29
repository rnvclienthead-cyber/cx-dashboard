#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
from sqlalchemy import text
from backend.database import SessionLocal

db = SessionLocal()
try:
    print("feedbacks Bo-Black-3 variants:")
    for r in db.execute(text("""
      SELECT supplier_article, count(*), round(avg(valuation)::numeric,2) AS r
      FROM ym_feedbacks
      WHERE lower(trim(supplier_article)) = lower('Bo-Black-3')
      GROUP BY 1
    """)).fetchall():
        print(" ", r)

    print("\nNewest matrix-only (no exact article in feedbacks):")
    for r in db.execute(text("""
    WITH matrix AS (
      SELECT TRIM(supplier_article) AS sku,
             MIN(report_date) AS first_dt, MAX(report_date) AS last_dt, COUNT(*) AS days
      FROM ym_ratings_report
      WHERE report_date >= CURRENT_DATE - INTERVAL '60 days'
        AND COALESCE(group_rating,0) > 0
        AND TRIM(supplier_article) != ''
      GROUP BY 1
    ),
    summary AS (
      SELECT TRIM(supplier_article) AS sku
      FROM ym_feedbacks
      WHERE valuation IS NOT NULL AND TRIM(supplier_article) != ''
      GROUP BY 1
    )
    SELECT m.sku, m.first_dt::text, m.last_dt::text, m.days
    FROM matrix m
    LEFT JOIN summary s ON s.sku = m.sku
    WHERE s.sku IS NULL
    ORDER BY m.first_dt DESC, m.sku
    """)).fetchall():
        print(" ", r)

    cols = [c[0] for c in db.execute(text("""
      SELECT column_name FROM information_schema.columns
      WHERE table_name='ym_ratings_report' ORDER BY ordinal_position
    """)).fetchall()]
    print("\nym_ratings_report:", cols)

    # sample recent for 2-СБ
    print("\nsample 2-СБ report:")
    for r in db.execute(text("""
      SELECT * FROM ym_ratings_report
      WHERE supplier_article LIKE '2-%%СБ%%'
      ORDER BY report_date DESC LIMIT 4
    """)).mappings():
        print(" ", dict(r))

    # is there card/model grouping? group_rating shared across SKUs?
    print("\nSame group_rating cluster example Dryer:")
    for r in db.execute(text("""
      SELECT report_date, supplier_article, group_rating
      FROM ym_ratings_report
      WHERE supplier_article LIKE 'Dryer%%' AND report_date='2026-07-28'
      ORDER BY 2
    """)).fetchall():
        print(" ", r)
finally:
    db.close()
