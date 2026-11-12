"""Individual causal baseline: validate, store, derive features, choose mode, vault.

Two storage layers:
- public profile JSON: only A/B derived-usable fields; safe for ranking/show;
- OS-encrypted vault: fields the user marked as "保险箱"; never used for daily
  ranking and only decrypted by an explicit unlock that is audit-logged.
"""
from __future__ import annotations

import copy
import json
import sqlite3

from . import crypto
from .db import atomic, now_iso

PROFILE_VERSION = "individual-baseline-v2"
MAX_PROFILE_BYTES = 256 * 1024
DOMAINS = ("career", "wealth", "health", "relationship", "family",
           "learning", "social", "inner")
DOMAIN_LABELS = {
    "career": "事业", "wealth": "财务", "health": "健康",
    "relationship": "亲密关系", "family": "家庭", "learning": "学习",
    "social": "社交", "inner": "内在",
}
MODES = ("auto", "general", "light", "individual")
MODE_LABELS = {
    "general": "通线（通用读者版）",
    "light": "轻个体线（只调排序）",
    "individual": "个体线（强潜意识）",
}
# Paths that must not remain in the public profile JSON. They are moved into
# the encrypted vault (or stripped when they duplicate other answers).
VAULT_PATHS = (
    ("position", "events"),
    ("quadrants", "mind", "change"),
    ("quadrants", "spirit", "meaning"),
    ("causality", "birth"),
    ("birth",),
    ("birth_time",),
    ("birth_place",),
    ("privacy", "answers"),
)


def _clamp(value, low, high):
    return max(low, min(high, value))


def validate_profile(data) -> dict:
    """Return a normalized deep copy; raise ValueError on malformed input."""
    if not isinstance(data, dict):
        raise ValueError("档案必须是 JSON 对象")
    raw = json.dumps(data, ensure_ascii=False)
    if len(raw.encode("utf-8")) > MAX_PROFILE_BYTES:
        raise ValueError(f"档案不能超过 {MAX_PROFILE_BYTES // 1024} KiB")
    profile = copy.deepcopy(data)
    version = profile.get("version", PROFILE_VERSION)
    if not isinstance(version, str) or not version:
        raise ValueError("version 必须是非空字符串")
    profile["version"] = version

    domains = profile.get("domains")
    if domains is not None:
        if not isinstance(domains, dict):
            raise ValueError("domains 必须是对象")
        cleaned = {}
        for key, value in domains.items():
            if key not in DOMAINS:
                raise ValueError(f"未知领域：{key}")
            if type(value) not in (int, float) or not 0 <= value <= 3:
                raise ValueError(f"领域 {key} 的权重必须是 0 到 3")
            cleaned[key] = float(value)
        profile["domains"] = cleaned
    return profile


def _pop_path(data: dict, path: tuple[str, ...]):
    current = data
    for key in path[:-1]:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    if not isinstance(current, dict) or path[-1] not in current:
        return None
    return current.pop(path[-1])


def _put_path(data: dict, path: tuple[str, ...], value) -> None:
    current = data
    for key in path[:-1]:
        if not isinstance(current.get(key), dict):
            current[key] = {}
        current = current[key]
    current[path[-1]] = value


def extract_vault(profile: dict) -> tuple[dict, dict]:
    """Split a validated profile into (public, vault) without mutating input."""
    public = copy.deepcopy(profile)
    vault = {}
    for path in VAULT_PATHS:
        value = _pop_path(public, path)
        if value not in (None, "", [], {}):
            vault[".".join(path)] = value
    return public, vault


def merge_vault(public: dict, vault: dict) -> dict:
    merged = copy.deepcopy(public)
    for path, value in vault.items():
        _put_path(merged, tuple(path.split(".")), value)
    return merged


def completeness(data: dict | None) -> float:
    """A coarse 0..1 completeness score. It is not a quality judgement."""
    if not isinstance(data, dict):
        return 0.0
    score = 0.0
    basics = data.get("basics") or {}
    if basics.get("timezone"):
        score += 0.05
    if basics.get("rhythm"):
        score += 0.05
    position = data.get("position") or {}
    if position.get("problem"):
        score += 0.10
    if position.get("goal"):
        score += 0.10
    causal = data.get("causal_position") or {}
    for key, weight in (("role", 0.05), ("force", 0.05), ("horizon", 0.05)):
        if causal.get(key):
            score += weight
    if causal.get("protect"):
        score += 0.05
    domains = data.get("domains") or {}
    nonzero = [value for value in domains.values() if float(value) > 0]
    if len(nonzero) >= 3:
        score += 0.20
    elif nonzero:
        score += 0.10
    psychology = data.get("psychology") or {}
    if psychology.get("values"):
        score += 0.05
    if psychology.get("change_stage"):
        score += 0.05
    if psychology.get("load") is not None:
        score += 0.05
    if position.get("constraints"):
        score += 0.05
    if position.get("leverage"):
        score += 0.05
    return round(min(1.0, score), 4)


