"""Создание и миграция схемы БД — вынесено из старта сервиса.

Раньше весь DDL выполнялся в lifespan main.py при старте КАЖДОГО воркера
uvicorn. При `--workers 2` два процесса одновременно делали ALTER (ACCESS
EXCLUSIVE) и дрались за блокировку: старт тормозил, а одна ошибка в DDL валила
запуск целиком. Теперь всё собрано здесь и выполняется под advisory-локом —
схему прогоняет один процесс, остальные ждут и получают уже готовую базу.

Можно прогнать отдельно (например, перед рестартом на сервере):

    PYTHONPATH=/root/cx-dashboard ./venv/bin/python -m backend.schema
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Свой ключ advisory-лока. Не пересекается с leader/worker-локами (87442201..223).
SCHEMA_LOCK_ID = 87442250


def apply_schema(engine) -> None:
    """Идемпотентно приводит схему БД к актуальной. Безопасно вызывать при
    каждом старте: всё построено на IF NOT EXISTS / OR REPLACE."""
    from .database import add_column_if_missing, index_exists
    from .routers import (
        kb, reshipment_skus, nonreturnable_cats, chat_playbook,
        question_playbook, roles, cx_responses,
    )
    from . import chat_bot, ym_product_card, tshopping, shop_orders, warehouse_intake
    with engine.connect() as conn:
        # Сериализуем схему между воркерами: один прогоняет, остальные ждут тут.
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": SCHEMA_LOCK_ID})
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
        # Яндекс Маркет / Ozon: привязка чата к заявке доотправки (аналог wb_chat_id)
        conn.execute(text("""
            ALTER TABLE reshipment_requests
                ADD COLUMN IF NOT EXISTS ym_chat_id TEXT
        """))
        conn.execute(text("""
            ALTER TABLE reshipment_requests
                ADD COLUMN IF NOT EXISTS ozon_chat_id TEXT
        """))
        # Внутренний артикул продавца (ЯМ context_article / WB supplier_article)
        conn.execute(text("""
            ALTER TABLE reshipment_requests
                ADD COLUMN IF NOT EXISTS supplier_article TEXT
        """))
        # Подтянуть артикул из уже привязанных чатов ЯМ / Ozon (если ещё пусто)
        conn.execute(text("""
            UPDATE reshipment_requests r
            SET supplier_article = NULLIF(TRIM(y.context_article), '')
            FROM ym_chats y
            WHERE r.ym_chat_id = y.chat_id
              AND (r.supplier_article IS NULL OR TRIM(r.supplier_article) = '')
              AND NULLIF(TRIM(y.context_article), '') IS NOT NULL
        """))
        conn.execute(text("""
            ALTER TABLE reshipment_chat_tokens
                ADD COLUMN IF NOT EXISTS mp VARCHAR(10)
        """))
        conn.execute(text("""
            ALTER TABLE reshipment_chat_tokens
                ADD COLUMN IF NOT EXISTS supplier_article TEXT
        """))
        # Ozon: после ALTER токенов — иначе эксклюзивный лок + UPDATE держат deadlock
        conn.execute(text("""
            UPDATE reshipment_requests r
            SET ozon_chat_id = t.chat_id,
                marketplace = 'ozon'
            FROM reshipment_chat_tokens t
            WHERE t.request_id = r.id
              AND lower(COALESCE(t.mp, '')) = 'ozon'
              AND NULLIF(TRIM(t.chat_id), '') IS NOT NULL
              AND (r.ozon_chat_id IS NULL OR TRIM(r.ozon_chat_id) = '')
        """))
        conn.execute(text("""
            UPDATE reshipment_requests r
            SET supplier_article = NULLIF(TRIM(o.context_article), ''),
                marketplace = 'ozon',
                order_number = COALESCE(NULLIF(TRIM(r.order_number), ''), NULLIF(TRIM(o.order_number), ''))
            FROM ozon_chats o
            WHERE r.ozon_chat_id = o.chat_id
              AND (r.supplier_article IS NULL OR TRIM(r.supplier_article) = '')
              AND NULLIF(TRIM(o.context_article), '') IS NOT NULL
        """))
        # Если у ссылки формы заранее указан артикул — переносим его в заявку при заполнении.
        conn.execute(text("""
            CREATE OR REPLACE FUNCTION reshipment_token_article_on_insert()
            RETURNS TRIGGER AS $$
            BEGIN
              IF NEW.chat_token IS NOT NULL
                 AND (NEW.supplier_article IS NULL OR btrim(NEW.supplier_article) = '') THEN
                SELECT NULLIF(btrim(t.supplier_article), '')
                  INTO NEW.supplier_article
                FROM reshipment_chat_tokens t
                WHERE t.token = NEW.chat_token;
              END IF;
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """))
        conn.execute(text("""
            DROP TRIGGER IF EXISTS trg_reshipment_token_article ON reshipment_requests
        """))
        conn.execute(text("""
            CREATE TRIGGER trg_reshipment_token_article
              BEFORE INSERT ON reshipment_requests
              FOR EACH ROW
              EXECUTE FUNCTION reshipment_token_article_on_insert()
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
            CREATE TABLE IF NOT EXISTS yandex_dispatch_acts (
                id BIGSERIAL PRIMARY KEY,
                act_number VARCHAR(64) UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                created_by VARCHAR(255),
                requests_count INTEGER NOT NULL DEFAULT 0,
                total_shipping_cost NUMERIC(12,2) NOT NULL DEFAULT 0,
                payload JSONB NOT NULL DEFAULT '[]'::jsonb
            )
        """))
        conn.execute(text("""
            ALTER TABLE reshipment_requests
                ADD COLUMN IF NOT EXISTS yandex_dispatch_act_id BIGINT
        """))
        conn.execute(text("""
            ALTER TABLE reshipment_requests
                ADD COLUMN IF NOT EXISTS cdek_cost_actual NUMERIC(10,2)
        """))
        conn.execute(text("""
            ALTER TABLE reshipment_requests
                ADD COLUMN IF NOT EXISTS ozon_pvz_code VARCHAR(50),
                ADD COLUMN IF NOT EXISTS ozon_pvz_address TEXT,
                ADD COLUMN IF NOT EXISTS ozon_pvz_name VARCHAR(255),
                ADD COLUMN IF NOT EXISTS ozon_cost NUMERIC(10,2),
                ADD COLUMN IF NOT EXISTS ozon_pvz_lat DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS ozon_pvz_lng DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS ozon_from_pvz_id VARCHAR(50),
                ADD COLUMN IF NOT EXISTS ozon_from_pvz_address TEXT,
                ADD COLUMN IF NOT EXISTS ozon_client_ok BOOLEAN,
                ADD COLUMN IF NOT EXISTS ozon_reason TEXT,
                ADD COLUMN IF NOT EXISTS ozon_shipment_method_id BIGINT,
                ADD COLUMN IF NOT EXISTS ozon_order_number VARCHAR(64)
        """))
        conn.execute(text("""
            ALTER TABLE reshipment_requests
                ADD COLUMN IF NOT EXISTS eta_date DATE
        """))
        conn.execute(text("""
            ALTER TABLE reshipment_requests
                ADD COLUMN IF NOT EXISTS ozon_posting_number VARCHAR(64)
        """))
        conn.execute(text("""
            ALTER TABLE reshipment_requests
                ADD COLUMN IF NOT EXISTS ozon_cost_actual NUMERIC(10,2)
        """))
        conn.execute(text("""
            ALTER TABLE reshipment_requests
                ALTER COLUMN ozon_shipment_method_id TYPE BIGINT
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
        add_column_if_missing(
            conn, "wb_chats", "is_closing",
            "ALTER TABLE wb_chats ADD COLUMN is_closing BOOLEAN DEFAULT FALSE",
        )
        add_column_if_missing(
            conn, "wb_chats", "pre_purchase",
            "ALTER TABLE wb_chats ADD COLUMN pre_purchase BOOLEAN",
        )
        if not index_exists(conn, "idx_wb_chats_needs"):
            conn.execute(text("""
                CREATE INDEX idx_wb_chats_needs ON wb_chats (needs_answer) WHERE needs_answer = true
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
                photos      TEXT,
                answered_at TIMESTAMP,
                answer_text TEXT,
                created_at  TIMESTAMP,
                synced_at   TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            ALTER TABLE ozon_reviews ADD COLUMN IF NOT EXISTS photos TEXT
        """))
        # Очередь ответов, которые публикует кабинетный бот Ozon.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ozon_review_outbox (
                id SERIAL PRIMARY KEY,
                review_id TEXT NOT NULL,
                answer_text TEXT NOT NULL,
                requested_by TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                sent_at TIMESTAMPTZ
            )
        """))
        # Ozon вопросы о товарах. Открытые NEW/VIEWED попадают в общую
        # очередь; ответ публикуется в Q&A карточки товара.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ozon_questions (
                question_id     TEXT PRIMARY KEY,
                sku             BIGINT NOT NULL,
                supplier_article TEXT,
                product_name    TEXT,
                product_url     TEXT,
                question_link   TEXT,
                author_name     TEXT,
                text            TEXT NOT NULL,
                status          TEXT NOT NULL,
                answers_count   INTEGER NOT NULL DEFAULT 0,
                published_at    TIMESTAMP,
                answer_text     TEXT,
                answered_at     TIMESTAMP,
                synced_at       TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_ozon_questions_open
            ON ozon_questions (published_at)
            WHERE status IN ('NEW', 'VIEWED', 'UNPROCESSED')
        """))
        conn.execute(text("""
            ALTER TABLE wb_questions
                ADD COLUMN IF NOT EXISTS nm_id BIGINT
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
        # Горячие индексы для быстрого списка чатов (VIEW cx_chats_queue)
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_wb_chats_last_ts
            ON wb_chats (last_ts DESC NULLS LAST)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_ozon_chats_status_open
            ON ozon_chats (chat_status)
            WHERE chat_status = 'OPENED'
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_ozon_chat_messages_chat_created_desc
            ON ozon_chat_messages (chat_id, created_at DESC)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_ym_chat_messages_chat_created_desc
            ON ym_chat_messages (chat_id, created_at DESC)
        """))
        # ЯМ отзывы: поля для хранения ответа менеджера
        conn.execute(text("""
            ALTER TABLE ym_feedbacks
                ADD COLUMN IF NOT EXISTS answer_text TEXT,
                ADD COLUMN IF NOT EXISTS answered_at TIMESTAMP
        """))
        # Клиент дополнил или изменил отзыв — храним прежнюю версию для очереди КС
        for _rev_tbl in ("wb_feedbacks", "ym_feedbacks", "ozon_reviews"):
            if conn.execute(text("""
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = :t
            """), {"t": _rev_tbl}).scalar():
                add_column_if_missing(
                    conn, _rev_tbl, "prev_text",
                    f"ALTER TABLE {_rev_tbl} ADD COLUMN prev_text TEXT",
                )
                add_column_if_missing(
                    conn, _rev_tbl, "prev_valuation",
                    f"ALTER TABLE {_rev_tbl} ADD COLUMN prev_valuation INTEGER",
                )
                add_column_if_missing(
                    conn, _rev_tbl, "buyer_updated_at",
                    f"ALTER TABLE {_rev_tbl} ADD COLUMN buyer_updated_at TIMESTAMPTZ",
                )
                add_column_if_missing(
                    conn, _rev_tbl, "update_kind",
                    f"ALTER TABLE {_rev_tbl} ADD COLUMN update_kind TEXT",
                )
        if conn.execute(text("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'wb_feedback_snapshots'
        """)).scalar():
            conn.execute(text("""
                UPDATE wb_feedbacks f
                SET
                    prev_text = COALESCE(NULLIF(TRIM(f.prev_text), ''), s.text),
                    prev_valuation = COALESCE(f.prev_valuation, s.valuation),
                    buyer_updated_at = COALESCE(f.buyer_updated_at, s.captured_at),
                    update_kind = COALESCE(NULLIF(f.update_kind, ''), 'edited')
                FROM (
                    SELECT DISTINCT ON (feedback_id)
                           feedback_id, text, valuation, captured_at
                    FROM wb_feedback_snapshots
                    WHERE snapshot_type IN ('before', 'on_approve')
                    ORDER BY feedback_id, captured_at ASC
                ) s
                WHERE s.feedback_id = f.id
                  AND f.state = 'active'
                  AND f.buyer_updated_at IS NULL
                  AND (
                      COALESCE(f.text, '') IS DISTINCT FROM COALESCE(s.text, '')
                      OR f.valuation IS DISTINCT FROM s.valuation
                  )
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
                mp            VARCHAR(10)  NOT NULL
                              CONSTRAINT cx_question_clarifications_mp_check
                              CHECK (mp IN ('wb', 'ym', 'ozon')),
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
        # Ozon добавлен в тот же универсальный поток уточнений, что WB/ЯМ.
        # Ограничение меняется один раз; корректное не трогаем при старте.
        conn.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = 'cx_question_clarifications'::regclass
                      AND conname = 'cx_question_clarifications_mp_check'
                      AND pg_get_constraintdef(oid) NOT LIKE '%ozon%'
                ) THEN
                    ALTER TABLE cx_question_clarifications
                    DROP CONSTRAINT cx_question_clarifications_mp_check;
                END IF;

                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = 'cx_question_clarifications'::regclass
                      AND conname = 'cx_question_clarifications_mp_check'
                ) THEN
                    ALTER TABLE cx_question_clarifications
                    ADD CONSTRAINT cx_question_clarifications_mp_check
                    CHECK (mp IN ('wb', 'ym', 'ozon'));
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
            CREATE INDEX IF NOT EXISTS idx_cx_dismissed_items_lookup
            ON cx_dismissed_items (mp, item_type, item_id)
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS wb_product_photos (
                nm_id      BIGINT PRIMARY KEY,
                photo_url  TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS product_photos (
                supplier_article TEXT PRIMARY KEY,
                photo_url        TEXT NOT NULL,
                source           TEXT DEFAULT 'ozon',
                updated_at       TIMESTAMP DEFAULT now()
            )
        """))
        if conn.execute(text("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'wb_claims'
        """)).scalar():
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_wb_claims_created_dt
                ON wb_claims (created_dt)
            """))
        if conn.execute(text("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'wb_logistics'
        """)).scalar():
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_wb_logistics_sale_article
                ON wb_logistics (supplier_article)
                WHERE doc_type = 'SALE' AND finish_price IS NOT NULL AND finish_price > 0
            """))
        for _staff_tbl in ("wb_claims", "ym_returns", "ozon_returns"):
            if conn.execute(text("""
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = :t
            """), {"t": _staff_tbl}).scalar():
                add_column_if_missing(
                    conn, _staff_tbl, "staff_tags",
                    f"ALTER TABLE {_staff_tbl} ADD COLUMN staff_tags JSONB",
                )
        # Частые поиски без полного прохода таблицы: заказ Ozon по номеру и дате,
        # отзывы WB за период, последние сообщения продавца в чате Ozon.
        if conn.execute(text("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'ozon_orders'
        """)).scalar():
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_ozon_orders_order_number
                ON ozon_orders (order_number)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_ozon_orders_created_at
                ON ozon_orders (created_at)
            """))
        if conn.execute(text("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'wb_feedbacks'
        """)).scalar():
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_wb_feedbacks_created_date
                ON wb_feedbacks (created_date)
            """))
        if conn.execute(text("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'ozon_chat_messages'
        """)).scalar():
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_ozon_chat_messages_seller_recent
                ON ozon_chat_messages (created_at DESC)
                WHERE sender_type IN ('seller', 'courier')
            """))
        if conn.execute(text("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'ym_questions'
        """)).scalar():
            add_column_if_missing(
                conn, "ym_questions", "market_sku",
                "ALTER TABLE ym_questions ADD COLUMN market_sku TEXT",
            )
        conn.commit()

        # Таблицы базы знаний ответов («Конструктор Vidovito»)
        kb.init_kb_tables(engine)
        kb.ensure_shelf_rules_without_wheels(engine)
        kb.ensure_furniture_wording(engine)
        kb.ensure_no_warm_cliches(engine)
        kb.ensure_no_stray_recommend_thanks(engine)

        # Список SKU, по которым разрешена доотправка деталей
        reshipment_skus.init_sku_table(engine)
        nonreturnable_cats.init_nonreturnable_cats(engine)

        # Плейбук автоответов в чатах: темы, варианты текстов, режимы
        chat_playbook.init_playbook_tables(engine)
        # Плейбук вопросов — копия чатового, дальше может расходиться
        question_playbook.init_question_playbook_tables(engine)
        # Движок бота: состояние диалога + журнал (shadow)
        chat_bot.init_bot_tables(engine)
        # Точечный кэш характеристик из карточек Яндекс Маркета для чатов.
        ym_product_card.init_product_card_cache(engine)
        # Чаты поддержки Т-Шопинга (почта + Telegram-бот)
        tshopping.init_tables(engine)
        shop_orders.init_tables(engine)
        warehouse_intake.init_tables(engine)
        from . import mp_api_watch
        mp_api_watch.init_tables(engine)
        # Менеджер КС: доступ к возвратам/чатам/отзывам/вопросам
        roles.ensure_cs_manager_permissions(engine)
        # Антидубль уведомлений клиенту по статусам доотправки
        from . import reshipment_notify
        reshipment_notify.init_notify_table(engine)

        from . import invoice_match, purged_claims
        purged_claims.init_purged_claims_table(engine)
        invoice_match.init_claims_enriched_view(engine)

        # cx_unified_queue: общий VIEW очереди (отзывы+вопросы+чаты+претензии
        # всех площадок) вместо Python-строки _QUEUE_UNION — Фаза 3 унификации.
        cx_responses.init_unified_queue_view(engine)
        cx_responses.init_review_settings(engine)
        cx_responses.ensure_packaging_review_templates(engine)
        cx_responses.ensure_paint_holes_review_templates(engine)
        cx_responses.init_chat_seen_table(engine)
        cx_responses.restore_client_waiting_chats(engine)
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": SCHEMA_LOCK_ID})


if __name__ == "__main__":
    from .database import engine as _engine
    logging.basicConfig(level=logging.INFO)
    apply_schema(_engine)
    print("schema applied")

