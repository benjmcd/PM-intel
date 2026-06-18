-- Partition-safe DB guard for canonical normalized trade identity.
-- normalized_trades is partitioned by received_at, so a unique constraint on
-- business identity alone cannot protect duplicates across partitions.

SET search_path TO pmfi, public;

CREATE TABLE IF NOT EXISTS normalized_trade_dedupe_keys (
    dedupe_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venue_code text NOT NULL REFERENCES venues(venue_code),
    venue_trade_id text,
    market_id uuid NOT NULL REFERENCES markets(market_id) ON DELETE CASCADE,
    exchange_ts timestamptz,
    exchange_ts_key timestamptz NOT NULL,
    price numeric(12,8) NOT NULL,
    contracts numeric(28,8) NOT NULL,
    outcome_key text NOT NULL,
    trade_id uuid,
    first_seen_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_normalized_trade_dedupe_venue_id
    ON normalized_trade_dedupe_keys (venue_code, venue_trade_id)
    WHERE venue_trade_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_normalized_trade_dedupe_fingerprint
    ON normalized_trade_dedupe_keys
        (venue_code, market_id, exchange_ts_key, price, contracts, outcome_key)
    WHERE venue_trade_id IS NULL;

INSERT INTO normalized_trade_dedupe_keys
    (venue_code, venue_trade_id, market_id, exchange_ts, exchange_ts_key,
     price, contracts, outcome_key, trade_id, first_seen_at)
SELECT DISTINCT ON (venue_code, venue_trade_id)
    venue_code,
    venue_trade_id,
    market_id,
    exchange_ts,
    COALESCE(exchange_ts, '-infinity'::timestamptz),
    price,
    contracts,
    outcome_key,
    trade_id,
    COALESCE(processed_at, received_at, now())
FROM normalized_trades
WHERE venue_trade_id IS NOT NULL
ORDER BY venue_code, venue_trade_id, received_at, trade_id
ON CONFLICT DO NOTHING;

INSERT INTO normalized_trade_dedupe_keys
    (venue_code, venue_trade_id, market_id, exchange_ts, exchange_ts_key,
     price, contracts, outcome_key, trade_id, first_seen_at)
SELECT DISTINCT ON (
    venue_code,
    market_id,
    COALESCE(exchange_ts, '-infinity'::timestamptz),
    price,
    contracts,
    outcome_key
)
    venue_code,
    NULL,
    market_id,
    exchange_ts,
    COALESCE(exchange_ts, '-infinity'::timestamptz),
    price,
    contracts,
    outcome_key,
    trade_id,
    COALESCE(processed_at, received_at, now())
FROM normalized_trades
WHERE venue_trade_id IS NULL
ORDER BY
    venue_code,
    market_id,
    COALESCE(exchange_ts, '-infinity'::timestamptz),
    price,
    contracts,
    outcome_key,
    received_at,
    trade_id
ON CONFLICT DO NOTHING;
