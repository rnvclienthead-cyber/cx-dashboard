"""План vs факт стоимости доставки в карточке доотправки."""


def _num(value):
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n else None


def delivery_amounts(
    carrier: str,
    *,
    cdek_cost=None,
    cdek_cost_actual=None,
    yandex_cost=None,
    shipping_cost=None,
    ozon_cost=None,
    ozon_cost_actual=None,
) -> dict:
    """План (калькулятор), факт (заказ/оффер/начисления), основная сумма для экономики."""
    if carrier == "yandex":
        plan = _num(yandex_cost)
        fact = _num(shipping_cost)
    elif carrier == "ozon":
        plan = _num(ozon_cost)
        fact = _num(ozon_cost_actual)
        if fact is None:
            fact = _num(shipping_cost)
    else:
        plan = _num(cdek_cost)
        fact = _num(cdek_cost_actual)
    main = fact if fact is not None else (plan or 0.0)
    diff = None
    if plan is not None and fact is not None:
        diff = round(fact - plan, 2)
    return {
        "plan": round(plan, 2) if plan is not None else None,
        "fact": round(fact, 2) if fact is not None else None,
        "main": round(main, 2),
        "diff": diff,
    }
