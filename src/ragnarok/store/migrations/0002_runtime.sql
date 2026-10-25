-- 0002: working memory, durable execution, ingestion queue, security and routing tables.

-- Working memory + task graph. Every transition is its own committed transaction (NFR3.2).
CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  compartment_id TEXT NOT NULL REFERENCES compartments(id),
  goal TEXT NOT NULL,
  trigger_kind TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('created','planning','running','awaiting_approval','succeeded','partial','failed','cancelled')),
  plan_json TEXT,
  working_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  budgets_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX tasks_state ON tasks(state, updated_at);

CREATE TABLE task_nodes (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  ord INTEGER NOT NULL,
  skill_id TEXT NOT NULL,
  params_json TEXT NOT NULL,
  depends_on_json TEXT NOT NULL DEFAULT '[]',
  criteria_json TEXT NOT NULL DEFAULT '[]',
  state TEXT NOT NULL CHECK (state IN ('pending','ready','running','awaiting_approval','succeeded','failed','skipped','blocked')),
  attempts INTEGER NOT NULL DEFAULT 0,
  result_json TEXT,
  error_code TEXT,
  min_trust TEXT NOT NULL DEFAULT 'user_direct',
  tier INTEGER NOT NULL DEFAULT 0 CHECK (tier BETWEEN 0 AND 3),
  updated_at INTEGER NOT NULL
);
CREATE INDEX task_nodes_task ON task_nodes(task_id, ord);

-- Intent -> effect -> confirm with idempotency keys (never repeat a side effect after a crash).
CREATE TABLE effects (
  key TEXT PRIMARY KEY,
  task_node_id TEXT NOT NULL REFERENCES task_nodes(id),
  intent_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('intent','performed','confirmed','failed')),
  created_at INTEGER NOT NULL,
  confirmed_at INTEGER
);

CREATE TABLE approvals (
  id TEXT PRIMARY KEY,
  task_node_id TEXT NOT NULL REFERENCES task_nodes(id),
  skill_id TEXT NOT NULL,
  params_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','approved','denied','expired','used')),
  warnings_json TEXT NOT NULL DEFAULT '[]',
  card_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  decided_at INTEGER,
  token_hash TEXT
);

CREATE TABLE decisions (
  id TEXT PRIMARY KEY,
  task_id TEXT,
  task_node_id TEXT,
  ts INTEGER NOT NULL,
  kind TEXT NOT NULL,
  tier TEXT,
  backend TEXT,
  inputs_json TEXT NOT NULL DEFAULT '{}',
  candidates_json TEXT NOT NULL DEFAULT '[]',
  choice_json TEXT NOT NULL DEFAULT '{}',
  rationale TEXT,
  outcome_json TEXT NOT NULL DEFAULT '{}',
  latency_ms REAL,
  model_id TEXT
);
CREATE INDEX decisions_task ON decisions(task_id, ts);

-- Persistent ingestion queue: stages are idempotent; dead-letter after repeated failure.
CREATE TABLE ingest_queue (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  item_ref TEXT NOT NULL,
  stage TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('queued','running','done','dead','held')),
  priority INTEGER NOT NULL DEFAULT 5,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error_code TEXT,
  payload_blob_id TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (source, item_ref)
);
CREATE INDEX ingest_queue_pick ON ingest_queue(state, priority, created_at);

CREATE TABLE trigger_state (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  config_json TEXT NOT NULL,
  cursor_json TEXT NOT NULL DEFAULT '{}',
  last_fired INTEGER,
  enabled INTEGER NOT NULL DEFAULT 1,
  failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE grants (
  skill_id TEXT NOT NULL,
  permission TEXT NOT NULL,
  scope TEXT NOT NULL,
  granted_at INTEGER NOT NULL,
  PRIMARY KEY (skill_id, permission, scope)
);

-- Zero Trust tables.
CREATE TABLE principals (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('user','router','arbiter','planner','extractor','skill','connector')),
  name TEXT NOT NULL,
  permissions_json TEXT NOT NULL,
  can_execute INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE skill_registry (
  skill_id TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  tree_hash TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0,
  scan_report TEXT,
  registered_at INTEGER NOT NULL
);
CREATE TABLE budgets_usage (
  principal TEXT NOT NULL,
  window_start INTEGER NOT NULL,
  counter TEXT NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY (principal, window_start, counter)
);
CREATE TABLE canaries (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  path_or_token_hash TEXT NOT NULL,
  tripped_at INTEGER
);
CREATE TABLE snapshots (
  id TEXT PRIMARY KEY,
  created_at INTEGER NOT NULL,
  scope TEXT NOT NULL,
  path TEXT NOT NULL,
  task_node_id TEXT
);
CREATE TABLE security_incidents (
  id TEXT PRIMARY KEY,
  ts INTEGER NOT NULL,
  kind TEXT NOT NULL,
  detail_codes TEXT NOT NULL,
  task_id TEXT
);
CREATE TABLE connector_state (
  name TEXT PRIMARY KEY,
  compartment_id TEXT NOT NULL REFERENCES compartments(id),
  autonomy TEXT NOT NULL CHECK (autonomy IN ('observe','draft','act_limited')),
  read_enabled INTEGER NOT NULL,
  write_enabled INTEGER NOT NULL,
  retention_days INTEGER,
  settings_json TEXT
);
CREATE TABLE sender_pairing (
  platform TEXT NOT NULL,
  handle TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('held','approved','blocked')),
  PRIMARY KEY (platform, handle)
);

-- Route library (the machine-readable half of procedural memory) and router learning data.
CREATE TABLE routes (
  id TEXT PRIMARY KEY,
  compartment_id TEXT NOT NULL REFERENCES compartments(id),
  signature_text TEXT NOT NULL,
  intent_family TEXT NOT NULL,
  recipe_json TEXT NOT NULL,
  risk_tier_hint INTEGER NOT NULL,
  trust_floor TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('candidate','shadow','active','demoted','retired')),
  shadow_agree INTEGER NOT NULL DEFAULT 0,
  successes INTEGER NOT NULL DEFAULT 0,
  failures INTEGER NOT NULL DEFAULT 0,
  demotions INTEGER NOT NULL DEFAULT 0,
  last_used INTEGER,
  created_at INTEGER NOT NULL,
  created_from_task TEXT,
  tree_hash TEXT NOT NULL,
  pinned INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE router_examples (
  id TEXT PRIMARY KEY,
  query_text TEXT NOT NULL,
  family TEXT,
  skill TEXT,
  plan_needed INTEGER,
  risk TEXT,
  labels_json TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('template','persona','paraphrase','l3_verified','user_corrected')),
  trust TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE router_calibration (
  decision TEXT NOT NULL,
  backend TEXT NOT NULL,
  temperature REAL NOT NULL,
  threshold REAL NOT NULL,
  fitted_at INTEGER NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY (decision, backend, fitted_at)
);
CREATE TABLE route_events (
  id TEXT PRIMARY KEY,
  route_id TEXT,
  ts INTEGER NOT NULL,
  kind TEXT NOT NULL,
  tier TEXT,
  latency_ms REAL,
  ok INTEGER,
  trace_id TEXT
);
