from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT UNIQUE NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    checked_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feed_items (
    id INTEGER PRIMARY KEY,
    feed_id INTEGER NOT NULL REFERENCES subscriptions(id),
    item_key TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    summary TEXT NOT NULL,
    published_at TEXT,
    published_raw TEXT,
    fetched_at TEXT NOT NULL,
    read_at TEXT,
    UNIQUE(feed_id, item_key)
);
CREATE INDEX IF NOT EXISTS feed_items_unread ON feed_items(read_at, published_at, fetched_at);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_domain_stats (
    source_id INTEGER NOT NULL REFERENCES sources(id),
    domain TEXT NOT NULL,
    alpha REAL NOT NULL,
    beta REAL NOT NULL,
    n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source_id, domain)
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    title TEXT,
    url TEXT,
    reporter_source_id INTEGER NOT NULL REFERENCES sources(id),
    raw TEXT,
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feed_ingests (
    id INTEGER PRIMARY KEY,
    feed_item_id INTEGER UNIQUE NOT NULL REFERENCES feed_items(id),
    document_id INTEGER NOT NULL REFERENCES documents(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS digests (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT,
    ok INTEGER NOT NULL DEFAULT 1 CHECK(ok IN (0,1)),
    summary_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS watchlists (
    id INTEGER PRIMARY KEY,
    topic TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_day ON notes(day, id);
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    data_json TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'auto' CHECK(mode IN ('auto','general','light','individual')),
    completeness REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vault (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    cipher BLOB NOT NULL,
    fields_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vault_audit (
    id INTEGER PRIMARY KEY,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS vault_audit_created ON vault_audit(created_at, id);
CREATE TABLE IF NOT EXISTS calibrations (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    days INTEGER NOT NULL,
    notes_count INTEGER NOT NULL,
    deltas_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blind_votes (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    run_id TEXT NOT NULL,
    day TEXT NOT NULL,
    choice TEXT NOT NULL CHECK(choice IN ('A','B')),
    a_kind TEXT NOT NULL,
    b_kind TEXT NOT NULL,
    winner_kind TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY,
    canonical_key TEXT UNIQUE NOT NULL,
    text TEXT NOT NULL,
    domain TEXT NOT NULL,
    layer TEXT NOT NULL,
    hard_signal TEXT,
    credibility REAL NOT NULL DEFAULT 0,
    trace TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chains (
    id INTEGER PRIMARY KEY,
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    document_id INTEGER NOT NULL REFERENCES documents(id),
    reporter_source_id INTEGER NOT NULL REFERENCES sources(id),
    origin_source_id INTEGER NOT NULL REFERENCES sources(id),
    quote TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY,
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    text TEXT NOT NULL,
    due_date TEXT NOT NULL,
    check_hint TEXT NOT NULL,
    p REAL CHECK(p IS NULL OR (p >= 0 AND p <= 1)),
    status TEXT NOT NULL DEFAULT 'pending',
    brier REAL,
    note TEXT,
    settled_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS credibility_log (
    id INTEGER PRIMARY KEY,
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    old REAL NOT NULL,
    new REAL NOT NULL,
    reason TEXT NOT NULL,
    at TEXT NOT NULL
);
"""


@contextmanager
def atomic(conn: sqlite3.Connection):
    conn.execute("SAVEPOINT ledger_write")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO ledger_write")
        conn.execute("RELEASE ledger_write")
        raise
    else:
        conn.execute("RELEASE ledger_write")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # SQLite's lower() folds ASCII only; use Python's Unicode casefold for search.
    conn.create_function("casefold", 1, str.casefold, deterministic=True)
    conn.executescript(SCHEMA)
    return conn
