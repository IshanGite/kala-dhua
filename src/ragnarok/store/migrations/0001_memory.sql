-- 0001: memory types 2-11 (working memory lives with tasks in 0002).
-- Every derived row links back to its source through `provenance` so deletion can cascade (NFR4.2).

CREATE TABLE compartments (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  created_at INTEGER NOT NULL
);

CREATE TABLE items (
  id TEXT PRIMARY KEY,
  compartment_id TEXT NOT NULL REFERENCES compartments(id),
  source TEXT NOT NULL,
  native_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'message',
  title TEXT,
  trust TEXT NOT NULL CHECK (trust IN ('user_direct','user_file','agent','received','web')),
  status TEXT NOT NULL CHECK (status IN ('raw','processed','archived','missing')),
  visibility TEXT NOT NULL DEFAULT 'private',
  thread_id TEXT,
  deep_link TEXT,
  occurred_at INTEGER,
  ingested_at INTEGER NOT NULL,
  flags_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (source, native_id)
);
CREATE INDEX items_status ON items(status, ingested_at);
CREATE INDEX items_compartment ON items(compartment_id, occurred_at);

CREATE TABLE chunks (
  id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL REFERENCES items(id),
  ordinal INTEGER NOT NULL,
  text TEXT NOT NULL,
  heading_path TEXT,
  page INTEGER,
  char_start INTEGER,
  char_end INTEGER,
  UNIQUE (item_id, ordinal)
);
CREATE VIRTUAL TABLE chunks_fts USING fts5(text, content='chunks', content_rowid='rowid');
CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;
CREATE TRIGGER chunks_au AFTER UPDATE OF text ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
  INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TABLE embeddings (
  owner_type TEXT NOT NULL CHECK (owner_type IN ('chunk','event','fact','route')),
  owner_id TEXT NOT NULL,
  compartment_id TEXT NOT NULL REFERENCES compartments(id),
  model_id TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vec BLOB NOT NULL,
  PRIMARY KEY (owner_type, owner_id, model_id)
);
CREATE INDEX embeddings_compartment ON embeddings(compartment_id, owner_type);

-- Episodic memory: append-only, hash-chained (SEC-08).
CREATE TABLE events (
  id TEXT PRIMARY KEY,
  seq INTEGER NOT NULL UNIQUE,
  compartment_id TEXT NOT NULL REFERENCES compartments(id),
  ts INTEGER NOT NULL,
  kind TEXT NOT NULL,
  actor TEXT NOT NULL,
  task_id TEXT,
  content TEXT NOT NULL,
  trust TEXT NOT NULL CHECK (trust IN ('user_direct','user_file','agent','received','web')),
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);
CREATE INDEX events_ts ON events(compartment_id, ts);
CREATE VIRTUAL TABLE events_fts USING fts5(content, content='events', content_rowid='rowid');
CREATE TRIGGER events_ai AFTER INSERT ON events BEGIN
  INSERT INTO events_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER events_ad AFTER DELETE ON events BEGIN
  INSERT INTO events_fts(events_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
END;
-- Events are append-only: no edits. Deletion is allowed only through the delete cascade routine.
CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN
  SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TABLE event_checkpoints (
  day TEXT PRIMARY KEY,
  last_seq INTEGER NOT NULL,
  last_hash TEXT NOT NULL,
  signature TEXT NOT NULL
);

-- Semantic memory and preferences (kind = 'preference').
CREATE TABLE facts (
  id TEXT PRIMARY KEY,
  compartment_id TEXT NOT NULL REFERENCES compartments(id),
  kind TEXT NOT NULL CHECK (kind IN ('preference','attribute','relationship','project_state','knowledge')),
  subject TEXT NOT NULL,
  predicate TEXT NOT NULL,
  value TEXT NOT NULL,
  confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  importance REAL NOT NULL DEFAULT 0.5 CHECK (importance BETWEEN 0 AND 1),
  trust TEXT NOT NULL CHECK (trust IN ('user_direct','user_file','agent','received','web')),
  valid_from INTEGER,
  valid_to INTEGER,
  learned_at INTEGER NOT NULL,
  state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','quarantined','needs_confirmation','archived')),
  pinned INTEGER NOT NULL DEFAULT 0,
  last_accessed INTEGER,
  access_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX facts_lookup ON facts(compartment_id, subject, predicate, state);
CREATE VIRTUAL TABLE facts_fts USING fts5(subject, predicate, value, content='facts', content_rowid='rowid');
CREATE TRIGGER facts_ai AFTER INSERT ON facts BEGIN
  INSERT INTO facts_fts(rowid, subject, predicate, value) VALUES (new.rowid, new.subject, new.predicate, new.value);
END;
CREATE TRIGGER facts_ad AFTER DELETE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, subject, predicate, value) VALUES ('delete', old.rowid, old.subject, old.predicate, old.value);
END;
CREATE TRIGGER facts_au AFTER UPDATE OF subject, predicate, value ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, subject, predicate, value) VALUES ('delete', old.rowid, old.subject, old.predicate, old.value);
  INSERT INTO facts_fts(rowid, subject, predicate, value) VALUES (new.rowid, new.subject, new.predicate, new.value);
