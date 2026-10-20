import asyncio
import logging
import os
import time
from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text
from contextlib import asynccontextmanager
from .database import get_db, engine, add_column_if_missing, index_exists
from .routers import claims, ai, system, production, ppm, ratings, auth, finances, voc, dashboard, ym, reshipment, roles, returns, events, cx_control, faq, cx_responses, kb, reshipment_skus, chat_playbook, chat_triage, chat_bot as chat_bot_router, nonreturnable_cats, question_playbook, tshopping as tshopping_router, shop_delivery as shop_delivery_router, shop_orders as shop_orders_router
from . import chat_bot, ym_product_card, tshopping, shop_orders
from . import wb_chat, wb_mini_sync, cx_alert, wb_chat_sync, ozon_chat_sync, ozon_photo_sync, ym_chat_sync
from .upload_guard import ChatAttachmentContentLengthMiddleware
from .access import ModuleAccessMiddleware
from . import loop_watch, sse_bridge


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Схема/таблицы вынесены в backend/schema.py и идут под advisory-локом:
    # прогоняет один процесс, остальные воркеры ждут готовую базу.
    from . import schema
    await asyncio.to_thread(schema.apply_schema, engine)

    # Фоновые синки/ИИ/доставки — отдельный процесс backend_bg.py (cx-bg.service).
    # Иначе лидер uvicorn зависает на минуты, и половина вкладок не открывается.
    bg_tasks = [
        asyncio.create_task(loop_watch.start_watchdog()),
        asyncio.create_task(sse_bridge.listen_forever()),
    ]
    logging.getLogger("uvicorn.error").info("cx-backend: HTTP only — background workers run in cx-bg")

    yield

    for t in bg_tasks:
        t.cancel()
    for t in bg_tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass


# Инициализируем приложение
app = FastAPI(
    title="CX Dashboard API",
    description="API для работы с рекламациями и аналитикой",
    version="1.0.0",
    lifespan=lifespan
)

# Ранний отказ до multipart-парсера. Не заменяет client_max_body_size в nginx.
app.add_middleware(ChatAttachmentContentLengthMiddleware)
app.add_middleware(ModuleAccessMiddleware)

# Настройка CORS для стабильного общения фронтенда и бэкенда
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Access-Token"],
    allow_credentials=True,
)
app.add_middleware(loop_watch.SlowRequestMiddleware)

# Подключаем маршруты к серверу
app.include_router(claims.router)
app.include_router(ai.router)
app.include_router(system.router)
app.include_router(production.router)  
app.include_router(ppm.router)         
app.include_router(ratings.router)
app.include_router(auth.router)
app.include_router(finances.router)
app.include_router(voc.router)
app.include_router(dashboard.router)
app.include_router(ym.router)
app.include_router(roles.router)
app.include_router(reshipment.public_router)
app.include_router(reshipment.router)
app.include_router(returns.router)
app.include_router(events.router)
app.include_router(cx_control.public_router)
app.include_router(cx_control.router)
app.include_router(faq.router)
app.include_router(cx_responses.public_router)
app.include_router(cx_responses.router)
app.include_router(cx_responses.quality_questions_router)
app.include_router(kb.router)
app.include_router(reshipment_skus.router)
app.include_router(nonreturnable_cats.router)
app.include_router(chat_playbook.router)
app.include_router(question_playbook.router)
app.include_router(chat_triage.router)
app.include_router(chat_bot_router.router)
app.include_router(chat_bot_router.bot_api_router)
app.include_router(tshopping_router.public_router)
app.include_router(tshopping_router.router)
app.include_router(shop_delivery_router.router)
app.include_router(shop_orders_router.public_router)
app.include_router(shop_orders_router.router)
app.mount("/uploads", StaticFiles(directory=os.getenv("CX_UPLOAD_BASE_DIR", "/root/cx-dashboard/uploads")), name="uploads")

