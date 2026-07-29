#!/usr/bin/env python3
"""One-off: sync/close July reshipment-linked claims stuck as 'На рассмотрении'.

Reality check (2026-07-28):
- Many are already rejected on WB (status=1), but local mapping wrongly stored them
  as 'На рассмотрении'.
- None of the target set is still active → rejectcustom is not allowed.
- Older claims fall out of WB archive window → update locally for stats.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

WB_API_KEY = (os.getenv("WB_API_KEY") or "").strip().strip('"')
DB_URL = os.getenv(
    "DATABASE_URL_LOCAL",
    "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard",
)

STATUS_MAP = {
    "0": "На рассмотрении",
    "1": "Отказ",
    "2": "Одобрено",
    "3": "Отказ",
}
LOCAL_FALLBACK_EX = "Товар остается у покупателя (Заявка отклонена) — доотправка детали"


def map_status(raw_status, status_ex="", wb_comment="") -> str:
    mapped = STATUS_MAP.get(str(raw_status).strip(), str(raw_status).strip())
    if mapped in ("На рассмотрении", "Одобрено", "Отказ"):
        return mapped
    blob = f"{status_ex} {wb_comment}".lower()
    if "одобрено" in blob or "возврат" in blob or "придут деньги" in blob:
        return "Одобрено"
    if "отклон" in blob or "отказ" in blob:
        return "Отказ"
    return "На рассмотрении"


def fetch_all(is_archive: bool) -> list[dict]:
    out: list[dict] = []
    offset = 0
    while True:
        time.sleep(1.3)
        resp = requests.get(
            "https://returns-api.wildberries.ru/api/v1/claims",
            headers={"Authorization": WB_API_KEY},
            params={"is_archive": str(is_archive).lower(), "limit": 200, "offset": offset},
            timeout=40,
        )
        if resp.status_code == 429:
            print("rate limit, sleep 15s")
            time.sleep(15)
            continue
        if resp.status_code != 200:
            print(f"fetch error archive={is_archive}: {resp.status_code} {resp.text[:200]}")
            break
        claims = (resp.json() or {}).get("claims") or []
        out.extend(claims)
        print(f"{'archive' if is_archive else 'active'}: +{len(claims)} (total {len(out)})")
        if len(claims) < 200:
            break
        offset += 200
        if offset > 3000:
            break
    return out


def main() -> int:
    if not WB_API_KEY:
        print("ERROR: WB_API_KEY missing")
        return 1

    engine = create_engine(DB_URL, pool_pre_ping=True)
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(text("""
            SELECT c.claim_id, c.srid, r.id AS reship_id, r.status AS reship_status
            FROM reshipment_requests r
            JOIN wb_claims c ON LOWER(c.srid) = LOWER(r.matched_srid)
            WHERE r.created_at >= '2026-07-01' AND r.created_at < '2026-08-01'
              AND c.status = 'На рассмотрении'
              AND r.status IN ('delivered', 'ready_for_pickup', 'in_transit', 'approved')
              AND c.claim_id IS NOT NULL AND c.claim_id <> ''
            ORDER BY r.id
        """)).mappings()]

    print(f"Target pending claims: {len(rows)}")
    by_srid = {r["srid"].lower(): r for r in rows}

    active = fetch_all(False)
    archive = fetch_all(True)
    api_by_srid = {}
    for c in active + archive:
        s = (c.get("srid") or "").lower()
        if s in by_srid:
            api_by_srid[s] = c

    print(f"Found in WB API by srid: {len(api_by_srid)}")

    synced = fallback = still_open = 0
    with engine.begin() as conn:
        for srid, local in by_srid.items():
            api = api_by_srid.get(srid)
            if api:
                status = map_status(api.get("status"), api.get("status_ex"), api.get("wb_comment") or "")
                status_ex = str(api.get("status_ex") or "")
                if api.get("wb_comment"):
                    status_ex = f"{status_ex} | {api.get('wb_comment')}"[:500]
                conn.execute(text("""
                    UPDATE wb_claims
                    SET status = :status,
                        status_ex = :status_ex,
                        is_archive = :is_archive,
                        last_sync = NOW()
                    WHERE LOWER(srid) = LOWER(:srid)
                """), {
                    "status": status,
                    "status_ex": status_ex,
                    "is_archive": srid not in {(c.get("srid") or "").lower() for c in active},
                    "srid": local["srid"],
                })
                synced += 1
                print(f"[API] reship#{local['reship_id']} -> {status}")
            else:
                # Уже нельзя закрыть через API (нет в активных/архиве). Денежного возврата нет.
                # Для статистики фиксируем отказ: деталь взамен возврата.
                conn.execute(text("""
                    UPDATE wb_claims
                    SET status = 'Отказ',
                        status_ex = :status_ex,
                        is_archive = TRUE,
                        last_sync = NOW()
                    WHERE LOWER(srid) = LOWER(:srid)
                """), {"status_ex": LOCAL_FALLBACK_EX, "srid": local["srid"]})
                fallback += 1
                print(f"[LOCAL] reship#{local['reship_id']} -> Отказ (вне окна API)")

    # Final snapshot for July set with claims
    with engine.connect() as conn:
        snap = conn.execute(text("""
            SELECT COALESCE(c.status, '(нет)') AS st, COUNT(*)
            FROM reshipment_requests r
            JOIN wb_claims c ON LOWER(c.srid) = LOWER(r.matched_srid)
            WHERE r.created_at >= '2026-07-01' AND r.created_at < '2026-08-01'
            GROUP BY 1 ORDER BY 2 DESC
        """)).fetchall()
        pending_left = conn.execute(text("""
            SELECT COUNT(*)
            FROM reshipment_requests r
            JOIN wb_claims c ON LOWER(c.srid) = LOWER(r.matched_srid)
            WHERE r.created_at >= '2026-07-01' AND r.created_at < '2026-08-01'
              AND c.status = 'На рассмотрении'
              AND r.status IN ('delivered', 'ready_for_pickup', 'in_transit', 'approved')
        """)).scalar()

    print("---")
    print(f"Synced from WB API: {synced}")
    print(f"Local fallback Отказ: {fallback}")
    print(f"Still pending in target set: {pending_left}")
    print("July claims with reshipment — status breakdown:")
    for st, cnt in snap:
        print(f"  {st}: {cnt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
