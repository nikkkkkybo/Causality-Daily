import sqlite3

import pytest

from causal_ledger.db import connect


def test_feed_schema_is_additive_and_persists_existing_claims(tmp_path):
    path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE documents(id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO documents VALUES (1, 'existing')")
    conn = connect(path)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"subscriptions", "feed_items", "feed_ingests", "digests", "watchlists"} <= names
        assert conn.execute("SELECT title FROM documents WHERE id=1").fetchone()[0] == "existing"
        conn.execute("INSERT INTO subscriptions(name,url,created_at) VALUES ('demo','https://example.org/feed','2026-11-09T00:00:00+00:00')")
        conn.commit()
    finally:
        conn.close()
    reopened = connect(path)
    try:
        assert reopened.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            reopened.execute("INSERT INTO subscriptions(name,url,created_at) VALUES ('demo','https://example.org/feed','now')")
    finally:
        reopened.close()