async def _cdek_number_filler(get_db_func):
    """Каждые 10 минут дозаполняет cdek_number и проверяет статус CDEK для актуализации."""
    import logging, secrets as _sec
    logger = logging.getLogger("cdek_filler")

    # Статусы CDEK, при которых посылка считается "отправлена" клиенту
    SHIPPED_CODES = {"ACCEPTED_AT_PICK_UP_POINT", "READY_FOR_PICKUP",
                     "RECEIVED_AT_DESTINATION_WAREHOUSE", "DELIVERED"}

    while True:
        await asyncio.sleep(600)
        try:
            from .cdek import get_order_by_uuid
            db = next(get_db_func())

            # Заявки без cdek_number но с uuid
            rows = db.execute(text("""
                SELECT id, cdek_uuid FROM reshipment_requests
                WHERE cdek_uuid IS NOT NULL AND cdek_uuid != ''
                  AND (cdek_number IS NULL OR cdek_number = '')
                  AND status NOT IN ('rejected', 'returned')
            """)).mappings().fetchall()

            for row in rows:
                await asyncio.sleep(1.5)  # СДЭК банит как бот при частых запросах подряд
                try:
                    info = await asyncio.to_thread(get_order_by_uuid, row["cdek_uuid"])
                    num = info.get("cdek_number", "")
                    cost = info.get("actual_cost")
                    status_code = info.get("status_code", "")
                    if not num:
                        continue

                    # Если CDEK уже в статусе "отправлено" — обновляем статус заявки сразу
                    if status_code in SHIPPED_CODES:
                        updated = db.execute(text("""
                            UPDATE reshipment_requests
                            SET cdek_number = :num,
                                cdek_cost_actual = COALESCE(:cost, cdek_cost_actual),
                                status = 'shipped',
                                shipped_at = COALESCE(shipped_at, NOW()),
                                confirmation_token = COALESCE(confirmation_token, :token)
                            WHERE id = :id AND status IN ('approved', 'packed')
                            RETURNING id, salebot_client_id, client_pvz_address, wb_reply_sign,
                                      ym_chat_id, ozon_chat_id, marketplace, wb_chat_id
                        """), {"num": num, "cost": cost, "id": row["id"],
                               "token": _sec.token_hex(32)}).mappings().first()
                        db.commit()
                        if updated:
                            from . import reshipment_notify
                            reshipment_notify.notify_delivery_status(
                                db, dict(updated), "shipped",
                                delivery_method="cdek",
                                track_number=num,
                                pvz_address=updated.get("client_pvz_address") or "",
                            )
                        logger.info("cdek_filler: заявка #%s → shipped (%s)", row["id"], status_code)
                    else:
                        db.execute(text("""
                            UPDATE reshipment_requests
                            SET cdek_number = :num,
                                cdek_cost_actual = COALESCE(:cost, cdek_cost_actual)
                            WHERE id = :id
                        """), {"num": num, "cost": cost, "id": row["id"]})
                        db.commit()
                        logger.info("cdek_filler: заявка #%s → cdek_number=%s", row["id"], num)
                except Exception as e:
                    logger.warning("cdek_filler: ошибка для #%s: %s", row["id"], e)

            try:
                from . import shop_orders as shop_orders_mod
                filled = shop_orders_mod.fill_cdek_numbers(db)
                if filled:
                    logger.info("cdek_filler: shop orders filled %s", filled)
            except Exception as e:
                logger.warning("cdek_filler: shop orders: %s", e)

            db.close()
        except Exception as e:
            logging.getLogger("cdek_filler").error("cdek_filler loop error: %s", e)


