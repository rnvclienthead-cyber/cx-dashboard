"""Проверка прав на сервере: роль режет данные, а не только меню."""
from __future__ import annotations

from typing import FrozenSet, Optional, Tuple

PUBLIC = "public"
ANY_AUTH = "any_auth"
ADMIN = "admin"
MODULES = "modules"
DENY = "deny"

Decision = Tuple[str, Optional[FrozenSet[str]]]


def _norm(path: str) -> str:
    raw = (path or "/").split("?", 1)[0]
    if len(raw) > 1:
        raw = raw.rstrip("/")
    return raw or "/"


def _prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def classify_path(path: str) -> Decision:
    """Какой доступ нужен для URL. Неизвестные /api — закрыты (кроме admin)."""
    path = _norm(path)

    if path in {"/", "/api/v1/health/db", "/api/v1/health/live"}:
        return PUBLIC, None
    if path.startswith("/uploads"):
        return PUBLIC, None

    if path in {"/api/auth/login", "/api/auth/register", "/api/auth/telegram-webapp"}:
        return PUBLIC, None
    if path in {"/api/auth/change-password", "/api/auth/centrifugo-token"}:
        return ANY_AUTH, None
    if _prefix(path, "/api/auth/admin") or _prefix(path, "/api/auth/users") or _prefix(path, "/api/auth/invites"):
        return ADMIN, None

    if path == "/api/v1/admin/roles/my-permissions":
        return ANY_AUTH, None
    if _prefix(path, "/api/v1/admin/roles"):
        return ADMIN, None

    if path == "/api/v1/cx-control/activity/ping":
        return ANY_AUTH, None
    if _prefix(path, "/api/v1/cx-control"):
        return ADMIN, None

    # SSE сам проверяет JWT в query — браузер не шлёт Authorization.
    if _prefix(path, "/api/v1/events"):
        return PUBLIC, None

    if _prefix(path, "/api/return"):
        return PUBLIC, None

    if path == "/api/v1/tshopping/telegram-webhook":
        return PUBLIC, None
    if _prefix(path, "/api/v1/tshopping"):
        return MODULES, frozenset({"tshopping"})

    if _prefix(path, "/api/v1/shop/delivery"):
        return PUBLIC, None
    if path == "/api/v1/shop/orders/ingest":
        return PUBLIC, None
    if _prefix(path, "/api/v1/shop/orders"):
        return MODULES, frozenset({"shop_orders"})

    if (
        path in {
            "/api/v1/reshipment/categories",
            "/api/v1/reshipment/pvz",
            "/api/v1/reshipment/suggest-address",
            "/api/v1/reshipment/nearest-pickup",
            "/api/v1/reshipment/ozon-check",
            "/api/v1/reshipment/upload-photo",
            "/api/v1/reshipment/submit",
            "/api/v1/reshipment/link-salebot",
            "/api/v1/reshipment/cdek-webhook",
        }
        or _prefix(path, "/api/v1/reshipment/confirm")
        or _prefix(path, "/api/v1/reshipment/check-token")
    ):
        return PUBLIC, None
    if _prefix(path, "/api/v1/reshipment") or _prefix(path, "/api/v1/reshipment-skus"):
        return MODULES, frozenset({"reshipment"})

    if _prefix(path, "/api/v1/cx-responses/wb-photo") or _prefix(path, "/api/v1/cx-responses/chat-download"):
        return PUBLIC, None
    if _prefix(path, "/api/v1/cx-responses"):
        return MODULES, frozenset({"cx_responses"})
    if _prefix(path, "/api/v1/quality-questions"):
        return MODULES, frozenset({"quality_questions"})

    if _prefix(path, "/api/v1/kb") or _prefix(path, "/api/v1/chat-playbook") or _prefix(path, "/api/v1/chat-triage"):
        return MODULES, frozenset({"cx_responses"})
    if _prefix(path, "/api/v1/chat-bot") or _prefix(path, "/api/v1/bot"):
        return MODULES, frozenset({"cx_responses"})
    if _prefix(path, "/api/v1/nonreturnable-cats"):
        return MODULES, frozenset({"cx_responses"})
    if _prefix(path, "/api/v1/question-playbook"):
        return MODULES, frozenset({"cx_responses", "quality_questions"})
    if _prefix(path, "/api/v1/faq"):
        return MODULES, frozenset({"faq"})

    if _prefix(path, "/api/v1/dashboard"):
        return MODULES, frozenset({"dashboard"})
    if path == "/api/v1/system/logs":
        return MODULES, frozenset({"logs"})
    if path == "/api/v1/system/monitor":
        return MODULES, frozenset({"logs", "sync"})
    if path == "/api/v1/system/doc-stats":
        return MODULES, frozenset({"docs"})
    if _prefix(path, "/api/v1/system"):
        return MODULES, frozenset({"sync"})

    if _prefix(path, "/api/v1/ai/moderation") or _prefix(path, "/api/v1/ai/feedback-moderation"):
        return MODULES, frozenset({"moderation"})
    if _prefix(path, "/api/v1/ai/knowledge"):
        return MODULES, frozenset({"ai_training"})
    if _prefix(path, "/api/v1/ai"):
        return MODULES, frozenset({"ai_tagging"})
    if _prefix(path, "/api/v1/claims"):
        return MODULES, frozenset({"moderation"})

    if _prefix(path, "/api/v1/analytics/production-claims") or _prefix(path, "/api/v1/analytics/sku-trend"):
        return MODULES, frozenset({"production"})
    if _prefix(path, "/api/v1/analytics/claim-media"):
        return MODULES, frozenset({"production", "ppm", "moderation"})
    if _prefix(path, "/api/v1/analytics"):
        return MODULES, frozenset({"ppm"})

    if _prefix(path, "/api/ratings"):
        return MODULES, frozenset({"ratings"})
    if path == "/api/v1/finances/pnl-by-article" or _prefix(path, "/api/v1/finances/pnl"):
        return MODULES, frozenset({"pnl"})
    if _prefix(path, "/api/v1/finances"):
        return MODULES, frozenset({"finances"})
    if _prefix(path, "/api/v1/voc"):
        return MODULES, frozenset({"voc"})

    if path == "/api/v1/ym/voc/dashboard" or _prefix(path, "/api/v1/ym/voc"):
        return MODULES, frozenset({"voc"})
    if _prefix(path, "/api/v1/ym/finance"):
        return MODULES, frozenset({"finances", "pnl"})
    if _prefix(path, "/api/v1/ym/returns"):
        return MODULES, frozenset({"cx_responses", "voc"})
    if _prefix(path, "/api/v1/ym"):
        return MODULES, frozenset({"dashboard", "voc", "finances"})

    if path.startswith("/api/"):
        return DENY, None
    return PUBLIC, None


