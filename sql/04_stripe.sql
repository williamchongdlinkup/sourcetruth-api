-- ============================================================
-- Migration 04 — Stripe billing + /answer quota
-- Run once against the production database:
--   psql $DATABASE_URL -f sql/04_stripe.sql
-- ============================================================

-- Stripe customer ID to link an API key to a Stripe billing record
ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT;

CREATE INDEX IF NOT EXISTS api_keys_stripe_customer_idx
    ON api_keys (stripe_customer_id);

-- Separate daily cap for /answer calls (LLM generation).
-- NULL = no /answer access (free tier).
-- Paid tiers: starter=100, professional=300.
ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS answer_daily_limit INT DEFAULT NULL;

-- Atomic daily counter for /answer calls (mirrors daily_quota for /search).
CREATE TABLE IF NOT EXISTS answer_daily_quota (
    key_id  INT  NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    date    DATE NOT NULL DEFAULT CURRENT_DATE,
    count   INT  NOT NULL DEFAULT 0,
    PRIMARY KEY (key_id, date)
);
