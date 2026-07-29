#!/usr/bin/env python3
"""Одноразово чинит залипшие статусы wb_claims.

1) Нормализация по status_ex / числовым кодам
2) Рефреш свежих «На рассмотрении» через Returns API (nm_id)
3) Архивные старше окна API → «Закрыта» (не «На рассмотрении»)
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from sqlalchemy import text
from backend.database import SessionLocal
from backend import wb_mini_sync as ms


def main():
    db = SessionLocal()
    try:
        n = ms._normalize_local_claim_statuses(db)
        db.commit()
        print(f"normalized: {n}")

        # Свежие — через API
        updated = ms.refresh_stale_claim_statuses(db, limit=80)
        print(f"api refresh: {updated}")

        # Старые архивные «На рассмотрении» → Закрыта (API уже не отдаёт)
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
        print(f"archived closed: {res.rowcount}")

        # Контроль: заявка Анастасии
        row = db.execute(text("""
            SELECT claim_id, status, is_archive, left(coalesce(status_ex,''), 80) AS ex
            FROM wb_claims
            WHERE claim_id = '442f40d1-7a2a-4f04-b820-4d42e1e44a61'
        """)).mappings().first()
        print("anastasia:", dict(row) if row else None)

        stuck = db.execute(text("""
            SELECT count(*) FROM wb_claims
            WHERE status = 'На рассмотрении' AND is_archive = true
        """)).scalar()
        print(f"still archive+на рассмотрении: {stuck}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
