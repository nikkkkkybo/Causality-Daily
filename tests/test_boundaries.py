from datetime import date, timedelta
import sqlite3

import pytest

from causal_ledger.db import connect
from causal_ledger.provenance import add_prediction, import_extraction
from causal_ledger.settle import settle


DUE = (date.today() + timedelta(days=7)).isoformat()


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


def data(hint=None):
    return {"reporter": {"name": "虚构测试", "type": "official"}, "claims": [{
        "text": "虚构公司计划发布新品", "canonical_key": "虚构公司|发布|新品",
        "layer": "plan", "hard_signal": hint,
    }]}


def test_verification_hint_is_not_evidence(conn):
    value = data()
    value["claims"][0]["layer"] = "manifestation"
    import_extraction(conn, value)
    before = conn.execute("SELECT credibility FROM claims").fetchone()[0]
    value["claims"][0]["hard_signal"] = "待查公告编号，尚未获取"
    import_extraction(conn, value)
    assert conn.execute("SELECT credibility FROM claims").fetchone()[0] == before


def test_word_boundaries_prevent_merging_opposite_claims(conn):
    import_extraction(conn, {"claims": [{"text": "The package is now here", "layer": "manifestation"}]})
    import_extraction(conn, {"reporter": {"name": "Another", "type": "official"},
                            "claims": [{"text": "The package is nowhere", "layer": "manifestation"}]})
    assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 2
    assert conn.execute("SELECT credibility FROM claims WHERE id=1").fetchone()[0] == 0.3


def test_source_type_upgrade_refreshes_all_affected_claims(conn):
    for name in ("first", "second"):
        import_extraction(conn, {"reporter": {"name": "S", "type": "unknown"},
                                "claims": [{"text": name, "layer": "manifestation"}]})
    import_extraction(conn, {"reporter": {"name": "S", "type": "official"},
                            "claims": [{"text": "first", "layer": "manifestation"}]})
    assert [r[0] for r in conn.execute("SELECT credibility FROM claims ORDER BY id")] == [0.6, 0.6]
    assert conn.execute("SELECT alpha FROM source_domain_stats").fetchone()[0] == pytest.approx(2.6)


def test_unspecified_probability_remains_unknown(conn):
    import_extraction(conn, data())
    pid = add_prediction(conn, 1, "官网出现新品页", DUE, "查官网")
    assert conn.execute("SELECT p FROM predictions").fetchone()[0] is None
    result = settle(conn, pid, "hit", note="虚构演练：已出现")
    assert result["brier"] is None
    assert conn.execute("SELECT status FROM predictions").fetchone()[0] == "hit"


@pytest.mark.parametrize("initial", [None, 0.7])
def test_probability_conflict_is_rejected_without_changing_frozen_value(conn, initial):
    import_extraction(conn, data())
    pid = add_prediction(conn, 1, "出现新品页", DUE, "查官网", p=initial)
    with pytest.raises(ValueError, match="冻结"):
        add_prediction(conn, 1, "出现新品页", DUE, "查官网", p=0.9)
    assert add_prediction(conn, 1, "出现新品页", DUE, "查官网") == pid
    assert conn.execute("SELECT p FROM predictions").fetchone()[0] == initial


def test_different_deadlines_do_not_merge(conn):
    import_extraction(conn, data())
    first = add_prediction(conn, 1, "出现新品页", DUE, "查官网")
    second = add_prediction(conn, 1, "出现新品页", (date.today() + timedelta(days=14)).isoformat(), "查官网")
    assert first != second


@pytest.mark.parametrize("p", [-0.1, 1.1, float("nan"), float("inf"), True, "0.5"])
def test_invalid_probability_rejected(conn, p):
    import_extraction(conn, data())
    with pytest.raises(ValueError, match="概率"):
        add_prediction(conn, 1, "出现新品页", DUE, "查官网", p=p)
    assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0


@pytest.mark.parametrize("text,due,hint", [
    ("", DUE, "官网"), ("出现新品页", "20261130", "官网"),
    ("出现新品页", "2026-02-30", "官网"), ("出现新品页", "2020-01-01", "官网"),
    ("出现新品页", DUE, ""),
])
def test_invalid_prediction_rejected(conn, text, due, hint):
    import_extraction(conn, data())
    with pytest.raises(ValueError):
        add_prediction(conn, 1, text, due, hint)


def test_missing_claim_rejected(conn):
    with pytest.raises(ValueError, match="不存在"):
        add_prediction(conn, 999, "出现新品页", DUE, "官网")


def test_settlement_requires_note(conn):
    import_extraction(conn, data())
    pid = add_prediction(conn, 1, "出现新品页", DUE, "查官网")
    with pytest.raises(ValueError, match="依据"):
        settle(conn, pid, "hit")
    assert conn.execute("SELECT status FROM predictions").fetchone()[0] == "pending"


def test_early_miss_rejected(conn):
    import_extraction(conn, data())
    pid = add_prediction(conn, 1, "出现新品页", DUE, "查官网")
    with pytest.raises(ValueError, match="截止"):
        settle(conn, pid, "miss", note="目前没有看到")
    assert conn.execute("SELECT status FROM predictions").fetchone()[0] == "pending"


@pytest.mark.parametrize("bad", [
    [], {"claims": "bad"}, {"reporter": "bad", "claims": []},
    {"claims": [{"text": ""}]}, {"claims": [{"text": "x", "origin": "bad"}]},
    {"claims": [{"text": "x", "hard_signal": {"verified": True}}]},
    {"claims": [{"text": "x", "predictions": ["bad"]}]},
])
def test_malformed_import_leaves_no_rows(conn, bad):
    with pytest.raises(ValueError):
        import_extraction(conn, bad)
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0


def test_import_rolls_back_on_later_database_failure(conn):
    conn.execute("CREATE TRIGGER reject_second BEFORE INSERT ON claims "
                 "WHEN NEW.text='second' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    value = data()
    value["claims"][0]["predictions"] = [{"text": "出现新品页", "due_date": DUE, "check_hint": "查官网"}]
    value["claims"].append({"text": "second"})
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        import_extraction(conn, value)
    for table in ("claims", "documents", "sources", "predictions", "chains", "credibility_log"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_invalid_forecast_reported_but_claim_kept(conn):
    value = data()
    value["claims"][0]["predictions"] = [{"text": "bad", "due_date": "n/a", "check_hint": "查官网"}]
    summary = import_extraction(conn, value)
    assert len(summary["claims"]) == 1
    assert len(summary["skipped_predictions"]) == 1
    assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0