async def _cdek_status_updater(get_db_func):
    """Каждые 10 минут опрашивает CDEK и обновляет статусы заказов в пути."""
    import logging, secrets as _sec
    logger = logging.getLogger("cdek_updater")

    # CDEK statuses → наши статусы (statuses[0] = самый свежий)
    CDEK_MAP = {
        # Отправлен: CDEK принял посылку на склад отправителя
        "RECEIVED_AT_SHIPMENT_WAREHOUSE":              ("shipped",          ["approved", "packed"]),
        # В пути: посылка в сети CDEK
        "READY_FOR_SHIPMENT_IN_SENDER_CITY":           ("in_transit",       ["approved", "packed", "shipped"]),
        "TAKEN_BY_TRANSPORTER_FROM_SENDER_CITY":       ("in_transit",       ["approved", "packed", "shipped"]),
        "SENT_TO_TRANSIT_CITY":                        ("in_transit",       ["approved", "packed", "shipped"]),
        "ACCEPTED_AT_TRANSIT_WAREHOUSE":               ("in_transit",       ["approved", "packed", "shipped"]),
        "READY_FOR_SHIPMENT_IN_TRANSIT_CITY":          ("in_transit",       ["approved", "packed", "shipped"]),
        "TAKEN_BY_TRANSPORTER_FROM_TRANSIT_CITY":      ("in_transit",       ["approved", "packed", "shipped"]),
        "SENT_TO_RECIPIENT_CITY":                      ("in_transit",       ["approved", "packed", "shipped"]),
        "ACCEPTED_IN_TRANSIT_CITY":                    ("in_transit",       ["approved", "packed", "shipped"]),
        "ACCEPTED_IN_RECIPIENT_CITY":                  ("in_transit",       ["approved", "packed", "shipped"]),
        # Готов к выдаче: посылка на ПВЗ получателя
        "ACCEPTED_AT_PICK_UP_POINT":                   ("ready_for_pickup", ["approved", "packed", "shipped", "in_transit"]),
        "READY_FOR_PICKUP":                            ("ready_for_pickup", ["approved", "packed", "shipped", "in_transit"]),
        # Получен / Вернулся
        "DELIVERED":                                   ("delivered",        ["shipped", "in_transit", "ready_for_pickup"]),
        "NOT_DELIVERED":                               ("returned",         ["shipped", "in_transit", "ready_for_pickup"]),
    }
    STATUS_ORDER = ["shipped", "in_transit", "ready_for_pickup", "delivered", "returned"]

    while True:
        await asyncio.sleep(600)
        try:
            from .cdek import get_order_by_uuid
            from .salebot import parse_eta_date
            db = next(get_db_func())
            rows = db.execute(text("""
                SELECT id, cdek_uuid, cdek_number, status, salebot_client_id, wb_reply_sign,
                       client_pvz_address, ym_chat_id, ozon_chat_id, marketplace
                FROM reshipment_requests
                WHERE cdek_uuid IS NOT NULL AND cdek_uuid != ''
                  AND cdek_number IS NOT NULL AND cdek_number != ''
                  AND status IN ('packed', 'shipped', 'in_transit', 'ready_for_pickup')
            """)).mappings().fetchall()

            for row in rows:
                await asyncio.sleep(1.5)  # СДЭК банит как бот при частых запросах подряд
                try:
                    info = await asyncio.to_thread(get_order_by_uuid, row["cdek_uuid"])
                    actual_cost = info.get("actual_cost")
                    eta = parse_eta_date(info.get("planned_delivery_date"))
                    if actual_cost or eta:
                        db.execute(text("""
                            UPDATE reshipment_requests
                            SET cdek_cost_actual = COALESCE(:cost, cdek_cost_actual),
                                eta_date = COALESCE(:eta, eta_date)
                            WHERE id = :id
                        """), {"cost": actual_cost, "eta": eta, "id": row["id"]})
                        db.commit()
                    cdek_code = info.get("status_code", "")
                    mapping = CDEK_MAP.get(cdek_code)
                    if not mapping:
                        continue
                    new_status, from_statuses = mapping
                    if new_status == row["status"]:
                        continue
                    # Только вперёд по цепочке
                    curr_idx = STATUS_ORDER.index(row["status"]) if row["status"] in STATUS_ORDER else -1
                    new_idx = STATUS_ORDER.index(new_status) if new_status in STATUS_ORDER else -1
                    if new_idx <= curr_idx:
                        continue

                    updated = db.execute(text("""
                        UPDATE reshipment_requests
                        SET status = :ns,
                            shipped_at  = CASE WHEN :ns = 'shipped'    THEN COALESCE(shipped_at, NOW())   ELSE shipped_at  END,
                            confirmed_at = CASE WHEN :ns IN ('delivered','returned') THEN COALESCE(confirmed_at, NOW()) ELSE confirmed_at END,
                            confirmation_token = COALESCE(confirmation_token, :tok)
                        WHERE id = :id AND status = ANY(:from_list)
                        RETURNING id, salebot_client_id, wb_reply_sign, cdek_number,
                                  ym_chat_id, ozon_chat_id, marketplace, wb_chat_id,
                                  client_pvz_address
                    """), {"ns": new_status, "id": row["id"],
                           "tok": _sec.token_hex(32), "from_list": from_statuses}).mappings().first()
                    db.commit()

                    if updated:
                        from . import reshipment_notify
                        merged = dict(row)
                        merged.update(dict(updated))
                        reshipment_notify.notify_delivery_status(
                            db, merged, new_status,
                            delivery_method="cdek",
                            track_number=updated.get("cdek_number") or row.get("cdek_number") or "",
                            pvz_address=(merged.get("client_pvz_address") or "").strip(),
                            eta_date=info.get("planned_delivery_date") or "",
                        )

                    logger.info("cdek_updater: #%s %s → %s (%s)", row["id"], row["status"], new_status, cdek_code)
                except Exception as e:
                    logger.warning("cdek_updater: ошибка #%s: %s", row["id"], e)

            db.close()
        except Exception as e:
            logging.getLogger("cdek_updater").error("cdek_updater loop error: %s", e)


