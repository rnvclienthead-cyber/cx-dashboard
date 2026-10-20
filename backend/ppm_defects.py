"""Правила числителя PPM: что считается заводским браком."""
from datetime import date

import pandas as pd

# С 1 августа 2026 WB: только теги рекламационного акта.
# ЯМ и Ozon — теги акта за все периоды.
PPM_FACTORY_FROM = date(2026, 8, 1)
PPM_FACTORY_CATS = (1, 2, 4, 5, 7, 9)
PPM_ALL_CATS = tuple(range(1, 14))
PPM_ALWAYS_FACTORY_PLATFORMS = ("ym", "ozon")
_TRUE_TAGS = {"1", "1.0", "+", "true", "да", "t"}


def is_tag_on(val) -> bool:
    if val is True:
        return True
    if val is False or val is None:
        return False
    try:
        if pd.isna(val):
            return False
    except (TypeError, ValueError):
        pass
    return str(val).strip().lower() in _TRUE_TAGS


def _cat_mask(df: pd.DataFrame, cats: tuple) -> pd.Series:
    mask = pd.Series(False, index=df.index)
    for i in cats:
        col = f"cat_{i}"
        if col in df.columns:
            mask = mask | df[col].map(is_tag_on)
    return mask


def mark_ppm_defects(df: pd.DataFrame) -> pd.Series:
    """Строка идёт в числитель PPM.

    ЯМ и Ozon: всегда теги акта 1, 2, 4, 5, 7, 9.
    WB: до 1 августа 2026 любой тег 1–13, с августа — только теги акта.
    """
    if df is None or df.empty:
        return pd.Series(dtype=bool)

    if "created_dt" in df.columns:
        date_src = df["created_dt"]
    else:
        date_src = pd.Series(pd.NaT, index=df.index)
    if "claim_date" in df.columns:
        date_src = date_src.where(pd.notna(date_src), df["claim_date"])
    dates = pd.to_datetime(date_src, utc=True, errors="coerce").dt.tz_localize(None)
    from_aug = dates >= pd.Timestamp(PPM_FACTORY_FROM)

    factory = _cat_mask(df, PPM_FACTORY_CATS)
    any_tag = _cat_mask(df, PPM_ALL_CATS)
    if "platform" in df.columns:
        always_factory = df["platform"].astype(str).str.lower().isin(PPM_ALWAYS_FACTORY_PLATFORMS)
    else:
        always_factory = pd.Series(False, index=df.index)
    wb_like = ~always_factory
    return (
        (always_factory & factory)
        | (wb_like & from_aug & factory)
        | (wb_like & ~from_aug & any_tag)
    )


def ym_reship_refusal_sql(alias: str = "r") -> str:
    """Отказ ЯМ связан с доотправкой детали (номер заказа или чат)."""
    a = alias
    return f"""(
    EXISTS (
        SELECT 1 FROM reshipment_requests rr
        WHERE TRIM(COALESCE(rr.order_number, '')) <> ''
          AND TRIM(rr.order_number) = TRIM({a}.order_id::text)
    )
    OR EXISTS (
        SELECT 1 FROM reshipment_requests rr
        JOIN ym_chats yc ON yc.chat_id = rr.ym_chat_id
        WHERE rr.ym_chat_id IS NOT NULL AND TRIM(rr.ym_chat_id) <> ''
          AND TRIM(yc.order_id::text) = TRIM({a}.order_id::text)
    )
)"""


def ym_not_plain_refusal_sql(alias: str = "r") -> str:
    """ЯМ в PPM/карту проблем: не отказ, либо отказ из‑за доотправки детали.

    CANCELLED не берём. REJECTED / NOT_RETURNED / FAILED — только с доотправкой.
    """
    a = alias
    reship = ym_reship_refusal_sql(a)
    return f"""(
        {a}.status NOT IN ('NOT_RETURNED', 'FAILED', 'CANCELLED', 'REJECTED')
        OR (
            {a}.status IN ('REJECTED', 'NOT_RETURNED', 'FAILED')
            AND {reship}
        )
    )"""


def ppm_defect_sql(date_expr: str, cat_prefix: str = "") -> str:
    """SQL-условие для WB (дашборд): отсечка августа, как в PPM-разделе."""
    p = cat_prefix
    any_cat = " OR ".join(f"{p}cat_{i}" for i in PPM_ALL_CATS)
    factory = " OR ".join(f"{p}cat_{i}" for i in PPM_FACTORY_CATS)
    cutoff = PPM_FACTORY_FROM.isoformat()
    return (
        f"(CASE WHEN {date_expr} >= DATE '{cutoff}' "
        f"THEN ({factory}) ELSE ({any_cat}) END)"
    )
