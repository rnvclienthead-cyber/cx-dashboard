#!/usr/bin/env python3
"""Refresh ozon_returns rows stuck on OnSellerApproval from live /v1/returns/list."""
import requests
from sqlalchemy import text
from backend.database import SessionLocal
from backend.ozon_client import BASE, headers
from backend import ozon_returns_api


def fetch_by_id(rid: int):
    resp = requests.post(
        BASE + "/v1/returns/list",
        headers=headers(),
        json={"filter": {}, "limit": 30, "last_id": max(0, rid - 1)},
        timeout=30,
    )
    if not resp.ok:
        return None
    return next(
        (x for x in (resp.json().get("returns") or []) if int(x.get("id") or 0) == rid),
        None,
    )


def main():
    db = SessionLocal()
    ozon_returns_api.ensure_schema(db.bind)
    ids = [
        int(r[0])
        for r in db.execute(
            text("SELECT return_id FROM ozon_returns WHERE status_sys = 'OnSellerApproval'")
        ).fetchall()
    ]
    print("to refresh:", len(ids))
    still = updated = missing = 0
    for rid in ids:
        hit = fetch_by_id(rid)
        if not hit:
            print(rid, "MISSING")
            missing += 1
            continue
        parsed = ozon_returns_api.parse_api_item(hit)
        print(rid, "->", parsed.get("status_sys"), parsed.get("status_ru"))
        if parsed.get("status_sys") == "OnSellerApproval":
            still += 1
        ozon_returns_api.upsert_parsed(db, parsed)
        updated += 1

    n = db.execute(
        text("SELECT count(*) FROM ozon_returns WHERE status_sys = 'OnSellerApproval'")
    ).scalar()
    print("done updated", updated, "still approval", still, "missing", missing)
    print("db approval now", n)
    for r in db.execute(
        text(
            "SELECT return_id, status_ru, posting_number, status_changed_at::date "
            "FROM ozon_returns WHERE status_sys = 'OnSellerApproval' ORDER BY return_id"
        )
    ).fetchall():
        print(" ", r)
    db.close()


if __name__ == "__main__":
    main()
