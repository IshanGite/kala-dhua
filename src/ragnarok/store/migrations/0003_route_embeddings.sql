-- 0003: vectors for learned routes (matched in L0 without a model call).
CREATE TABLE route_embeddings (
  route_id TEXT PRIMARY KEY REFERENCES routes(id),
  model_id TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vec BLOB NOT NULL
);
