"""Persistent topic watchlist used as the default filter for inbox/brief.

Topics filter subscription titles/summaries only; they never score, verify, or
promote content. No network, no model.
"""
from __future__ import annotations

import sqlite3

from .db import atomic, now_iso

MAX_TOPIC_LENGTH = 200


def _clean_topic(topic) -> str:
    if not isinstance(topic, str):
        raise ValueError("关注主题必须是非空文本")
    cleaned = " ".join(topic.split())
    if not cleaned:
        raise ValueError("关注主题必须是非空文本")
    if len(cleaned) > MAX_TOPIC_LENGTH:
        raise ValueError(f"关注主题不能超过 {MAX_TOPIC_LENGTH} 个字符")
    return cleaned


def add_topic(conn: sqlite3.Connection, topic) -> int:
    topic = _clean_topic(topic)
    with atomic(conn):
        if conn.execute("SELECT id FROM watchlists WHERE topic=?", (topic,)).fetchone() is not None:
            raise ValueError(f"关注主题已存在：{topic}")
        cursor = conn.execute(
            "INSERT INTO watchlists(topic,created_at) VALUES(?,?)", (topic, now_iso())
        )
    return cursor.lastrowid


def remove_topic(conn: sqlite3.Connection, topic) -> None:
    topic = _clean_topic(topic)
    with atomic(conn):
        cursor = conn.execute("DELETE FROM watchlists WHERE topic=?", (topic,))
        if cursor.rowcount == 0:
            raise ValueError(f"关注主题不存在：{topic}")


def set_topic_enabled(conn: sqlite3.Connection, topic, enabled: bool) -> None:
    topic = _clean_topic(topic)
    if not isinstance(enabled, (bool, int)) or enabled not in (0, 1):
        raise ValueError("enabled 必须是 0 或 1")
    with atomic(conn):
        cursor = conn.execute(
            "UPDATE watchlists SET enabled=? WHERE topic=?", (int(enabled), topic))
        if cursor.rowcount == 0:
            raise ValueError(f"关注主题不存在：{topic}")


def list_topics(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM watchlists ORDER BY id").fetchall()


def enabled_topics(conn: sqlite3.Connection) -> list[str]:
    return [row["topic"] for row in conn.execute(
        "SELECT topic FROM watchlists WHERE enabled=1 ORDER BY id")]
