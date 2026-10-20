"""ЯМ: продажа деталей 2-СБ со скидкой.

Детали в магазине есть только для вешалки 2-СБ. Промокод API Маркета
создать нельзя — один скрытый код заводят в кабинете, в акции:
«покупатель использует один раз» и только карточки этих деталей.
CX подставляет этот код и нужный артикул (или список, если неясно какая деталь).
"""
from __future__ import annotations

import logging
import re
from typing import NamedTuple, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger("ym_parts")

DEFAULT_PROMO_PCT = "50"
DEFAULT_PROMO_CODE = "AHAZE8RF"

# Карточки деталей на ЯМ — только 2-СБ. Строка: артикул, название, подсказки из чата.
DEFAULT_PARTS = (
    ("2СБ-TRUB-A-677-10-White", "Трубка А 677 мм", "трубка а,трубка a,трубки а,труба а"),
    ("2СБ-TRUB-B-712-10-White", "Трубка B 712 мм", "трубка b,трубка б,трубки б,труба б"),
    ("2СБ-TRUB-С-520-16-White", "Трубка С 520 мм", "трубка с,трубка c,трубки с,труба с"),
    ("2СБ-TRUB-D-660-16-White", "Трубка D 660 мм", "трубка d,трубка д,трубки д,труба д"),
    ("2СБ-KREP-260-Black", "Крепёж 260", "крепёж 260,крепеж 260,260"),
    ("2СБ-KREP-210-Black", "Крепёж 210", "крепёж 210,крепеж 210,210"),
    ("2СБ-KREP-496-Black", "Крепёж 496", "крепёж 496,крепеж 496,496"),
    ("2СБ-KOL-ST-Black", "Колёса", "колес,колёс,колесо"),
    ("2СБ-VINT-М6х30", "Винт М6×30", "винт,болт,м6,шуруп,м6х30,м6×30"),
)

DEFAULT_CATALOG = "\n".join(
    f"{sku}\t{name}\t{aliases}" for sku, name, aliases in DEFAULT_PARTS
)

_PLACEHOLDER_RE = re.compile(
    r"\{(promo_code|promo_pct|part_article|part_name|part_list)\}"
)
_2SB_HANGER_RE = re.compile(r"2[\s\-]?[сc]б|2[\s\-]?sb", re.I)
_2SB_PART_RE = re.compile(
    r"2[\s\-]?[сc]б[\s\-]?(trub|krep|kol|vint)|"
    r"2[\s\-]?sb[\s\-]?(trub|krep|kol|vint)",
    re.I,
)


class Part(NamedTuple):
    sku: str
    name: str
    aliases: tuple[str, ...]


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("ё", "е").lower()).strip()


def is_2sb_hanger(article: str) -> bool:
    """Заказ клиента — вешалка 2-СБ, а не швабра и не сама карточка детали."""
    src = _fold(article)
    if not src:
        return False
    if _2SB_PART_RE.search(src):
        return False
    return bool(_2SB_HANGER_RE.search(src))


def parse_catalog(raw: str) -> list[Part]:
    rows: list[Part] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\t+| {2,}", line, maxsplit=2)
        if len(parts) < 2:
            continue
        sku = parts[0].strip()
        name = (parts[1] if len(parts) > 1 else sku).strip() or sku
        aliases_raw = parts[2] if len(parts) > 2 else ""
        aliases = tuple(
            _fold(a) for a in re.split(r"[,;]+", aliases_raw) if _fold(a)
        )
        if sku:
            rows.append(Part(sku, name, aliases))
    return rows


def catalog_parts(catalog_raw: str = "") -> list[Part]:
    rows = parse_catalog(catalog_raw)
    return rows or parse_catalog(DEFAULT_CATALOG)


