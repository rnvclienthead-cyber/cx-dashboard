"""Свежесть сбора рейтингов Яндекс Маркета.

Отдельный модуль без БД: его дергает system_monitor, а тесты проверяют
порог без SQLAlchemy.
"""
from datetime import datetime, timedelta, timezone

# Рейтинги собираются раз в сутки (~07:00 МСК). Порог с запасом на один
# пропущенный ночной запуск, но не на «две недели тишины».
YM_RATINGS_STALE_HOURS = 36


def ym_ratings_is_stale(last_success_at, now=None, max_age_hours=YM_RATINGS_STALE_HOURS) -> bool:
    """Нет успешного сбора рейтингов ЯМ или он старше порога."""
    if last_success_at is None:
        return True
    now = now or datetime.now(timezone.utc)
    ts = last_success_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - ts) > timedelta(hours=max_age_hours)
