import asyncio
import logging
import os
from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text
from contextlib import asynccontextmanager
from .database import get_db, engine
from .routers import claims, ai, system, production, ppm, ratings, auth, finances, voc, dashboard, ym, reshipment, roles, returns, events, cx_control, faq, cx_responses, kb, reshipment_skus, chat_playbook, chat_triage, chat_bot as chat_bot_router, nonreturnable_cats, question_playbook
from . import chat_bot
from . import wb_chat, wb_mini_sync, cx_alert, wb_chat_sync, ozon_chat_sync, ozon_photo_sync, ym_chat_sync
from .upload_guard import ChatAttachmentContentLengthMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                token VARCHAR(128) PRIMARY KEY,
                username VARCHAR(255) NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used BOOLEAN DEFAULT FALSE
            )
        """))
        # WB чат: таблица ожидающих заявок
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS wb_chat_pending (
                id          SERIAL PRIMARY KEY,
                chat_id     VARCHAR(100) UNIQUE NOT NULL,
                reply_sign  TEXT,
                client_name VARCHAR(255),
                client_id   VARCHAR(100),
                nm_id       BIGINT,
                rid         VARCHAR(100),
                status      VARCHAR(50) DEFAULT 'waiting_form',
                request_id  INTEGER,
                created_at  TIMESTAMP DEFAULT NOW(),
                updated_at  TIMESTAMP DEFAULT NOW()
            )
        """))
        # WB чат: новые колонки в заявках доотправки
        conn.execute(text("""
            ALTER TABLE reshipment_requests
                ADD COLUMN IF NOT EXISTS wb_chat_id     VARCHAR(100),
                ADD COLUMN IF NOT EXISTS wb_reply_sign  TEXT,
                ADD COLUMN IF NOT EXISTS wb_nm_id       BIGINT,
                ADD COLUMN IF NOT EXISTS wb_client_id   VARCHAR(100)
        """))
        # Яндекс Маркет: привязка чата к заявке доотправки (аналог wb_chat_id)
        conn.execute(text("""
            ALTER TABLE reshipment_requests
                ADD COLUMN IF NOT EXISTS ym_chat_id TEXT
        """))
        conn.execute(text("""
            ALTER TABLE reshipment_chat_tokens
                ADD COLUMN IF NOT EXISTS mp VARCHAR(10)
        """))
        # Сравнение СДЭК/Яндекс при подаче заявки: ближайший ПВЗ + цена по каждой ТК.
        # client_pvz_code/address/city + cdek_cost — уже существующие поля СДЭК (как раньше).
        # Ниже — параллельные поля под кандидата Яндекса. Перевозчик не выбирается
        # автоматически — КС сам сравнивает оба варианта на этапе одобрения.
        conn.execute(text("""
            ALTER TABLE reshipment_requests
                ADD COLUMN IF NOT EXISTS yandex_pvz_code    VARCHAR(50),
                ADD COLUMN IF NOT EXISTS yandex_pvz_address TEXT,
                ADD COLUMN IF NOT EXISTS yandex_pvz_name    VARCHAR(255),
                ADD COLUMN IF NOT EXISTS yandex_cost        NUMERIC(10,2),
                ADD COLUMN IF NOT EXISTS yandex_sharing_url TEXT
        """))
        # Координаты адреса клиента и подобранных ПВЗ — для расстояния и карты в КС
        conn.execute(text("""
            ALTER TABLE reshipment_requests
                ADD COLUMN IF NOT EXISTS client_lat     DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS client_lon     DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS cdek_pvz_lat   DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS cdek_pvz_lng   DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS yandex_pvz_lat DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS yandex_pvz_lng DOUBLE PRECISION
        """))
        conn.execute(text("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS tg_chat_id VARCHAR(100)
        """))
        # WB чаты с покупателями: состояние для мониторинга очереди ответов
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS wb_chats (
                chat_id       TEXT PRIMARY KEY,
                client_name   TEXT,
                nm_id         BIGINT,
                good_name     TEXT,
                last_message  TEXT,
                last_sender   TEXT,
                last_ts       BIGINT,
                needs_answer  BOOLEAN DEFAULT FALSE,
                is_closing    BOOLEAN DEFAULT FALSE,
                waiting_since TIMESTAMP,
                reply_sign    TEXT,
                created_at    TIMESTAMP DEFAULT now(),
                updated_at    TIMESTAMP DEFAULT now()
            )
        """))
        conn.execute(text("""
            ALTER TABLE wb_chats ADD COLUMN IF NOT EXISTS is_closing BOOLEAN DEFAULT FALSE
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_wb_chats_needs ON wb_chats (needs_answer) WHERE needs_answer = true
        """))
        # Полная лента переписки WB (копится из потока событий)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS wb_chat_messages (
                id          BIGSERIAL PRIMARY KEY,
                chat_id     TEXT        NOT NULL,
                sender      VARCHAR(20),
                text        TEXT,
                attachments JSONB,
                add_ts      BIGINT,
                created_at  TIMESTAMP   DEFAULT now(),
                UNIQUE (chat_id, add_ts, sender)
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_wb_chat_messages_chat "
            "ON wb_chat_messages (chat_id, add_ts)"
        ))
        # SLA-настройки: сид для чатов (24 часа по умолчанию)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_sla_settings (
                queue_id   VARCHAR(50) PRIMARY KEY,
                sla_min    INTEGER NOT NULL DEFAULT 1440,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            INSERT INTO cx_sla_settings (queue_id, sla_min) VALUES ('wb_chat', 1440)
            ON CONFLICT (queue_id) DO NOTHING
        """))
        # Получать ли системные алерты (утренний дайджест + падения) в Telegram
        conn.execute(text("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS tg_alerts BOOLEAN DEFAULT false
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS user_activity (
                user_id       INTEGER PRIMARY KEY,
                username      VARCHAR(255) NOT NULL,
                last_seen     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                last_path     VARCHAR(500),
                session_start TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        # Ozon отзывы покупателей
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ozon_reviews (
                id          SERIAL PRIMARY KEY,
                review_id   TEXT UNIQUE NOT NULL,
                sku         BIGINT,
                supplier_article TEXT,
                product_name TEXT,
                rating      INTEGER,
                review_text TEXT,
                pros        TEXT,
                cons        TEXT,
                answered_at TIMESTAMP,
                answer_text TEXT,
                created_at  TIMESTAMP,
                synced_at   TIMESTAMP DEFAULT NOW()
            )
        """))
        # Ozon чаты с покупателями
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ozon_chats (
                id              SERIAL PRIMARY KEY,
                chat_id         TEXT UNIQUE NOT NULL,
                chat_status     TEXT,
                last_message    TEXT,
                last_sender     TEXT,
                needs_answer    BOOLEAN DEFAULT FALSE,
                waiting_since   TIMESTAMP,
                context_sku     BIGINT,
                context_article TEXT,
                order_number    TEXT,
                updated_at      TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("ALTER TABLE ozon_chats ADD COLUMN IF NOT EXISTS chat_type TEXT"))
        conn.execute(text("ALTER TABLE ozon_chats ADD COLUMN IF NOT EXISTS unread_count INTEGER DEFAULT 0"))
        conn.execute(text("ALTER TABLE ozon_chats ADD COLUMN IF NOT EXISTS last_message_id TEXT"))
        conn.execute(text("ALTER TABLE ozon_chats ADD COLUMN IF NOT EXISTS created_at TIMESTAMP"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ozon_chat_messages (
                id           SERIAL PRIMARY KEY,
                message_id   TEXT UNIQUE NOT NULL,
                chat_id      TEXT NOT NULL,
                sender_type  TEXT,
                sender_id    TEXT,
                text         TEXT,
                is_image     BOOLEAN DEFAULT FALSE,
                is_read      BOOLEAN DEFAULT FALSE,
                attachments  TEXT,
                created_at   TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_ozon_chat_messages_chat
            ON ozon_chat_messages (chat_id, created_at)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_ozon_chats_needs
            ON ozon_chats (needs_answer) WHERE needs_answer = true
        """))
        # Яндекс Маркет чаты
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ym_chats (
                id                   SERIAL PRIMARY KEY,
                chat_id              TEXT UNIQUE NOT NULL,
                status               TEXT,
                context_type         TEXT,
                customer_name        TEXT,
                customer_public_id   TEXT,
                order_id             TEXT,
                return_id            TEXT,
                last_message         TEXT,
                last_sender          TEXT,
                needs_answer         BOOLEAN DEFAULT FALSE,
                waiting_since        TIMESTAMP,
                created_at           TIMESTAMP,
                updated_at           TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ym_chat_messages (
                id           SERIAL PRIMARY KEY,
                message_id   TEXT UNIQUE NOT NULL,
                chat_id      TEXT NOT NULL,
                sender_type  TEXT,
                text         TEXT,
                created_at   TIMESTAMP
            )
        """))
        conn.execute(text(
            "ALTER TABLE ym_chat_messages ADD COLUMN IF NOT EXISTS attachments TEXT"
        ))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_ym_chats_needs
            ON ym_chats (needs_answer) WHERE needs_answer = true
        """))
        conn.execute(text(
            "ALTER TABLE ym_chats ADD COLUMN IF NOT EXISTS cabinet_chat_id TEXT"
        ))
        conn.execute(text(
            "ALTER TABLE ym_chats ADD COLUMN IF NOT EXISTS context_article TEXT"
        ))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_ym_chat_messages_chat
            ON ym_chat_messages (chat_id, created_at)
        """))
        # ЯМ отзывы: поля для хранения ответа менеджера
        conn.execute(text("""
            ALTER TABLE ym_feedbacks
                ADD COLUMN IF NOT EXISTS answer_text TEXT,
                ADD COLUMN IF NOT EXISTS answered_at TIMESTAMP
        """))
        # Лог ответов КС: кто, когда, что ответил через систему
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_response_log (
                id          SERIAL PRIMARY KEY,
                item_type   VARCHAR(20) NOT NULL,
                item_id     TEXT NOT NULL,
                mp          VARCHAR(10) NOT NULL DEFAULT 'wb',
                answered_by VARCHAR(255),
                answer_text TEXT,
                sent_at     TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_cx_response_log_item
            ON cx_response_log (item_type, item_id)
        """))
        # Шаблоны ответов КС: ручные + авто-ответы
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_response_templates (
                id                    SERIAL PRIMARY KEY,
                title                 VARCHAR(200) NOT NULL,
                body                  TEXT NOT NULL,
                mp                    VARCHAR(10)  DEFAULT 'all',
                item_type             VARCHAR(20)  DEFAULT 'all',
                tone                  VARCHAR(20)  DEFAULT 'all',
                auto_reply            BOOLEAN      DEFAULT FALSE,
                min_rating            INTEGER,
                max_rating            INTEGER,
                require_no_text       BOOLEAN      DEFAULT FALSE,
                stop_words            TEXT         DEFAULT '',
                ignore_questions      BOOLEAN      DEFAULT FALSE,
                requires_confirmation BOOLEAN      DEFAULT FALSE,
                created_by            VARCHAR(255),
                created_at            TIMESTAMP    DEFAULT NOW(),
                updated_at            TIMESTAMP    DEFAULT NOW()
            )
        """))
        conn.execute(text("ALTER TABLE cx_response_templates ADD COLUMN IF NOT EXISTS stop_words TEXT DEFAULT ''"))
        conn.execute(text("ALTER TABLE cx_response_templates ADD COLUMN IF NOT EXISTS ignore_questions BOOLEAN DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE cx_response_templates ADD COLUMN IF NOT EXISTS requires_confirmation BOOLEAN DEFAULT FALSE"))
        # Очередь черновиков на модерацию (авто-ответы с requires_confirmation=true)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_pending_moderation (
                id             SERIAL PRIMARY KEY,
                item_type      VARCHAR(20)  NOT NULL,
                item_id        TEXT         NOT NULL,
                mp             VARCHAR(10)  NOT NULL,
                draft_text     TEXT         NOT NULL,
                template_id    INTEGER,
                template_title VARCHAR(200),
                created_at     TIMESTAMP    DEFAULT NOW(),
                UNIQUE (item_type, item_id, mp)
            )
        """))
        # Уточнения по вопросам покупателей: КС ставит вопрос в общую очередь
        # качества, QA готовит текст, а отправляет его в маркетплейс только КС.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_question_clarifications (
                id            SERIAL PRIMARY KEY,
                mp            VARCHAR(10)  NOT NULL CHECK (mp IN ('wb', 'ym')),
                question_id   TEXT         NOT NULL,
                requested_by  VARCHAR(255) NOT NULL,
                note          TEXT         NOT NULL DEFAULT '',
                status        VARCHAR(20)  NOT NULL DEFAULT 'pending'
                              CONSTRAINT cx_question_clarifications_status_check
                              CHECK (status IN ('pending', 'answered', 'cancelled')),
                proposed_text TEXT,
                answered_by   VARCHAR(255),
                requested_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
                answered_at   TIMESTAMP
            )
        """))
        # Миграция ранней версии: отменённое уточнение освобождает pending-key
        # и остаётся в истории, если покупателю уже ответили вне этой очереди.
        # Каталог проверяется до изменения: на каждом старте приложения
        # существующее корректное ограничение не пересоздаётся.
        conn.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = 'cx_question_clarifications'::regclass
                      AND conname = 'cx_question_clarifications_status_check'
                      AND pg_get_constraintdef(oid) NOT LIKE '%cancelled%'
                ) THEN
                    ALTER TABLE cx_question_clarifications
                    DROP CONSTRAINT cx_question_clarifications_status_check;
                END IF;

                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = 'cx_question_clarifications'::regclass
                      AND conname = 'cx_question_clarifications_status_check'
                ) THEN
                    ALTER TABLE cx_question_clarifications
                    ADD CONSTRAINT cx_question_clarifications_status_check
                    CHECK (status IN ('pending', 'answered', 'cancelled'));
                END IF;
            END $$
        """))
        # Нельзя отправить один и тот же живой вопрос в QA дважды, но после
        # готового или отменённого уточнения КС может запросить повторное.
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_cx_question_clarifications_pending
            ON cx_question_clarifications (mp, question_id)
            WHERE status = 'pending'
        """))
        # Вручную закрытые обращения (без отправки ответа)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_dismissed_items (
                id           SERIAL PRIMARY KEY,
                item_type    VARCHAR(20)  NOT NULL,
                item_id      TEXT         NOT NULL,
                mp           VARCHAR(10)  NOT NULL,
                dismissed_by VARCHAR(255),
                note         TEXT,
                dismissed_at TIMESTAMP    DEFAULT NOW(),
                UNIQUE (item_type, item_id, mp)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS wb_product_photos (
                nm_id      BIGINT PRIMARY KEY,
                photo_url  TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT now()
            )
        """))
        conn.commit()

    # Таблицы базы знаний ответов («Конструктор Vidovito»)
    kb.init_kb_tables(engine)

    # Список SKU, по которым разрешена доотправка деталей
    reshipment_skus.init_sku_table(engine)
    nonreturnable_cats.init_nonreturnable_cats(engine)

    # Плейбук автоответов в чатах: темы, варианты текстов, режимы
    chat_playbook.init_playbook_tables(engine)
    # Плейбук вопросов — копия чатового, дальше может расходиться
    question_playbook.init_question_playbook_tables(engine)
    # Движок бота: состояние диалога + журнал (shadow)
    chat_bot.init_bot_tables(engine)
    # Менеджер КС: доступ к возвратам/чатам/отзывам/вопросам
    roles.ensure_cs_manager_permissions(engine)
    # Антидубль уведомлений клиенту по статусам доотправки
    from . import reshipment_notify
    reshipment_notify.init_notify_table(engine)

    # cx_unified_queue: общий VIEW очереди (отзывы+вопросы+чаты+претензии
    # всех площадок) вместо Python-строки _QUEUE_UNION — Фаза 3 унификации.
    cx_responses.init_unified_queue_view(engine)

    # При --workers 2 каждый процесс поднимает lifespan. Фоновые задачи
    # (опрос СДЭК/Яндекс, синки) должны крутиться только в одном процессе,
    # иначе уведомления клиенту уходят дважды.
    leader_conn = engine.connect()
    is_leader = bool(leader_conn.execute(text("SELECT pg_try_advisory_lock(87442201)")).scalar())
    app.state.leader_conn = leader_conn
    app.state.is_leader = is_leader

    bg_tasks = []
    if is_leader:
        # Инициализируем курсор WB событий, захватываем незалинкованные токены из истории.
        # ВАЖНО: только на лидере — раньше это шло до проверки is_leader и при
        # --workers 2+ каждый процесс независимо дёргал живой API WB при старте.
        wb_chat.init_event_cursor(get_db)

        from .routers.cx_responses import start_photo_sync_worker
        logging.getLogger("uvicorn.error").info("cx-backend: leader — starting background workers")
        bg_tasks = [
            asyncio.create_task(wb_chat.start_worker(get_db)),
            asyncio.create_task(wb_mini_sync.start_mini_sync_worker(get_db)),
            asyncio.create_task(_cdek_number_filler(get_db)),
            asyncio.create_task(_cdek_status_updater(get_db)),
            asyncio.create_task(_yandex_status_updater(get_db)),
            asyncio.create_task(wb_chat_sync.start_chat_sync_worker(get_db)),
            asyncio.create_task(ozon_chat_sync.start_ozon_chat_sync_worker(get_db)),
            asyncio.create_task(ym_chat_sync.start_ym_chat_sync_worker(get_db)),
            asyncio.create_task(start_photo_sync_worker(get_db)),
            asyncio.create_task(ozon_photo_sync.start_ozon_photo_sync_worker(get_db)),
            asyncio.create_task(chat_bot.start_chat_bot_worker(get_db)),
        ]
    else:
        logging.getLogger("uvicorn.error").info("cx-backend: follower — background workers skipped")

    yield

    for t in bg_tasks:
        t.cancel()
    for t in bg_tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass
    try:
        leader_conn.close()
    except Exception:
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

# Настройка CORS для стабильного общения фронтенда и бэкенда
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=True,
)

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
                                cdek_cost = COALESCE(:cost, cdek_cost),
                                status = 'shipped',
                                shipped_at = COALESCE(shipped_at, NOW()),
                                confirmation_token = COALESCE(confirmation_token, :token)
                            WHERE id = :id AND status IN ('approved', 'packed')
                            RETURNING id, salebot_client_id
                        """), {"num": num, "cost": cost, "id": row["id"],
                               "token": _sec.token_hex(32)}).mappings().first()
                        db.commit()
                        if updated:
                            from . import reshipment_notify
                            if reshipment_notify.claim_notify(db, row["id"], "shipped"):
                                sid = updated.get("salebot_client_id")
                                if sid:
                                    try:
                                        from .salebot import notify_shipped, is_enabled
                                        if is_enabled():
                                            notify_shipped(sid, row["id"],
                                                           track_number=num, delivery_method="cdek")
                                    except Exception as ex:
                                        logger.warning("cdek_filler: salebot error #%s: %s", row["id"], ex)
                            else:
                                logger.info("cdek_filler: skip notify #%s shipped (already sent)", row["id"])
                        logger.info("cdek_filler: заявка #%s → shipped (%s)", row["id"], status_code)
                    else:
                        db.execute(text("""
                            UPDATE reshipment_requests
                            SET cdek_number = :num,
                                cdek_cost = COALESCE(:cost, cdek_cost)
                            WHERE id = :id
                        """), {"num": num, "cost": cost, "id": row["id"]})
                        db.commit()
                        logger.info("cdek_filler: заявка #%s → cdek_number=%s", row["id"], num)
                except Exception as e:
                    logger.warning("cdek_filler: ошибка для #%s: %s", row["id"], e)

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
            from .salebot import (notify_shipped, notify_in_transit, notify_ready_for_pickup,
                                  notify_delivered, is_enabled)
            db = next(get_db_func())
            rows = db.execute(text("""
                SELECT id, cdek_uuid, cdek_number, status, salebot_client_id, wb_reply_sign
                FROM reshipment_requests
                WHERE cdek_uuid IS NOT NULL AND cdek_uuid != ''
                  AND cdek_number IS NOT NULL AND cdek_number != ''
                  AND status IN ('packed', 'shipped', 'in_transit', 'ready_for_pickup')
            """)).mappings().fetchall()

            for row in rows:
                await asyncio.sleep(1.5)  # СДЭК банит как бот при частых запросах подряд
                try:
                    info = await asyncio.to_thread(get_order_by_uuid, row["cdek_uuid"])
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
                        RETURNING id, salebot_client_id, wb_reply_sign, cdek_number
                    """), {"ns": new_status, "id": row["id"],
                           "tok": _sec.token_hex(32), "from_list": from_statuses}).mappings().first()
                    db.commit()

                    if updated:
                        from . import reshipment_notify
                        num = updated.get("cdek_number") or row.get("cdek_number") or ""
                        sid = updated.get("salebot_client_id")
                        wb_sign = updated.get("wb_reply_sign") or row.get("wb_reply_sign")

                        if not reshipment_notify.claim_notify(db, row["id"], new_status):
                            logger.info("cdek_updater: skip notify #%s %s (already sent)", row["id"], new_status)
                        else:
                            salebot_sent = False
                            if sid and is_enabled():
                                try:
                                    if new_status == "shipped":
                                        salebot_sent = notify_shipped(sid, row["id"], track_number=num, delivery_method="cdek")
                                    elif new_status == "in_transit":
                                        salebot_sent = notify_in_transit(sid, row["id"], track_number=num)
                                    elif new_status == "ready_for_pickup":
                                        salebot_sent = notify_ready_for_pickup(sid, row["id"], track_number=num)
                                    elif new_status == "delivered":
                                        salebot_sent = notify_delivered(sid, row["id"])
                                except Exception as ex:
                                    logger.warning("cdek_updater: salebot error #%s: %s", row["id"], ex)

                            if not salebot_sent and wb_sign:
                                try:
                                    from . import wb_chat as _wbc
                                    if new_status == "shipped":
                                        _wbc.send_message(wb_sign, f"📦 Ваша посылка отправлена!\nТрек-номер СДЭК: {num}")
                                    elif new_status == "in_transit":
                                        _wbc.send_message(wb_sign, f"🚚 Ваша посылка в пути!\nТрек-номер СДЭК: {num}\nСкоро прибудет в ваш город.")
                                    elif new_status == "ready_for_pickup":
                                        _wbc.send_message(wb_sign, f"📬 Ваша посылка готова к выдаче!\nТрек-номер СДЭК: {num}\nЗаберите её в пункте выдачи СДЭК.")
                                    elif new_status == "delivered":
                                        _wbc.send_message(wb_sign, "✅ Посылка получена! Надеемся, деталь подошла.")
                                except Exception as ex:
                                    logger.warning("cdek_updater: wb_chat error #%s: %s", row["id"], ex)

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
            from .salebot import (notify_in_transit, notify_ready_for_pickup,
                                  notify_delivered, is_enabled,
                                  format_delivery_status_message)
            db = next(get_db_func())
            rows = db.execute(text("""
                SELECT id, track_number, status, salebot_client_id, wb_reply_sign,
                       yandex_sharing_url, yandex_pvz_address, yandex_pvz_name,
                       client_pvz_address, items_to_send
                FROM reshipment_requests
                WHERE delivery_method = 'yandex'
                  AND track_number IS NOT NULL AND track_number != ''
                  AND status IN ('packed', 'shipped', 'in_transit', 'ready_for_pickup')
            """)).mappings().fetchall()

            for row in rows:
                await asyncio.sleep(1)
                try:
                    info = await asyncio.to_thread(yandex_ndd.get_order_status, row["track_number"])
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
                        RETURNING id, salebot_client_id, wb_reply_sign
                    """), {"ns": new_status, "id": row["id"], "curr": row["status"],
                           "tok": _sec.token_hex(32), "sharing_url": sharing_url}).mappings().first()
                    db.commit()

                    if updated:
                        from . import reshipment_notify
                        num = row["track_number"]
                        sid = updated.get("salebot_client_id")
                        wb_sign = updated.get("wb_reply_sign") or row.get("wb_reply_sign")
                        pvz_addr = (row.get("yandex_pvz_address") or row.get("client_pvz_address") or "").strip()
                        pvz_name = (row.get("yandex_pvz_name") or "").strip()
                        items = row.get("items_to_send") or ""

                        if not reshipment_notify.claim_notify(db, row["id"], new_status):
                            logger.info("yandex_updater: skip notify #%s %s (already sent)", row["id"], new_status)
                        else:
                            salebot_sent = False
                            if sid and is_enabled():
                                try:
                                    if new_status == "in_transit":
                                        salebot_sent = notify_in_transit(
                                            sid, row["id"], track_number=num, delivery_method="yandex",
                                            sharing_url=sharing_url, items_to_send=items,
                                            pvz_address=pvz_addr, pvz_name=pvz_name,
                                        )
                                    elif new_status == "ready_for_pickup":
                                        salebot_sent = notify_ready_for_pickup(
                                            sid, row["id"], track_number=num, delivery_method="yandex",
                                            sharing_url=sharing_url, pvz_address=pvz_addr,
                                            pvz_name=pvz_name, items_to_send=items,
                                        )
                                    elif new_status == "delivered":
                                        salebot_sent = notify_delivered(sid, row["id"])
                                except Exception as ex:
                                    logger.warning("yandex_updater: salebot error #%s: %s", row["id"], ex)

                            if not salebot_sent and wb_sign:
                                try:
                                    from . import wb_chat as _wbc
                                    msg = format_delivery_status_message(
                                        new_status,
                                        track_number=num,
                                        delivery_method="yandex",
                                        sharing_url=sharing_url,
                                        pvz_address=pvz_addr,
                                        pvz_name=pvz_name,
                                        items_to_send=items,
                                    )
                                    if msg:
                                        _wbc.send_message(wb_sign, msg)
                                except Exception as ex:
                                    logger.warning("yandex_updater: wb_chat error #%s: %s", row["id"], ex)

                    logger.info("yandex_updater: #%s %s → %s (%s)", row["id"], row["status"], new_status, raw_status)
                except Exception as e:
                    logger.warning("yandex_updater: ошибка #%s: %s", row["id"], e)

            db.close()
        except Exception as e:
            logging.getLogger("yandex_updater").error("yandex_updater loop error: %s", e)


# --- ТЕСТОВЫЕ МАРШРУТЫ ---

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Бэкенд успешно запущен"}

@app.get("/api/v1/health/db")
def check_db(db: Session = Depends(get_db)):
    """Проверка подключения к базе данных"""
    try:
        # Простой запрос для проверки соединения
        result = db.execute(text("SELECT 1")).scalar()
        return {"db_status": "ok", "value": result}
    except Exception as e:
        return {"db_status": "error", "details": str(e)}
