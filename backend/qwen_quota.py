"""Бесплатные лимиты Qwen: очередь моделей и учёт остатка, чтобы менять модель заранее."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("qwen_quota")

YANDEX_LITE_KEYS = {"yandex-lite", "yandexgpt-lite"}

# У каждой модели свой бесплатный лимит. Новые имена — в начале очереди.
# 2026-10-13: в кабинете ~99 моделей, старые 28 имён выгорели. В начало —
# текстовые, которые в этот день ещё отвечали бесплатно.
FREE_QWEN_MODELS = (
    "qwen3.8-max-0902",
    "qwen3.8-2.4t-a95b",
    "qwen3.7-flash-2026-07-15",
    "qwen3.7-max-2026-06-08",
    "qwen3.7-max-preview",
    "qwen3.6-flash-2026-04-16",
    "qwen3.6-35b-a3b",
    "qwen3.5-flash-2026-02-23",
    "qwen3.5-35b-a3b",
    "qwen-plus-2025-07-14",
    "qwen-plus-2025-04-28",
    "qwen3-coder-flash",
    "qwen3.8-flash",
    "qwen3.7-flash",
    "qwen3.6-flash",
    "qwen3.5-flash",
    "qwen3.8-27b",
    "qwen3.6-27b",
    "qwen3.5-27b",
    "qwen3.8-max",
    "qwen3.7-max",
    "qwen3.7-plus-2026-05-26",
    "qwen3.6-plus-2026-04-02",
    "qwen3.5-plus-2026-04-20",
    "qwen-plus-2025-12-01",
    "qwen3-next-80b-a3b-instruct",
    "qwen-flash",
    "qwen-turbo",
    "qwen3.5-plus",
    "qwen3.6-plus",
    "qwen-plus-latest",
    "qwen-flash-latest",
    "qwen-max-latest",
    "qwen3.5-122b-a10b",
    "qwen3-max",
    "qwen-max",
    "qwen3.5-plus-2026-02-15",
    "qwen-plus-2025-07-28",
    "qwen3.7-plus",
    "qwen-plus",
)

# Если осталось мало — не ждём 403, сразу берём следующую модель.
LOW_REMAINING_REQUESTS = 5
LOW_REMAINING_TOKENS = 12000
LOW_REMAINING_RATIO = 0.10

# Пауза одной модели, не до следующей ночи: 15 мин → 30 → … максимум 3 часа.
COOLDOWN_SEC = 15 * 60
COOLDOWN_MAX_SEC = 3 * 3600
MISSING_MODEL_SEC = 12 * 3600

_HEADER_REMAINING_REQUESTS = (
    "x-ratelimit-remaining-requests",
    "openai-ratelimit-remaining-requests",
    "x-dashscope-remaining-requests",
    "x-dashscope-call-limit-remaining",
)
_HEADER_LIMIT_REQUESTS = (
    "x-ratelimit-limit-requests",
    "openai-ratelimit-limit-requests",
    "x-dashscope-limit-requests",
)
_HEADER_REMAINING_TOKENS = (
    "x-ratelimit-remaining-tokens",
    "openai-ratelimit-remaining-tokens",
    "x-dashscope-remaining-tokens",
)
_HEADER_LIMIT_TOKENS = (
    "x-ratelimit-limit-tokens",
    "openai-ratelimit-limit-tokens",
    "x-dashscope-limit-tokens",
)

_lock = threading.Lock()
_state: Dict[str, Dict[str, Any]] = {}
_loaded_mtime = 0.0

_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = Path(os.getenv("QWEN_QUOTA_STATE") or (_ROOT / "tmp" / "qwen_quota.json"))


def is_yandex_lite(model_key: Optional[str]) -> bool:
    return (model_key or "").strip().lower() in YANDEX_LITE_KEYS


def _state_path() -> Optional[Path]:
    if os.getenv("QWEN_QUOTA_PERSIST", "1") == "0":
        return None
    return STATE_PATH


def _cooldown_until(row: Optional[Dict[str, Any]] = None) -> tuple[float, int]:
    hits = int((row or {}).get("exhaust_hits") or 0) + 1
    delay = min(COOLDOWN_MAX_SEC, COOLDOWN_SEC * (2 ** min(hits - 1, 5)))
    return time.time() + delay, hits


def _first_int(value: Any) -> Optional[int]:
    m = re.search(r"\d+", str(value or ""))
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


def _headers_map(headers: Any) -> Dict[str, str]:
    if not headers:
        return {}
    try:
        return {str(k).lower(): str(v) for k, v in headers.items()}
    except Exception:
        return {}


def _header_int(headers: Dict[str, str], names: tuple[str, ...]) -> Optional[int]:
    for name in names:
        if name in headers:
            parsed = _first_int(headers[name])
            if parsed is not None:
                return parsed
    return None


def _load_unlocked() -> None:
    global _loaded_mtime
    path = _state_path()
    if path is None or not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        if isinstance(raw, dict):
            _state.clear()
            for key, val in raw.items():
                if isinstance(val, dict):
                    _state[str(key)] = val
        _loaded_mtime = path.stat().st_mtime
    except Exception:
        logger.warning("Не удалось прочитать %s", path)


def _maybe_reload_unlocked() -> None:
    path = _state_path()
    if path is None or not path.exists():
        return
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return
    if mtime > _loaded_mtime:
        _load_unlocked()


def _save_unlocked() -> None:
    path = _state_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        global _loaded_mtime
        _loaded_mtime = path.stat().st_mtime
    except Exception:
        logger.warning("Не удалось сохранить %s", path)


def reset_quota_state() -> None:
    """Для тестов: забыть лимиты, файл не трогаем если persist выключен."""
    global _loaded_mtime
    with _lock:
        _state.clear()
        _loaded_mtime = 0.0


def _is_low(row: Dict[str, Any]) -> bool:
    if row.get("low"):
        return True
    rem_req = row.get("remaining_requests")
    lim_req = row.get("limit_requests")
    rem_tok = row.get("remaining_tokens")
    lim_tok = row.get("limit_tokens")
    if rem_req is not None and rem_req <= LOW_REMAINING_REQUESTS:
        return True
    if rem_tok is not None and rem_tok <= LOW_REMAINING_TOKENS:
        return True
    if rem_req is not None and lim_req:
        try:
            if rem_req / float(lim_req) <= LOW_REMAINING_RATIO:
                return True
        except ZeroDivisionError:
            pass
    if rem_tok is not None and lim_tok:
        try:
            if rem_tok / float(lim_tok) <= LOW_REMAINING_RATIO:
                return True
        except ZeroDivisionError:
            pass
    return False


def _base_chain(model_key: Optional[str] = None) -> List[str]:
    if is_yandex_lite(model_key):
        return [(model_key or "").strip() or "yandex-lite"]
    k = (model_key or "").strip()
    kl = k.lower()
    chain = list(FREE_QWEN_MODELS)
    if kl in ("", "qwen-plus", "qwen-plus-latest", "qwen-reviews", "qwen-feedback", "qwen-claims", "qwen-free"):
        return chain
    if k in chain:
        # Не начинать с выгоревшего имени: живые модели в начале общего списка.
        return chain
    return [k] + [m for m in chain if m != k]


def _order_by_quota(chain: List[str]) -> List[str]:
    now = time.time()
    fresh: List[str] = []
    low: List[str] = []
    for model in chain:
        row = _state.get(model) or {}
        until = float(row.get("exhausted_until") or 0)
        if until > now:
            continue
        if _is_low(row):
            low.append(model)
        else:
            fresh.append(model)
    available = fresh + low
    if available:
        return available
    return []


def resolve_free_qwen_models(model_key: Optional[str] = None) -> List[str]:
    chain = _base_chain(model_key)
    if is_yandex_lite(model_key):
        return chain
    with _lock:
        _maybe_reload_unlocked()
        return _order_by_quota(chain)


def all_qwen_models_exhausted() -> bool:
    return qwen_wait_sec() > 0


def qwen_pause_message() -> Optional[str]:
    """Текст для экрана: когда снова можно бесплатный Qwen. None — можно сейчас."""
    wait = qwen_wait_sec()
    if wait <= 0:
        return None
    until = datetime.now(timezone.utc) + timedelta(seconds=wait)
    try:
        from zoneinfo import ZoneInfo
        clock = until.astimezone(ZoneInfo("Europe/Moscow")).strftime("%H:%M")
    except Exception:
        clock = until.strftime("%H:%M UTC")
    return (
        f"Бесплатный лимит Qwen закончился. Следующая попытка около {clock} МСК. "
        "Чтобы разметить сейчас — выбери YandexGPT Lite."
    )


def qwen_wait_sec() -> float:
    """0 — можно звать API. Иначе секунд до ближайшей модели (как в отзывах, без общей паузы на полчаса)."""
    now = time.time()
    with _lock:
        _maybe_reload_unlocked()
        earliest = None
        for model in FREE_QWEN_MODELS:
            until = float((_state.get(model) or {}).get("exhausted_until") or 0)
            if until <= now:
                return 0.0
            if earliest is None or until < earliest:
                earliest = until
        if earliest is None:
            return 0.0
        return max(1.0, earliest - now)


def note_exhausted(model: str, *, until: Optional[float] = None) -> None:
    name = (model or "").strip()
    if not name:
        return
    with _lock:
        _maybe_reload_unlocked()
        row = dict(_state.get(name) or {})
        existing_until = float(row.get("exhausted_until") or 0)
        if until is not None:
            stamp = until
            hits = int(row.get("exhaust_hits") or 0) + 1
        elif existing_until > time.time():
            # Уже на паузе (например, после HTTP 403) — не удваивать ожидание.
            stamp = existing_until
            hits = int(row.get("exhaust_hits") or 1)
        else:
            stamp, hits = _cooldown_until(row)
        row["exhausted_until"] = stamp
        row["exhaust_hits"] = hits
        row["low"] = True
        _state[name] = row
        _save_unlocked()
    logger.warning(
        "Qwen %s: лимит исчерпан, до %s берём другую модель",
        name, datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat(),
    )


def note_http_quota(model: str, status: Optional[int], headers: Any = None, body: str = "") -> None:
    """Разобрать ответ API: если лимит кончается — пометить модель и в следующий раз взять другую."""
    name = (model or "").strip()
    if not name:
        return
    hdrs = _headers_map(headers)
    rem_req = _header_int(hdrs, _HEADER_REMAINING_REQUESTS)
    lim_req = _header_int(hdrs, _HEADER_LIMIT_REQUESTS)
    rem_tok = _header_int(hdrs, _HEADER_REMAINING_TOKENS)
    lim_tok = _header_int(hdrs, _HEADER_LIMIT_TOKENS)
    body_s = body or ""
    missing = is_model_missing_error(body_s)
    quota_err = is_quota_error(body_s) or rem_req == 0 or status == 429
    hard_fail = status in (403, 404) and (missing or is_quota_error(body_s) or not body_s.strip())

    with _lock:
        _maybe_reload_unlocked()
        row = dict(_state.get(name) or {})
        if rem_req is not None:
            row["remaining_requests"] = rem_req
        if lim_req is not None:
            row["limit_requests"] = lim_req
        if rem_tok is not None:
            row["remaining_tokens"] = rem_tok
        if lim_tok is not None:
            row["limit_tokens"] = lim_tok

        if missing or quota_err or hard_fail:
            if missing:
                stamp = time.time() + MISSING_MODEL_SEC
                hits = int(row.get("exhaust_hits") or 0)
            else:
                stamp, hits = _cooldown_until(row)
            row["exhausted_until"] = stamp
            row["exhaust_hits"] = hits
            row["low"] = True
            _state[name] = row
            _save_unlocked()
            logger.warning("Qwen %s: лимит закончился (HTTP %s) — переключаемся", name, status)
            return

        low = _is_low(row)
        was_low = bool(row.get("low"))
        row["low"] = low
        if status == 200 or (not low and rem_req is not None and rem_req > LOW_REMAINING_REQUESTS):
            row.pop("exhausted_until", None)
            row.pop("exhaust_hits", None)
        _state[name] = row
        _save_unlocked()
        if low and not was_low:
            logger.info(
                "Qwen %s: лимит на исходе (запросов %s, токенов %s) — дальше другая модель",
                name, rem_req, rem_tok,
            )


def is_model_missing_error(err: str) -> bool:
    e = (err or "").lower()
    needles = (
        "invalidparameter.model",
        "model_not_found",
        "unknown model",
        "model does not exist",
        "model not exist",
        "invalid model",
    )
    return any(n in e for n in needles)


def is_quota_error(err: str) -> bool:
    e = (err or "").lower()
    needles = (
        "allocationquota",
        "freetieronly",
        "insufficient_quota",
        "quota exhausted",
        "quota exceeded",
        "free tier",
        "free quota",
        "rate limit",
        "rate_limit",
        "на паузе",
        "без запроса к api",
    )
    return any(n in e for n in needles)


def is_quota_or_model_error(err: str) -> bool:
    e = (err or "").lower()
    if is_quota_error(e) or is_model_missing_error(e):
        return True
    needles = (
        "http 403",
        "http 404",
        "http 429",
        "лимит",
    )
    return any(n in e for n in needles)


def is_transient_network_error(err: str) -> bool:
    """Таймаут/сеть: модель не выгорела, просто берём следующую."""
    e = (err or "").lower()
    needles = (
        "таймаут",
        "timeout",
        "сеть/",
        "server disconnected",
        "cannot connect",
        "connection reset",
        "clientconnectorerror",
    )
    return any(n in e for n in needles)


def should_rotate_qwen_model(err: str) -> bool:
    return is_quota_or_model_error(err) or is_transient_network_error(err)
