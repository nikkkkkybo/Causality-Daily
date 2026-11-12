"""7-day calibration: turn end-of-report notes into small, auditable weight shifts.

This is the "strong subconscious" adjustment loop:
- notes are read locally (never sent anywhere);
- only domain mentions plus explicit positive/negative words change weights;
- a proposal is previewed first; --apply stores one calibration row;
- daily applies the latest stored calibration without touching the profile itself.
"""
from __future__ import annotations

import copy
import json
import sqlite3
from collections import defaultdict
from datetime import date, timedelta

from . import personalize
from .db import atomic, now_iso
from .profile import DOMAIN_LABELS

STEP = 0.05
MAX_DELTA = 0.15
NEGATIVE = ("无关", "没用", "不准", "别再推", "不想看", "不是我要", "太远",
            "错", "泛", "没兴趣", "反感")
POSITIVE = ("有用", "相关", "想看", "重要", "准确", "多推", "继续",
            "对", "命中", "有价值")


def _sentiment(text: str) -> int:
    folded = (text or "").casefold()
    if any(word.casefold() in folded for word in NEGATIVE):
        return -1
    if any(word.casefold() in folded for word in POSITIVE):
        return 1
    return 0


def propose(conn: sqlite3.Connection, days: int = 7, today: str | None = None) -> dict:
    if type(days) is not int or not 1 <= days <= 90:
        raise ValueError("days 必须是 1 到 90 的整数")
    end = date.fromisoformat(today) if today else date.today()
    since = (end - timedelta(days=days - 1)).isoformat()
    rows = conn.execute(
        "SELECT id, day, text FROM notes WHERE day>=? ORDER BY id", (since,)
    ).fetchall()
    deltas: dict[str, float] = defaultdict(float)
    evidence: dict[str, list[dict]] = defaultdict(list)
    skipped = 0
    for row in rows:
        text = row["text"] or ""
        domains = personalize.classify_domains(text)
        sentiment = _sentiment(text)
        if not domains or sentiment == 0:
            skipped += 1
            continue
        for domain in domains:
            deltas[domain] += STEP * sentiment
            evidence[domain].append({
                "note_id": row["id"], "day": row["day"], "sentiment": sentiment,
            })
    cleaned = {}
    for domain, value in deltas.items():
        value = round(max(-MAX_DELTA, min(MAX_DELTA, value)), 3)
        if value:
            cleaned[domain] = value
    return {
        "days": days, "since": since, "notes_count": len(rows),
        "skipped": skipped, "deltas": cleaned,
        "evidence": {domain: evidence[domain] for domain in cleaned},
    }


def apply(conn: sqlite3.Connection, proposal: dict) -> int:
    deltas = proposal.get("deltas") or {}
    if not deltas:
        raise ValueError("没有可应用的调整（备注里没有明确的领域 + 正/负向词）")
    with atomic(conn):
        cursor = conn.execute(
            "INSERT INTO calibrations(created_at, days, notes_count, deltas_json)"
            " VALUES (?,?,?,?)",
            (now_iso(), proposal["days"], proposal["notes_count"],
             json.dumps(deltas, ensure_ascii=False)),
        )
    return cursor.lastrowid


def latest(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT * FROM calibrations ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    try:
        deltas = json.loads(row["deltas_json"])
    except (TypeError, ValueError):
        deltas = {}
    return {"id": row["id"], "created_at": row["created_at"], "days": row["days"],
            "notes_count": row["notes_count"], "deltas": deltas}


def effective_features(conn: sqlite3.Connection, features: dict) -> tuple[dict, str]:
    """Apply the latest calibration to a copy of features; return (features, note)."""
    stored = latest(conn)
    if not stored or not stored["deltas"]:
        return copy.deepcopy(features), ""
    adjusted = copy.deepcopy(features)
    domains = adjusted.setdefault("domains", {})
    for domain, delta in stored["deltas"].items():
        if domain not in DOMAIN_LABELS:
            continue
        current = float(domains.get(domain, 0.0))
        domains[domain] = round(max(0.0, min(1.0, current + float(delta))), 3)
    parts = "、".join(f"{DOMAIN_LABELS.get(domain, domain)}{delta:+.2f}"
                      for domain, delta in stored["deltas"].items())
    return adjusted, f"校准：应用最近一次（C#{stored['id']}，{stored['days']} 天备注）：{parts}"
