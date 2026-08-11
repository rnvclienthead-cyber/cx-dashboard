#!/usr/bin/env python3
"""Проверка: конкретные ВРЦ из БД — есть ли в телах ответов API и в ym_supplies."""
from __future__ import annotations

import re
import subprocess
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
WHERE marketplace='ym' AND supply_id ~ '^ВРЦ-'
GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 12
"""
).splitlines()

print("=== ВРЦ из наших накладных (топ) ===")
for r in rows:
    print(r)

vrc_ids = [r.split("|")[0] for r in rows if "|" in r]
digits = [re.sub(r"\D", "", v) for v in vrc_ids]
print("digits:", digits)

files = [p for p in RAW.rglob("*") if p.is_file() and p.suffix != ".bin"]
print(f"\nфайлов выгрузки для поиска по СОДЕРЖИМОМУ: {len(files)}")

print("\n=== Поиск в теле ответов API (не в названиях методов) ===")
for v, dig in zip(vrc_ids, digits):
    hits_full, hits_dig = [], []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        if v in text:
            hits_full.append(f.name)
        if dig and dig in text:
            hits_dig.append(f.name)
    print(v)
    print(f"  точная '{v}': {hits_full or 'НЕТ'}")
    print(f"  цифры '{dig}': {hits_dig or 'НЕТ'}")

print("\n=== Эти цифры в таблице ym_supplies? ===")
for dig in digits:
    hit = psql(
        f"""
SELECT COUNT(*) FROM ym_supplies
WHERE id::text='{dig}'
   OR COALESCE(request_id::text,'')='{dig}'
   OR COALESCE(marketplace_request_id,'')='{dig}'
   OR COALESCE(warehouse_request_id,'')='{dig}'
   OR ltrim(COALESCE(warehouse_request_id,''),'0')='{dig}'
   OR COALESCE(parent_request_id::text,'')='{dig}'
"""
    )
    print(f"  {dig}: {hit}")

print("\n=== Что такое ym_supplies ===")
print("строк всего:", psql("SELECT COUNT(*) FROM ym_supplies"))
print(
    "с номером заявки МП:",
    psql(
        "SELECT COUNT(*) FROM ym_supplies WHERE marketplace_request_id IS NOT NULL AND marketplace_request_id<>''"
    ),
)
print(
    "пример:",
    psql(
        "SELECT id, request_id, marketplace_request_id, warehouse_request_id, status FROM ym_supplies WHERE marketplace_request_id IS NOT NULL ORDER BY id DESC LIMIT 3"
    ),
)

print("\n=== Колонки wb_invoices (откуда мог взяться ВРЦ) ===")
print(psql("SELECT column_name FROM information_schema.columns WHERE table_name='wb_invoices' ORDER BY ordinal_position"))
print(
    "примеры строк ВРЦ:",
    psql(
        "SELECT supply_id, article, created_at::date FROM wb_invoices WHERE marketplace='ym' AND supply_id LIKE 'ВРЦ-%' ORDER BY created_at DESC NULLS LAST LIMIT 5"
    ),
)
