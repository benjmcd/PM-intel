-- Development seed data for local fixtures.

SET search_path TO pmfi, public;

INSERT INTO venues (venue_code, display_name, base_url)
VALUES
  ('polymarket', 'Polymarket', 'https://polymarket.com'),
  ('kalshi', 'Kalshi', 'https://kalshi.com')
ON CONFLICT (venue_code) DO NOTHING;
