"""Склейка поставка → инвойс → завод.

Номер инвойса в листе «Завод» почти всегда с дефисом (RNV26-P212),
в таблице поставок часто без (RNV26P212). Точное сравнение строк
теряет завод. Номер поставки 0 / пустой — не поставка: его нельзя
писать поверх настоящего income_id и нельзя джойнить с инвойсами.
"""
from __future__ import annotations

import re

from sqlalchemy import text

FAKE_SUPPLY_IDS = frozenset({
    "", "0", "0.0", "-", "nan", "none", "null", "undefined",
})
_INVOICE_KEY_RE = re.compile(r"[^A-Z0-9]+")

# Кириллица, которую путают с латиницей в артикулах (СБ / CB).
ARTICLE_KEY_SQL = "translate(lower(trim({col})),'авсенкмортху','abcehkmoptxy')"
_FAKE_SUPPLY_SQL = "('', '0', '0.0', '-', 'nan', 'none', 'null', 'undefined')"


def article_key_sql(col: str) -> str:
    return ARTICLE_KEY_SQL.format(col=col)


def invoice_key_sql(col: str) -> str:
    return (
        f"regexp_replace(upper(trim(COALESCE({col}::text, ''))), "
        f"'[^A-Z0-9]', '', 'g')"
    )


def supply_key_sql(col: str) -> str:
    return f"split_part(trim(COALESCE({col}::text, '')), '.', 1)"


def is_real_supply_sql(col: str) -> str:
    return (
        f"(NULLIF(trim(COALESCE({col}::text, '')), '') IS NOT NULL "
        f"AND lower(trim({col}::text)) NOT IN {_FAKE_SUPPLY_SQL})"
    )


def normalize_invoice_key(value) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    return _INVOICE_KEY_RE.sub("", raw)


def is_real_supply_id(value) -> bool:
    if value is None:
        return False
    try:
        if value != value:  # NaN
            return False
    except Exception:
        pass
    raw = str(value).strip().lower()
    if raw.endswith(".0") and raw[:-2].isdigit():
        raw = raw[:-2]
    return raw not in FAKE_SUPPLY_IDS


def clean_supply_id(value):
    if not is_real_supply_id(value):
        return None
    raw = str(value).strip()
    if raw.lower().endswith(".0") and raw[:-2].replace("-", "").isdigit():
        raw = raw[:-2]
    return raw


def prefer_real_supply_rows(
    df,
    srid_col: str = "srid",
    income_col: str = "income_id",
    date_col: str = "last_change_date",
):
    """В одном куске API один srid может прийти и с поставкой, и с нулём.
    Оставляем строку с настоящей поставкой, иначе последнюю по дате."""
    if df is None or getattr(df, "empty", True):
        return df
    work = df.copy()
    if income_col not in work.columns or srid_col not in work.columns:
        return work.drop_duplicates(subset=[srid_col], keep="last")
    work["_real_income"] = work[income_col].map(is_real_supply_id).astype(int)
    sort_cols = [srid_col, "_real_income"]
    if date_col in work.columns:
        sort_cols.append(date_col)
    work = (
        work.sort_values(sort_cols, kind="mergesort")
        .drop_duplicates(subset=[srid_col], keep="last")
        .drop(columns=["_real_income"])
    )
    return work


def logistics_upsert_sql() -> str:
    fake = _FAKE_SUPPLY_SQL
    return f"""
INSERT INTO wb_logistics (
    srid, doc_type, dt, supplier_article, nm_id, warehouse_name, category,
    subject, brand, is_cancel, last_change_date, income_id, finish_price, last_sync
)
SELECT DISTINCT ON (srid)
    srid, doc_type, CAST(dt AS TIMESTAMP), supplier_article, nm_id, warehouse_name,
    category, subject, brand, CAST(is_cancel AS BOOLEAN),
    CAST(last_change_date AS TIMESTAMP), income_id,
    CAST(NULLIF(finish_price::text, 'None') AS NUMERIC), CAST(last_sync AS TIMESTAMP)
FROM temp_wb_logistics
ORDER BY srid,
    CASE WHEN income_id IS NULL OR lower(trim(income_id::text)) IN {fake}
         THEN 1 ELSE 0 END,
    last_change_date DESC NULLS LAST
ON CONFLICT (srid) DO UPDATE SET
    is_cancel = EXCLUDED.is_cancel,
    nm_id = COALESCE(EXCLUDED.nm_id, wb_logistics.nm_id),
    last_change_date = EXCLUDED.last_change_date,
    income_id = CASE
        WHEN EXCLUDED.income_id IS NULL
          OR lower(trim(EXCLUDED.income_id::text)) IN {fake}
        THEN wb_logistics.income_id
        ELSE EXCLUDED.income_id
    END,
    finish_price = COALESCE(EXCLUDED.finish_price, wb_logistics.finish_price),
    last_sync = EXCLUDED.last_sync
"""


