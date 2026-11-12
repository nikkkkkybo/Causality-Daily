"""Read-only listing and keyword search over imported documents and claims.

Nothing here scores, verifies, or re-ranks beyond the existing uncalibrated
experimental score. Keyword matching is a substring match, not relevance or truth.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, time, timezone

DISCLAIMER = "实验分未经校准，不是真实概率；匹配不等于相关，也不代表内容为真。"

LAYERS = ("intent", "plan", "formation", "manifestation")


def _positive_int(value, label: str, maximum: int = 100) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{label} 必须是 1 到 {maximum} 的整数")
    return value


def _since_utc(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        raise ValueError("日期必须是 YYYY-MM-DD")
    try:
        day = date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError("日期必须是 YYYY-MM-DD") from None
    return datetime.combine(day, time.min).astimezone(timezone.utc).isoformat()


def _source_name(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("来源名称必须是非空文本")
    return value.strip()


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + escaped + "%"


def _row_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def list_documents(conn: sqlite3.Connection, since: str | None = None,
                   source: str | None = None, limit: int = 20) -> list[sqlite3.Row]:
    """List documents newest first; source matches the document reporter only."""
    limit = _positive_int(limit, "limit")
    since_utc = _since_utc(since)
    source = _source_name(source)
    sql = (
        "SELECT d.id, d.title, d.url, d.imported_at, s.name AS reporter, s.type AS reporter_type,"
        " (SELECT COUNT(DISTINCT ch.claim_id) FROM chains ch WHERE ch.document_id=d.id) AS claim_count"
        " FROM documents d JOIN sources s ON s.id=d.reporter_source_id"
    )
    where, params = [], []
    if since_utc is not None:
        where.append("d.imported_at>=?")
        params.append(since_utc)
    if source is not None:
        where.append("s.name=?")
        params.append(source)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY d.imported_at DESC, d.id DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def get_document(conn: sqlite3.Connection, document_id: int) -> dict:
    """Return one document with its claims and feed-item link (if any)."""
    if type(document_id) is not int or document_id < 1:
        raise ValueError("文档 ID 必须是正整数")
    row = conn.execute(
        "SELECT d.*, s.name AS reporter, s.type AS reporter_type FROM documents d"
        " JOIN sources s ON s.id=d.reporter_source_id WHERE d.id=?",
        (document_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"文档 #{document_id} 不存在")
    claims = conn.execute(
        "SELECT c.*, (SELECT COUNT(DISTINCT origin_source_id) FROM chains WHERE claim_id=c.id) AS origins"
        " FROM claims c WHERE c.id IN (SELECT claim_id FROM chains WHERE document_id=?)"
        " ORDER BY c.credibility DESC, c.id",
        (document_id,),
    ).fetchall()
    ingest_row = conn.execute(
        "SELECT f.feed_item_id, f.created_at, i.title AS item_title, s.name AS feed_name"
        " FROM feed_ingests f JOIN feed_items i ON i.id=f.feed_item_id"
        " JOIN subscriptions s ON s.id=i.feed_id WHERE f.document_id=? ORDER BY f.id LIMIT 1",
        (document_id,),
    ).fetchone()
    document = _row_dict(row)
    document["raw_length"] = len(row["raw"] or "")
    return {"document": document, "claims": [_row_dict(c) for c in claims],
            "ingest": _row_dict(ingest_row) if ingest_row is not None else None}


def search_claims(conn: sqlite3.Connection, keywords, domain: str | None = None,
                  layer: str | None = None, since: str | None = None,
                  min_credibility: float = 0.0, source: str | None = None,
                  limit: int = 20) -> list[dict]:
    """Substring search: every keyword must match claim text/key or document title.

    Matching uses Unicode casefold (SQLite's lower() is ASCII-only), so "Ärger"
    and "Straße"/"STRASSE" behave sensibly; it is still substring matching, not
    tokenization, relevance, or verification.
    """
    if isinstance(keywords, str):
        keywords = [keywords]
    if not isinstance(keywords, (list, tuple)) or not keywords:
        raise ValueError("至少提供一个关键词")
    cleaned: list[str] = []
    for keyword in keywords:
        if not isinstance(keyword, str) or not keyword.strip():
            raise ValueError("关键词必须是非空文本")
        keyword = keyword.strip()
        if keyword not in cleaned:
            cleaned.append(keyword)
    limit = _positive_int(limit, "limit")
    if (type(min_credibility) not in (int, float) or isinstance(min_credibility, bool)
            or not 0 <= min_credibility <= 1):
        raise ValueError("min 必须是 0 到 1 的数值")
    if domain is not None:
        if not isinstance(domain, str) or not domain.strip():
            raise ValueError("domain 必须是非空文本")
        domain = domain.strip().lower()
    if layer is not None:
        if layer not in LAYERS:
            raise ValueError("layer 必须是 intent / plan / formation / manifestation")
    since_utc = _since_utc(since)
    source = _source_name(source)

    sql = (
        "SELECT c.*,"
        " (SELECT COUNT(DISTINCT origin_source_id) FROM chains WHERE claim_id=c.id) AS origins"
        " FROM claims c WHERE 1=1"
    )
    params: list = []
    for keyword in cleaned:
        like = _like(keyword.casefold())
        sql += (" AND (casefold(c.text) LIKE ? ESCAPE '\\' OR casefold(c.canonical_key) LIKE ? ESCAPE '\\'"
                " OR c.id IN (SELECT ch.claim_id FROM chains ch JOIN documents d ON d.id=ch.document_id"
                " WHERE casefold(d.title) LIKE ? ESCAPE '\\'))")
        params.extend([like, like, like])
    if domain is not None:
        sql += " AND c.domain=?"
        params.append(domain)
    if layer is not None:
        sql += " AND c.layer=?"
        params.append(layer)
    if since_utc is not None:
        sql += " AND c.created_at>=?"
        params.append(since_utc)
    if min_credibility:
        sql += " AND c.credibility>=?"
        params.append(float(min_credibility))
    if source is not None:
        sql += (" AND EXISTS (SELECT 1 FROM chains ch JOIN sources s"
                " ON s.id IN (ch.reporter_source_id, ch.origin_source_id)"
                " WHERE ch.claim_id=c.id AND s.name=?)")
        params.append(source)
    sql += " ORDER BY c.credibility DESC, c.id ASC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [dict(_row_dict(row), disclaimer=DISCLAIMER) for row in rows]
