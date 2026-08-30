-- One play per row, so a count is a fact rather than a number someone edits.
-- The id comes from the client, so retrying a submission that failed halfway
-- cannot count the same play twice.
CREATE TABLE IF NOT EXISTS plays (
  id   TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS plays_name_at ON plays (name, at);

-- Submissions are queued, never published. Content lives in the repository's
-- recipes/ folder; this is only the doorway for someone without a GitHub
-- account, and a human moves things through it.
CREATE TABLE IF NOT EXISTS submissions (
  id    TEXT PRIMARY KEY,
  name  TEXT NOT NULL,
  body  TEXT NOT NULL,
  at    INTEGER NOT NULL,
  state TEXT NOT NULL DEFAULT 'queued'
);
CREATE INDEX IF NOT EXISTS submissions_state ON submissions (state, at);
