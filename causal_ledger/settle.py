from __future__ import annotations

import sqlite3
from datetime import date

from .db import atomic, now_iso

OUTCOMES = ("hit", "miss", "void")


def settle(conn: sqlite3.Connection, prediction_id: int, outcome: str, note: str | None = None) -> dict:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome 必须是 {OUTCOMES}")
    if not isinstance(note, str) or not note.strip():
        raise ValueError("结算必须提供核验依据；作废也需说明原因")
    with atomic(conn):
        pred = conn.execute("SELECT * FROM predictions WHERE id=?", (prediction_id,)).fetchone()
        if pred is None:
            raise ValueError(f"预测 #{prediction_id} 不存在")
        if pred["status"] != "pending":
            raise ValueError(f"预测 #{prediction_id} 已结算为 {pred['status']}，不可重复结算")
        if outcome == "miss" and date.today().isoformat() <= pred["due_date"]:
            raise ValueError("截止日尚未结束，不能判定 miss；缺乏观察不等于未发生")
        brier = None
        if outcome != "void" and pred["p"] is not None:
            brier = (pred["p"] - (1.0 if outcome == "hit" else 0.0)) ** 2
        conn.execute(
            "UPDATE predictions SET status=?, brier=?, note=?, settled_at=? WHERE id=?",
            (outcome, brier, note.strip(), now_iso(), prediction_id),
        )
    return {"prediction_id": prediction_id, "claim_id": pred["claim_id"], "outcome": outcome, "brier": brier}
