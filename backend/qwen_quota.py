"""Бесплатные лимиты Qwen: общая очередь моделей для тегирования брака и отзывов."""
from __future__ import annotations

from typing import List, Optional

from .llm import is_yandex_lite

# qwen-plus в конце: лимит уже выжжен. Сначала модели с живым free-tier.
FREE_QWEN_MODELS = (
    "qwen3.5-122b-a10b",
    "qwen3-max",
    "qwen-max",
    "qwen3.5-plus-2026-02-15",
    "qwen-plus-2025-07-28",
    "qwen3.7-plus",
)


def resolve_free_qwen_models(model_key: Optional[str] = None) -> List[str]:
    if is_yandex_lite(model_key):
        return [(model_key or "").strip() or "yandex-lite"]
    k = (model_key or "").strip()
    kl = k.lower()
    chain = list(FREE_QWEN_MODELS)
    # UI/cron алиасы и выгоревший plus → сразу на свободную цепочку
    if kl in ("", "qwen-plus", "qwen-reviews", "qwen-feedback", "qwen-claims", "qwen-free"):
        return chain
    if k in chain:
        i = chain.index(k)
        return chain[i:] + chain[:i]
    return [k] + [m for m in chain if m != k]


def is_quota_or_model_error(err: str) -> bool:
    e = (err or "").lower()
    needles = (
        "allocationquota",
        "freetieronly",
        "insufficient_quota",
        "quota exhausted",
        "quota exceeded",
        "free tier",
        "free quota",
        "invalidparameter.model",
        "invalid_parameter",
        "model_not_found",
        "unknown model",
        "model does not exist",
        "model not exist",
        "invalid model",
        "http 403",
        "http 404",
    )
    return any(n in e for n in needles)