async def _yandex_status_updater(get_db_func):
    """Каждые 10 минут опрашивает Яндекс NDD и обновляет статусы заказов в пути.
    У Яндекса нет вебхука в этой интеграции — только периодический опрос."""
    import logging, secrets as _sec
    logger = logging.getLogger("yandex_updater")

    STATUS_ORDER = ["shipped", "in_transit", "ready_for_pickup", "delivered", "returned"]

    while True:
        await asyncio.sleep(600)
        try:
            from . import yandex_ndd
            from .salebot import parse_eta_date
            db = next(get_db_func())
            rows = db.execute(text("""
                SELECT id, track_number, status, salebot_client_id, wb_reply_sign,
                       yandex_sharing_url, yandex_pvz_address, yandex_pvz_name,
                       client_pvz_address, items_to_send, ym_chat_id, ozon_chat_id, marketplace
                FROM reshipment_requests
                WHERE delivery_method = 'yandex'
                  AND track_number IS NOT NULL AND track_number != ''
                  AND status IN ('packed', 'shipped', 'in_transit', 'ready_for_pickup')
            """)).mappings().fetchall()

            for row in rows:
                await asyncio.sleep(1)
                try:
                    info = await asyncio.to_thread(yandex_ndd.get_order_status, row["track_number"])
                    eta = parse_eta_date(info.get("eta_raw"))
                    if eta:
                        db.execute(text("""
                            UPDATE reshipment_requests SET eta_date = COALESCE(:eta, eta_date)
                            WHERE id = :id
                        """), {"eta": eta, "id": row["id"]})
                        db.commit()
                    raw_status = info.get("status", "")
                    new_status = yandex_ndd.STATUS_MAP.get(raw_status)
                    if not new_status or new_status == row["status"]:
                        continue
                    # Только вперёд по цепочке (packed = до shipped)
                    curr_idx = STATUS_ORDER.index(row["status"]) if row["status"] in STATUS_ORDER else -1
                    new_idx  = STATUS_ORDER.index(new_status) if new_status in STATUS_ORDER else -1
                    if new_idx <= curr_idx:
                        continue

                    sharing_url = (info.get("sharing_url") or row.get("yandex_sharing_url") or "").strip()
                    updated = db.execute(text("""
                        UPDATE reshipment_requests
                        SET status = :ns,
                            shipped_at = CASE
                              WHEN :ns IN ('shipped','in_transit','ready_for_pickup','delivered')
                               AND shipped_at IS NULL THEN NOW()
                              ELSE shipped_at END,
                            returned_at = CASE
                              WHEN :ns = 'returned' THEN COALESCE(returned_at, NOW())
                              ELSE returned_at END,
                            confirmed_at = CASE WHEN :ns IN ('delivered','returned') THEN COALESCE(confirmed_at, NOW()) ELSE confirmed_at END,
                            confirmation_token = COALESCE(confirmation_token, :tok),
                            yandex_sharing_url = COALESCE(NULLIF(:sharing_url, ''), yandex_sharing_url)
                        WHERE id = :id AND status = :curr
                        RETURNING id, salebot_client_id, wb_reply_sign, ym_chat_id,
                                  ozon_chat_id, marketplace, wb_chat_id
                    """), {"ns": new_status, "id": row["id"], "curr": row["status"],
                           "tok": _sec.token_hex(32), "sharing_url": sharing_url}).mappings().first()
                    db.commit()

                    if updated:
                        from . import reshipment_notify
                        merged = dict(row)
                        merged.update(dict(updated))
                        reshipment_notify.notify_delivery_status(
                            db, merged, new_status,
                            delivery_method="yandex",
                            track_number=row["track_number"],
                            sharing_url=sharing_url,
                            pvz_address=(row.get("yandex_pvz_address") or row.get("client_pvz_address") or "").strip(),
                            pvz_name=(row.get("yandex_pvz_name") or "").strip(),
                            items_to_send=row.get("items_to_send") or "",
                            eta_date=info.get("eta_raw") or "",
                        )

                    logger.info("yandex_updater: #%s %s → %s (%s)", row["id"], row["status"], new_status, raw_status)
                except Exception as e:
                    logger.warning("yandex_updater: ошибка #%s: %s", row["id"], e)

            try:
                from . import shop_orders as shop_orders_mod
                moved = shop_orders_mod.sync_open_yandex(db)
                if moved:
                    logger.info("yandex_updater: shop orders moved %s", moved)
            except Exception as e:
                logger.warning("yandex_updater: shop orders: %s", e)

            db.close()
        except Exception as e:
            logging.getLogger("yandex_updater").error("yandex_updater loop error: %s", e)


