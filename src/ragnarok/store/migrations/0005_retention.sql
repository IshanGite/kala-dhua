ALTER TABLE facts ADD COLUMN archived_at INTEGER;
CREATE TABLE prune_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE UNIQUE INDEX saga_period ON saga(compartment_id, period);
