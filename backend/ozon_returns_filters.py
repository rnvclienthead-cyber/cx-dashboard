"""Фильтры ozon_returns: Карта проблем vs PPM.

Карта проблем — все причины возвратов (без логистических отмен).
PPM — только заводские/производственные причины.
"""

# Заводские причины → числитель PPM
OZON_PPM_REASONS = (
    "Товар в неполной комплектации",                                      # 1
    "Покупатель отказался при вручении: неполная комплектация",           # 2
    "Товар не работает / брак",                                          # 4
    "Покупатель отказался при вручении: недоволен качеством товара",    # 5
    "Товар поврежден, но упаковка цела",                                 # 8
    "Товар сломался при эксплуатации",                                   # 9
    "Блокирующее повреждение",                                           # 10
    "Проблемы с качеством товара (брак товара)",                         # 14
    "Отказ клиента: поврежден товар",                                    # 15
)

# Расширенный список для справки / rule tags (карта показывает ВСЕ причины возвратов)
OZON_QUALITY_REASONS = OZON_PPM_REASONS + (
    "Упаковка и товар повреждены",                                       # 3 — часто логистика
    "Покупатель получил не те товары",                                   # 6
    "Покупатель отказался при вручении: в заказе не тот товар",         # 7
    "Неправильно указаны ОВХ товара",                                    # 11
    "Товар поддельный",                                                  # 12
    "Изменил решение о покупке/Товар не подошёл",                        # 13
    "Привезли другой товар",                                             # 16
)

OZON_RETURN_STATUSES = (
    "ReturnedToOzon",
    "Utilized",
    "WriteOff",
    "ReceivedBySeller",
    "Lost",
    "Utilizing",
    "WaitingShipment",
    "MovingToOzon",
    "MovingToSeller",
    "ArrivedAtReturnPlace",
    "OnSellerApproval",
)


def sql_in_list(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _base_return_filters(alias: str) -> str:
    """Общее: артикул, не Cancellation, статусы пайплайна, не отмены по тексту."""
    a = alias
    statuses = sql_in_list(OZON_RETURN_STATUSES)
    return f"""
        {a}.supplier_article IS NOT NULL
        AND TRIM({a}.supplier_article) <> ''
        AND COALESCE({a}.return_type, '') <> 'Cancellation'
        AND {a}.status_sys IN ({statuses})
        AND COALESCE({a}.return_reason, '') NOT ILIKE 'Покупатель отменил%'
        AND COALESCE({a}.return_reason, '') NOT ILIKE '%не забрал%'
        AND COALESCE({a}.return_reason, '') NOT ILIKE 'Не удалось доставить%'
        AND COALESCE({a}.return_reason, '') NOT ILIKE 'Автовозврат%'
        AND COALESCE({a}.return_reason, '') NOT ILIKE 'Слишком долго ждать%'
        AND COALESCE({a}.return_reason, '') NOT ILIKE 'Нашлось дешевле%'
        AND COALESCE({a}.return_reason, '') <> ''
    """


def ozon_returns_where_sql(alias: str = "r", *, for_ppm: bool = False) -> str:
    """
    for_ppm=False → Карта проблем: все причины возвратов (без отмен).
    for_ppm=True  → PPM: только заводские причины.
    """
    a = alias
    base = _base_return_filters(a)
    if for_ppm:
        reasons = sql_in_list(OZON_PPM_REASONS)
        return f"{base} AND {a}.return_reason IN ({reasons})"
    return base
