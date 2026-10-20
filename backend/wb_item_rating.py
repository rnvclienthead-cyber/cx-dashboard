"""Отчёт WB «Оценка товара» (item-rating).

С 30.07.2026 Wildberries отключил v1. Живой метод — v2:
POST /api/analytics/v2/item-rating, список товаров в data.items.
"""
from datetime import date, datetime

ITEM_RATING_URL = "https://seller-analytics-api.wildberries.ru/api/analytics/v2/item-rating"

# Колонки, которые уже есть в wb_ratings_raw. Лишние поля v2 (isShadowed и т.п.)
# в таблицу не пишем — pandas to_sql иначе падает или плодит столбцы.
RAW_COLUMNS = [
    "nmId", "title", "vendorCode", "subjectId", "subjectName", "brandName",
    "pinnedFeedback", "rating", "disqualified",
    "feedbackRating_current", "feedbackRating_percentile",
    "feedbackCount_current",
    "fiveStar_current", "fourStar_current", "threeStar_current",
    "twoStar_current", "oneStar_current",
    "sys_date", "sys_article",
]


def extract_items(payload: dict) -> list:
    data = (payload or {}).get("data") or {}
    return data.get("items") or data.get("cards") or []


def request_body(start: str, end: str, nm_ids: list, offset: int = 0) -> dict:
    return {
        "currentPeriod": {"start": start, "end": end},
        "nmIds": nm_ids,
        "orderBy": {"field": "feedbackCount", "mode": "desc"},
        "isNotIncludeNmsWithoutSales": False,
        "limit": 100,
        "offset": offset,
    }


def keep_raw_columns(columns) -> list:
    return [c for c in RAW_COLUMNS if c in columns]


def wb_ratings_date_is_stale(last_date, today=None, max_lag_days=2) -> bool:
    """WB пишет sys_date = вчера. Старше двух дней — дыра."""
    if last_date is None:
        return True
    today = today or date.today()
    if isinstance(last_date, datetime):
        last_date = last_date.date()
    elif isinstance(last_date, str):
        last_date = datetime.strptime(last_date[:10], "%Y-%m-%d").date()
    return (today - last_date).days > max_lag_days
