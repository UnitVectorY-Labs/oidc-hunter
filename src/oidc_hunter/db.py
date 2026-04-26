"""SQLite persistence for oidc-hunter."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from typing import Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    summary TEXT
);

CREATE TABLE IF NOT EXISTS catalog_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    service_id TEXT,
    name TEXT,
    issuer_hint TEXT,
    openid_configuration_url TEXT,
    jwks_uri TEXT,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(run_id, service_id, openid_configuration_url, jwks_uri)
);

CREATE TABLE IF NOT EXISTS candidate_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT,
    name TEXT,
    issuer TEXT,
    openid_configuration_url TEXT,
    jwks_uri TEXT,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    source TEXT NOT NULL DEFAULT 'candidate_file',
    UNIQUE(candidate_id, issuer, openid_configuration_url, jwks_uri)
);

CREATE TABLE IF NOT EXISTS domain_state (
    domain TEXT PRIMARY KEY,
    discovered_by_tactic TEXT NOT NULL,
    first_seen_run_id TEXT NOT NULL,
    last_seen_run_id TEXT NOT NULL,
    last_probe_status TEXT NOT NULL,
    last_probe_classification TEXT NOT NULL,
    last_openid_configuration_url TEXT,
    last_issuer TEXT,
    last_jwks_uri TEXT,
    needs_followup INTEGER NOT NULL DEFAULT 0,
    artifact_ref TEXT
);

CREATE TABLE IF NOT EXISTS run_plans (
    run_id TEXT PRIMARY KEY,
    plan_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    issuer TEXT NOT NULL,
    domain TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, issuer, domain)
);
"""


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def database(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def start_run(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO runs(run_id, started_at, status) VALUES (?, ?, ?)",
        (run_id, utc_now(), "running"),
    )


def close_run(conn: sqlite3.Connection, run_id: str, status: str, summary: str) -> None:
    conn.execute(
        """
        UPDATE runs
        SET completed_at = ?, status = ?, summary = ?
        WHERE run_id = ?
        """,
        (utc_now(), status, summary, run_id),
    )
