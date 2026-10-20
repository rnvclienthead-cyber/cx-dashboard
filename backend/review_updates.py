"""Пометка отзыва, который покупатель дополнил или изменил."""
from __future__ import annotations

KIND_LABELS = {
    "edited": "Обновлён",
    "reopened": "Снова в очереди",
    "added_text": "Добавил текст",
}


def update_label(kind) -> str:
    return KIND_LABELS.get(str(kind or "").strip(), "")


def _change_pred(tbl: str, text_col: str, rating_col: str, extra: list[str]) -> str:
    parts = [
        f"COALESCE({tbl}.{text_col},'') IS DISTINCT FROM COALESCE(EXCLUDED.{text_col},'')",
        f"{tbl}.{rating_col} IS DISTINCT FROM EXCLUDED.{rating_col}",
    ]
    for col in extra:
        parts.append(
            f"COALESCE({tbl}.{col},'') IS DISTINCT FROM COALESCE(EXCLUDED.{col},'')"
        )
    return "(" + " OR ".join(parts) + ")"


def conflict_mark_sql(
    table: str,
    text_col: str,
    rating_col: str,
    extra: list[str] | None = None,
    *,
    reopen_pred: str = "",
) -> str:
    """Фрагмент SET для ON CONFLICT: сохранить прежнюю версию и вид изменения."""
    extra = extra or []
    changed = _change_pred(table, text_col, rating_col, extra)
    reopen = reopen_pred.strip() or "FALSE"
    saw = f"({changed} OR ({reopen}))"
    return f"""
        prev_text = CASE
            WHEN {saw} THEN COALESCE(NULLIF(TRIM({table}.prev_text), ''), {table}.{text_col})
            ELSE {table}.prev_text
        END,
        prev_valuation = CASE
            WHEN {saw} THEN COALESCE({table}.prev_valuation, {table}.{rating_col})
            ELSE {table}.prev_valuation
        END,
        buyer_updated_at = CASE
            WHEN {saw} THEN now()
            ELSE {table}.buyer_updated_at
        END,
        update_kind = CASE
            WHEN {reopen} THEN 'reopened'
            WHEN COALESCE({table}.{text_col},'') = '' AND COALESCE(EXCLUDED.{text_col},'') <> '' THEN 'added_text'
            WHEN {changed} THEN COALESCE(NULLIF({table}.update_kind, ''), 'edited')
            ELSE {table}.update_kind
        END
    """.strip()


WB_UNANSWERED_MARK = conflict_mark_sql(
    "wb_feedbacks", "text", "valuation", ["photos", "pros", "cons"],
    reopen_pred="wb_feedbacks.state IN ('answered', 'archive')",
)
WB_ANSWERED_MARK = conflict_mark_sql(
    "wb_feedbacks", "text", "valuation", ["photos", "pros", "cons"],
)
YM_MARK = conflict_mark_sql(
    "ym_feedbacks", "comment", "valuation", ["pro_text", "contra_text"],
    reopen_pred="ym_feedbacks.answered_at IS NOT NULL AND EXCLUDED.answered_at IS NULL",
)
OZON_MARK = conflict_mark_sql(
    "ozon_reviews", "review_text", "rating", ["pros", "cons", "photos"],
)
