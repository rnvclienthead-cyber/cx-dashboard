#!/usr/bin/env python3
"""Verify platform=all orders ≈ WB+YM+Ozon and factory split has no Ozon FBO."""
from backend.database import SessionLocal
from backend.routers.ppm import load_orders_data, load_hybrid_data


def month_totals(db, platform):
    df = load_orders_data(db, platform)
    if df is None or df.empty:
        return {}
    g = df.groupby(df["month_dt"].dt.strftime("%Y-%m"))["pure_orders"].sum()
    return {k: int(v) for k, v in g.items()}


def main():
    db = SessionLocal()
    wb = month_totals(db, "wb")
    ym = month_totals(db, "ym")
    oz = month_totals(db, "ozon")
    all_m = month_totals(db, "all")

    months = sorted(set(wb) | set(ym) | set(oz) | set(all_m))[-6:]
    print("=== orders by month (last 6) ===")
    print(f"{'month':8} {'WB':>10} {'YM':>10} {'Ozon':>10} {'sum3':>10} {'ALL':>10} {'delta':>8}")
    ok = True
    for m in months:
        a, b, c = wb.get(m, 0), ym.get(m, 0), oz.get(m, 0)
        s = a + b + c
        t = all_m.get(m, 0)
        d = t - s
        if d != 0:
            ok = False
        print(f"{m:8} {a:10} {b:10} {c:10} {s:10} {t:10} {d:8}")
    print("orders match:", ok)

    for p in ("wb", "ym", "ozon", "all"):
        df = load_hybrid_data(db, p)
        n = 0 if df is None or df.empty else len(df)
        print(f"claims {p}: {n}")

    df_all = load_hybrid_data(db, "all")
    if df_all is not None and not df_all.empty:
        factories = sorted({str(x) for x in df_all["factory_name"].dropna().unique()})
        ozon_f = [f for f in factories if "ozon" in f.lower()]
        print("ozon factory_name in all claims (should be empty after nulling):", ozon_f)

    # HTTP-level factory endpoint
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    r = client.get("/api/v1/analytics/orders-by-factory", params={"platform": "all"})
    print("orders-by-factory status:", r.status_code)
    if r.status_code == 200:
        payload = r.json()
        data = payload.get("data", payload)
        names = set()
        if isinstance(data, dict):
            names |= set(data.keys())
            for v in data.values():
                if isinstance(v, list):
                    for row in v:
                        if isinstance(row, dict):
                            names.add(row.get("factory") or row.get("factory_name") or row.get("Завод"))
                elif isinstance(v, dict):
                    names |= set(v.keys())
        elif isinstance(data, list):
            for row in data:
                if isinstance(row, dict):
                    names.add(row.get("factory") or row.get("factory_name") or row.get("Завод"))
        names = {n for n in names if n}
        print("factory sample:", sorted(names)[:15])
        print("has Ozon FBO:", any("ozon" in str(n).lower() for n in names))

    # dataset chart totals: all vs wb for a recent month
    for plat in ("wb", "ym", "ozon", "all"):
        r = client.get("/api/v1/analytics/ppm-dataset", params={"platform": plat})
        print(f"ppm-dataset {plat}:", r.status_code, end=" ")
        if r.status_code == 200:
            body = r.json()
            chart = body.get("chart") or body.get("monthly") or body.get("data") or {}
            # try common shapes
            if isinstance(chart, list) and chart:
                last = chart[-1]
                print("last", last)
            elif isinstance(chart, dict):
                keys = list(chart.keys())[:3]
                print("keys", keys)
            else:
                print("top keys", list(body.keys())[:8])
        else:
            print(r.text[:200])

    db.close()


if __name__ == "__main__":
    main()
