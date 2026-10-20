"""Винты в карточке товара — по таблице маркировок склада.

Один артикул может иметь несколько винтов (как BW).
"""
from __future__ import annotations

import re
from functools import cache


# Пары «артикул, маркировка» как в таблице склада. Порядок внутри артикула сохраняем.
FASTENER_ROWS: tuple[tuple[str, str], ...] = (
    ("POLKO-BW-Black-2", "VINT-M6x35-SILV"),
    ("POLKO-BW-White-2", "VINT-M6x35-SILV"),
    ("POLKO-Black-2", "VINT-M6x35-SILV"),
    ("POLKO-White-2", "VINT-M6x35-SILV"),
    ("BO-Black-2", "VINT-M6x35-SILV"),
    ("BO-White-2", "VINT-M6x35-SILV"),
    ("TO-Black", "VINT-M6x25-SILV"),
    ("TO-White", "VINT-M6x25-SILV"),
    ("BO-Black-1", "VINT-M6x35-SILV"),
    ("BO-White-1", "VINT-M6x35-SILV"),
    ("SH-Black-2", "VINT-M6x25-SILV"),
    ("SH-White-2", "VINT-M6x25-SILV"),
    ("SH-Black-3", "VINT-M6x25-SILV"),
    ("SH-White-3", "VINT-M6x25-SILV"),
    ("SH-Black-4", "VINT-M6x25-SILV"),
    ("SH-White-4", "VINT-M6x25-SILV"),
    ("2-СБ-Grey", "VINT-M6x30-SILV"),
    ("2-СБ-White", "VINT-M6x30-SILV"),
    ("2-СБ-Black", "VINT-M6x30-SILV"),
    ("2-СБ-Green", "VINT-M6x30-SILV"),
    ("BB-Black", "VINT-M6x30-SILV"),
    ("BB-White", "VINT-M6x30-SILV"),
    ("BW-Black-1", "VINT-M6x20-1-SILV"),
    ("BW-Black-1", "VINT-M6x35-SILV"),
    ("BW-White-1", "VINT-M6x20-1-SILV"),
    ("BW-White-1", "VINT-M6x40-1-SILV"),
    ("BS-Black-2", "VINT-M6x40-1-SILV"),
    ("BS-White-2", "VINT-M6x40-1-SILV"),
    ("BS-Black-1", "VINT-M6x40-1-SILV"),
    ("BS-White-1", "VINT-M6x40-1-SILV"),
    ("Shelf-black-5/43", "VINT-M6x25-SILV"),
    ("Shelf-white-5/43", "VINT-M6x25-SILV"),
    ("Shelf-black-5/63", "Винт-28-10,3-6-Черный"),
    ("Shelf-white-5/63", "Винт-28-10,3-6-Серебрянный"),
    ("Flower-stand-black-4", "VINT-M6x25-1-BLACK"),
    ("Flower-stand-white-4", "VINT-M6x25-1-BLACK"),
    ("Flower-stand-black-5", "VINT-M6x25-1-BLACK"),
    ("Flower-stand-white-5", "VINT-M6x25-1-BLACK"),
    ("SR-Loft-Black-4", "VINT-M6x30-1-BLACK"),
)

_VINT_CODE = re.compile(
    r"^VINT-M(\d+)x(\d+)(?:-\d+)?-(SILV|BLACK)$",
    re.I,
)
_RU_CODE = re.compile(
    r"^Винт-(\d+(?:,\d+)?)-(\d+(?:,\d+)?)-(\d+)-(Черный|Серебрянн?ый)$",
    re.I,
)


def normalize_article(article: str) -> str:
    text = (article or "").replace("ё", "е").replace("Ё", "Е").strip()
    text = re.sub(r"\s+", "", text)
    return text.casefold()


@cache
def _index() -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for sku, code in FASTENER_ROWS:
        key = normalize_article(sku)
        codes = grouped.setdefault(key, [])
        if code not in codes:
            codes.append(code)
    return {key: tuple(codes) for key, codes in grouped.items()}


def format_marking(code: str) -> str:
    raw = (code or "").strip()
    match = _VINT_CODE.match(raw)
    if match:
        color = "серебристый" if match.group(3).upper() == "SILV" else "чёрный"
        return f"М{match.group(1)}×{match.group(2)}, {color}"
    match = _RU_CODE.match(raw)
    if match:
        color = "чёрный" if "черн" in match.group(4).lower() else "серебристый"
        return f"{match.group(1)}×{match.group(2)} мм, М{match.group(3)}, {color}"
    return raw


def lookup_codes(article: str) -> tuple[str, ...]:
    key = normalize_article(article)
    if not key:
        return ()
    found = _index().get(key)
    if found:
        return found
    # «2-СБ-Grey Старый заказ» — те же винты, что у текущей 2-СБ.
    base, marker, _rest = key.partition("старый")
    if marker and base:
        return _index().get(base, ())
    return ()


def fasteners_for(article: str) -> list[dict[str, str]]:
    return [
        {"code": code, "label": format_marking(code)}
        for code in lookup_codes(article)
    ]


def attach_fasteners(payload: dict, article: str) -> dict:
    fasteners = fasteners_for(article)
    result = dict(payload or {})
    result["fasteners"] = fasteners
    product = result.get("product")
    if isinstance(product, dict):
        product = dict(product)
        product["fasteners"] = fasteners
        result["product"] = product
    return result
