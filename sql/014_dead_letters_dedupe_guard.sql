SET search_path TO pmfi, public;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'pmfi'
      AND c.relname = 'idx_dead_letters_raw_stage_class_dedupe'
  ) THEN
    WITH duplicate_rows AS (
      SELECT
        dead_letter_id,
        ROW_NUMBER() OVER (
          PARTITION BY raw_event_id, failure_stage, error_class
          ORDER BY created_at, dead_letter_id
        ) AS rn
      FROM dead_letters
      WHERE raw_event_id IS NOT NULL
    )
    UPDATE dead_letters dl
    SET
      error_class = CONCAT(
        COALESCE(dl.error_class, 'unknown_error'),
        ':dedupe_preserved:',
        LEFT(dl.dead_letter_id::text, 8)
      ),
      error_message = CONCAT(
        dl.error_message,
        ' [duplicate row preserved before idx_dead_letters_raw_stage_class_dedupe]'
      )
    FROM duplicate_rows
    WHERE dl.dead_letter_id = duplicate_rows.dead_letter_id
      AND duplicate_rows.rn > 1;
  END IF;
END;
$$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_dead_letters_raw_stage_class_dedupe
    ON dead_letters (raw_event_id, failure_stage, error_class)
    WHERE raw_event_id IS NOT NULL;
