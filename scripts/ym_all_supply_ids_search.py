#!/usr/bin/env python3
"""Все номера поставок ЯМ из накладных (Google) — поиск по выгрузке API и ym_supplies."""
from __future__ import annotations

import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path


def psql(sql: str) -> str:
    return subprocess.check_output(
        ["sudo", "-u", "postgres", "psql", "-d", "cx_dashboard", "-tAc", sql],
        text=True,
    ).strip()


RAW = Path("/tmp/ym_api_probe/raw")

rows = psql(
    """
SELECT supply_id, COUNT(*)::text
FROM wb_invoices
WHERE marketplace='ym' AND supply_id IS NOT NULL AND TRIM(supply_id) <> ''
GROUP BY 1
ORDER BY 1
"""
).splitlines()

supplies = []
for r in rows:
    if "|" not in r:
        continue
    sid, cnt = r.split("|", 1)
    supplies.append((sid.strip(), int(cnt)))

print(f"Всего уникальных supply_id из таблицы: {len(supplies)}")
fmt = Counter()
for sid, _ in supplies:
    if sid.startswith("ВРЦ-"):
        fmt["ВРЦ"] += 1
    elif sid.isdigit():
        fmt["digits"] += 1
    else:
        fmt["other"] += 1
print("форматы:", dict(fmt))

files = [p for p in RAW.rglob("*") if p.is_file() and p.suffix != ".bin"]
corpus = {f.name: f.read_text(encoding="utf-8", errors="replace") for f in files}
print(f"файлов API-выгрузки: {len(corpus)}")

ym = psql(
    """
SELECT COALESCE(request_id::text,'') || '|' ||
       COALESCE(marketplace_request_id,'') || '|' ||
       COALESCE(warehouse_request_id,'') || '|' ||
       COALESCE(ltrim(warehouse_request_id,'0'),'') || '|' ||
       COALESCE(parent_request_id::text,'')
FROM ym_supplies
"""
).splitlines()

req_ids: set[str] = set()
mkt: set[str] = set()
wh: set[str] = set()
wh_bare: set[str] = set()
parent: set[str] = set()
for line in ym:
    parts = line.split("|")
    if len(parts) < 5:
        continue
    a, b, c, d, e = parts[:5]
    if a:
        req_ids.add(a)
    if b:
        mkt.add(b)
    if c:
        wh.add(c)
    if d:
        wh_bare.add(d)
    if e:
        parent.add(e)


def variants(sid: str) -> set[str]:
    s = {sid}
    dig = re.sub(r"\D", "", sid)
    if dig:
        s.add(dig)
        s.add(dig.lstrip("0") or "0")
        s.add("ВРЦ-" + dig)
    return s


def fmt_of(sid: str) -> str:
    if sid.startswith("ВРЦ-"):
        return "ВРЦ"
    if sid.isdigit():
        return "digits"
    return "other"


api_hits: dict[str, list[str]] = {}
db_hits: dict[str, list[str]] = {}
both, only_api, only_db, nowhere = [], [], [], []

for sid, _cnt in supplies:
    vars_ = variants(sid)
    dig = re.sub(r"\D", "", sid)

    hits = []
    for fname, text in corpus.items():
        if fname == "targets.json":
            continue
        for v in vars_:
            if v and v in text:
                hits.append(fname)
                break
    if hits:
        api_hits[sid] = sorted(set(hits))

    how = []
    if dig:
        if dig in req_ids:
            how.append("request_id")
        if dig in parent:
            how.append("parent_request_id")
        if dig in mkt or sid in mkt:
            how.append("marketplace_request_id")
        if dig in wh or sid in wh or dig in wh_bare:
            how.append("warehouse_request_id")
    db_hits[sid] = how

    in_api = bool(hits)
    in_db = bool(how)
    if in_api and in_db:
        both.append(sid)
    elif in_api:
        only_api.append(sid)
    elif in_db:
        only_db.append(sid)
    else:
        nowhere.append(sid)

print("\n========== ИТОГ ==========")
print(f"в API-выгрузке (любой метод): {len(api_hits)} / {len(supplies)}")
print(f"в таблице ym_supplies:         {sum(1 for s, _ in supplies if db_hits[s])} / {len(supplies)}")
print(f"и там и там:                  {len(both)}")
print(f"только API-выгрузка:          {len(only_api)}")
print(f"только ym_supplies:           {len(only_db)}")
print(f"НИГДЕ:                       {len(nowhere)}")

file_counter: Counter[str] = Counter()
interesting_counter: Counter[str] = Counter()
for sid, hits in api_hits.items():
    for h in hits:
        file_counter[h] += 1
        if not (h.startswith("30_") or h.startswith("31_") or h.startswith("32_") or "supply" in h):
            interesting_counter[h] += 1

print("\n--- В каких файлах API нашлись номера (топ) ---")
for name, c in file_counter.most_common(15):
    print(f"  {c:4d}  {name}")

print("\n--- Попадания ВНЕ supply-requests ---")
if not interesting_counter:
    print("  НЕТ ни одного. Заказы/возвраты/стоки/отчёты — пусто.")
else:
    for name, c in interesting_counter.most_common():
        print(f"  {c:4d}  {name}")

print("\n--- НИГДЕ по формату ---")
print(dict(Counter(fmt_of(s) for s in nowhere)))
print("\n--- только ym_supplies по формату ---")
print(dict(Counter(fmt_of(s) for s in only_db)))
print("\n--- в API-выгрузке по формату ---")
print(dict(Counter(fmt_of(s) for s in api_hits)))

print("\n--- как стыкуются в ym_supplies ---")
how_c: Counter[str] = Counter()
for sid, _ in supplies:
    for h in db_hits[sid]:
        how_c[h] += 1
print(dict(how_c))

print("\n--- примеры НИГДЕ (digits / ВРЦ по 8) ---")
nd = [s for s in nowhere if s.isdigit()][:8]
nv = [s for s in nowhere if s.startswith("ВРЦ-")][:8]
print(" digits:", nd)
print(" ВРЦ:", nv)

print("\n--- примеры digits → marketplace ---")
n = 0
for sid, _ in supplies:
    if sid.isdigit() and "marketplace_request_id" in db_hits[sid]:
        print(" ", sid, db_hits[sid])
        n += 1
        if n >= 6:
            break

print("\n--- примеры ВРЦ → request/parent ---")
n = 0
for sid, _ in supplies:
    if sid.startswith("ВРЦ-") and db_hits[sid]:
        print(" ", sid, db_hits[sid])
        n += 1
        if n >= 6:
            break

out = Path("/tmp/ym_api_probe/ALL_SUPPLY_IDS_SEARCH.txt")
lines = [
    f"total={len(supplies)} api_dump={len(api_hits)} "
    f"ym_supplies={sum(1 for s, _ in supplies if db_hits[s])} nowhere={len(nowhere)}",
    "",
    "=== НИГДЕ ===",
]
lines.extend(nowhere)
lines.append("")
lines.append("=== ТОЛЬКО ym_supplies ===")
for s in only_db:
    lines.append(f"{s} | {','.join(db_hits[s])}")
lines.append("")
lines.append("=== В API выгрузке ===")
for s in sorted(api_hits):
    lines.append(
        f"{s} | files={','.join(api_hits[s][:8])} | db={','.join(db_hits[s]) or '-'}"
    )
out.write_text("\n".join(lines), encoding="utf-8")
print(f"\nполный список: {out}")
