"""Восстановление дыры ежедневных рейтингов ЯМ.

Яндекс не отдаёт отчёт «на прошлую дату» — только снимок на сейчас.
Берём последний официальный день до дыры и накручиваем отзывы из ym_feedbacks.
"""
from datetime import date, timedelta


def add_stars(s5, s4, s3, s2, s1, n5=0, n4=0, n3=0, n2=0, n1=0):
    return (
        int(s5 or 0) + int(n5 or 0),
        int(s4 or 0) + int(n4 or 0),
        int(s3 or 0) + int(n3 or 0),
        int(s2 or 0) + int(n2 or 0),
        int(s1 or 0) + int(n1 or 0),
    )


def rating_from_stars(s5, s4, s3, s2, s1):
    total = s5 + s4 + s3 + s2 + s1
    if total <= 0:
        return None
    return round((5 * s5 + 4 * s4 + 3 * s3 + 2 * s2 + 1 * s1) / total, 2)


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)