def _mentioned(blob: str, alias: str) -> bool:
    alias = _fold(alias)
    if not alias or not blob:
        return False
    if alias.isdigit():
        return bool(re.search(rf"(?<!\d){re.escape(alias)}(?!\d)", blob))
    tokens = alias.split()
    if len(tokens) > 1:
        pattern = r"\s+".join(re.escape(t) for t in tokens)
        return bool(re.search(pattern + r"(?!\w)", blob))
    if len(alias) <= 3:
        return bool(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", blob))
    return alias in blob


def guess_parts(article: str, msg: str = "", catalog_raw: str = "") -> list[Part]:
    """Детали, которые можно предложить купить: только 2-СБ, по тексту — узкий набор."""
    if not is_2sb_hanger(article):
        return []
    parts = catalog_parts(catalog_raw)
    blob = _fold(msg)
    if not blob:
        return parts
    matched = []
    for part in parts:
        if any(_mentioned(blob, a) for a in part.aliases):
            matched.append(part)
            continue
        if _mentioned(blob, part.sku) or _mentioned(blob, part.name):
            matched.append(part)
    return matched or parts


def lookup_part(article: str, catalog_raw: str, msg: str = "") -> tuple[str, str]:
    """Совместимость: (название, артикул). Пусто, если это не 2-СБ."""
    found = guess_parts(article, msg, catalog_raw)
    if len(found) == 1:
        return found[0].name, found[0].sku
    if found:
        return "деталь для вешалки 2-СБ", ", ".join(p.sku for p in found)
    return "", ""


def format_part_list(parts: list[Part]) -> str:
    if not parts:
        return "для этой модели отдельных деталей в магазине нет"
    return "\n".join(f"• {p.name} — {p.sku}" for p in parts)


def settings(db: Session) -> dict:
    from .routers import chat_playbook

    code = (chat_playbook.get_setting(db, "ym_parts_promo_code", DEFAULT_PROMO_CODE) or "").strip() or DEFAULT_PROMO_CODE
    pct = (chat_playbook.get_setting(db, "ym_parts_promo_pct", DEFAULT_PROMO_PCT) or DEFAULT_PROMO_PCT).strip()
    catalog = chat_playbook.get_setting(db, "ym_parts_catalog", DEFAULT_CATALOG) or DEFAULT_CATALOG
    promo_id = (chat_playbook.get_setting(db, "ym_parts_promo_id", "") or "").strip()
    return {
        "promo_code": code,
        "promo_pct": pct or DEFAULT_PROMO_PCT,
        "catalog": catalog,
        "promo_id": promo_id,
    }


def fill_part_placeholders(body: str, *, product_article: str = "",
                           client_msg: str = "",
                           db: Optional[Session] = None) -> str:
    if not body or not _PLACEHOLDER_RE.search(body):
        return body
    cfg = settings(db) if db is not None else {
        "promo_code": DEFAULT_PROMO_CODE,
        "promo_pct": DEFAULT_PROMO_PCT,
        "catalog": DEFAULT_CATALOG,
    }
    parts = guess_parts(product_article, client_msg, cfg.get("catalog") or "")
    if len(parts) == 1:
        name, sku = parts[0].name, parts[0].sku
    elif parts:
        name, sku = "деталь для вешалки 2-СБ", ", ".join(p.sku for p in parts)
    else:
        name, sku = "недостающая деталь", ""
    code = cfg.get("promo_code") or "промокод пришлём следом"
    return (
        body.replace("{promo_code}", code)
            .replace("{promo_pct}", str(cfg.get("promo_pct") or DEFAULT_PROMO_PCT))
            .replace("{part_article}", sku or "уточните модель в поиске магазина")
            .replace("{part_name}", name)
            .replace("{part_list}", format_part_list(parts))
    )


def attach_offers_to_promo(skus: list[str], promo_id: str) -> bool:
    """Вешает карточки деталей на уже созданную акцию с промокодом."""
    offers = [{"offerId": s} for s in (skus or []) if (s or "").strip()]
    pid = (promo_id or "").strip()
    if not offers or not pid:
        return False
    try:
        from . import ym_client
        biz = ym_client.business_id()
        if not biz:
            return False
        ym_client.post(
            f"/v2/businesses/{biz}/promos/offers/update",
            {
                "promoId": int(pid) if pid.isdigit() else pid,
                "offers": offers,
            },
            timeout=20,
        )
        return True
    except Exception as e:
        logger.warning("[YM parts] не удалось добавить офферы в акцию %s: %s", pid, e)
        return False


def attach_offer_to_promo(part_article: str, promo_id: str) -> bool:
    return attach_offers_to_promo([part_article], promo_id)


def prepare_buy_reply(db: Session, body: str, product_article: str,
                      client_msg: str = "") -> str:
    cfg = settings(db)
    parts = guess_parts(product_article, client_msg, cfg.get("catalog") or "")
    if parts and cfg.get("promo_id"):
        attach_offers_to_promo([p.sku for p in parts], cfg["promo_id"])
    return fill_part_placeholders(
        body, product_article=product_article, client_msg=client_msg, db=db,
    )
