#!/usr/bin/env python3
from backend.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()
res = db.execute(text("""
UPDATE wb_claims
SET status = 'Закрыта',
    status_ex = CASE
      WHEN coalesce(nullif(trim(status_ex), ''), '') = ''
      THEN 'Статус не получен из WB API (заявка вне окна выдачи)'
      ELSE status_ex
    END,
    last_sync = NOW()
WHERE status IN ('На рассмотрении', '0', '0.0', 'Открыта')
  AND is_archive = true
  AND coalesce(claim_date, created_dt, last_sync) < NOW() - INTERVAL '14 days'
"""))
db.commit()
print("archived closed", res.rowcount)
row = db.execute(text("""
SELECT status, is_archive, left(coalesce(status_ex, ''), 60) AS ex
FROM wb_claims WHERE claim_id = '442f40d1-7a2a-4f04-b820-4d42e1e44a61'
""")).mappings().first()
print("anastasia", dict(row) if row else None)
stuck = db.execute(text("""
SELECT count(*) FROM wb_claims WHERE status = 'На рассмотрении' AND is_archive
""")).scalar()
print("still stuck archive", stuck)
db.close()
