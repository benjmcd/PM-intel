CREATE UNIQUE INDEX IF NOT EXISTS idx_dead_letters_raw_stage_class_dedupe
    ON dead_letters (raw_event_id, failure_stage, error_class)
    WHERE raw_event_id IS NOT NULL;
