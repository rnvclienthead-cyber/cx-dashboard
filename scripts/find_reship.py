#!/usr/bin/env python3
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
from sqlalchemy import text
from backend.database import SessionLocal

q = sys.argv[1] if len(sys.argv) > 1 else "4013ee4ae67c4fcb839ff15b0baca16a"

def short(d):
    out = {}
    for k, v in dict(d).items():
        if v is None:
            continue
        s = str(v)
        out[k] = s[:200] + ("..." if len(s) > 200 else "")
    return out

db = SessionLocal()
try:
    cols = [r[0] for r in db.execute(text("""
      SELECT column_name FROM information_schema.columns
      WHERE table_name='reshipment_requests' ORDER BY ordinal_position
    """)).fetchall()]
    print("COLS", cols)

    # search broadly
    where_bits = []
    params = {"q": f"%{q}%"}
    for c in cols:
        where_bits.append(f"cast({c} as text) ILIKE :q")
    sql = f"SELECT * FROM reshipment_requests WHERE {' OR '.join(where_bits)} ORDER BY id DESC LIMIT 10"
    rows = db.execute(text(sql), params).mappings().fetchall()
    print("FOUND", len(rows))
    for r in rows:
        print(json.dumps(short(r), ensure_ascii=False, default=str))

    # also yandex/cdek fields specifically
    for table in ("reshipment_requests",):
        pass
finally:
    db.close()
