-- Токены для привязки WB чата к заявке на отправку детали
-- psql -U db_user -d cx_dashboard -f migration_chat_tokens.sql

CREATE TABLE IF NOT EXISTS reshipment_chat_tokens (
    token       VARCHAR(32) PRIMARY KEY,
    chat_id     VARCHAR(200),
    created_by  VARCHAR(100),
    created_at  TIMESTAMP DEFAULT NOW(),
    expires_at  TIMESTAMP,             -- NOW() + 7 days при создании
    expired     BOOLEAN DEFAULT FALSE, -- помечается если истёк срок, но клиент ещё не перешёл
    used_at     TIMESTAMP,             -- когда клиент заполнил форму
    request_id  INTEGER REFERENCES reshipment_requests(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_tokens_chat_id    ON reshipment_chat_tokens(chat_id);
CREATE INDEX IF NOT EXISTS idx_chat_tokens_request_id ON reshipment_chat_tokens(request_id);

-- Колонка в заявках для хранения токена (если chatID ещё не определён в момент отправки формы)
ALTER TABLE reshipment_requests ADD COLUMN IF NOT EXISTS chat_token VARCHAR(32);
CREATE INDEX IF NOT EXISTS idx_reshipment_chat_token ON reshipment_requests(chat_token);