async def _ozon_status_updater(get_db_func):
    """Опрашивает Ozon Доставку: подтверждает отправление и двигает статус после сдачи."""
    import logging
    logger = logging.getLogger("ozon_updater")
    delay = 15
    last_finance_ts = 0.0

    while True:
        await asyncio.sleep(delay)
        delay = 600
        try:
            from . import ozon_reshipment_sync
            db = next(get_db_func())
            try:
                from . import shop_orders as shop_orders_mod
                try:
                    shop_moved = await asyncio.to_thread(shop_orders_mod.sync_open_ozon, db)
                    if shop_moved:
                        logger.info("ozon_updater: shop orders moved %s", shop_moved)
                except Exception as e:
                    logger.warning("ozon_updater: shop orders: %s", e)
                stats = await asyncio.to_thread(ozon_reshipment_sync.sync_open_ozon_reshipments, db)
                if stats.get("moved") or stats.get("approved") or stats.get("healed"):
                    logger.info("ozon_updater: %s", stats)
                now = time.time()
                if now - last_finance_ts >= 6 * 3600:
                    from . import ozon_finance
                    try:
                        fin = await asyncio.to_thread(ozon_finance.sync_reshipment_ozon_costs, db)
                        last_finance_ts = now
                        if fin.get("updated"):
                            logger.info("ozon_updater finance: %s", fin)
                    except Exception as e:
                        logger.warning("ozon_updater finance: %s", e)
            finally:
                db.close()
        except Exception as e:
            logging.getLogger("ozon_updater").error("ozon_updater loop error: %s", e)


# --- ТЕСТОВЫЕ МАРШРУТЫ ---

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Бэкенд успешно запущен"}

@app.get("/api/v1/health/live")
def health_live():
    """Лёгкая проверка: API принимает запросы. Без базы — чтобы отличить
    «процесс завис» от «Postgres не отвечает»."""
    stats = loop_watch.snapshot()
    return {
        "ok": True,
        "loop_lag_ms": stats.get("loop_lag_ms"),
        "slow_requests": stats.get("slow_requests"),
    }


@app.get("/api/v1/health/db")
def check_db(db: Session = Depends(get_db)):
    """Проверка подключения к базе данных"""
    t0 = time.perf_counter()
    try:
        result = db.execute(text("SELECT 1")).scalar()
        ms = round((time.perf_counter() - t0) * 1000, 1)
        return {"db_status": "ok", "value": result, "elapsed_ms": ms}
    except Exception as e:
        ms = round((time.perf_counter() - t0) * 1000, 1)
        # Код 503, а не 200: внешний монитор должен видеть сбой по HTTP-статусу,
        # не разбирая тело ответа.
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"db_status": "error", "details": str(e), "elapsed_ms": ms},
        )

