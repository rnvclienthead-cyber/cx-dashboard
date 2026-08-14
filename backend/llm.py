"""Единая точка вызова LLM: Qwen Plus по умолчанию, Yandex Lite как запасной."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

import aiohttp

from .qwen_client import QwenClient, mask_pii

logger = logging.getLogger("llm")

YANDEX_LITE_KEYS = {"yandex-lite", "yandexgpt-lite"}
DEFAULT_QWEN = "qwen-plus"


def is_yandex_lite(model_key: Optional[str]) -> bool:
    return (model_key or "").strip().lower() in YANDEX_LITE_KEYS


def qwen_model_name(model_key: Optional[str] = None) -> str:
    """Pro/Claude/пустое → qwen-plus. Явный qwen-* оставляем как есть."""
    raw = (model_key or "").strip()
    k = raw.lower()
    # Алиас очереди отзывов — не имя API. Остальные qwen* (plus/max/даты) идут как есть.
    if k.startswith("qwen") and k not in {"qwen-reviews", "qwen-feedback"}:
        return raw
    env = (os.getenv("QWEN_MODEL") or DEFAULT_QWEN).strip()
    return env or DEFAULT_QWEN


def _yandex_payload(system: str, user: str, *, temperature: float, max_tokens: int) -> Dict[str, Any]:
    folder_id = os.getenv("FOLDER_ID") or ""
    return {
        "modelUri": f"gpt://{folder_id}/yandexgpt-lite/latest",
        "completionOptions": {"temperature": temperature, "maxTokens": max_tokens},
        "messages": [
            {"role": "system", "text": system},
            {"role": "user", "text": user},
        ],
    }


def _yandex_headers() -> Dict[str, str]:
    api_key = os.getenv("YANDEX_API_KEY") or ""
    folder_id = os.getenv("FOLDER_ID") or ""
    headers = {"Authorization": f"Api-Key {api_key}", "Content-Type": "application/json"}
    if folder_id:
        headers["x-folder-id"] = folder_id
    return headers


def complete_sync(
    *,
    system: str,
    user: str,
    model_key: str = DEFAULT_QWEN,
    temperature: float = 0.1,
    max_tokens: int = 2000,
    response_format: Optional[Dict[str, Any]] = None,
    mask_user_text: bool = True,
) -> str:
    system_s = mask_pii(system) if mask_user_text else system
    user_s = mask_pii(user) if mask_user_text else user

    if is_yandex_lite(model_key):
        import requests

        if not os.getenv("YANDEX_API_KEY") or not os.getenv("FOLDER_ID"):
            raise RuntimeError("Yandex Lite: нет YANDEX_API_KEY / FOLDER_ID")
        resp = requests.post(
            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
            headers=_yandex_headers(),
            json=_yandex_payload(system_s, user_s, temperature=temperature, max_tokens=max_tokens),
            timeout=45,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Yandex Lite HTTP {resp.status_code}: {resp.text[:400]}")
        return (resp.json()["result"]["alternatives"][0]["message"]["text"] or "").strip()

    return QwenClient(model=qwen_model_name(model_key)).chat_sync(
        system=system_s,
        messages=[{"role": "user", "content": user_s}],
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
        mask_user_text=False,
    )


async def complete_async(
    *,
    system: str,
    user: str,
    model_key: str = DEFAULT_QWEN,
    temperature: float = 0.1,
    max_tokens: int = 2000,
    response_format: Optional[Dict[str, Any]] = None,
    session: Optional[aiohttp.ClientSession] = None,
    mask_user_text: bool = True,
) -> str:
    system_s = mask_pii(system) if mask_user_text else system
    user_s = mask_pii(user) if mask_user_text else user

    if is_yandex_lite(model_key):
        if not os.getenv("YANDEX_API_KEY") or not os.getenv("FOLDER_ID"):
            raise RuntimeError("Yandex Lite: нет YANDEX_API_KEY / FOLDER_ID")
        own = session is None
        http = session or aiohttp.ClientSession()
        try:
            last_error = None
            for attempt in range(3):
                try:
                    async with http.post(
                        "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                        headers=_yandex_headers(),
                        json=_yandex_payload(
                            system_s, user_s, temperature=temperature, max_tokens=max_tokens
                        ),
                        timeout=aiohttp.ClientTimeout(total=90),
                    ) as resp:
                        if resp.status == 200:
                            res = await resp.json()
                            return (res["result"]["alternatives"][0]["message"]["text"] or "").strip()
                        if resp.status == 429:
                            await asyncio.sleep(5 * (attempt + 1))
                            continue
                        raise RuntimeError(f"Yandex Lite HTTP {resp.status}: {await resp.text()}")
                except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                    last_error = e
                    await asyncio.sleep(3)
            raise RuntimeError(f"Yandex Lite: {last_error}")
        finally:
            if own:
                await http.close()

    return await QwenClient(model=qwen_model_name(model_key)).chat(
        system=system_s,
        messages=[{"role": "user", "content": user_s}],
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
        session=session,
        mask_user_text=False,
    )
