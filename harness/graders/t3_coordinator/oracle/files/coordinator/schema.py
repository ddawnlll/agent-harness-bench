"""SQLite schema bootstrap and migration support.

The schema is created with ``IF NOT EXISTS`` so that ``init_schema`` is safe to
run concurrently from many processes and idempotent across restarts.
``meta.schema_version`` records the applied version for future migrations.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL UNIQUE,
    payload      TEXT,
    state        TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER,
    result       TEXT,
    error        TEXT,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS leases (
    job_id       TEXT PRIMARY KEY,
    worker_id    TEXT NOT NULL,
    expires_at   REAL NOT NULL,
    heartbeat_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS results (
    job_id       TEXT PRIMARY KEY,
    worker_id    TEXT NOT NULL,
    result       TEXT,
    completed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    job_id    TEXT,
    worker_id TEXT,
    type      TEXT NOT NULL,
    detail    TEXT
);

CREATE INDEX IF NOT EXISTS jobs_state_idx ON jobs (state, seq);
CREATE INDEX IF NOT EXISTS events_job_idx ON events (job_id, event_id);

-- Audit receipts are append-only: history must be reconstructable and must
-- never be rewritten or deleted by application code.
CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;
"""


def init_schema(conn, now: float) -> None:
    """Create every table/index/trigger and stamp the schema version."""

    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('created_at', ?) "
        "ON CONFLICT(key) DO NOTHING",
        (repr(now),),
    )
