"""Explicit bridge from unverified feed items to ledger documents.

Reading, marking read, or ingesting a feed item never creates claims by itself and
never marks anything verified. Extraction is a separate, consent-gated step.
"""
from __future__ import annotations

import sqlite3

from . import extract
from .db import atomic, now_iso
from .provenance import ensure_source


def _require_text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须是非空文本")
    return value.strip()


def get_item(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    if type(item_id) is not int or item_id < 1:
        raise ValueError("订阅条目 ID 必须是正整数")
    row = conn.execute(
        "SELECT i.*, s.name AS feed_name FROM feed_items i"
        " JOIN subscriptions s ON s.id=i.feed_id WHERE i.id=?",
        (item_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"订阅条目 I#{item_id} 不存在")
    return row


def linked_document_id(conn: sqlite3.Connection, item_id: int) -> int | None:
    row = conn.execute(
        "SELECT document_id FROM feed_ingests WHERE feed_item_id=?", (item_id,)
    ).fetchone()
    return row["document_id"] if row is not None else None


def prepare_content(conn: sqlite3.Connection, item_id: int, *,
                    fetch: bool = False) -> tuple[str, str | None]:
    """Return (content, url). Default uses the stored summary; --fetch reads the article."""
    item = get_item(conn, item_id)
    if fetch:
        if not item["url"]:
            raise ValueError("条目没有可用原文链接，无法抓取全文")
        content = extract.fetch_url(item["url"])
        if not isinstance(content, str) or not content.strip():
            raise ValueError("抓取到的正文为空，无法建立文档")
        return content.strip(), item["url"]
    content = (item["summary"] or "").strip()
    if not content:
        raise ValueError("条目摘要为空；可加 --fetch 抓取全文再建文档")
    return content, item["url"]


def store_item_document(conn: sqlite3.Connection, item_id: int, *, title: str,
                        reporter_name: str, reporter_type: str | None, raw: str,
                        url: str | None) -> tuple[int, bool]:
    """Create one document for a feed item and link it. Idempotent per item.

    Returns (document_id, created). A repeated call returns the first document
    unchanged and never overwrites its content.
    """
    item = get_item(conn, item_id)
    title = _require_text(title, "文档标题")
    raw = _require_text(raw, "文档内容")
    reporter_name = _require_text(reporter_name, "来源名称")
    with atomic(conn):
        existing = conn.execute(
            "SELECT document_id FROM feed_ingests WHERE feed_item_id=?", (item_id,)
        ).fetchone()
        if existing is not None:
            return existing["document_id"], False
        reporter_id = ensure_source(conn, reporter_name, reporter_type)
        document_id = conn.execute(
            "INSERT INTO documents(title,url,reporter_source_id,raw,imported_at) VALUES(?,?,?,?,?)",
            (title, url, reporter_id, raw, now_iso()),
        ).lastrowid
        conn.execute(
            "INSERT INTO feed_ingests(feed_item_id,document_id,created_at) VALUES(?,?,?)",
            (item_id, document_id, now_iso()),
        )
    return document_id, True


def ingest_item(conn: sqlite3.Connection, item_id: int, *, title: str | None = None,
                reporter: str | None = None, reporter_type: str | None = None,
                fetch: bool = False) -> dict:
    """Non-extracting convenience path: feed item -> document only."""
    item = get_item(conn, item_id)
    existing = linked_document_id(conn, item_id)
    if existing is not None:
        return {"item_id": item_id, "document_id": existing, "created": False,
                "content_kind": None, "raw_length": None}
    content, url = prepare_content(conn, item_id, fetch=fetch)
    document_id, created = store_item_document(
        conn, item_id, title=title or item["title"],
        reporter_name=reporter or item["feed_name"],
        reporter_type=reporter_type, raw=content, url=url,
    )
    return {"item_id": item_id, "document_id": document_id, "created": created,
            "content_kind": "fetched" if fetch else "summary",
            "raw_length": len(content)}


def link_document(conn: sqlite3.Connection, item_id: int, document_id: int) -> None:
    """Link an already-created document (e.g. from import_extraction) to a feed item."""
    get_item(conn, item_id)
    if type(document_id) is not int or document_id < 1:
        raise ValueError("文档 ID 必须是正整数")
    if conn.execute("SELECT id FROM documents WHERE id=?", (document_id,)).fetchone() is None:
        raise ValueError(f"文档 #{document_id} 不存在")
    with atomic(conn):
        existing = conn.execute(
            "SELECT document_id FROM feed_ingests WHERE feed_item_id=?", (item_id,)
        ).fetchone()
        if existing is not None:
            if existing["document_id"] == document_id:
                return
            raise ValueError(
                f"条目 I#{item_id} 已转换为文档 #{existing['document_id']}，不能改链到 #{document_id}"
            )
        conn.execute(
            "INSERT INTO feed_ingests(feed_item_id,document_id,created_at) VALUES(?,?,?)",
            (item_id, document_id, now_iso()),
        )
