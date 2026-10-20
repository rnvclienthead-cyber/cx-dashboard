#!/usr/bin/env python3
"""Разовая «лечилка» отзывов, которые старый тегировщик молча похоронил.

Контекст. До фикса, если пачка спотыкалась об ошибку ИИ (лимит/таймаут/сбой),
её отзывы всё равно помечались «обработано, тегов нет» — и навсегда выпадали
из очереди тегирования. Отличить их от честно-пустых в данных нельзя: метка
одинаковая. Поэтому лечение простое — сбросить метку у пустых записей, чтобы
тегировщик прогнал их заново. Честно-пустые снова станут пустыми (это не вредит),
а реально потерянные наконец получат теги.

Короткие 5★ (их закрывают без ИИ намеренно) НЕ трогаем — они и так вне очереди.

Безопасность:
- По умолчанию СУХОЙ ПРОГОН: только считает, ничего не меняет.
- Меняет данные только с флагом --apply.
- --since YYYY-MM-DD ограничивает период (если в таблице есть колонка даты).

Примеры:
    ./venv/bin/python scripts/heal_empty_tags.py                 # сухой прогон, все
    ./venv/bin/python scripts/heal_empty_tags.py --since 2026-06-01
    ./venv/bin/python scripts/heal_empty_tags.py --apply --since 2026-06-01
"""
import argparse
import sys

from sqlalchemy import text

sys.path.insert(0, ".")
from backend.database import engine  # noqa: E402

# (платформа, таблица, SQL-выражение текста отзыва — как в тегировщике)
TABLES = {
    "wb": ("wb_feedbacks", "text"),
    "ym": (
        "ym_feedbacks",
        "TRIM(CONCAT_WS(' ', NULLIF(pro_text,''), NULLIF(contra_text,''), NULLIF(comment,'')))",
    ),
}

# Кандидаты на колонку даты — берём первую, что реально есть в таблице.
DATE_CANDIDATES = ("created_at", "created_date", "date_created", "feedback_date", "dt", "updated_at")


def _date_column(conn, table: str) -> str | None:
    rows = conn.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = :t
    """), {"t": table}).fetchall()
    have = {r[0] for r in rows}
    for c in DATE_CANDIDATES:
        if c in have:
            return c
    return None


def _where(text_sql: str, date_col: str | None, since: str | None) -> tuple[str, dict]:
    # Пустые теги: метка processed есть, а тегов ноль.
    clauses = [
        "ai_tags ? 'processed'",
        "COALESCE(jsonb_array_length(ai_tags->'tags'), 0) = 0",
        # Короткие 5★ — не наши, их закрывают без ИИ намеренно.
        f"NOT (COALESCE(valuation, 5) = 5 AND length(btrim(coalesce(({text_sql}), ''))) < 60)",
    ]
    params: dict = {}
    if since and date_col:
        clauses.append(f"{date_col} >= :since")
        params["since"] = since
    return " AND ".join(clauses), params


def heal(platform: str, table: str, text_sql: str, since: str | None, apply: bool) -> int:
    with engine.begin() as conn:
        date_col = _date_column(conn, table)
        if since and not date_col:
            print(f"  [{platform}] колонка даты не найдена — период игнорирую, беру все")
        where, params = _where(text_sql, date_col, since)

        total = conn.execute(
            text(f"SELECT count(*) FROM {table} WHERE {where}"), params
        ).scalar() or 0

        if not apply:
            span = f" c {since}" if (since and date_col) else ""
            print(f"  [{platform}] под лечение{span}: {total} записей (сухой прогон, не меняю)")
            return total

        changed = conn.execute(
            text(f"UPDATE {table} SET ai_tags = NULL WHERE {where}"), params
        ).rowcount
        print(f"  [{platform}] сброшено в очередь: {changed} записей — тегировщик прогонит их заново")
        return changed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Лечение молча похороненных тегов отзывов")
    parser.add_argument("--platform", default="all", choices=("wb", "ym", "all"))
    parser.add_argument("--since", default=None, help="дата YYYY-MM-DD, ограничить период")
    parser.add_argument("--apply", action="store_true", help="реально изменить данные (иначе сухой прогон)")
    args = parser.parse_args(argv)

    mode = "ПРИМЕНЕНИЕ" if args.apply else "СУХОЙ ПРОГОН (ничего не меняю)"
    print(f"Лечение пустых тегов — {mode}")

    platforms = ["wb", "ym"] if args.platform == "all" else [args.platform]
    grand = 0
    for p in platforms:
        table, text_sql = TABLES[p]
        grand += heal(p, table, text_sql, args.since, args.apply)

    if not args.apply:
        print(f"Итого под лечение: {grand}. Чтобы применить — добавь --apply.")
    else:
        print(f"Итого сброшено в очередь: {grand}. Тегировщик разберёт постепенно (по квоте ИИ).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
