"""
Клиент Qwen в формате OpenAI Chat Completions.

Один и тот же код работает с:
  - облачным Qwen API (DashScope compatible-mode),
  - локальным Ollama / vLLM на отдельном GPU-сервере.

Переменные окружения:
  QWEN_BASE_URL   например http://GPU:8000/v1
                  или https://dashscope.aliyuncs.com/compatible-mode/v1
  QWEN_API_KEY    ключ (для Ollama можно пустой)
  QWEN_MODEL      qwen-plus / qwen2.5-72b-instruct / ...
  QWEN_TIMEOUT_SEC  таймаут одного запроса, по умолчанию 90
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional

import aiohttp
from sqlalchemy import text

logger = logging.getLogger("qwen_client")

PHONE_RE = re.compile(
    r"\+?7?\s*\(?\d{3}\)?\s*\d{3}[-\s]?\d{2}[-\s]?\d{2}"
)
# 11 цифр подряд (7/8 + 10) — пользовательский regex выше их часто режет не целиком.
PHONE_COMPACT_RE = re.compile(
    r"(?:\+7|8|7)\s*\(?\d{3}\)?\s*\d{3}[-\s]?\d{2}[-\s]?\d{2}"
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_DIGITS_RE = re.compile(r"\d{10,15}")

# Телефоны, вынутые из исходного текста до маскирования — чтобы tools
# могли найти клиента, не отправляя номер в модель.
_extracted_pii: contextvars.ContextVar[Dict[str, List[str]]] = contextvars.ContextVar(
    "qwen_extracted_pii", default={"phones": [], "emails": []}
)

_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {}


def mask_pii(text: Optional[str]) -> str:
    """Прячет телефоны и email перед отправкой во внешний API."""
    if not text:
        return ""
    masked = PHONE_COMPACT_RE.sub("[ТЕЛЕФОН]", str(text))
    masked = PHONE_RE.sub("[ТЕЛЕФОН]", masked)
    masked = EMAIL_RE.sub("[EMAIL]", masked)
    return masked


def extract_phones(text: Optional[str]) -> List[str]:
    if not text:
        return []
    found = []
    for raw in PHONE_RE.findall(str(text)):
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 10:
            found.append(digits)
    if not found:
        for raw in PHONE_DIGITS_RE.findall(str(text)):
            if len(raw) >= 10:
                found.append(raw)
    return found


def _normalize_phone_tail(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    return digits[-10:] if len(digits) >= 10 else ""


def ai_tool(name: str, description: str, parameters: Dict[str, Any]):
    """Реестр инструментов: модель вызывает имя, Python выполняет функцию."""

    def decorator(fn: Callable):
        _TOOL_REGISTRY[name] = {
            "fn": fn,
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            },
        }
        return fn

    return decorator


def openai_tool_schemas(names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    if names is None:
        names = list(_TOOL_REGISTRY.keys())
    return [_TOOL_REGISTRY[n]["schema"] for n in names if n in _TOOL_REGISTRY]


def execute_tool(name: str, arguments: Dict[str, Any]) -> str:
    if name not in _TOOL_REGISTRY:
        return json.dumps({"error": f"неизвестный инструмент: {name}"}, ensure_ascii=False)
    try:
        result = _TOOL_REGISTRY[name]["fn"](**(arguments or {}))
        return json.dumps(result, ensure_ascii=False, default=str)
    except TypeError as e:
        return json.dumps({"error": f"неверные аргументы: {e}"}, ensure_ascii=False)
    except Exception as e:
        logger.exception("tool %s failed", name)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@ai_tool(
    name="get_client_info",
    description=(
        "Найти клиента и его последние заявки на доотправку по телефону. "
        "Не выдумывай статус — опирайся только на то, что вернула функция."
    ),
    parameters={
        "type": "object",
        "properties": {
            "phone": {
                "type": "string",
                "description": "Телефон клиента. Если в тексте он скрыт как [ТЕЛЕФОН] — передай пустую строку.",
            }
        },
        "required": [],
    },
)
def get_client_info(phone: str = "") -> Dict[str, Any]:
    from .database import SessionLocal

    raw_phone = (phone or "").strip()
    if not raw_phone or "[ТЕЛЕФОН]" in raw_phone:
        extracted = _extracted_pii.get().get("phones") or []
        raw_phone = extracted[0] if extracted else ""

    tail = _normalize_phone_tail(raw_phone)
    if not tail:
        return {"found": False, "reason": "нет телефона для поиска"}

    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT id, customer_name, status, order_number, marketplace,
                   items_to_send, track_number, created_at
            FROM reshipment_requests
            WHERE right(regexp_replace(coalesce(customer_phone, ''), '[^0-9]', '', 'g'), 10) = :tail
            ORDER BY created_at DESC NULLS LAST
            LIMIT 5
        """), {"tail": tail}).mappings().all()
        if not rows:
            return {"found": False, "requests": []}
        return {
            "found": True,
            "customer_name": rows[0]["customer_name"] or "",
            "requests": [
                {
                    "id": r["id"],
                    "status": r["status"],
                    "order_number": r["order_number"],
                    "marketplace": r["marketplace"],
                    "items": r["items_to_send"],
                    "has_track": bool(r["track_number"]),
                    "created_at": str(r["created_at"]) if r["created_at"] else None,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@ai_tool(
    name="get_kb_article",
    description=(
        "Найти похожие случаи в базе знаний тегирования (pg_trgm). "
        "Используй для подсказки категории брака / формулировки ответа."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Текст обращения или описание дефекта",
            }
        },
        "required": ["query"],
    },
)
def get_kb_article(query: str) -> Dict[str, Any]:
    from .database import SessionLocal

    q = mask_pii((query or "").strip())
    if not q:
        return {"found": False, "items": []}

    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT content, tags, similarity(content, :q) AS sml
            FROM ai_knowledge_base
            WHERE similarity(content, :q) > 0.05
            ORDER BY sml DESC
            LIMIT 5
        """), {"q": q}).fetchall()
        if not rows:
            return {"found": False, "items": []}
        return {
            "found": True,
            "items": [
                {
                    "content": mask_pii(row[0] or ""),
                    "tags": row[1],
                    "similarity": float(row[2] or 0),
                }
                for row in rows
            ],
        }
    finally:
        db.close()


class QwenError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class QwenClient:
    """OpenAI-совместимый клиент: system + history + tools, retry на 5xx."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: int = 3,
    ):
        self.base_url = (base_url or os.getenv("QWEN_BASE_URL") or "").rstrip("/")
        self.api_key = api_key if api_key is not None else (os.getenv("QWEN_API_KEY") or "")
        self.model = model or os.getenv("QWEN_MODEL") or "qwen-plus"
        self.timeout = float(timeout if timeout is not None else os.getenv("QWEN_TIMEOUT_SEC") or 90)
        self.max_retries = max_retries

    def _chat_url(self) -> str:
        if not self.base_url:
            raise QwenError("Не задан QWEN_BASE_URL")
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _prepare_messages(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        mask_user_text: bool,
    ) -> List[Dict[str, Any]]:
        phones: List[str] = []
        emails: List[str] = []
        prepared: List[Dict[str, Any]] = []
        if system:
            prepared.append({"role": "system", "content": mask_pii(system) if mask_user_text else system})
        for msg in messages or []:
            item = dict(msg)
            content = item.get("content")
            if isinstance(content, str):
                phones.extend(extract_phones(content))
                emails.extend(EMAIL_RE.findall(content))
                if mask_user_text and item.get("role") in ("user", "system"):
                    item["content"] = mask_pii(content)
            prepared.append(item)
        _extracted_pii.set({"phones": phones, "emails": emails})
        return prepared

    async def _post(
        self,
        session: aiohttp.ClientSession,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        url = self._chat_url()
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                async with session.post(
                    url, headers=self._headers(), json=payload, timeout=timeout
                ) as resp:
                    body = await resp.text()
                    try:
                        from .qwen_quota import note_http_quota
                        note_http_quota(self.model, resp.status, resp.headers, body)
                    except Exception:
                        pass
                    if resp.status == 200:
                        try:
                            return json.loads(body)
                        except json.JSONDecodeError as e:
                            raise QwenError(f"Qwen вернул не-JSON: {body[:400]}") from e
                    if resp.status == 429 or resp.status >= 500:
                        last_error = QwenError(
                            f"Qwen HTTP {resp.status}: {body[:400]}", status=resp.status
                        )
                        await asyncio.sleep(min(20, 2 ** attempt * 2))
                        continue
                    raise QwenError(f"Qwen HTTP {resp.status}: {body[:400]}", status=resp.status)
            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                last_error = QwenError(f"Qwen сеть/таймаут: {e}")
                await asyncio.sleep(min(20, 2 ** attempt * 2))
                continue

        raise last_error or QwenError("Qwen: исчерпаны попытки")

    async def chat(
        self,
        *,
        system: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
        tools: Optional[List[Any]] = None,
        response_format: Optional[Dict[str, Any]] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        mask_user_text: bool = True,
        max_tool_rounds: int = 5,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> str:
        """
        Возвращает финальный текст ответа.
        tools: список имён из реестра или готовые OpenAI-схемы.
        """
        history = self._prepare_messages(system, messages or [], mask_user_text)
        tool_schemas: Optional[List[Dict[str, Any]]] = None
        if tools:
            if tools and isinstance(tools[0], str):
                tool_schemas = openai_tool_schemas(tools)
            else:
                tool_schemas = tools

        own_session = session is None
        http = session or aiohttp.ClientSession()
        try:
            for _ in range(max_tool_rounds):
                payload: Dict[str, Any] = {
                    "model": self.model,
                    "messages": history,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                if tool_schemas:
                    payload["tools"] = tool_schemas
                    payload["tool_choice"] = "auto"
                if response_format:
                    payload["response_format"] = response_format

                data = await self._post(http, payload)
                choices = data.get("choices") or []
                if not choices:
                    raise QwenError(f"Qwen: пустой choices: {str(data)[:400]}")
                msg = choices[0].get("message") or {}
                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    content = msg.get("content") or ""
                    if isinstance(content, list):
                        content = "".join(
                            part.get("text", "") if isinstance(part, dict) else str(part)
                            for part in content
                        )
                    return str(content)

                history.append(msg)
                for call in tool_calls:
                    fn = call.get("function") or {}
                    name = fn.get("name") or ""
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                    except json.JSONDecodeError:
                        args = {}
                    result = execute_tool(name, args)
                    history.append({
                        "role": "tool",
                        "tool_call_id": call.get("id") or name,
                        "name": name,
                        "content": result,
                    })
            raise QwenError("Qwen: слишком много вызовов инструментов")
        finally:
            if own_session:
                await http.close()

    def chat_sync(
        self,
        *,
        system: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
        response_format: Optional[Dict[str, Any]] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        mask_user_text: bool = True,
    ) -> str:
        """Синхронный chat без tools — для бота и черновиков в FastAPI."""
        import requests

        history = self._prepare_messages(system, messages or [], mask_user_text)
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": history,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                resp = requests.post(
                    self._chat_url(),
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout,
                )
                try:
                    from .qwen_quota import note_http_quota
                    note_http_quota(self.model, resp.status_code, resp.headers, resp.text)
                except Exception:
                    pass
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices") or []
                    if not choices:
                        raise QwenError(f"Qwen: пустой choices: {str(data)[:400]}")
                    content = (choices[0].get("message") or {}).get("content") or ""
                    return str(content)
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_error = QwenError(
                        f"Qwen HTTP {resp.status_code}: {resp.text[:400]}",
                        status=resp.status_code,
                    )
                    time.sleep(min(20, 2 ** attempt * 2))
                    continue
                raise QwenError(
                    f"Qwen HTTP {resp.status_code}: {resp.text[:400]}",
                    status=resp.status_code,
                )
            except requests.RequestException as e:
                last_error = QwenError(f"Qwen сеть/таймаут: {e}")
                time.sleep(min(20, 2 ** attempt * 2))
        raise last_error or QwenError("Qwen: исчерпаны попытки")

    async def chat_json(self, **kwargs) -> Dict[str, Any]:
        """То же, что chat, но требует JSON-объект в ответе."""
        kwargs.setdefault("response_format", {"type": "json_object"})
        raw = await self.chat(**kwargs)
        clean = re.sub(r"```json|```", "", raw).strip()
        start = min([i for i in [clean.find("["), clean.find("{")] if i >= 0] or [0])
        end = max(clean.rfind("]"), clean.rfind("}")) + 1
        if end > start:
            clean = clean[start:end]
        return json.loads(clean)
