-- 0004: sources the user deleted must not silently come back when the watched folder or mailbox is re-read (NFR4.2 step "block re-ingestion").
CREATE TABLE ingest_exclusions (
  source TEXT NOT NULL,
  native_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (source, native_id)
);
