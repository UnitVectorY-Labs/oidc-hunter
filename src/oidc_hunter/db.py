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

CREATE TABLE IF NOT EXISTS catalog_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    artifact_ref TEXT,
    entry_count INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT,
    run_id TEXT NOT NULL,
    service_id TEXT,
    name TEXT,
    issuer_hint TEXT,
    openid_configuration_url TEXT,
    jwks_uri TEXT,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(run_id, service_id, openid_configuration_url, jwks_uri)
);

CREATE TABLE IF NOT EXISTS candidate_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    artifact_ref TEXT,
    entry_count INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    name TEXT NOT NULL,
    issuer TEXT,
    openid_configuration_url TEXT,
    jwks_uri TEXT,
    primary_domain TEXT,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    source TEXT NOT NULL DEFAULT 'candidate_file',
    first_seen_run_id TEXT,
    last_seen_run_id TEXT,
    review_notes TEXT,
    UNIQUE(candidate_id),
    UNIQUE(issuer, jwks_uri)
);

CREATE TABLE IF NOT EXISTS strategy_tactics (
    tactic_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    historical_runs INTEGER NOT NULL DEFAULT 0,
    successes INTEGER NOT NULL DEFAULT 0,
    false_positives INTEGER NOT NULL DEFAULT 0,
    last_used_run_id TEXT,
    last_score REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_plans (
    run_id TEXT PRIMARY KEY,
    plan_json TEXT NOT NULL,
    plan_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS investigation_batches (
    batch_ref TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    tactic_id TEXT NOT NULL,
    artifact_ref TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    notes TEXT,
    target_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS probe_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    batch_ref TEXT,
    domain TEXT NOT NULL,
    status TEXT NOT NULL,
    classification TEXT NOT NULL,
    openid_configuration_url TEXT NOT NULL,
    issuer TEXT,
    jwks_uri TEXT,
    error TEXT,
    evidence_artifact_ref TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issuer_clusters (
    cluster_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    issuer TEXT NOT NULL,
    jwks_uri TEXT,
    openid_configuration_url TEXT,
    canonical_domain TEXT NOT NULL,
    domains_json TEXT NOT NULL DEFAULT '[]',
    known_match_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending_review',
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS domain_state (
    domain TEXT PRIMARY KEY,
    discovered_by_tactic TEXT NOT NULL,
    first_seen_run_id TEXT NOT NULL,
    last_seen_run_id TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    last_probe_status TEXT NOT NULL,
    last_probe_classification TEXT NOT NULL,
    last_openid_configuration_url TEXT,
    last_issuer TEXT,
    last_jwks_uri TEXT,
    needs_followup INTEGER NOT NULL DEFAULT 0,
    artifact_ref TEXT
);

CREATE TABLE IF NOT EXISTS candidate_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    cluster_id TEXT,
    issuer TEXT NOT NULL,
    domain TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lessons_learned (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    category TEXT NOT NULL,
    lesson TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_entries_status ON candidate_entries(status);
CREATE INDEX IF NOT EXISTS idx_candidate_entries_issuer ON candidate_entries(issuer);
CREATE INDEX IF NOT EXISTS idx_catalog_entries_issuer_hint ON catalog_entries(issuer_hint);
CREATE INDEX IF NOT EXISTS idx_probe_results_run_batch ON probe_results(run_id, batch_ref);
CREATE INDEX IF NOT EXISTS idx_issuer_clusters_run_status ON issuer_clusters(run_id, status);
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
        """
        UPDATE runs
        SET completed_at = ?, status = 'interrupted',
            summary = COALESCE(summary, 'Run was superseded by a later start or terminated before clean shutdown.')
        WHERE status = 'running' AND run_id != ?
        """,
        (utc_now(), run_id),
    )
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
