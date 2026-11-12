from __future__ import annotations

import json
import re
import sqlite3
from datetime import date

from .db import atomic, now_iso
from .scoring import (
    ChainEvidence,
    credibility,
    normalize_layer,
    prior_pseudo_counts,
    reliability_mean,
)

VALID_SOURCE_TYPES = {"primary_data", "official", "participant", "institutional_media", "analysis", "anonymous", "unknown"}


def normalize_type(source_type: str | None) -> str:
    t = (source_type or "unknown").strip().lower()
    return t if t in VALID_SOURCE_TYPES else "unknown"


def normalize_key(key: str) -> str:
    parts = [re.sub(r"\s+", " ", p.strip()).lower() for p in key.replace("｜", "|").split("|")]
    return "|".join(p for p in parts if p)


def ensure_source(conn: sqlite3.Connection, name: str, source_type: str | None = None) -> int:
    name = name.strip()
    t = normalize_type(source_type)
    row = conn.execute("SELECT id, type FROM sources WHERE name=?", (name,)).fetchone()
    if row:
        if row["type"] == "unknown" and t != "unknown":
            with atomic(conn):
                conn.execute("UPDATE sources SET type=? WHERE id=?", (t, row["id"]))
                alpha, beta = prior_pseudo_counts(t)
                conn.execute("UPDATE source_domain_stats SET alpha=?, beta=? WHERE source_id=?",
                             (alpha, beta, row["id"]))
                affected = conn.execute("SELECT DISTINCT claim_id FROM chains WHERE origin_source_id=?",
                                        (row["id"],)).fetchall()
                for claim in affected:
                    recompute_claim(conn, claim["claim_id"], reason=f"source#{row['id']} type prior updated")
        return row["id"]
    cur = conn.execute("INSERT INTO sources(name, type, created_at) VALUES (?,?,?)", (name, t, now_iso()))
    return cur.lastrowid


def ensure_stats(conn: sqlite3.Connection, source_id: int, domain: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM source_domain_stats WHERE source_id=? AND domain=?", (source_id, domain)
    ).fetchone()
    if row:
        return row
    stype = conn.execute("SELECT type FROM sources WHERE id=?", (source_id,)).fetchone()["type"]
    a, b = prior_pseudo_counts(stype)
    conn.execute(
        "INSERT INTO source_domain_stats(source_id, domain, alpha, beta, n) VALUES (?,?,?,?,0)",
        (source_id, domain, a, b),
    )
    return conn.execute(
        "SELECT * FROM source_domain_stats WHERE source_id=? AND domain=?", (source_id, domain)
    ).fetchone()


def source_reliability(conn: sqlite3.Connection, source_id: int, domain: str) -> float:
    row = ensure_stats(conn, source_id, domain)
    return reliability_mean(row["alpha"], row["beta"])


def independent_origins(conn: sqlite3.Connection, claim_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(DISTINCT origin_source_id) FROM chains WHERE claim_id=?", (claim_id,)
    ).fetchone()[0]


def chain_evidence(conn: sqlite3.Connection, claim_id: int, domain: str) -> list[ChainEvidence]:
    rows = conn.execute("SELECT DISTINCT origin_source_id FROM chains WHERE claim_id=?", (claim_id,)).fetchall()
    return [ChainEvidence(r["origin_source_id"], source_reliability(conn, r["origin_source_id"], domain)) for r in rows]


def recompute_claim(conn: sqlite3.Connection, claim_id: int, reason: str) -> float:
    with atomic(conn):
        c = conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
        if c is None:
            raise ValueError(f"主张 #{claim_id} 不存在")
        new, trace = credibility(chain_evidence(conn, claim_id, c["domain"]), layer=c["layer"])
        conn.execute(
            "UPDATE claims SET credibility=?, trace=?, updated_at=? WHERE id=?",
            (new, json.dumps(trace, ensure_ascii=False), now_iso(), claim_id),
        )
        if new != c["credibility"]:
            conn.execute(
                "INSERT INTO credibility_log(claim_id, old, new, reason, at) VALUES (?,?,?,?,?)",
                (claim_id, c["credibility"], new, reason, now_iso()),
            )
    return new


def _required_text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须是非空文本")
    return value.strip()


