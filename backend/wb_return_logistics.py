"""Обратная логистика WB из финотчёта — среднее по последним возвратам артикула.

Раньше брали только строки mp.*.r (возврат на наш ПВЗ) и подставляли
общую среднюю, если по артикулу ничего не находилось. Нужные суммы лежат
в обычных строках логистики (пустой doc_type_name) плюс mp.*.r.
"""

from typing import Optional

from sqlalchemy import text

LAST_N = 10
FALLBACK = 372.0

RETURN_LOG_WHERE = """
    delivery_rub > 0
    AND sa_name IS NOT NULL AND TRIM(sa_name) <> ''
    AND LOWER(TRIM(sa_name)) <> 'неопознанный товар'
    AND (doc_type_name = '' OR srid LIKE 'mp.%.r')
"""


def sku_return_log_cte(last_n: int = LAST_N) -> str:
    """CTE sku_return_log(sku_l, avg_delivery) — среднее по последним last_n возвратам."""
    n = int(last_n)
    return f"""
sku_return_log AS (
    SELECT sku_l, AVG(delivery_rub) AS avg_delivery
    FROM (
        SELECT
            LOWER(TRIM(sa_name)) AS sku_l,
            delivery_rub,
            ROW_NUMBER() OVER (
                PARTITION BY LOWER(TRIM(sa_name))
                ORDER BY rr_dt DESC NULLS LAST
            ) AS rn
        FROM wb_report_detail
        WHERE {RETURN_LOG_WHERE}
    ) ranked
    WHERE rn <= {n}
    GROUP BY sku_l
)
"""


def fetch_global_fallback(db) -> float:
    row = db.execute(text(f"""
        SELECT AVG(delivery_rub) AS avg_delivery
        FROM wb_report_detail
        WHERE {RETURN_LOG_WHERE}
    """)).mappings().first()
    if row and row["avg_delivery"]:
        return round(float(row["avg_delivery"]), 2)
    return FALLBACK


def fetch_avg_return_delivery(db, sa_name: Optional[str], last_n: int = LAST_N) -> dict:
    """Средняя обратка по последним last_n возвратам артикула.

    source: sku — по этому артикулу; fallback — общее среднее, если строк нет.
    """
    n_limit = int(last_n)
    if sa_name and str(sa_name).strip():
        row = db.execute(text(f"""
            SELECT AVG(delivery_rub) AS avg_delivery, COUNT(*)::int AS n
            FROM (
                SELECT delivery_rub
                FROM wb_report_detail
                WHERE {RETURN_LOG_WHERE}
                  AND LOWER(TRIM(sa_name)) = LOWER(TRIM(:sa))
                ORDER BY rr_dt DESC NULLS LAST
                LIMIT :n
            ) t
        """), {"sa": sa_name, "n": n_limit}).mappings().first()
        if row and row["n"] and row["avg_delivery"]:
            return {
                "value": round(float(row["avg_delivery"]), 2),
                "n": int(row["n"]),
                "source": "sku",
            }
    return {
        "value": fetch_global_fallback(db),
        "n": 0,
        "source": "fallback",
    }
