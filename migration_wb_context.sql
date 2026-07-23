-- Migration: WB context enrichment for reshipment requests
-- Run once on the server via: psql cx_dashboard < migration_wb_context.sql

-- 1. Add nm_id and photos to wb_feedbacks
ALTER TABLE wb_feedbacks
  ADD COLUMN IF NOT EXISTS nm_id TEXT,
  ADD COLUMN IF NOT EXISTS photos TEXT;

-- 2. Feedback snapshots: history of changes per feedback
CREATE TABLE IF NOT EXISTS wb_feedback_snapshots (
  id          SERIAL PRIMARY KEY,
  feedback_id TEXT        NOT NULL,
  valuation   INT,
  text        TEXT,
  photos      TEXT,
  captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wb_feedback_snapshots_fid
  ON wb_feedback_snapshots (feedback_id, captured_at DESC);