def decide_access(path: str, role: Optional[str], modules: FrozenSet[str] | set[str] | None) -> Optional[int]:
    """None = пустить. Иначе код ответа 401/403."""
    kind, needed = classify_path(path)
    if kind == PUBLIC:
        return None
    if not role:
        return 401
    if role == "admin":
        return None
    if kind == ANY_AUTH:
        return None
    if kind == ADMIN:
        return 403
    if kind == MODULES and needed:
        if set(modules or ()) & set(needed):
            return None
        return 403
    return 403


def session_touch_requested(headers) -> bool:
    """Браузер ставит заголовок только после действия человека, не на фоновом опросе."""
    if not headers:
        return False
    raw = headers.get(b"x-session-touch")
    if raw is None:
        return False
    try:
        value = raw.decode("latin-1").strip()
    except Exception:
        return False
    return value == "1"


def sliding_token_for(user: Optional[dict], headers) -> Optional[str]:
    """Новый вход на сутки вперёд. None — срок не двигаем."""
    if not user or not user.get("username"):
        return None
    if not session_touch_requested(headers):
        return None
    from .routers.auth import create_access_token

    return create_access_token({
        "sub": user["username"],
        "role": user.get("role") or "",
    })


def _bearer_token(headers) -> Optional[str]:
    raw = headers.get(b"authorization") or headers.get(b"Authorization")
    if not raw:
        return None
    try:
        value = raw.decode("latin-1")
    except Exception:
        return None
    if not value.lower().startswith("bearer "):
        return None
    token = value[7:].strip()
    return token or None


def _user_from_token(token: str) -> Optional[dict]:
    from jose import JWTError, jwt
    from .routers.auth import ALGORITHM, SECRET_KEY

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    username = payload.get("sub")
    role = payload.get("role")
    if not username:
        return None
    return {"username": str(username), "role": str(role or "")}


def _role_modules(role: str) -> FrozenSet[str]:
    from sqlalchemy import text
    from .database import engine

    if role == "admin":
        return frozenset()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT module FROM role_permissions WHERE role_name = :r"),
            {"r": role},
        ).fetchall()
    return frozenset(str(r[0]) for r in rows if r and r[0])


def _reject(status: int, detail: str):
    from starlette.responses import JSONResponse
    return JSONResponse(status_code=status, content={"detail": detail})


class ModuleAccessMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or (scope.get("method") or "") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or "/"
        kind, _needed = classify_path(path)
        if kind == PUBLIC:
            await self.app(scope, receive, send)
            return

        token = _bearer_token(dict(scope.get("headers") or []))
        user = _user_from_token(token) if token else None
        role = user["role"] if user else None
        modules: FrozenSet[str] = frozenset()
        if role and role != "admin":
            try:
                modules = _role_modules(role)
            except Exception:
                response = _reject(500, "Не удалось проверить права доступа")
                await response(scope, receive, send)
                return

        status = decide_access(path, role, modules)
        if status == 401:
            response = _reject(401, "Not authenticated")
            await response(scope, receive, send)
            return
        if status == 403:
            response = _reject(403, "Нет доступа к этому разделу")
            await response(scope, receive, send)
            return

        fresh = sliding_token_for(user, dict(scope.get("headers") or []))

        async def send_with_session(message):
            if fresh and message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-access-token", fresh.encode("ascii")))
                message = dict(message)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_session)
