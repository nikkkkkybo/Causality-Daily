"""Local digest history records. Records generation only: no scheduling, no delivery."""
from __future__ import annotations

import json
import sqlite3

from .db import atomic, now_iso


def record_digest(conn: sqlite3.Connection, *, kind: str, path: str | None,
                  ok: bool, summary: dict) -> int:
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError("kind 必须是非空文本")
    if not isinstance(ok, bool):
        raise ValueError("ok 必须是布尔值")
    if not isinstance(summary, dict):
        raise ValueError("summary 必须是对象")
    if path is not None and not isinstance(path, str):
        raise ValueError("path 必须是文本或 null")
    payload = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    with atomic(conn):
        cursor = conn.execute(
            "INSERT INTO digests(created_at, kind, path, ok, summary_json) VALUES (?,?,?,?,?)",
            (now_iso(), kind.strip(), path, 1 if ok else 0, payload),
        )
    return cursor.lastrowid


def list_digests(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit 必须是 1 到 100 的整数")
    return conn.execute(
        "SELECT * FROM digests ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
