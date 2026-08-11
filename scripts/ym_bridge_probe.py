#!/usr/bin/env python3
"""Поиск пути А(поставка) → … → Д(возврат) только через живой API Яндекса.

Не использует таблицу ym_supplies.
Фокус: отчёт goods-movement (movement_by_type: movementNumber + orderId),
плюс перекрёстный поиск общих идентификаторов между supply / order / return.
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv("/root/cx-dashboard/.env")
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

BASE = "https://api.partner.market.yandex.ru"
KEY = os.getenv("YM_API_KEY", "").strip()
CID = os.getenv("YM_CAMPAIGN_ID", "").strip()
H = {"Api-Key": KEY, "Content-Type": "application/json"}
OUT = Path("/tmp/ym_bridge_probe")
OUT.mkdir(parents=True, exist_ok=True)


def api(method: str, path: str, *, params=None, body=None):
    url = f"{BASE}{path}"
    if method.upper() == "GET":
        r = requests.get(url, headers=H, params=params or {}, timeout=90)
    else:
        r = requests.post(url, headers=H, params=params or {}, json=body if body is not None else {}, timeout=90)
    try:
        data = r.json()
    except Exception:
        data = {"__text__": r.text[:3000]}
    return r.status_code, data


def wait_report(report_id: str, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        code, data = api("GET", f"/v2/reports/info/{report_id}")
        result = (data or {}).get("result") or {}
        st = result.get("status")
        print(f"  report {report_id}: {st}")
        if st == "DONE":
            return result
        if st in ("FAILED", "ERROR"):
            return result
        time.sleep(4)
    return None


def download(url: str, name: str) -> Path:
    r = requests.get(url, headers=H, timeout=180)
    path = OUT / name
    path.write_bytes(r.content)
    return path


def extract_xlsx_rows(path: Path):
    """Грубый разбор xlsx → {sheet: [shared string cells...]} и сырой xml текст."""
    sheets = {}
    with zipfile.ZipFile(path) as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            raw = zf.read("xl/sharedStrings.xml").decode("utf-8", errors="replace")
            shared = re.findall(r"<t[^>]*>([^<]*)</t>", raw)
        for name in zf.namelist():
            if not name.startswith("xl/worksheets/") or not name.endswith(".xml"):
                continue
            xml = zf.read(name).decode("utf-8", errors="replace")
            # значения ячеек: inlineStr / v index
            vals = []
            for m in re.finditer(r"<c([^>]*)>(?:<v>([^<]*)</v>|<is><t[^>]*>([^<]*)</t></is>)?</c>", xml):
                attrs, v, inline = m.group(1), m.group(2), m.group(3)
                if inline is not None:
                    vals.append(inline)
                elif v is not None:
                    if 't="s"' in attrs:
                        try:
                            vals.append(shared[int(v)])
                        except Exception:
                            vals.append(v)
                    else:
                        vals.append(v)
            sheets[name] = {"cells": vals, "xml_head": xml[:500], "n_cells": len(vals)}
    return sheets


def collect_ids(obj, bag=None):
    if bag is None:
        bag = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if isinstance(v, (str, int, float)) and v not in ("", None):
                s = str(v).strip()
                bag.add(s)
                if any(x in kl for x in ("id", "number", "request", "supply", "order", "return", "sku", "offer", "barcode", "uit", "cis")):
                    bag.add(f"{k}={s}")
            else:
                collect_ids(v, bag)
    elif isinstance(obj, list):
        for x in obj[:100]:
            collect_ids(x, bag)
    return bag


def main():
    if not KEY or not CID:
        raise SystemExit("no YM creds")

    summary = []
    summary.append("YM BRIDGE PROBE A(supply) → D(return)")
    summary.append(f"at {datetime.now(timezone.utc).isoformat()}")
    summary.append("")

    # ── A: живые поставки из API ─────────────────────────────────────────
    code, supplies = api("POST", f"/v2/campaigns/{CID}/supply-requests", body={
        "limit": 30,
        "dateFrom": "2026-05-01",
        "dateTo": "2026-08-05",
        "types": ["SUPPLY"],
    })
    (OUT / "A_supplies.json").write_text(json.dumps(supplies, ensure_ascii=False, indent=2), encoding="utf-8")
    reqs = ((supplies or {}).get("result") or {}).get("requests") or []
    summary.append(f"A supplies HTTP {code}, count={len(reqs)}")

    supply_ids = []
    for req in reqs:
        rid = (req.get("id") or {})
        supply_ids.append({
            "request_id": rid.get("id"),
            "marketplaceRequestId": rid.get("marketplaceRequestId"),
            "warehouseRequestId": rid.get("warehouseRequestId"),
            "status": req.get("status"),
            "warehouse": (req.get("targetLocation") or {}).get("name"),
            "date": (req.get("targetLocation") or {}).get("requestedDate"),
        })
    # товары по 5 поставкам
    supply_skus = defaultdict(set)
    for s in supply_ids[:8]:
        rid = s["request_id"]
        if not rid:
            continue
        c, items = api("POST", f"/v2/campaigns/{CID}/supply-requests/items", body={"requestId": rid, "limit": 50})
        (OUT / f"A_items_{rid}.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        for it in ((items or {}).get("result") or {}).get("items") or []:
            if it.get("offerId"):
                supply_skus[rid].add(it["offerId"])
        # документы
        c2, docs = api("POST", f"/v2/campaigns/{CID}/supply-requests/documents", body={"requestId": rid})
        (OUT / f"A_docs_{rid}.json").write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(0.2)

    summary.append(f"A sample ids: {supply_ids[:3]}")
    summary.append(f"A skus loaded for {len(supply_skus)} supplies")

    # ── D: живые возвраты ────────────────────────────────────────────────
    code, returns = api("GET", f"/v2/campaigns/{CID}/returns", params={"limit": 30})
    (OUT / "D_returns.json").write_text(json.dumps(returns, ensure_ascii=False, indent=2), encoding="utf-8")
    rets = ((returns or {}).get("result") or {}).get("returns") or []
    summary.append(f"D returns HTTP {code}, count={len(rets)}")

    return_samples = []
    for ret in rets[:10]:
        return_samples.append({
            "returnId": ret.get("id") or ret.get("returnId"),
            "orderId": ret.get("orderId"),
            "warehouseId": ret.get("warehouseId") or (ret.get("logisticPickupPoint") or {}).get("id"),
            "items": [
                {
                    "shopSku": (it.get("shopSku") or it.get("offerId")),
                    "marketSku": it.get("marketSku"),
                    "count": it.get("count"),
                }
                for it in (ret.get("items") or [])[:5]
            ],
            "keys": sorted(ret.keys()),
        })
    summary.append(f"D sample: {return_samples[:2]}")

    # ── B: заказы по orderId из возвратов ────────────────────────────────
    order_bags = {}
    for rs in return_samples[:5]:
        oid = rs.get("orderId")
        if not oid:
            continue
        c, od = api("GET", f"/v2/campaigns/{CID}/orders/{oid}")
        (OUT / f"B_order_{oid}.json").write_text(json.dumps(od, ensure_ascii=False, indent=2), encoding="utf-8")
        order_bags[oid] = collect_ids(od)
        c2, st = api("POST", f"/v2/campaigns/{CID}/stats/orders", body={"orders": [int(oid)]})
        (OUT / f"B_stats_{oid}.json").write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        order_bags[oid] |= collect_ids(st)
        time.sleep(0.2)

    # ── Пересечение идентификаторов supply ↔ order/return ────────────────
    supply_bags = {}
    for s in supply_ids[:8]:
        rid = s["request_id"]
        bag = set()
        for k, v in s.items():
            if v:
                bag.add(str(v))
        for sku in supply_skus.get(rid, set()):
            bag.add(sku)
        # docs/items files
        for p in OUT.glob(f"A_*_{rid}.json"):
            try:
                bag |= collect_ids(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass
        supply_bags[rid] = bag

    return_bags = {}
    for rs in return_samples:
        rid = rs.get("returnId")
        bag = set()
        for k, v in rs.items():
            if isinstance(v, (str, int)):
                bag.add(str(v))
        for it in rs.get("items") or []:
            for v in it.values():
                if v:
                    bag.add(str(v))
        return_bags[rid] = bag

    summary.append("")
    summary.append("=== ПЕРЕСЕЧЕНИЯ supply ↔ return (без SKU-эвристики по имени файла) ===")
    interesting_overlap = 0
    for srid, sbag in supply_bags.items():
        s = next((x for x in supply_ids if x["request_id"] == srid), {})
        s_ids = {str(s.get("request_id") or ""), str(s.get("marketplaceRequestId") or ""), str(s.get("warehouseRequestId") or "")}
        s_ids = {x for x in s_ids if x}
        for rrid, rbag in return_bags.items():
            # пересечение «сильных» id поставок с возвратом
            hit = s_ids & rbag
            if hit:
                interesting_overlap += 1
                summary.append(f"HIT supply#{srid} ∩ return#{rrid}: {hit}")
    if not interesting_overlap:
        summary.append("Сильные id поставки (request/marketplace/warehouse) в возвратах НЕ встречаются.")

    summary.append("")
    summary.append("=== ПЕРЕСЕЧЕНИЯ supply ↔ order ===")
    so = 0
    for srid, sbag in supply_bags.items():
        s = next((x for x in supply_ids if x["request_id"] == srid), {})
        s_ids = {str(s.get("request_id") or ""), str(s.get("marketplaceRequestId") or ""), str(s.get("warehouseRequestId") or "")}
        s_ids = {x for x in s_ids if x}
        for oid, obag in order_bags.items():
            hit = s_ids & obag
            if hit:
                so += 1
                summary.append(f"HIT supply#{srid} ∩ order#{oid}: {hit}")
    if not so:
        summary.append("Сильные id поставки в заказах НЕ встречаются.")

    # SKU overlap (слабая зацепка B)
    summary.append("")
    summary.append("=== Слабая зацепка: общий SKU у поставки и возврата ===")
    sku_hits = 0
    for srid, skus in supply_skus.items():
        for rs in return_samples:
            rskus = {it.get("shopSku") for it in (rs.get("items") or []) if it.get("shopSku")}
            common = skus & rskus
            if common:
                sku_hits += 1
                summary.append(f"SKU overlap supply#{srid} return#{rs.get('returnId')}: {sorted(common)[:5]}")
                if sku_hits >= 8:
                    break
        if sku_hits >= 8:
            break
    summary.append(f"sku_overlap_pairs(capped)={sku_hits}")

    # ── C: отчёт goods-movement — главная надежда ────────────────────────
    summary.append("")
    summary.append("=== C: goods-movement report ===")
    for fmt in ("FILE", "JSON"):
        c, gen = api("POST", "/v2/reports/goods-movement/generate", body={
            "campaignId": int(CID),
            "dateFrom": "2026-07-01",
            "dateTo": "2026-07-31",
            "format": fmt,
        })
        (OUT / f"C_generate_{fmt}.json").write_text(json.dumps(gen, ensure_ascii=False, indent=2), encoding="utf-8")
        rid = ((gen or {}).get("result") or {}).get("reportId")
        summary.append(f"generate {fmt}: HTTP {c}, reportId={rid}")
        if not rid:
            continue
        info = wait_report(rid)
        (OUT / f"C_info_{fmt}.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        if not info:
            continue
        file_url = info.get("file")
        if isinstance(file_url, list):
            file_url = file_url[0].get("url") if file_url and isinstance(file_url[0], dict) else (file_url[0] if file_url else None)
        if isinstance(file_url, dict):
            file_url = file_url.get("url")
        if not file_url:
            summary.append(f"  no file url for {fmt}: keys={list(info.keys())}")
            continue
        path = download(file_url, f"C_movement_{fmt}.bin")
        summary.append(f"  downloaded {path.name} ({path.stat().st_size} bytes)")

        # zip/xlsx?
        if path.read_bytes()[:2] == b"PK":
            sheets = extract_xlsx_rows(path)
            (OUT / f"C_sheets_{fmt}.json").write_text(
                json.dumps({k: {"n_cells": v["n_cells"], "sample": v["cells"][:80]} for k, v in sheets.items()},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            all_cells = []
            for v in sheets.values():
                all_cells.extend(v["cells"])
            summary.append(f"  sheets={list(sheets.keys())} total_cells={len(all_cells)}")
            # типы событий / номера
            types = [x for x in all_cells if isinstance(x, str) and any(w in x.lower() for w in ("постав", "возврат", "заказ", "supply", "return", "order", "shipment", "приход", "расход"))]
            summary.append(f"  event-like strings sample: {types[:30]}")
            # поиск сильных id поставок в ячейках
            cell_set = set(str(x) for x in all_cells)
            found_mkt = []
            found_wh = []
            found_req = []
            for s in supply_ids:
                if s.get("marketplaceRequestId") and str(s["marketplaceRequestId"]) in cell_set:
                    found_mkt.append(s["marketplaceRequestId"])
                if s.get("warehouseRequestId") and str(s["warehouseRequestId"]) in cell_set:
                    found_wh.append(s["warehouseRequestId"])
                if s.get("request_id") and str(s["request_id"]) in cell_set:
                    found_req.append(s["request_id"])
            summary.append(f"  supply marketplaceRequestId in report: {found_mkt[:10]} (n={len(found_mkt)})")
            summary.append(f"  supply warehouseRequestId in report: {found_wh[:10]} (n={len(found_wh)})")
            summary.append(f"  supply request_id in report: {found_req[:10]} (n={len(found_req)})")
            # order ids from returns
            found_orders = []
            for rs in return_samples:
                oid = rs.get("orderId")
                if oid and str(oid) in cell_set:
                    found_orders.append(oid)
            summary.append(f"  return orderIds in report: {found_orders} (n={len(found_orders)})")
        else:
            # maybe json
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                (OUT / f"C_movement_{fmt}.txt").write_text(text[:50000], encoding="utf-8")
                summary.append(f"  text preview: {text[:800]}")
            except Exception as e:
                summary.append(f"  not zip/text: {e}")

        # rate limit between reports
        time.sleep(65)

    # ── Итог по возможным путям ───────────────────────────────────────────
    summary.append("")
    summary.append("=== ВЫВОД ПО ПУТЯМ ===")
    summary.append("A = supply-requests (+items/docs)")
    summary.append("B = order / stats/orders")
    summary.append("C = goods-movement (+другие отчёты)")
    summary.append("D = returns")
    summary.append("Если сильные id A не входят в B/C/D — прямой API-цепи A↔D нет.")
    summary.append("SKU-пересечение A∩D — только эвристика, не идентификатор партии.")

    text = "\n".join(summary) + "\n"
    (OUT / "SUMMARY.txt").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
