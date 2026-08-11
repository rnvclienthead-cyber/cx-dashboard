#!/usr/bin/env python3
"""Выгрузка сэмплов (~10 строк) по методам Яндекс API + поиск номеров поставок.

Запуск на сервере:
  cd /root/cx-dashboard && venv/bin/python scripts/ym_api_supply_probe.py

Результаты:
  /tmp/ym_api_probe/raw/<method>.json
  /tmp/ym_api_probe/SUMMARY.txt
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
load_dotenv("/root/cx-dashboard/.env")

BASE = "https://api.partner.market.yandex.ru"
KEY = os.getenv("YM_API_KEY", "").strip()
CID = os.getenv("YM_CAMPAIGN_ID", "").strip()
BID = os.getenv("YM_BUSINESS_ID", "").strip()
OUT = Path("/tmp/ym_api_probe")
RAW = OUT / "raw"
LIMIT = 10

H = {"Api-Key": KEY, "Content-Type": "application/json"}


def psql(sql: str) -> str:
    return subprocess.check_output(
        ["sudo", "-u", "postgres", "psql", "-d", "cx_dashboard", "-tAc", sql],
        text=True,
    ).strip()


def call(method: str, path: str, *, params=None, body=None, label: str = "") -> dict:
    label = label or path.replace("/", "_").strip("_")
    url = f"{BASE}{path}"
    try:
        if method.upper() == "GET":
            r = requests.get(url, headers=H, params=params or {}, timeout=60)
        else:
            r = requests.post(url, headers=H, params=params or {}, json=body if body is not None else {}, timeout=60)
        try:
            data = r.json()
        except Exception:
            data = {"__text__": r.text[:2000]}
        payload = {
            "_meta": {
                "label": label,
                "method": method.upper(),
                "path": path,
                "params": params,
                "body": body,
                "status_code": r.status_code,
                "at": datetime.now(timezone.utc).isoformat(),
            },
            "response": data,
        }
    except Exception as e:
        payload = {
            "_meta": {"label": label, "method": method.upper(), "path": path, "error": str(e)},
            "response": None,
        }
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / f"{label}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [{payload['_meta'].get('status_code', 'ERR')}] {label}")
    time.sleep(0.35)
    return payload


def flatten_strings(obj, out=None):
    if out is None:
        out = set()
    if isinstance(obj, dict):
        for v in obj.values():
            flatten_strings(v, out)
    elif isinstance(obj, list):
        for v in obj[:50]:
            flatten_strings(v, out)
    elif isinstance(obj, (str, int, float, bool)) and obj not in (None, "", True, False):
        s = str(obj).strip()
        if s:
            out.add(s)
            # варианты без ведущих нулей
            if s.isdigit():
                out.add(s.lstrip("0") or "0")
            if s.startswith("ВРЦ-"):
                out.add(s.replace("ВРЦ-", ""))
    return out


def wait_report(report_id: str, timeout_sec: int = 90) -> dict | None:
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        info = call("GET", f"/v2/reports/info/{report_id}", label=f"report_info_{report_id}")
        result = (info.get("response") or {}).get("result") or {}
        status = result.get("status")
        if status == "DONE":
            return result
        if status in ("FAILED", "ERROR"):
            return result
        time.sleep(3)
    return None


def download_report_file(url: str, label: str) -> Path | None:
    try:
        r = requests.get(url, headers=H, timeout=120)
        path = RAW / f"{label}.bin"
        path.write_bytes(r.content)
        # если zip/xlsx — сохраним также текст-превью
        preview = RAW / f"{label}_preview.txt"
        text = ""
        if r.content[:2] == b"PK":
            import zipfile, io
            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                names = zf.namelist()
                preview.write_text("ZIP files:\n" + "\n".join(names) + "\n\n", encoding="utf-8")
                for name in names[:8]:
                    if name.endswith((".csv", ".json", ".txt")):
                        chunk = zf.read(name)[:8000]
                        try:
                            text += f"\n===== {name} =====\n" + chunk.decode("utf-8", errors="replace")
                        except Exception:
                            text += f"\n===== {name} =====\n<binary>"
                preview.write_text(preview.read_text(encoding="utf-8") + text, encoding="utf-8")
        else:
            preview.write_text(r.content[:12000].decode("utf-8", errors="replace"), encoding="utf-8")
        print(f"  [FILE] {label} ({len(r.content)} bytes)")
        return path
    except Exception as e:
        print(f"  [FILE ERR] {label}: {e}")
        return None


def main():
    if not KEY or not CID:
        raise SystemExit("YM_API_KEY / YM_CAMPAIGN_ID missing")

    OUT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)

    # ── целевые номера из БД ─────────────────────────────────────────────
    mkt_ids = [x for x in psql(
        "SELECT DISTINCT marketplace_request_id FROM ym_supplies "
        "WHERE marketplace_request_id IS NOT NULL AND TRIM(marketplace_request_id) <> '' "
        "ORDER BY 1 DESC LIMIT 40"
    ).splitlines() if x]
    wh_ids = [x for x in psql(
        "SELECT DISTINCT warehouse_request_id FROM ym_supplies "
        "WHERE warehouse_request_id IS NOT NULL AND TRIM(warehouse_request_id) <> '' "
        "ORDER BY 1 DESC LIMIT 40"
    ).splitlines() if x]
    inv_supply_ids = [x for x in psql(
        "SELECT DISTINCT supply_id FROM wb_invoices "
        "WHERE marketplace='ym' AND supply_id IS NOT NULL AND supply_id <> '' "
        "ORDER BY 1 DESC LIMIT 40"
    ).splitlines() if x]
    sample_orders = [x for x in psql(
        "SELECT order_id::text FROM ym_returns WHERE order_id IS NOT NULL ORDER BY created_at DESC LIMIT 5"
    ).splitlines() if x]
    sample_returns = psql(
        "SELECT return_id::text || ',' || order_id::text FROM ym_returns "
        "WHERE order_id IS NOT NULL ORDER BY created_at DESC LIMIT 5"
    ).splitlines()
    sample_articles = [x for x in psql(
        "SELECT DISTINCT supplier_article FROM ym_returns "
        "WHERE supplier_article IS NOT NULL AND supplier_article <> '' LIMIT 10"
    ).splitlines() if x]

    targets = {
        "marketplaceRequestId": mkt_ids,
        "warehouseRequestId": wh_ids,
        "invoice_supply_id": inv_supply_ids,
    }
    (OUT / "targets.json").write_text(json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Targets: mkt={len(mkt_ids)} wh={len(wh_ids)} invoice_supply={len(inv_supply_ids)}")
    print(f"Sample orders={sample_orders[:3]} articles={sample_articles[:5]}")

    all_payloads = []

    # ── 1. Кабинет / склады ──────────────────────────────────────────────
    all_payloads.append(call("GET", f"/v2/campaigns/{CID}", label="01_campaign"))
    all_payloads.append(call("GET", f"/v2/campaigns/{CID}/settings", label="02_campaign_settings"))
    all_payloads.append(call("GET", "/v2/warehouses", label="03_warehouses"))

    # ── 2. Заказы ────────────────────────────────────────────────────────
    all_payloads.append(call(
        "GET", f"/v2/campaigns/{CID}/orders",
        params={"limit": LIMIT, "fromDate": "01-07-2026"},
        label="10_orders_list",
    ))
    for i, oid in enumerate(sample_orders[:3]):
        all_payloads.append(call("GET", f"/v2/campaigns/{CID}/orders/{oid}", label=f"11_order_{oid}"))
        all_payloads.append(call(
            "POST", f"/v1/businesses/{BID}/orders",
            body={"orderIds": [int(oid)]},
            label=f"12_business_order_{oid}",
        ))
        all_payloads.append(call(
            "POST", f"/v2/campaigns/{CID}/stats/orders",
            body={"orders": [int(oid)]},
            label=f"13_stats_order_{oid}",
        ))
        # документы/buyer — могут 404 на физлицо
        all_payloads.append(call(
            "POST", f"/v2/campaigns/{CID}/orders/{oid}/documents",
            body={},
            label=f"14_order_docs_{oid}",
        ))

    # ── 3. Возвраты ──────────────────────────────────────────────────────
    all_payloads.append(call(
        "GET", f"/v2/campaigns/{CID}/returns",
        params={"limit": LIMIT},
        label="20_returns_list",
    ))
    for row in sample_returns[:3]:
        if "," not in row:
            continue
        rid, oid = row.split(",", 1)
        all_payloads.append(call(
            "GET", f"/v2/campaigns/{CID}/orders/{oid}/returns/{rid}",
            label=f"21_return_{rid}",
        ))

    # ── 4. Поставки ──────────────────────────────────────────────────────
    supplies = call(
        "POST", f"/v2/campaigns/{CID}/supply-requests",
        body={"limit": LIMIT, "dateFrom": "2026-01-01", "dateTo": "2026-12-31", "types": ["SUPPLY"]},
        label="30_supply_requests",
    )
    all_payloads.append(supplies)
    req_ids = []
    for req in ((supplies.get("response") or {}).get("result") or {}).get("requests") or []:
        rid = (req.get("id") or {}).get("id")
        if rid:
            req_ids.append(rid)
    for rid in req_ids[:5]:
        all_payloads.append(call(
            "POST", f"/v2/campaigns/{CID}/supply-requests/items",
            body={"requestId": rid, "limit": LIMIT},
            label=f"31_supply_items_{rid}",
        ))
        all_payloads.append(call(
            "POST", f"/v2/campaigns/{CID}/supply-requests/documents",
            body={"requestId": rid},
            label=f"32_supply_docs_{rid}",
        ))

    # ── 5. Товары / остатки / цены ───────────────────────────────────────
    arts = sample_articles[:LIMIT] or ["WH-White-4"]
    all_payloads.append(call(
        "POST", f"/v2/campaigns/{CID}/offers",
        body={"offerIds": arts},
        label="40_campaign_offers",
    ))
    all_payloads.append(call(
        "POST", f"/v2/businesses/{BID}/offer-mappings",
        body={"offerIds": arts, "limit": LIMIT},
        label="41_offer_mappings",
    ))
    all_payloads.append(call(
        "POST", f"/v2/campaigns/{CID}/offers/stocks",
        body={"offerIds": arts, "withTurnover": True},
        label="42_stocks",
    ))
    all_payloads.append(call(
        "POST", f"/v2/businesses/{BID}/offers/stocks",
        body={"offerIds": arts, "withTurnover": True},
        label="43_business_stocks",
    ))
    all_payloads.append(call(
        "POST", f"/v2/campaigns/{CID}/offer-prices",
        body={"offerIds": arts},
        label="44_offer_prices",
    ))
    all_payloads.append(call(
        "POST", f"/v2/campaigns/{CID}/stats/skus",
        body={"shopSkus": arts},
        label="45_goods_stats",
    ))

    # ── 6. FBS first-mile (на всякий случай) ─────────────────────────────
    all_payloads.append(call(
        "GET", f"/v2/campaigns/{CID}/first-mile/shipments",
        params={"limit": LIMIT, "dateFrom": "2026-01-01", "dateTo": "2026-12-31"},
        label="50_first_mile_shipments",
    ))

    # ── 7. Асинхронные отчёты, где теоретически может быть партия ────────
    report_specs = [
        ("60_report_goods_movement", "/v2/reports/goods-movement/generate", {
            "campaignId": int(CID),
            "dateFrom": "2026-07-01",
            "dateTo": "2026-07-31",
            "format": "JSON",
        }),
        ("61_report_united_orders", "/v2/reports/united-orders/generate", {
            "campaignId": int(CID),
            "dateFrom": "2026-07-01",
            "dateTo": "2026-07-07",
            "format": "JSON",
        }),
        ("62_report_realization", "/v2/reports/goods-realization/generate", {
            "campaignId": int(CID),
            "year": 2026,
            "month": 7,
            "format": "JSON",
        }),
        ("63_report_stocks_wh", "/v2/reports/stocks-on-warehouses/generate", {
            "campaignId": int(CID),
            "format": "JSON",
        }),
    ]
    for label, path, body in report_specs:
        gen = call("POST", path, body=body, label=f"{label}_generate")
        all_payloads.append(gen)
        report_id = ((gen.get("response") or {}).get("result") or {}).get("reportId")
        if not report_id:
            continue
        info = wait_report(report_id, timeout_sec=120)
        if not info:
            continue
        file_url = info.get("file") or info.get("files")
        if isinstance(file_url, list) and file_url:
            file_url = file_url[0].get("url") if isinstance(file_url[0], dict) else file_url[0]
        if isinstance(file_url, dict):
            file_url = file_url.get("url")
        if file_url:
            download_report_file(file_url, f"{label}_file")

    # ── 8. Поиск номеров ─────────────────────────────────────────────────
    # Собираем все строки из raw json + preview txt
    corpus_by_file: dict[str, set[str]] = {}
    for path in RAW.glob("*"):
        try:
            if path.suffix == ".bin":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            # также вытащим все последовательности цифр длины >= 6 и ВРЦ-
            vals = set(re.findall(r"ВРЦ-\d+", text))
            vals |= set(re.findall(r"\b\d{6,}\b", text))
            # и обычный flatten если json
            if path.suffix == ".json":
                try:
                    flatten_strings(json.loads(text), vals)
                except Exception:
                    pass
            corpus_by_file[path.name] = vals
        except Exception:
            continue

    def variants(v: str) -> set[str]:
        s = {v, v.lstrip("0") or "0"}
        if v.startswith("ВРЦ-"):
            s.add(v.replace("ВРЦ-", ""))
        else:
            s.add("ВРЦ-" + v)
        return s

    lines = []
    lines.append("YM API SUPPLY ID PROBE SUMMARY")
    lines.append(f"at: {datetime.now().isoformat()}")
    lines.append(f"files: {len(list(RAW.glob('*')))}")
    lines.append("")

    for group_name, ids in targets.items():
        lines.append("=" * 70)
        lines.append(f"GROUP: {group_name} ({len(ids)} ids)")
        lines.append("=" * 70)
        found_any = 0
        for vid in ids:
            hits = []
            for fname, vals in corpus_by_file.items():
                if variants(vid) & vals:
                    # не считаем попадание только в targets.json / supply_requests как единственный интерес —
                    # но всё равно покажем
                    hits.append(fname)
            if hits:
                found_any += 1
                # интересные хиты = не только supply_requests и targets
                interesting = [h for h in hits if not h.startswith("30_supply_requests") and h != "targets.json"
                               and "supply_items" not in h and "supply_docs" not in h and not h.startswith("targets")]
                mark = "★" if interesting else "·"
                lines.append(f"{mark} {vid}")
                lines.append(f"    all: {', '.join(sorted(hits)[:12])}")
                if interesting:
                    lines.append(f"    INTERESTING: {', '.join(sorted(interesting)[:12])}")
            else:
                lines.append(f"✗ {vid} — не найден нигде")
        lines.append(f"\nНайдено хотя бы где-то: {found_any}/{len(ids)}")
        lines.append("")

    # отдельный блок: пересечение вне supply-*
    lines.append("=" * 70)
    lines.append("ИНТЕРЕСНЫЕ ПОПАДАНИЯ ВНЕ supply-requests")
    lines.append("=" * 70)
    interesting_hits = []
    for group_name, ids in targets.items():
        for vid in ids:
            for fname, vals in corpus_by_file.items():
                if fname.startswith("30_") or fname.startswith("31_") or fname.startswith("32_") or fname == "targets.json":
                    continue
                if "supply_" in fname:
                    continue
                if variants(vid) & vals:
                    interesting_hits.append((group_name, vid, fname))
    if not interesting_hits:
        lines.append("Нет. Номера поставок/заявок на складе НЕ встречаются в заказах, возвратах, стоках и скачанных отчётах.")
    else:
        for g, vid, fname in interesting_hits[:100]:
            lines.append(f"{g} | {vid} | {fname}")

    summary = "\n".join(lines) + "\n"
    (OUT / "SUMMARY.txt").write_text(summary, encoding="utf-8")
    print("\n" + summary)
    print(f"Saved to {OUT}")


if __name__ == "__main__":
    main()
