#!/usr/bin/env python3
from collections import defaultdict
from backend.database import SessionLocal
from backend.routers import ppm as m

db = SessionLocal()
for key in list(m._PPM_CACHE.keys()):
    if str(key).startswith("ppm-dataset-"):
        m._PPM_CACHE.pop(key, None)


def month_agg(platform):
    m._PPM_CACHE.pop(f"ppm-dataset-{platform}", None)
    res = m.get_ppm_dataset(platform=platform, db=db)
    rows = res.get("data") or []
    by = defaultdict(lambda: {"orders": 0.0, "defects": 0.0})
    for r in rows:
        key = (r.get("month_dt") or "")[:7]
        by[key]["orders"] += float(r.get("orders") or 0)
        by[key]["defects"] += float(r.get("defects") or 0)
    return by, len(rows)


wb, n_wb = month_agg("wb")
ym, n_ym = month_agg("ym")
oz, n_oz = month_agg("ozon")
all_m, n_all = month_agg("all")
print("rows wb/ym/ozon/all:", n_wb, n_ym, n_oz, n_all)

months = sorted(set(wb) | set(ym) | set(oz) | set(all_m))
months = [x for x in months if x.startswith("2026-")][-5:]
hdr = "month     WBord    YMord    OZord      sum   ALLord   dOrd |    WBd    YMd    OZd   sumD   ALLd"
print(hdr)
for mo in months:
    wo, yo, oo = wb[mo]["orders"], ym[mo]["orders"], oz[mo]["orders"]
    ao = all_m[mo]["orders"]
    wd, yd, od = wb[mo]["defects"], ym[mo]["defects"], oz[mo]["defects"]
    ad = all_m[mo]["defects"]
    print(
        f"{mo:8} {wo:8.0f} {yo:8.0f} {oo:8.0f} {wo+yo+oo:8.0f} {ao:8.0f} {ao-(wo+yo+oo):6.0f} | "
        f"{wd:6.0f} {yd:6.0f} {od:6.0f} {wd+yd+od:6.0f} {ad:6.0f}"
    )

ob = m.get_orders_by_factory(platform="all", db=db)
fac = {r.get("factory_name") for r in (ob.get("data") or [])}
print("Ozon FBO in factory:", any(f and "ozon" in str(f).lower() for f in fac))
print("all != wb (Jul orders):", all_m["2026-07"]["orders"], "vs", wb["2026-07"]["orders"])
db.close()
