#!/usr/bin/env python3
from sqlalchemy import text
from backend.database import engine

NM = "208670312"
with engine.connect() as c:
    rows = c.execute(text("""
      SELECT claim_id, status, status_ex, nm_id, srid, claim_date, created_dt, last_sync, is_archive,
             left(coalesce(user_comment,''), 140) uc
      FROM wb_claims WHERE nm_id::text = :nm
      ORDER BY coalesce(claim_date, created_dt) DESC NULLS LAST LIMIT 20
    """), {"nm": NM}).mappings().fetchall()
    print("claims", len(rows))
    for r in rows:
        print(dict(r))

    chats = c.execute(text("""
      SELECT chat_id, client_name, nm_id, needs_answer, order_rid,
             to_timestamp(last_ts/1000.0) AS last_at
      FROM wb_chats WHERE nm_id::text = :nm OR client_name ILIKE '%Алин%'
      ORDER BY last_ts DESC NULLS LAST LIMIT 10
    """), {"nm": NM}).mappings().fetchall()
    print("chats:")
    for r in chats:
        print(dict(r))

    # sync freshness overall
    s = c.execute(text("""
      SELECT
        count(*) AS total,
        count(*) FILTER (WHERE last_sync > now() - interval '1 hour') AS sync_1h,
        count(*) FILTER (WHERE last_sync > now() - interval '24 hours') AS sync_24h,
        count(*) FILTER (WHERE last_sync IS NULL) AS sync_null,
        max(last_sync) AS max_sync,
        min(last_sync) AS min_sync,
        count(*) FILTER (WHERE status = 'На рассмотрении') AS status_review,
        count(*) FILTER (WHERE status ILIKE '%отказ%' OR status = 'Отклонено' OR status = 'Отказ') AS status_reject
      FROM wb_claims
    """)).mappings().first()
    print("freshness:", dict(s))

    # distinct statuses
    for r in c.execute(text("""
      SELECT status, count(*) FROM wb_claims GROUP BY 1 ORDER BY 2 DESC LIMIT 20
    """)).mappings():
        print("status:", dict(r))
