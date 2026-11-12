"""Blind comparison and ablation for the general vs individual line.

The point is not to prove the personal line is better. It is to make the claim
testable:
- blind: two briefs (A/B) for the same day, one general, one individual; the
  mapping is hidden until the reader votes; votes are stored locally;
- ablation: compare top-item selection across variants (no calibration,
  uniform domain weights, light mode) to see whether a component changes
  anything at all.
"""
from __future__ import annotations

import copy
import json
import secrets
import sqlite3
import uuid
from datetime import date
from pathlib import Path

from . import personalize
from .db import atomic, now_iso
from .feeds import recommend
from .profile import choose_mode, load_profile
from .calibrate import effective_features
from .report import _brief_selection, render_brief

BLIND_FILE = "blind.json"


def _load_context(conn: sqlite3.Connection, mode: str = "auto"):
    stored = load_profile(conn)
    if stored is None:
        raise ValueError("盲评需要个体档案；先运行 profile import FILE")
    effective = choose_mode(stored, mode)
    features, calibration_note = effective_features(conn, stored["features"])
    personalization = {"mode": effective, "features": features,
                       "completeness": stored["completeness"]}
    return stored, personalization, calibration_note


def _write_brief(conn, path: Path, personalization, *, days, limit, per_source, topics):
    text = render_brief(conn, days=days, limit=limit, per_source=per_source,
                        topics=topics, personalization=personalization)
    path.write_text(text + "\n", encoding="utf-8")


def blind_new(conn: sqlite3.Connection, directory: Path, *, days: int = 2,
              limit: int = 10, per_source: int = 2, topics=None) -> dict:
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise FileExistsError(f"盲评目录已存在且非空，不覆盖：{directory}")
    stored, personalization, calibration_note = _load_context(conn, "individual")
    if personalization["mode"] != "individual":
        raise ValueError(f"盲评需要个体线；当前档案完整度只到 {personalization['mode']}")
    directory.mkdir(parents=True, exist_ok=True)
    if secrets.randbelow(2):
        a_kind, b_kind = "individual", "general"
        a_personal, b_personal = personalization, None
    else:
        a_kind, b_kind = "general", "individual"
        a_personal, b_personal = None, personalization
    _write_brief(conn, directory / "A.txt", a_personal, days=days, limit=limit,
                 per_source=per_source, topics=topics)
    _write_brief(conn, directory / "B.txt", b_personal, days=days, limit=limit,
                 per_source=per_source, topics=topics)
    meta = {
        "version": 1,
        "run_id": uuid.uuid4().hex,
        "created_at": now_iso(),
        "day": date.today().isoformat(),
        "days": days, "limit": limit, "per_source": per_source,
        "topics": list(topics or []),
        "a_kind": a_kind, "b_kind": b_kind,
        "calibration": calibration_note or None,
    }
    (directory / BLIND_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    return {"directory": str(directory), "meta": meta,
            "a": str(directory / "A.txt"), "b": str(directory / "B.txt")}


def _read_meta(directory: Path) -> dict:
    path = Path(directory) / BLIND_FILE
    if not path.is_file():
        raise ValueError(f"找不到 {path}；请先运行 eval blind")
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("blind.json 无法解析") from None
    for key in ("run_id", "day", "a_kind", "b_kind"):
        if not meta.get(key):
            raise ValueError(f"blind.json 缺少字段：{key}")
    return meta


def blind_vote(conn: sqlite3.Connection, directory: Path, choice: str) -> dict:
    if choice not in ("A", "B"):
        raise ValueError("choice 必须是 A 或 B")
    meta = _read_meta(directory)
    winner = meta["a_kind"] if choice == "A" else meta["b_kind"]
    with atomic(conn):
        conn.execute(
            "INSERT INTO blind_votes(created_at, run_id, day, choice, a_kind, b_kind, winner_kind)"
            " VALUES (?,?,?,?,?,?,?)",
            (now_iso(), meta["run_id"], meta["day"], choice,
             meta["a_kind"], meta["b_kind"], winner),
        )
    return {"choice": choice, "winner": winner, "meta": meta}


def blind_stats(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT winner_kind, COUNT(*) AS n FROM blind_votes"
                        " GROUP BY winner_kind").fetchall()
    counts = {row["winner_kind"]: row["n"] for row in rows}
    total = sum(counts.values())
    return {
        "total": total,
        "individual": counts.get("individual", 0),
        "general": counts.get("general", 0),
        "individual_share": round(counts.get("individual", 0) / total, 3) if total else 0.0,
    }


def ablate(conn: sqlite3.Connection, *, days: int = 2, limit: int = 10,
           per_source: int = 2, topics=None) -> list[dict]:
    stored, full, calibration_note = _load_context(conn, "individual")
    base = recommend(conn, days=days, limit=max(limit, 20),
                     per_source=max(per_source, 2), topics=topics or None)
    uncalibrated = copy.deepcopy(stored["features"])
    uniform = copy.deepcopy(full["features"])
    uniform["domains"] = {key: 1.0 for key in uniform.get("domains", {})}
    variants = [
        ("general", None, "无个体先验"),
        ("individual", full, "完整个体线（含校准）"),
        ("individual-no-calibration", {"mode": "individual", "features": uncalibrated,
                                       "completeness": stored["completeness"]},
         "个体线，但不应用最近校准"),
        ("individual-uniform-domains", {"mode": "individual", "features": uniform,
                                        "completeness": stored["completeness"]},
         "个体线，但所有领域权重相同"),
        ("individual-light", {"mode": "light", "features": full["features"],
                              "completeness": stored["completeness"]},
         "轻个体线（只排序，不加段落）"),
    ]
    full_ids = None
    results = []
    for name, personalization, note in variants:
        items = copy.deepcopy(base)
        mode = "general"
        if personalization:
            mode = personalization["mode"]
            if mode != "general":
                items = personalize.rank_items(items, personalization["features"], mode)
        shown = _brief_selection(items, max_items=4)
        ids = [row["id"] for row in shown]
        if name == "individual":
            full_ids = ids
        results.append({"name": name, "note": note, "ids": ids,
                        "titles": [row["title"] for row in shown]})
    for result in results:
        if full_ids is None:
            result["overlap"] = 0.0
            result["same_order"] = 0
            continue
        overlap = len(set(result["ids"]) & set(full_ids)) / max(1, len(full_ids))
        same_order = sum(1 for left, right in zip(result["ids"], full_ids) if left == right)
        result["overlap"] = round(overlap, 3)
        result["same_order"] = same_order
    return results
