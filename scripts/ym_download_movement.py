#!/usr/bin/env python3
"""Скачать отчёт «движение товаров» правильно: format — query-параметр."""
from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv("/root/cx-dashboard/.env")

BASE = "https://api.partner.market.yandex.ru"
KEY = os.getenv("YM_API_KEY", "").strip()
CID = int(os.getenv("YM_CAMPAIGN_ID", "0"))
H = {"Api-Key": KEY, "Content-Type": "application/json"}
OUT = Path("/tmp/ym_movement_dl")
OUT.mkdir(parents=True, exist_ok=True)


def api(method, path, *, params=None, body=None):
    url = f"{BASE}{path}"
    if method == "GET":
        r = requests.get(url, headers=H, params=params or {}, timeout=120)
    else:
        r = requests.post(url, headers=H, params=params or {}, json=body, timeout=120)
    try:
        data = r.json()
    except Exception:
        data = {"text": r.text[:2000]}
    print(f"  {method} {path} -> {r.status_code}")
    return r.status_code, data


def wait_report(rid, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, data = api("GET", f"/v2/reports/info/{rid}")
        res = (data or {}).get("result") or {}
        st = res.get("status")
        print(f"    status={st}")
        if st == "DONE":
            return res
        if st in ("FAILED", "ERROR"):
            print("    fail:", json.dumps(res, ensure_ascii=False)[:500])
            return res
        time.sleep(5)
    return None


def inspect_bytes(raw: bytes, label: str):
    path = OUT / label
    path.write_bytes(raw)
    print(f"  saved {path} ({len(raw)} bytes)")
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = zf.namelist()
            print(f"  zip files ({len(names)}): {names[:20]}")
            for name in names:
                data = zf.read(name)
                if name.endswith((".csv", ".json", ".txt")):
                    text = data.decode("utf-8", errors="replace")
                    preview = OUT / f"{label}__{Path(name).name}"
                    preview.write_text(text[:100000], encoding="utf-8")
                    lines = text.splitlines()
                    print(f"  --- {name}: {len(lines)} lines, {len(data)} bytes ---")
                    print("\n".join(lines[:8]))
                    # шаблон?
                    if "${" in text[:2000]:
                        print("  !! TEMPLATE PLACEHOLDERS")
                    else:
                        print("  OK: looks like real data")
                elif name.endswith(".xlsx") or name.startswith("xl/"):
                    pass
            # if xlsx structure
            if any(n.startswith("xl/") for n in names):
                ss = ""
                if "xl/sharedStrings.xml" in names:
                    ss = zf.read("xl/sharedStrings.xml").decode("utf-8", errors="replace")
                    strings = re.findall(r"<t[^>]*>([^<]*)</t>", ss)
                    print(f"  xlsx sharedStrings={len(strings)}")
                    print("  sample:", strings[:15])
                    if any("${" in s for s in strings):
                        print("  !! TEMPLATE in xlsx")
                    else:
                        print("  OK xlsx strings look real")
                print("  worksheets:", [n for n in names if "worksheets" in n])
    else:
        text = raw.decode("utf-8", errors="replace")
        (OUT / f"{label}.txt").write_text(text[:50000], encoding="utf-8")
        print(text[:600])


def generate_and_download(fmt: str, body: dict, tag: str):
    print(f"\n=== {tag} format={fmt} body={body} ===")
    # docs: format is QUERY param
    code, gen = api(
        "POST",
        "/v2/reports/goods-movement/generate",
        params={"format": fmt},
        body=body,
    )
    (OUT / f"{tag}_generate.json").write_text(json.dumps(gen, ensure_ascii=False, indent=2), encoding="utf-8")
    rid = ((gen or {}).get("result") or {}).get("reportId")
    if not rid:
        print("  no reportId", gen)
        return
    info = wait_report(rid)
    (OUT / f"{tag}_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    if not info:
        return
    file_url = info.get("file")
    if isinstance(file_url, list) and file_url:
        file_url = file_url[0].get("url") if isinstance(file_url[0], dict) else file_url[0]
    if isinstance(file_url, dict):
        file_url = file_url.get("url")
    # sometimes files[]
    if not file_url:
        files = info.get("files") or []
        if files:
            file_url = files[0].get("url") if isinstance(files[0], dict) else files[0]
    if not file_url:
        print("  no file url, keys:", list(info.keys()), info)
        return
    r = requests.get(file_url, headers=H, timeout=180)
    ext = {"FILE": "xlsx", "CSV": "zip", "JSON": "zip"}.get(fmt, "bin")
    inspect_bytes(r.content, f"{tag}_{fmt}.{ext if r.content[:2]==b'PK' else 'bin'}")


def main():
    if not KEY or not CID:
        raise SystemExit("no creds")

    today = date.today()
    # короткий период (в лимите 90 дней)
    d_to = today - timedelta(days=1)
    d_from = d_to - timedelta(days=6)

    # живой SKU из возвратов API
    _, ret = api("GET", f"/v2/campaigns/{CID}/returns", params={"limit": 5})
    sku = None
    for item in ((ret or {}).get("result") or {}).get("returns") or []:
        for it in item.get("items") or []:
            sku = it.get("shopSku") or it.get("offerId")
            if sku:
                break
        if sku:
            break
    print("sample sku:", sku)

    base_body = {
        "campaignId": CID,
        "dateFrom": d_from.isoformat(),
        "dateTo": d_to.isoformat(),
    }

    # 1) CSV без SKU
    generate_and_download("CSV", base_body, "week_all")
    time.sleep(65)  # лимит 1 отчёт / 2 мин без подписки

    # 2) CSV с одним SKU
    if sku:
        body2 = dict(base_body)
        body2["shopSku"] = sku
        generate_and_download("CSV", body2, f"week_sku")
        time.sleep(65)

    # 3) JSON неделя
    generate_and_download("JSON", base_body, "week_json")
    time.sleep(65)

    # 4) FILE (xlsx) на всякий
    generate_and_download("FILE", base_body, "week_file")

    print("\nDone. Files in", OUT)


if __name__ == "__main__":
    main()
