"""Фоновые задачи CX вне процесса сайта.

Сайт (cx-backend) только отвечает на страницы.
Этот процесс тянет WB/Ozon/ЯМ, чаты, ИИ и статусы доставок.
Если он завис — вкладки всё равно открываются.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from sqlalchemy import text

from .database import engine, get_db

logger = logging.getLogger("cx.bg")

# Тот же ключ, что раньше держал «лидер» uvicorn — чтобы не запустить фон дважды.
LEADER_LOCK_ID = 87442201

# Надзор за задачами: упала → лог + Telegram + перезапуск, а не тихая смерть.
_RESTART_DELAY_SEC = 30
_ALERT_COOLDOWN_SEC = 3600
_last_alert: dict[str, float] = {}


def _alert_bg(name: str, msg: str) -> None:
    """Telegram-алерт про фоновую задачу, не чаще раза в час на задачу."""
    now = time.time()
    if now - _last_alert.get(name, 0) < _ALERT_COOLDOWN_SEC:
        return
    _last_alert[name] = now
    chat = (os.getenv("TG_ADMIN_CHAT_ID") or os.getenv("TG_ALERT_CHAT_ID") or "").strip()
    if not chat:
        return
    try:
        from . import telegram_proxy
        telegram_proxy.send_text(chat, telegram_proxy.strip_tg_html(msg))
    except Exception as e:
        logger.warning("cx-bg: не удалось отправить алерт про %s: %s", name, e)


async def _supervise(name: str, factory) -> None:
    """Держит одну фоновую задачу живой.

    factory() создаёт корутину воркера. Если воркер упал с ошибкой — пишем в
    лог, шлём алерт и через паузу поднимаем заново. Если воркер сам корректно
    завершился (например, отключён и делает return) — не трогаем, это осознанный
    выход, а не сбой. Одна больная задача больше не роняет весь cx-bg.
    """
    while True:
        try:
            await factory()
            logger.info("cx-bg: задача %s завершилась штатно, не перезапускаю", name)
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("cx-bg: задача %s упала: %s", name, e)
            _alert_bg(
                name,
                f"⚠️ Фоновая задача «{name}» упала и будет перезапущена через "
                f"{_RESTART_DELAY_SEC} с.\nОшибка: {type(e).__name__}: {e}",
            )
            try:
                await asyncio.sleep(_RESTART_DELAY_SEC)
            except asyncio.CancelledError:
                raise


async def acquire_leader_lock():
    """Ждём, пока старый процесс (uvicorn-лидер) отпустит блокировку."""
    while True:
        conn = engine.connect()
        try:
            # Сессионный advisory lock живёт до закрытия соединения.
            # AUTOCOMMIT: иначе соединение остаётся idle in transaction,
            # и idle_in_transaction_session_timeout его убивает — монитор
            # думает, что фон не запущен.
            conn = conn.execution_options(isolation_level="AUTOCOMMIT")
            got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:i)"), {"i": LEADER_LOCK_ID}).scalar())
        except Exception as e:
            logger.warning("cx-bg: lock query failed: %s", e)
            try:
                conn.close()
            except Exception:
                pass
            await asyncio.sleep(5)
            continue
        if got:
            logger.info("cx-bg: leader lock %s acquired", LEADER_LOCK_ID)
            return conn
        conn.close()
        logger.info("cx-bg: lock busy — жду 3с (ещё держит старый процесс)")
        await asyncio.sleep(3)


def spawn_tasks(get_db_func) -> list[asyncio.Task]:
    from . import chat_bot, mp_api_watch, ozon_chat_sync, ozon_photo_sync, tshopping, wb_cabinet_bot, wb_chat, wb_chat_sync, wb_mini_sync, ym_chat_sync
    from .main import (
        _cdek_number_filler,
        _cdek_status_updater,
        _ozon_status_updater,
        _yandex_status_updater,
    )
    from .routers.cx_responses import start_photo_sync_worker, start_review_auto_worker

    # (имя, фабрика корутины). Фабрика нужна, чтобы надзиратель мог поднять
    # воркер заново после падения — повторно вызвав start_*.
    workers = [
        ("wb_chat", lambda: wb_chat.start_worker(get_db_func)),
        ("wb_mini_sync", lambda: wb_mini_sync.start_mini_sync_worker(get_db_func)),
        ("cdek_filler", lambda: _cdek_number_filler(get_db_func)),
        ("cdek_updater", lambda: _cdek_status_updater(get_db_func)),
        ("yandex_updater", lambda: _yandex_status_updater(get_db_func)),
        ("ozon_updater", lambda: _ozon_status_updater(get_db_func)),
        ("wb_chat_sync", lambda: wb_chat_sync.start_chat_sync_worker(get_db_func)),
        ("wb_claim_chat", lambda: wb_cabinet_bot.start_wb_claim_chat_worker(get_db_func)),
        ("ozon_chat_sync", lambda: ozon_chat_sync.start_ozon_chat_sync_worker(get_db_func)),
        ("ym_chat_sync", lambda: ym_chat_sync.start_ym_chat_sync_worker(get_db_func)),
        ("photo_sync", lambda: start_photo_sync_worker(get_db_func)),
        ("ozon_photo_sync", lambda: ozon_photo_sync.start_ozon_photo_sync_worker(get_db_func)),
        ("chat_bot", lambda: chat_bot.start_chat_bot_worker(get_db_func)),
        ("tshopping", lambda: tshopping.start_worker(get_db_func)),
        ("review_auto", lambda: start_review_auto_worker(get_db_func)),
        ("mp_api_watch", lambda: mp_api_watch.start_worker(get_db_func)),
    ]
    return [asyncio.create_task(_supervise(name, factory), name=name)
            for name, factory in workers]


# Ключи, без которых соответствующий синк молча ничего не делает.
# (имя переменной в .env, за что отвечает)
_REQUIRED_KEYS = [
    ("WB_API_KEY", "чаты и заказы Wildberries"),
    ("OZON_CLIENT_ID", "Ozon (id кабинета)"),
    ("OZON_API_KEY", "чаты и возвраты Ozon"),
    ("YM_API_KEY", "Яндекс Маркет"),
    ("QWEN_API_KEY", "ИИ-ответы и теги (Qwen)"),
]


def _check_api_keys() -> None:
    """При старте фона проверяем, что ключи заданы. Пусто → лог + алерт.

    Ловит класс «синк молча не работает, потому что ключ забыли/стёрли».
    Протухший ключ так не поймать — это делает монитор свежести данных.
    """
    missing = [(k, what) for k, what in _REQUIRED_KEYS if not (os.getenv(k) or "").strip()]
    if not missing:
        logger.info("cx-bg: все ключи API на месте")
        return
    lines = "\n".join(f"• {k} — {what}" for k, what in missing)
    logger.error("cx-bg: не заданы ключи API:\n%s", lines)
    _alert_bg(
        "api_keys",
        "⚠️ При запуске фона не заданы ключи API — эти синки работать не будут:\n" + lines,
    )


async def run_forever() -> None:
    from . import wb_chat

    lock_conn = await acquire_leader_lock()
    _check_api_keys()
    wb_chat.init_event_cursor(get_db)
    try:
        from . import telegram_webapp
        telegram_webapp.configure_menu_button()
    except Exception as e:
        logger.warning("cx-bg: Mini App menu button: %s", e)

    tasks = spawn_tasks(get_db)
    logger.info("cx-bg: %s background tasks running", len(tasks))
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("cx-bg: stopping")
        raise
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        try:
            lock_conn.close()
        except Exception:
            pass