def add_prediction(
    conn: sqlite3.Connection, claim_id: int, text: str, due_date: str,
    check_hint: str, p: float | None = None,
) -> int:
    text = _required_text(text, "预测 text")
    check_hint = _required_text(check_hint, "check_hint（核验方法）")
    if not isinstance(due_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due_date):
        raise ValueError("due_date 必须是 YYYY-MM-DD")
    due = date.fromisoformat(due_date)
    if due < date.today():
        raise ValueError("不能登记已经过截止日的预测")
    if p is not None and (type(p) not in (int, float) or not 0 <= p <= 1):
        raise ValueError("预测概率必须是 0 到 1 的有限数值，或省略")
    with atomic(conn):
        if conn.execute("SELECT id FROM claims WHERE id=?", (claim_id,)).fetchone() is None:
            raise ValueError(f"主张 #{claim_id} 不存在")
        dup = conn.execute(
            "SELECT id, p FROM predictions WHERE claim_id=? AND text=? AND due_date=? AND check_hint=?",
            (claim_id, text, due_date, check_hint),
        ).fetchone()
        if dup:
            if p is not None and p != dup["p"]:
                stored = str(dup["p"]) if dup["p"] is not None else "未登记"
                raise ValueError(f"预测 P#{dup['id']} 已存在，概率已冻结为 {stored}，不能改写")
            return dup["id"]
        return conn.execute(
            "INSERT INTO predictions(claim_id, text, due_date, check_hint, p, created_at) VALUES (?,?,?,?,?,?)",
            (claim_id, text, due_date, check_hint, p, now_iso()),
        ).lastrowid


def _validate_source(source, label: str) -> None:
    if source is None:
        return
    if not isinstance(source, dict):
        raise ValueError(f"{label} 必须是对象或 null")
    for field in ("name", "type"):
        if source.get(field) is not None:
            _required_text(source[field], f"{label}.{field}")


def validate_extraction(extraction: dict) -> None:
    if not isinstance(extraction, dict) or not isinstance(extraction.get("claims"), list):
        raise ValueError("抽取结果必须是包含 claims 数组的 JSON 对象")
    _validate_source(extraction.get("reporter"), "reporter")
    for index, item in enumerate(extraction["claims"]):
        label = f"claims[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{label} 必须是对象")
        _required_text(item.get("text"), f"{label}.text")
        for field in ("canonical_key", "domain", "layer", "quote", "hard_signal"):
            if item.get(field) is not None and not isinstance(item[field], str):
                raise ValueError(f"{label}.{field} 必须是文本或 null")
        if not normalize_key(item.get("canonical_key") or item["text"]):
            raise ValueError(f"{label}.canonical_key 不能为空")
        _validate_source(item.get("origin"), f"{label}.origin")
        predictions = item.get("predictions", [])
        if not isinstance(predictions, list) or any(not isinstance(p, dict) for p in predictions):
            raise ValueError(f"{label}.predictions 必须是对象数组")


def import_extraction(
    conn: sqlite3.Connection,
    extraction: dict,
    title: str | None = None,
    url: str | None = None,
    raw: str | None = None,
) -> dict:
    validate_extraction(extraction)
    with atomic(conn):
        reporter = extraction.get("reporter") or {}
        reporter_id = ensure_source(conn, reporter.get("name") or "未知来源", reporter.get("type"))
        doc_id = conn.execute(
            "INSERT INTO documents(title, url, reporter_source_id, raw, imported_at) VALUES (?,?,?,?,?)",
            (title, url, reporter_id, raw, now_iso()),
        ).lastrowid
        summary = {"document_id": doc_id, "reporter_id": reporter_id, "claims": [], "skipped_predictions": []}
        for item in extraction["claims"]:
            key = normalize_key(item.get("canonical_key") or item["text"])
            domain = (item.get("domain") or "general").strip().lower()
            layer = normalize_layer((item.get("layer") or "intent").strip().lower())
            hard_signal = (item.get("hard_signal") or "").strip() or None
            row = conn.execute("SELECT id, hard_signal FROM claims WHERE canonical_key=?", (key,)).fetchone()
            created = row is None
            if created:
                ts = now_iso()
                claim_id = conn.execute(
                    "INSERT INTO claims(canonical_key, text, domain, layer, hard_signal, credibility, trace, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,0,'',?,?)",
                    (key, item["text"].strip(), domain, layer, hard_signal, ts, ts),
                ).lastrowid
            else:
                claim_id = row["id"]
                if hard_signal and not row["hard_signal"]:
                    conn.execute("UPDATE claims SET hard_signal=? WHERE id=?", (hard_signal, claim_id))
            origin = item.get("origin")
            origin_id = ensure_source(conn, origin["name"], origin.get("type")) if origin and origin.get("name") else reporter_id
            conn.execute(
                "INSERT INTO chains(claim_id, document_id, reporter_source_id, origin_source_id, quote, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (claim_id, doc_id, reporter_id, origin_id, item.get("quote"), now_iso()),
            )
            for prediction in item.get("predictions", []):
                try:
                    add_prediction(conn, claim_id, prediction.get("text"), prediction.get("due_date"),
                                   prediction.get("check_hint"), p=prediction.get("p"))
                except ValueError as e:
                    summary["skipped_predictions"].append({"claim_id": claim_id, "text": prediction.get("text"), "why": str(e)})
            score = recompute_claim(conn, claim_id, reason=f"import doc#{doc_id}")
            summary["claims"].append({"claim_id": claim_id, "created": created, "credibility": score, "key": key})
    return summary
