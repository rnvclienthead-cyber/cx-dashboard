#!/usr/bin/env python3
"""Why 22 OnSellerApproval in DB vs ~3 in LK."""
from sqlalchemy import text
from backend.database import engine
import requests
from backend.ozon_client import BASE, headers

with engine.connect() as c:
    rows = c.execute(text("""
      SELECT return_id, posting_number, status_sys, status_ru, return_reason,
             return_date::date AS return_d, status_changed_at::date AS changed,
             synced_at::date AS synced, is_opened, schema_code, return_type
      FROM ozon_returns
      WHERE status_sys = 'OnSellerApproval'
      ORDER BY COALESCE(status_changed_at, return_date) ASC NULLS LAST
    """)).mappings().all()

print("db OnSellerApproval:", len(rows))
for r in rows:
    print(dict(r))

ids = [int(r["return_id"]) for r in rows]
print("\n=== live check ===")
live_map = {}
last_id = 0
for _ in range(40):
    resp = requests.post(
        BASE + "/v1/returns/list",
        headers=headers(),
        json={"filter": {}, "limit": 100, "last_id": last_id},
        timeout=40,
    )
    data = resp.json() if resp.ok else {}
    rets = data.get("returns") or []
    if not rets:
        break
    for item in rets:
        rid = int(item.get("id") or 0)
        st = ((item.get("visual") or {}).get("status") or {}).get("sys_name")
        live_map[rid] = {
            "status": st,
            "display": ((item.get("visual") or {}).get("status") or {}).get("display_name"),
            "opened": (item.get("additional_info") or {}).get("is_opened"),
        }
        last_id = max(last_id, rid)
    if not data.get("has_next"):
        break

print("scanned live returns:", len(live_map), "max id", last_id)

# Also count how many live are currently OnSellerApproval
live_approval = [rid for rid, v in live_map.items() if v["status"] == "OnSellerApproval"]
print("live OnSellerApproval in scan:", len(live_approval), sorted(live_approval)[:30])

still, stale, unknown = [], [], []
for rid in ids:
    info = live_map.get(rid)
    if info is None:
        unknown.append(rid)
    elif info["status"] == "OnSellerApproval":
        still.append(rid)
    else:
        stale.append((rid, info["status"], info["display"], info["opened"]))

print("\nDB approval still live approval:", len(still), still)
print("DB approval but live other status:", len(stale))
for row in stale:
    print(" ", row)
print("DB approval not in scanned window:", len(unknown), unknown[:30])

# Direct fetch missing ones by last_id near id
print("\n=== direct fetch unknown/stale sample ===")
for rid in (unknown + [s[0] for s in stale])[:15]:
    resp = requests.post(
        BASE + "/v1/returns/list",
        headers=headers(),
        json={"filter": {}, "limit": 20, "last_id": max(0, rid - 1)},
        timeout=30,
    )
    hit = next((x for x in (resp.json().get("returns") or []) if int(x.get("id") or 0) == rid), None)
    if not hit:
        print(rid, "NOT FOUND near id")
        continue
    st = ((hit.get("visual") or {}).get("status") or {})
    print(rid, "->", st.get("sys_name"), st.get("display_name"), "opened", (hit.get("additional_info") or {}).get("is_opened"))
