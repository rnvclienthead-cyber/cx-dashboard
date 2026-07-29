#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
from sqlalchemy import text
from backend.database import SessionLocal

db = SessionLocal()
try:
    summary = db.execute(text("""
        SELECT TRIM(supplier_article) AS sku, COUNT(*) AS total,
               ROUND(AVG(valuation)::numeric, 2) AS rating
        FROM ym_feedbacks
        WHERE supplier_article IS NOT NULL AND TRIM(supplier_article) != ''
          AND valuation IS NOT NULL
        GROUP BY TRIM(supplier_article)
    """)).mappings().fetchall()
    summary_skus = {r["sku"] for r in summary}

    matrix = db.execute(text("""
        SELECT TRIM(supplier_article) AS sku,
               COUNT(*) AS days,
               MIN(report_date)::text AS first_dt,
               MAX(report_date)::text AS last_dt,
               ROUND(AVG(group_rating)::numeric, 2) AS avg_r,
               ROUND(MAX(group_rating)::numeric, 2) AS max_r
        FROM ym_ratings_report
        WHERE supplier_article IS NOT NULL AND TRIM(supplier_article) != ''
          AND report_date >= CURRENT_DATE - INTERVAL '60 days'
          AND COALESCE(group_rating, 0) > 0
        GROUP BY TRIM(supplier_article)
    """)).mappings().fetchall()
    matrix_skus = {r["sku"] for r in matrix}

    only_matrix = sorted(matrix_skus - summary_skus)
    only_summary = sorted(summary_skus - matrix_skus)

    print(f"YM Рейтинги (ym_feedbacks): {len(summary_skus)}")
    print(f"YM По дням 60д (ym_ratings_report): {len(matrix_skus)}")
    print(f"\n=== В «По дням», НЕТ в «Рейтинги» ({len(only_matrix)}) ===")
    for sku in only_matrix:
        row = next(r for r in matrix if r["sku"] == sku)
        fb = db.execute(text("""
          SELECT count(*) AS n,
                 count(*) FILTER (WHERE valuation IS NULL) AS null_val
          FROM ym_feedbacks
          WHERE trim(coalesce(supplier_article,'')) = :s
        """), {"s": sku}).mappings().first()
        # also case-insensitive / similar
        fb_ilike = db.execute(text("""
          SELECT count(*) AS n FROM ym_feedbacks
          WHERE lower(trim(coalesce(supplier_article,''))) = lower(:s)
        """), {"s": sku}).scalar()
        print(f"  {sku}")
        print(f"    report: days={row['days']} {row['first_dt']}..{row['last_dt']} avg={row['avg_r']}")
        print(f"    feedbacks exact={dict(fb)} ilike={fb_ilike}")

    print(f"\n=== В «Рейтинги», НЕТ в «По дням» 60д ({len(only_summary)}) ===")
    for sku in only_summary:
        row = next(r for r in summary if r["sku"] == sku)
        # any report ever?
        ever = db.execute(text("""
          SELECT count(*) FROM ym_ratings_report
          WHERE trim(supplier_article)=:s
        """), {"s": sku}).scalar()
        print(f"  {sku}: feedbacks={row['total']} rating={row['rating']} report_rows_all_time={ever}")

    # newest matrix-only (likely what user noticed)
    print("\n=== Только в По дням, появились недавно (last 14d) ===")
    for sku in only_matrix:
        row = next(r for r in matrix if r["sku"] == sku)
        if row["last_dt"] >= "2026-07-14":
            print(f"  {sku}: first={row['first_dt']} last={row['last_dt']} days={row['days']}")
finally:
    db.close()
