# Заглушки из Google-таблицы инвойсов — не настоящие номера, в поиске не нужны.
INVOICE_PLACEHOLDER_NUMS = {
    '', '0', '0.0', '-', '---', 'nan', 'none', 'undefined',
    'не указан', 'нет инвойса',
    'не закрытый инвойс', 'не закрытые инвойсы', 'незакрытый инвойс',
}


def is_placeholder_invoice(value) -> bool:
    raw = str(value or '').strip()
    if not raw:
        return True
    lowered = raw.lower()
    if lowered in INVOICE_PLACEHOLDER_NUMS:
        return True
    return 'не закрыт' in lowered


def normalize_invoice_suggestions(values, limit: int = 80):
    """Убирает заглушки и повторы, сохраняет порядок."""
    seen = set()
    out = []
    for value in values or []:
        raw = str(value or '').strip()
        if not raw or is_placeholder_invoice(raw):
            continue
        key = raw.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(raw)
        if len(out) >= limit:
            break
    return out


def invoice_suggestions_sql(search_q: str, limit: int) -> tuple:
    q = (search_q or '').strip()
    sql = """
        SELECT inv FROM (
            SELECT TRIM(invoice_num) AS inv, MAX(last_sync) AS ts
            FROM wb_invoices
            WHERE invoice_num IS NOT NULL
              AND TRIM(invoice_num) <> ''
              AND LOWER(TRIM(invoice_num)) NOT IN (
                    '0', '0.0', '-', '---', 'nan', 'none', 'undefined',
                    'не указан', 'нет инвойса',
                    'не закрытый инвойс', 'не закрытые инвойсы', 'незакрытый инвойс'
              )
              AND LOWER(TRIM(invoice_num)) NOT LIKE '%не закрыт%'
              AND (:q = '' OR LOWER(TRIM(invoice_num)) LIKE :q_like)
            GROUP BY TRIM(invoice_num)
        ) x
        ORDER BY ts DESC NULLS LAST, inv DESC
        LIMIT :lim
    """
    params = {
        "q": q,
        "q_like": f"%{q.lower()}%" if q else "%",
        "lim": limit,
    }
    return sql, params
