from datetime import date, timedelta

import pytest

from causal_ledger.db import connect
from causal_ledger.provenance import (
    add_prediction,
    ensure_source,
    import_extraction,
    independent_origins,
    recompute_claim,
    source_reliability,
)
from causal_ledger.settle import settle


@pytest.fixture
def conn():
    return connect(":memory:")


def extraction(reporter, claims):
    return {"reporter": reporter, "claims": claims}


def claim(text, key, origin=None, layer="manifestation", hard_signal=None, predictions=None, domain="tech"):
    return {
        "text": text,
        "canonical_key": key,
        "domain": domain,
        "layer": layer,
        "origin": origin,
        "quote": text,
        "hard_signal": hard_signal,
        "predictions": predictions or [],
    }


DUE = (date.today() + timedelta(days=7)).isoformat()
PRED = [{"text": "一周内官网出现公告", "due_date": DUE, "check_hint": "查官网公告页", "p": 0.7}]


def test_same_claim_from_two_independent_origins_merges_and_counts_two(conn):
    import_extraction(conn, extraction(
        {"name": "账号A", "type": "anonymous"},
        [claim("X公司将裁员30%", "X公司|裁员|30%", origin={"name": "路透社", "type": "institutional_media"}, predictions=PRED)],
    ), title="d1")
    import_extraction(conn, extraction(
        {"name": "账号B", "type": "anonymous"},
        [claim("X公司裁员三成", "X公司|裁员|30%", origin={"name": "X公司公告", "type": "official"})],
    ), title="d2")

    rows = conn.execute("SELECT * FROM claims").fetchall()
    assert len(rows) == 1
    c = rows[0]
    assert independent_origins(conn, c["id"]) == 2
    assert conn.execute("SELECT COUNT(*) FROM chains WHERE claim_id=?", (c["id"],)).fetchone()[0] == 2
    assert c["credibility"] > 0.6


def test_two_reporters_same_wire_count_as_one(conn):
    for name in ("账号A", "账号B"):
        import_extraction(conn, extraction(
            {"name": name, "type": "anonymous"},
            [claim("Y公司发布新品", "Y公司|发布|新品", origin={"name": "新华社", "type": "institutional_media"}, predictions=PRED)],
        ), title=name)
    c = conn.execute("SELECT * FROM claims").fetchone()
    assert independent_origins(conn, c["id"]) == 1
    assert c["credibility"] <= 0.6


def test_reporter_is_origin_when_no_citation(conn):
    import_extraction(conn, extraction(
        {"name": "某博主", "type": "analysis"},
        [claim("Z行业明年会洗牌", "Z行业|洗牌|2027", layer="intent")],
    ), title="d")
    ch = conn.execute("SELECT * FROM chains").fetchone()
    assert ch["reporter_source_id"] == ch["origin_source_id"]
    c = conn.execute("SELECT * FROM claims").fetchone()
    assert c["credibility"] <= 0.5


def test_hit_settlement_does_not_grade_source_or_parent(conn):
    import_extraction(conn, extraction(
        {"name": "官方账号", "type": "official"},
        [claim("W公司将于下周发布财报", "W公司|发布|财报", layer="plan", predictions=PRED)],
    ), title="d")
    c = conn.execute("SELECT * FROM claims").fetchone()
    src = ensure_source(conn, "官方账号", "official")
    before_rel = source_reliability(conn, src, "tech")
    before_cred = c["credibility"]
    pred = conn.execute("SELECT * FROM predictions").fetchone()

    result = settle(conn, pred["id"], "hit", note="官网已发布")

    assert result["brier"] == pytest.approx((pred["p"] - 1.0) ** 2)
    assert pred["p"] == 0.7
    assert source_reliability(conn, src, "tech") == before_rel
    after = conn.execute("SELECT credibility FROM claims WHERE id=?", (c["id"],)).fetchone()[0]
    assert after == before_cred
    stats = conn.execute("SELECT alpha, beta, n FROM source_domain_stats WHERE source_id=? AND domain='tech'", (src,)).fetchone()
    assert stats["n"] == 0 and stats["alpha"] == pytest.approx(2.6)


def test_miss_settlement_does_not_grade_source_or_parent(conn):
    import_extraction(conn, extraction(
        {"name": "官方账号", "type": "official"},
        [claim("V公司将于下周发布财报", "V公司|发布|财报", layer="plan", predictions=PRED)],
    ), title="d")
    c = conn.execute("SELECT * FROM claims").fetchone()
    src = ensure_source(conn, "官方账号", "official")
    before_rel = source_reliability(conn, src, "tech")
    pred = conn.execute("SELECT * FROM predictions").fetchone()

    conn.execute("UPDATE predictions SET due_date=? WHERE id=?",
                 ((date.today() - timedelta(days=1)).isoformat(), pred["id"]))
    conn.commit()
    result = settle(conn, pred["id"], "miss", note="虚构演练：截至昨日未出现")

    assert result["brier"] == pytest.approx(0.49)
    assert source_reliability(conn, src, "tech") == before_rel
    after = conn.execute("SELECT credibility FROM claims WHERE id=?", (c["id"],)).fetchone()[0]
    assert after == c["credibility"]


def test_void_changes_nothing_but_status(conn):
    import_extraction(conn, extraction(
        {"name": "官方账号", "type": "official"},
        [claim("U公司将于下周发布财报", "U公司|发布|财报", layer="plan", predictions=PRED)],
    ), title="d")
    c = conn.execute("SELECT * FROM claims").fetchone()
    pred = conn.execute("SELECT * FROM predictions").fetchone()
    settle(conn, pred["id"], "void", note="问题本身无效")
    after = conn.execute("SELECT credibility FROM claims WHERE id=?", (c["id"],)).fetchone()[0]
    assert after == c["credibility"]
    assert conn.execute("SELECT status FROM predictions WHERE id=?", (pred["id"],)).fetchone()[0] == "void"


def test_settle_twice_rejected(conn):
    import_extraction(conn, extraction(
        {"name": "官方账号", "type": "official"},
        [claim("T公司将于下周发布财报", "T公司|发布|财报", layer="plan", predictions=PRED)],
    ), title="d")
    pred = conn.execute("SELECT * FROM predictions").fetchone()
    settle(conn, pred["id"], "hit", note="虚构演练：已观察到")
    with pytest.raises(ValueError):
        settle(conn, pred["id"], "miss", note="重复结算")


def test_add_prediction_requires_check_hint_without_changing_score(conn):
    import_extraction(conn, extraction(
        {"name": "官方账号", "type": "official"},
        [claim("S公司计划上市", "S公司|上市|2027", layer="plan")],
    ), title="d")
    c = conn.execute("SELECT * FROM claims").fetchone()
    assert c["credibility"] <= 0.65
    with pytest.raises(ValueError):
        add_prediction(conn, c["id"], "半年内递交招股书", "2027-05-01", "")
    add_prediction(conn, c["id"], "半年内递交招股书", "2027-05-01", "查交易所披露")
    after = conn.execute("SELECT credibility FROM claims WHERE id=?", (c["id"],)).fetchone()[0]
    assert after == c["credibility"]


def test_recompute_is_idempotent(conn):
    import_extraction(conn, extraction(
        {"name": "官方账号", "type": "official"},
        [claim("R公司将于下周发布财报", "R公司|发布|财报", layer="plan", predictions=PRED)],
    ), title="d")
    c = conn.execute("SELECT * FROM claims").fetchone()
    a = recompute_claim(conn, c["id"], reason="test")
    b = recompute_claim(conn, c["id"], reason="test")
    assert a == b