def derive_features(profile: dict) -> dict:
    """Turn a stored profile into non-sensitive ranking features."""
    data = profile.get("data", profile) if isinstance(profile, dict) else {}
    if not isinstance(data, dict):
        return {"domains": {}, "constraints": [], "phase": "", "role": "",
                "force": "", "horizon": "", "relations": [], "values": [],
                "load": None, "change_stage": "", "goals": {}, "completeness": 0.0}
    domains = {key: round(float(value) / 3.0, 3)
               for key, value in (data.get("domains") or {}).items()
               if key in DOMAINS}
    position = data.get("position") or {}
    causal = data.get("causal_position") or {}
    psychology = data.get("psychology") or {}
    return {
        "domains": domains,
        "constraints": [str(item) for item in position.get("constraints") or []],
        "phase": str(position.get("phase") or ""),
        "role": str(causal.get("role") or ""),
        "force": str(causal.get("force") or ""),
        "horizon": str(causal.get("horizon") or ""),
        "relations": [str(item) for item in causal.get("relations") or []],
        "values": [str(item) for item in psychology.get("values") or []],
        "load": psychology.get("load"),
        "change_stage": str(psychology.get("change_stage") or ""),
        "goals": {
            "problem": str(position.get("problem") or ""),
            "goal": str(position.get("goal") or ""),
            "leverage": str(position.get("leverage") or ""),
        },
        "completeness": completeness(data),
    }


def choose_mode(profile, requested: str = "auto") -> str:
    """Return general/light/individual. A profile is required for non-general."""
    if requested not in MODES:
        raise ValueError("mode 必须是 auto/general/light/individual")
    if requested != "auto":
        return requested if profile is not None else "general"
    if profile is None:
        return "general"
    score = profile.get("completeness", completeness(profile.get("data")))
    if score < 0.4:
        return "general"
    if score < 0.7:
        return "light"
    return "individual"


def vault_status(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT fields_json, created_at, updated_at FROM vault WHERE id=1").fetchone()
    if row is None:
        return None
    try:
        fields = json.loads(row["fields_json"])
    except (TypeError, ValueError):
        fields = []
    return {"fields": fields, "created_at": row["created_at"],
            "updated_at": row["updated_at"]}


def _audit(conn: sqlite3.Connection, action: str, reason: str) -> None:
    conn.execute("INSERT INTO vault_audit(action, reason, created_at) VALUES (?,?,?)",
                 (action, reason, now_iso()))


def save_profile(conn: sqlite3.Connection, data, mode: str = "auto") -> dict:
    profile = validate_profile(data)
    if mode not in MODES:
        raise ValueError("mode 必须是 auto/general/light/individual")
    public, vault = extract_vault(profile)
    score = completeness(public)
    payload = json.dumps(public, ensure_ascii=False)
    fields = sorted(vault)
    cipher = None
    if vault:
        if not crypto.available():
            raise crypto.VaultUnavailable(
                "当前平台不支持保险箱加密；请移除出生/事件等敏感字段后再导入")
        cipher = crypto.protect(json.dumps(vault, ensure_ascii=False).encode("utf-8"))
    with atomic(conn):
        conn.execute(
            "INSERT INTO profiles(id, data_json, mode, completeness, updated_at) VALUES(1,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json, "
            "mode=excluded.mode, completeness=excluded.completeness, updated_at=excluded.updated_at",
            (payload, mode, score, now_iso()),
        )
        if vault:
            conn.execute(
                "INSERT INTO vault(id, cipher, fields_json, created_at, updated_at) VALUES(1,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET cipher=excluded.cipher, "
                "fields_json=excluded.fields_json, updated_at=excluded.updated_at",
                (cipher, json.dumps(fields, ensure_ascii=False), now_iso(), now_iso()),
            )
            _audit(conn, "seal", "profile import")
        else:
            had_vault = conn.execute("SELECT 1 FROM vault WHERE id=1").fetchone() is not None
            conn.execute("DELETE FROM vault WHERE id=1")
            if had_vault:
                _audit(conn, "clear", "profile without vault fields")
    return {"data": public, "mode": mode, "completeness": score,
            "has_vault": bool(vault), "vault_fields": fields}


def load_profile(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM profiles WHERE id=1").fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["data_json"])
    except (TypeError, ValueError):
        raise ValueError("保存的档案不是有效 JSON；请重新导入或清除档案") from None
    status = vault_status(conn)
    return {
        "data": data,
        "mode": row["mode"],
        "completeness": row["completeness"],
        "updated_at": row["updated_at"],
        "features": derive_features({"data": data}),
        "has_vault": status is not None,
        "vault_fields": status["fields"] if status else [],
    }


def unlock_vault(conn: sqlite3.Connection, reason: str) -> dict:
    """Decrypt the vault for one explicit purpose; always audit the unlock."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("解锁保险箱必须填写原因")
    reason = reason.strip()
    if len(reason) > 500:
        raise ValueError("解锁原因不能超过 500 字")
    row = conn.execute("SELECT cipher FROM vault WHERE id=1").fetchone()
    if row is None:
        raise ValueError("没有可解锁的保险箱")
    try:
        raw = crypto.unprotect(row["cipher"])
        vault = json.loads(raw.decode("utf-8"))
    except crypto.VaultUnavailable:
        raise
    except (UnicodeDecodeError, ValueError):
        raise ValueError("保险箱内容无法解析") from None
    with atomic(conn):
        _audit(conn, "unlock", reason)
    return vault


def list_vault_audit(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit 必须是 1 到 100 的整数")
    return conn.execute(
        "SELECT * FROM vault_audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def clear_profile(conn: sqlite3.Connection) -> int:
    with atomic(conn):
        row = conn.execute("SELECT 1 FROM profiles WHERE id=1").fetchone()
        conn.execute("DELETE FROM profiles")
        had_vault = conn.execute("SELECT 1 FROM vault WHERE id=1").fetchone() is not None
        conn.execute("DELETE FROM vault")
        if row is not None or had_vault:
            _audit(conn, "clear", "profile clear")
    return 0 if row is None else 1