def claims_income_update_sql() -> str:
    """Фрагмент SET для wb_claims: ноль не затирает уже известную поставку."""
    fake = _FAKE_SUPPLY_SQL
    return (
        "income_id = CASE "
        "WHEN EXCLUDED.income_id IS NULL "
        f"OR lower(trim(EXCLUDED.income_id::text)) IN {fake} "
        "THEN wb_claims.income_id "
        "ELSE EXCLUDED.income_id END"
    )


def claims_enriched_view_sql() -> str:
    art_claim = article_key_sql("c_1.supplier_article")
    art_inv = article_key_sql("inv.supplier_article")
    art_f = article_key_sql("f.supplier_article")
    art_co = article_key_sql("co.supplier_article")
    art_c = article_key_sql(
        "COALESCE(NULLIF(TRIM(c.supplier_article), ''), na.supplier_article::text)"
    )
    art_fact = article_key_sql("wb_factories.supplier_article")
    art_cogs = article_key_sql("wb_cogs.supplier_article")
    art_asrt = article_key_sql("wb_assortment.supplier_article")
    inv_f = invoice_key_sql("f.invoice_num")
    inv_ci = invoice_key_sql("ci.invoice_num")
    inv_co = invoice_key_sql("co.invoice_num")
    return f"""
WITH nm_article AS (
    SELECT DISTINCT ON (wb_ratings.nm_id) wb_ratings.nm_id,
           wb_ratings.supplier_article
    FROM wb_ratings
    WHERE wb_ratings.supplier_article IS NOT NULL
      AND TRIM(BOTH FROM wb_ratings.supplier_article) <> ''
    ORDER BY wb_ratings.nm_id
), claim_invoice AS (
    SELECT DISTINCT ON (c_1.srid) c_1.srid,
           COALESCE(inv.invoice_num, 'Не указан') AS invoice_num
    FROM wb_claims c_1
    LEFT JOIN wb_logistics l ON l.srid = c_1.srid
    LEFT JOIN wb_invoices inv
      ON {is_real_supply_sql("l.income_id")}
     AND {is_real_supply_sql("inv.supply_id")}
     AND {supply_key_sql("l.income_id")} = {supply_key_sql("inv.supply_id")}
     AND {art_claim} = {art_inv}
    ORDER BY c_1.srid, (inv.invoice_num IS NULL), inv.last_sync DESC NULLS LAST
), fallback_factory AS (
    SELECT {art_fact} AS clean_art,
           MIN(wb_factories.factory_name) AS factory_name
    FROM wb_factories
    WHERE wb_factories.factory_name IS NOT NULL
      AND wb_factories.factory_name::text <> ''
      AND wb_factories.factory_name::text <> 'Уточняется'
    GROUP BY 1
    HAVING COUNT(DISTINCT wb_factories.factory_name) = 1
), multi_factory AS (
    SELECT {art_fact} AS clean_art
    FROM wb_factories
    WHERE wb_factories.factory_name IS NOT NULL
      AND wb_factories.factory_name::text <> ''
      AND wb_factories.factory_name::text <> 'Уточняется'
    GROUP BY 1
    HAVING COUNT(DISTINCT wb_factories.factory_name) > 1
), fallback_cog AS (
    SELECT DISTINCT ON ({art_cogs})
           {art_cogs} AS clean_art,
           wb_cogs.container_num,
           wb_cogs.shipment_date,
           wb_cogs.invoice_num
    FROM wb_cogs
    WHERE wb_cogs.container_num IS NOT NULL
      AND wb_cogs.container_num::text <> ''
    ORDER BY {art_cogs}, wb_cogs.shipment_date DESC NULLS LAST
), fallback_assortment AS (
    SELECT {art_asrt} AS clean_art,
           wb_assortment.manufacturer AS factory_name
    FROM wb_assortment
    WHERE wb_assortment.manufacturer IS NOT NULL
      AND wb_assortment.manufacturer <> ''
)
SELECT c.srid,
       c.created_dt,
       COALESCE(NULLIF(TRIM(BOTH FROM c.supplier_article), ''), na.supplier_article::text)
           AS supplier_article,
       c.user_comment,
       c.status,
       c.status_ex,
       c.income_id,
       c.photos,
       c.video_paths,
       c.cat_1, c.cat_2, c.cat_3, c.cat_4, c.cat_5, c.cat_6, c.cat_7,
       c.cat_8, c.cat_9, c.cat_10, c.cat_11, c.cat_12, c.cat_13,
       c.order_date,
       c.claim_date,
       COALESCE(f.factory_name, ff.factory_name, fa.factory_name::character varying,
                'Уточняется'::character varying) AS factory_name,
       COALESCE(co.container_num, fc.container_num, '—'::character varying) AS container_num,
       COALESCE(to_char(co.shipment_date, 'DD.MM.YYYY'::text),
                to_char(fc.shipment_date, 'DD.MM.YYYY'::text),
                '—'::text) AS shipment_date,
       CASE
           WHEN ci.invoice_num <> 'Не указан'::text THEN ci.invoice_num
           ELSE COALESCE(fc.invoice_num, 'Не указан'::text)
       END AS invoice_num
FROM wb_claims c
LEFT JOIN nm_article na ON c.nm_id::bigint = na.nm_id
LEFT JOIN claim_invoice ci ON c.srid = ci.srid
LEFT JOIN wb_factories f
  ON ci.invoice_num <> 'Не указан'::text
 AND {inv_ci} <> ''
 AND {inv_f} = {inv_ci}
 AND {art_f} = {art_c}
LEFT JOIN wb_cogs co
  ON ci.invoice_num <> 'Не указан'::text
 AND {inv_ci} <> ''
 AND {inv_co} = {inv_ci}
 AND {art_co} = {art_c}
LEFT JOIN fallback_factory ff ON ff.clean_art = {art_c}
LEFT JOIN multi_factory mf ON mf.clean_art = {art_c}
LEFT JOIN fallback_cog fc ON fc.clean_art = {art_c}
LEFT JOIN fallback_assortment fa
  ON fa.clean_art = {art_c}
 AND mf.clean_art IS NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cx_purged_claims p WHERE p.srid = c.srid
)
"""


