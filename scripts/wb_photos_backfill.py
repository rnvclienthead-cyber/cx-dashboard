#!/usr/bin/env python3
"""Разовый бэкфилл: скачать фото WB за последние N дней на диск сервера."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, "/root/cx-dashboard")
os.chdir("/root/cx-dashboard")

from sqlalchemy import text
from dotenv import load_dotenv

load_dotenv("/root/cx-dashboard/.env")

from backend.database import engine
from backend.wb_mini_sync import _cache_wb_photo, _is_cached_photos, WB_PHOTO_DIR

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 90


def main():
    os.makedirs(WB_PHOTO_DIR, exist_ok=True)
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT srid, photos FROM wb_claims
            WHERE photos IS NOT NULL AND TRIM(photos) <> ''
              AND created_dt >= NOW() - make_interval(days => :days)
            ORDER BY created_dt DESC
        """), {"days": DAYS}).mappings().all()

    print(f"claims to check: {len(rows)}, dir={WB_PHOTO_DIR}")
    updated = 0
    for i, row in enumerate(rows, 1):
        photos = row["photos"] or ""
        if _is_cached_photos(photos):
            continue
        parts = [p for p in photos.split() if p.strip()]
        new_parts = []
        changed = False
        for p in parts:
            cdn = p.split("|")[-1].strip()
            if cdn.startswith("//"):
                cdn = "https:" + cdn
            if not cdn.startswith("http"):
                new_parts.append(p)
                continue
            cached = _cache_wb_photo(cdn)
            if cached != cdn and cached.startswith("/static/"):
                changed = True
            new_parts.append(cached)
        if not changed:
            continue
        new_photos = " ".join(new_parts)
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE wb_claims SET photos = :p WHERE srid = :s"),
                {"p": new_photos, "s": row["srid"]},
            )
        updated += 1
        if updated % 20 == 0:
            print(f"  updated {updated}/{i}")
    print(f"done. updated claims: {updated}")


if __name__ == "__main__":
    main()
