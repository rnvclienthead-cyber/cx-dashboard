-- Модуль доотправки деталей клиентам
-- Запустить на сервере: psql -U db_user -d cx_dashboard -f migration_reshipment.sql

CREATE TABLE IF NOT EXISTS reshipment_requests (
    id                  SERIAL PRIMARY KEY,
    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW(),

    -- Данные клиента
    customer_name       VARCHAR(200) NOT NULL,
    customer_email      VARCHAR(200),
    customer_phone      VARCHAR(50),

    -- Данные заказа
    order_number        VARCHAR(100),
    marketplace         VARCHAR(20) DEFAULT 'wb',   -- wb / ym / other

    -- Проблема и что нужно
    problem_description TEXT NOT NULL,
    items_to_send       TEXT NOT NULL,

    -- Адрес и фото
    shipping_address    TEXT NOT NULL,
    photo_urls          TEXT,   -- JSON-массив ссылок ["url1", "url2"]

    -- Согласие
    privacy_consent     BOOLEAN NOT NULL DEFAULT FALSE,

    -- Статус (new → matched → approved / rejected → shipped → delivered)
    status              VARCHAR(30) NOT NULL DEFAULT 'new',

    -- Сопоставление с возвратом
    matched_srid        VARCHAR(100),
    match_notes         TEXT,

    -- Обработка
    moderator_comment   TEXT,
    processed_by        VARCHAR(100),
    processed_at        TIMESTAMP,

    -- Отправка
    track_number        VARCHAR(100),
    shipping_cost       NUMERIC(10, 2),
    shipped_at          TIMESTAMP,

    -- Подтверждение получения
    confirmation_token  VARCHAR(64) UNIQUE,
    confirmed_at        TIMESTAMP,

    -- Пометка об отзыве
    review_requested    BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_reshipment_status    ON reshipment_requests(status);
CREATE INDEX IF NOT EXISTS idx_reshipment_order     ON reshipment_requests(order_number);
CREATE INDEX IF NOT EXISTS idx_reshipment_srid      ON reshipment_requests(matched_srid);
CREATE INDEX IF NOT EXISTS idx_reshipment_token     ON reshipment_requests(confirmation_token);

-- Автообновление updated_at
CREATE OR REPLACE FUNCTION update_reshipment_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_reshipment_updated_at ON reshipment_requests;
CREATE TRIGGER trg_reshipment_updated_at
    BEFORE UPDATE ON reshipment_requests
    FOR EACH ROW EXECUTE FUNCTION update_reshipment_updated_at();