def marketplace_from_sheet(mp_raw) -> str:
    s = str(mp_raw or "").strip().lower()
    if not s:
        return "other"
    if "озон" in s or s == "ozon":
        return "ozon"
    if "яндекс" in s:
        return "ym"
    if s in ("вб", "wb") or s.startswith("вб"):
        return "wb"
    return "other"


def _clean_sheet_id(raw) -> str:
    sid = str(raw or "").strip()
    if sid.lower().endswith(".0") and sid[:-2].replace("-", "").isdigit():
        return sid[:-2]
    return sid


def _pick_invoice(invoices: list[str], factory_keys=None):
    from .invoice_suggestions import is_placeholder_invoice
    real = [inv for inv in invoices if inv and not is_placeholder_invoice(inv)]
    if not real:
        return None
    keys = factory_keys or set()
    if keys:
        for inv in reversed(real):
            if normalize_invoice_key(inv) in keys:
                return inv
    return real[-1]


def parse_invoice_sheet_rows(records, factory_keys=None) -> list[dict]:
    """Строки листа «Отгрузки»: поставка + артикул + инвойс + МП.

    Одна поставка+артикул может повторяться по городам. Если инвойсы разные,
    берём тот, который есть в справочнике заводов, иначе последний нормальный.
    """
    if not records:
        return []
    first = records[0] or {}
    cols_lower = {str(k).strip().lower(): k for k in first.keys()}
    sup_col = cols_lower.get("номер поставки")
    art_col = cols_lower.get("артикул")
    inv_col = cols_lower.get("инвойс")
    mp_col = cols_lower.get("мп")
    if not sup_col or not art_col or not inv_col:
        return []

    by_key = {}
    for rec in records:
        sid = _clean_sheet_id(rec.get(sup_col))
        art = _clean_sheet_id(rec.get(art_col))
        inv = str(rec.get(inv_col) or "").strip()
        if not is_real_supply_id(sid) or not art:
            continue
        mp = marketplace_from_sheet(rec.get(mp_col) if mp_col else "")
        slot = by_key.setdefault((sid, art), {"sid": sid, "art": art, "invoices": [], "mp": mp})
        slot["invoices"].append(inv)
        if mp in ("wb", "ym", "ozon"):
            slot["mp"] = mp
    out = []
    for slot in by_key.values():
        inv = _pick_invoice(slot["invoices"], factory_keys)
        if not inv:
            continue
        out.append({
            "sid": slot["sid"],
            "art": slot["art"],
            "inv": inv,
            "mp": slot["mp"],
        })
    return out


def init_claims_enriched_view(engine) -> None:
    """VIEW претензий WB с заводом и инвойсом. Нужен при старте API."""
    sql = claims_enriched_view_sql().strip()
    with engine.begin() as conn:
        exists = conn.execute(text("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'wb_claims'
        """)).scalar()
        if not exists:
            return
        conn.execute(text(f"CREATE OR REPLACE VIEW view_wb_claims_enriched AS\n{sql}"))
