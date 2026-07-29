#!/usr/bin/env python3
from sqlalchemy import text
from backend.database import SessionLocal
db = SessionLocal()
print("2-СБ in feedbacks:")
for r in db.execute(text("""
  SELECT trim(supplier_article), count(*)
  FROM ym_feedbacks
  WHERE supplier_article LIKE '2-СБ%' AND valuation IS NOT NULL
  GROUP BY 1 ORDER BY 1
""")).fetchall():
    print(" ", r)
print("2-СБ latest report:")
for r in db.execute(text("""
  SELECT supplier_article, review_count, group_rating
  FROM ym_ratings_report
  WHERE report_date = (SELECT max(report_date) FROM ym_ratings_report)
    AND supplier_article LIKE '2-СБ%'
  ORDER BY 1
""")).fetchall():
    print(" ", r)
db.close()