END;

-- The provenance graph: every fact, chunk, vector, summary links to what produced it.
CREATE TABLE provenance (
  child_type TEXT NOT NULL,
  child_id TEXT NOT NULL,
  parent_type TEXT NOT NULL,
  parent_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  quote TEXT,
  PRIMARY KEY (child_type, child_id, parent_type, parent_id)
);
CREATE INDEX provenance_parent ON provenance(parent_type, parent_id);

-- Relational memory: the people graph with reversible merges.
CREATE TABLE people (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  merged_into TEXT REFERENCES people(id),
  created_at INTEGER NOT NULL,
  last_seen INTEGER
);
CREATE TABLE identities (
  person_id TEXT NOT NULL REFERENCES people(id),
  platform TEXT NOT NULL,
  handle TEXT NOT NULL,
  UNIQUE (platform, handle)
);
CREATE TABLE merge_log (
  id TEXT PRIMARY KEY,
  ts INTEGER NOT NULL,
  from_person TEXT NOT NULL,
  into_person TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  undone INTEGER NOT NULL DEFAULT 0
);

-- Prospective memory.
CREATE TABLE commitments (
  id TEXT PRIMARY KEY,
  compartment_id TEXT NOT NULL REFERENCES compartments(id),
  what TEXT NOT NULL,
  due_at INTEGER,
  state TEXT NOT NULL CHECK (state IN ('open','done','dropped')),
  trust TEXT NOT NULL DEFAULT 'user_direct',
  source_item_id TEXT REFERENCES items(id)
);
CREATE INDEX commitments_due ON commitments(state, due_at);

-- Procedural memory.
CREATE TABLE procedures (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  successes INTEGER NOT NULL DEFAULT 0,
  failures INTEGER NOT NULL DEFAULT 0,
  last_used INTEGER
);

-- Meta-memory: numbers only.
CREATE TABLE router_stats (
  decision TEXT NOT NULL,
  backend TEXT NOT NULL,
  bucket INTEGER NOT NULL,
  n INTEGER NOT NULL,
  correct INTEGER NOT NULL,
  PRIMARY KEY (decision, backend, bucket)
);
CREATE TABLE skill_stats (
  skill_id TEXT NOT NULL,
  day TEXT NOT NULL,
  calls INTEGER NOT NULL DEFAULT 0,
  failures INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (skill_id, day)
);

-- Autobiographical timeline.
CREATE TABLE saga (
  id TEXT PRIMARY KEY,
  compartment_id TEXT NOT NULL REFERENCES compartments(id),
  period TEXT NOT NULL,
  summary TEXT NOT NULL
);

-- Encrypted file blobs: random key per file, stored wrapped. Deleting the row destroys the key.
CREATE TABLE blob_keys (
  blob_id TEXT PRIMARY KEY,
  item_id TEXT REFERENCES items(id),
  wrapped_key BLOB NOT NULL,
  path TEXT NOT NULL,
  size INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);

-- Deletion proof: counts only, never content.
CREATE TABLE tombstones (
  id TEXT PRIMARY KEY,
  deleted_at INTEGER NOT NULL,
  scope TEXT NOT NULL,
  counts_json TEXT NOT NULL
);

-- Change log for destructive operations: who, what kind, how many, why. Never content.
CREATE TABLE change_log (
  id TEXT PRIMARY KEY,
  ts INTEGER NOT NULL,
  actor TEXT NOT NULL,
  op TEXT NOT NULL,
  counts_json TEXT NOT NULL,
  reason_code TEXT NOT NULL
);
