-- Миграция: таблицы Яндекс Маркета.
-- Запуск на VPS: psql -U db_user -d cx_dashboard -f db_ym_migration.sql

CREATE TABLE IF NOT EXISTS ym_orders (
    id               SERIAL PRIMARY KEY,
    order_id         BIGINT UNIQUE NOT NULL,
    status           VARCHAR(50),
    supplier_article VARCHAR(100),
    sku_name         TEXT,
    created_at       TIMESTAMP,
    price            NUMERIC(12,2),
    buyer_total      NUMERIC(12,2),
    currency         VARCHAR(10)  DEFAULT 'RUR',
    region_name      VARCHAR(200),
    synced_at        TIMESTAMP    DEFAULT NOW()
);

-- ym_returns: фактическая схема v2 (фото, комментарии, ИИ-теги, причины Маркета).
-- cat_1..cat_13 — ИИ-тегирование (NULL до разметки); reason_type/subreason_type — причины ЯМ.
CREATE TABLE IF NOT EXISTS ym_returns (
    id               SERIAL PRIMARY KEY,
    return_id        BIGINT UNIQUE NOT NULL,
    order_id         BIGINT,
    supplier_article VARCHAR(100),
    return_type      VARCHAR(30),
    reason           TEXT,
    defect_category  VARCHAR(100),
    reason_type      VARCHAR(50),     -- reasonType ЯМ: BAD_QUALITY / DOES_NOT_FIT / WRONG_ITEM …
    subreason_type   VARCHAR(50),     -- subreasonType ЯМ: WRONG_COLOR / INCOMPLETENESS / NOT_WORKING …
    status           VARCHAR(50),
    status_ru        VARCHAR(50),
    return_comment   TEXT,
    photos           TEXT,            -- пробел-разделённые URL /static/ym_returns/…
    cat_1  INTEGER, cat_2  INTEGER, cat_3  INTEGER, cat_4  INTEGER, cat_5  INTEGER,
    cat_6  INTEGER, cat_7  INTEGER, cat_8  INTEGER, cat_9  INTEGER, cat_10 INTEGER,
    cat_11 INTEGER, cat_12 INTEGER, cat_13 INTEGER,
    created_at       TIMESTAMP,
    updated_at       TIMESTAMP,
    synced_at        TIMESTAMP    DEFAULT NOW()
);

-- Идемпотентные ALTER для уже существующей на проде таблицы ym_returns.
ALTER TABLE ym_returns ADD COLUMN IF NOT EXISTS reason_type    VARCHAR(50);
ALTER TABLE ym_returns ADD COLUMN IF NOT EXISTS subreason_type VARCHAR(50);

CREATE TABLE IF NOT EXISTS ym_feedbacks (
    id               SERIAL PRIMARY KEY,
    feedback_id      VARCHAR(150) UNIQUE NOT NULL,
    supplier_article VARCHAR(100),
    created_date     TIMESTAMP,
    valuation        INTEGER,
    pro_text         TEXT,
    contra_text      TEXT,
    comment          TEXT,
    ai_tags          JSONB,
    synced_at        TIMESTAMP    DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ym_questions (
    id               SERIAL PRIMARY KEY,
    question_id      VARCHAR(150) UNIQUE NOT NULL,
    supplier_article VARCHAR(100),
    created_date     TIMESTAMP,
    text             TEXT,
    answer_text      TEXT,
    state            VARCHAR(50),
    synced_at        TIMESTAMP    DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ym_assortment (
    id               SERIAL PRIMARY KEY,
    offer_id         VARCHAR(150) UNIQUE NOT NULL,
    supplier_article VARCHAR(100),
    name             TEXT,
    category         VARCHAR(300),
    market_sku       BIGINT,
    synced_at        TIMESTAMP    DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ym_prices (
    id               SERIAL PRIMARY KEY,
    supplier_article VARCHAR(100) UNIQUE NOT NULL,
    price            NUMERIC(12,2),
    discount_base    NUMERIC(12,2),
    currency         VARCHAR(10)  DEFAULT 'RUR',
    updated_at       TIMESTAMP    DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ym_finance_reports (
    id           SERIAL PRIMARY KEY,
    report_type  VARCHAR(50)   NOT NULL,
    report_id    VARCHAR(200),
    status       VARCHAR(20)   DEFAULT 'pending',
    period_from  DATE,
    period_to    DATE,
    download_url TEXT,
    revenue      NUMERIC(15,2),
    commissions  NUMERIC(15,2),
    raw_data     JSONB,
    created_at   TIMESTAMP     DEFAULT NOW(),
    downloaded_at TIMESTAMP
);

-- ym_supplies: заявки на поставку ЯМ (тянутся воркером ежедневно).
-- Ключ: request_id (внутренний ID ЯМ).
-- marketplace_request_id = номер с акта приёмки (напр. "30725002").
-- parent_request_id = для VDC-дочерних заявок, ссылка на родительскую.
CREATE TABLE IF NOT EXISTS ym_supplies (
    id                      SERIAL PRIMARY KEY,
    request_id              BIGINT UNIQUE NOT NULL,
    marketplace_request_id  VARCHAR(50),
    warehouse_request_id    VARCHAR(50),
    parent_request_id       BIGINT,
    warehouse_id            INTEGER,
    warehouse_name          VARCHAR(200),
    requested_date          TIMESTAMP WITH TIME ZONE,
    status                  VARCHAR(50),
    subtype                 VARCHAR(100),
    synced_at               TIMESTAMP DEFAULT NOW()
);

-- ym_supply_items: товарный состав каждой заявки (offerId + счётчики).
CREATE TABLE IF NOT EXISTS ym_supply_items (
    id           SERIAL PRIMARY KEY,
    request_id   BIGINT NOT NULL,
    offer_id     VARCHAR(150) NOT NULL,
    plan_count   INTEGER,
    fact_count   INTEGER,
    defect_count INTEGER,
    synced_at    TIMESTAMP DEFAULT NOW(),
    UNIQUE (request_id, offer_id)
);

-- wb_invoices: добавляем marketplace, чтобы хранить и WB, и ЯМ инвойсы в одной таблице.
ALTER TABLE wb_invoices ADD COLUMN IF NOT EXISTS marketplace VARCHAR(20) DEFAULT 'wb';

CREATE INDEX IF NOT EXISTS idx_ym_orders_article    ON ym_orders(supplier_article);
CREATE INDEX IF NOT EXISTS idx_ym_orders_created    ON ym_orders(created_at);
CREATE INDEX IF NOT EXISTS idx_ym_returns_article   ON ym_returns(supplier_article);
CREATE INDEX IF NOT EXISTS idx_ym_feedbacks_article ON ym_feedbacks(supplier_article);
CREATE INDEX IF NOT EXISTS idx_ym_feedbacks_date    ON ym_feedbacks(created_date);
CREATE INDEX IF NOT EXISTS idx_ym_questions_article ON ym_questions(supplier_article);
CREATE INDEX IF NOT EXISTS idx_ym_finrep_status     ON ym_finance_reports(status);
CREATE INDEX IF NOT EXISTS idx_ym_supplies_wh       ON ym_supplies(warehouse_id);
CREATE INDEX IF NOT EXISTS idx_ym_supplies_date     ON ym_supplies(requested_date);
CREATE INDEX IF NOT EXISTS idx_ym_supply_items_sku  ON ym_supply_items(offer_id);
CREATE INDEX IF NOT EXISTS idx_ym_supply_items_req  ON ym_supply_items(request_id);
