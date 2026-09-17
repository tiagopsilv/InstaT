"""Schema do JobStore (roadmap §6.3.11). `PRAGMA user_version = 1`."""

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
  account TEXT PRIMARY KEY,
  auth_state TEXT NOT NULL CHECK(auth_state IN ('ok','needs_attention','restricted')),
  restricted_until REAL, restricted_at REAL, auth_validated_at REAL, released_by TEXT,
  lease_owner TEXT, lease_until REAL, lease_gen INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS endpoint_cooldowns(
  account TEXT NOT NULL, endpoint TEXT NOT NULL, cooldown_until REAL NOT NULL, reason TEXT,
  PRIMARY KEY(account, endpoint));
CREATE TABLE IF NOT EXISTS jobs(
  job_id TEXT PRIMARY KEY, target TEXT NOT NULL, list_type TEXT NOT NULL, endpoint TEXT NOT NULL,
  progress_kind TEXT NOT NULL CHECK(progress_kind IN ('cursor','scan')),
  status TEXT NOT NULL CHECK(status IN ('pending','running','partial','complete','failed')),
  end_reason TEXT, current_run INTEGER, attempts INTEGER NOT NULL DEFAULT 0,
  requeued_at REAL, requeued_by TEXT, cancel_requested_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS runs(
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL REFERENCES jobs(job_id),
  account TEXT NOT NULL, lease_owner TEXT NOT NULL, lease_gen INTEGER NOT NULL,
  cursor_context TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL, stop_reason TEXT,
  progress_state TEXT NOT NULL DEFAULT 'not_started'
    CHECK(progress_state IN ('not_started','in_progress','end_confirmed','end_unknown')),
  end_evidence TEXT,
  next_pos INTEGER NOT NULL DEFAULT 0, trusted_cursor TEXT,
  segment INTEGER NOT NULL DEFAULT 0, segment_started_at REAL NOT NULL,
  replay_target INTEGER NOT NULL DEFAULT 0, replay_done INTEGER NOT NULL DEFAULT 0,
  frontier_streak INTEGER NOT NULL DEFAULT 0,
  rounds_without_new INTEGER NOT NULL DEFAULT 0, stuck_rounds INTEGER NOT NULL DEFAULT 0,
  continuity_gaps INTEGER NOT NULL DEFAULT 0,
  last_screen_hash TEXT, last_screen_members TEXT,
  counter_kind TEXT, counter_lo INTEGER, counter_hi INTEGER, counter_read_at REAL,
  last_commit_at REAL);
CREATE TABLE IF NOT EXISTS pages(
  page_id INTEGER PRIMARY KEY AUTOINCREMENT,
  attempt_id TEXT NOT NULL UNIQUE,
  run_id INTEGER NOT NULL REFERENCES runs(run_id),
  segment INTEGER NOT NULL, pos INTEGER NOT NULL, cursor_in TEXT, cursor_out TEXT,
  canonical_schema TEXT NOT NULL, content_hash TEXT NOT NULL, n_items INTEGER NOT NULL,
  quality TEXT NOT NULL CHECK(quality IN ('trusted','suspect')), reason TEXT,
  policy_version TEXT NOT NULL, received_at REAL NOT NULL, committed_at REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_pages_trusted_pos ON pages(run_id, pos) WHERE quality = 'trusted';
CREATE INDEX IF NOT EXISTS ix_pages_run_pos ON pages(run_id, pos);
CREATE TABLE IF NOT EXISTS members(
  member_id INTEGER PRIMARY KEY AUTOINCREMENT,
  target TEXT NOT NULL, list_type TEXT NOT NULL, member_pk TEXT, username TEXT NOT NULL,
  first_seen_at REAL NOT NULL, last_seen_at REAL NOT NULL,
  identity_state TEXT NOT NULL DEFAULT 'ok' CHECK(identity_state IN ('ok','ambiguous')));
CREATE UNIQUE INDEX IF NOT EXISTS ux_mem_pk ON members(target, list_type, member_pk) WHERE member_pk IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_mem_user ON members(target, list_type, username) WHERE member_pk IS NULL;
CREATE INDEX IF NOT EXISTS ix_mem_username ON members(target, list_type, username);
CREATE TABLE IF NOT EXISTS observations(
  page_id INTEGER NOT NULL REFERENCES pages(page_id),
  member_id INTEGER NOT NULL REFERENCES members(member_id),
  username_seen TEXT NOT NULL,
  PRIMARY KEY(page_id, member_id));
CREATE INDEX IF NOT EXISTS ix_obs_member ON observations(member_id);
CREATE TABLE IF NOT EXISTS member_merges(
  merge_id INTEGER PRIMARY KEY AUTOINCREMENT,
  loser_member_id INTEGER NOT NULL, survivor_member_id INTEGER NOT NULL,
  merged_at REAL NOT NULL, reason TEXT NOT NULL);
"""

__all__ = ["SCHEMA", "SCHEMA_VERSION"]
