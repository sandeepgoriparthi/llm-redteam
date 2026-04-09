from __future__ import annotations

import sqlite3
from pathlib import Path

# Bump this when you add or change tables.
# The migrate() function handles upgrades from older versions.
SCHEMA_VERSION = 2


_CREATE_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT    PRIMARY KEY,
    timestamp       TEXT    NOT NULL,
    target_model    TEXT    NOT NULL,
    target_provider TEXT    NOT NULL,
    probe_categories TEXT   NOT NULL,   -- JSON array
    garak_version   TEXT    NOT NULL DEFAULT '',
    duration_seconds REAL   NOT NULL DEFAULT 0.0,
    exit_code       INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT    NOT NULL DEFAULT ''
);
"""

_CREATE_PROBE_RESULTS = """
CREATE TABLE IF NOT EXISTS probe_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT    NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    probe_category  TEXT    NOT NULL,
    probe_name      TEXT    NOT NULL,
    status          TEXT    NOT NULL,   -- passed | failed | error | skipped
    severity        TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    attempts        INTEGER NOT NULL DEFAULT 0,
    failures        INTEGER NOT NULL DEFAULT 0,
    raw_output      TEXT    NOT NULL DEFAULT ''
);
"""

_CREATE_PROBE_RESULTS_IDX = """
CREATE INDEX IF NOT EXISTS idx_probe_results_run_id
    ON probe_results (run_id);
"""

_CREATE_PROBE_RESULTS_CATEGORY_IDX = """
CREATE INDEX IF NOT EXISTS idx_probe_results_category_name
    ON probe_results (probe_category, probe_name);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """
    Open a connection with foreign keys enforced and WAL mode enabled.
    WAL gives better read concurrency -- important if the scheduler and
    CLI query the DB at the same time.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """
    Create tables if they don't exist and apply any pending migrations.
    Safe to call on every startup -- idempotent.
    """
    with conn:
        conn.execute(_CREATE_SCHEMA_VERSION)
        conn.execute(_CREATE_RUNS)
        conn.execute(_CREATE_PROBE_RESULTS)
        conn.execute(_CREATE_PROBE_RESULTS_IDX)
        conn.execute(_CREATE_PROBE_RESULTS_CATEGORY_IDX)

        current = _get_version(conn)
        if current < SCHEMA_VERSION:
            _apply_migrations(conn, from_version=current)
            _set_version(conn, SCHEMA_VERSION)


def _get_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1"
    ).fetchone()
    return row["version"] if row else 0


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


def _apply_migrations(conn: sqlite3.Connection, from_version: int) -> None:
    """
    Ordered list of migration steps. Each step is applied only once.
    Add new steps here when SCHEMA_VERSION bumps -- never edit existing ones.
    """
    steps: dict[int, list[str]] = {
        # v1 -> v2: nothing yet, placeholder
        1: [],
    }
    for version in sorted(steps):
        if version > from_version:
            for sql in steps[version]:
                conn.execute(sql)
