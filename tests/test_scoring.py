from causal_ledger.scoring import (
    ChainEvidence,
    credibility,
    prior_for_type,
    reliability_mean,
)


def ev(origin, r):
    return ChainEvidence(origin_source_id=origin, reliability=r)


def test_no_chains_gives_zero():
    c, trace = credibility([], layer="manifestation")
    assert c == 0.0


def test_single_chain_capped_by_two_witness_rule():
    c, trace = credibility([ev(1, 0.9)], layer="manifestation")
    assert c == 0.6
    assert any("单来源" in t for t in trace)


def test_two_independent_chains_exceed_single_cap():
    c, _ = credibility([ev(1, 0.6), ev(2, 0.6)], layer="manifestation")
    assert abs(c - 0.84) < 1e-6


def test_same_origin_counts_once():
    c, trace = credibility([ev(1, 0.6), ev(1, 0.7)], layer="manifestation")
    assert c == 0.6
    assert any("去重来源=1" in t for t in trace)


def test_single_source_score_uses_only_source_weight():
    c, trace = credibility([ev(1, 0.6)], layer="manifestation")
    assert c == 0.6
    assert not any("独立证人" in t for t in trace)


def test_layer_caps():
    chains = [ev(1, 0.9), ev(2, 0.9), ev(3, 0.9)]
    for layer, cap in (("intent", 0.5), ("plan", 0.65), ("formation", 0.8)):
        c, _ = credibility(chains, layer=layer)
        assert c == cap


def test_no_hard_signal_caps_at_085():
    c, _ = credibility([ev(1, 0.95), ev(2, 0.95)], layer="manifestation")
    assert c == 0.85


def test_plan_score_capped_at_stage_limit():
    c, trace = credibility([ev(1, 0.9), ev(2, 0.9)], layer="plan")
    assert c == 0.65


def test_manifestation_without_predictions_not_capped_for_falsifiability():
    c, _ = credibility([ev(1, 0.9), ev(2, 0.9)], layer="manifestation")
    assert c > 0.35


def test_score_explicitly_disclaims_probability_and_independence():
    score, trace = credibility([ev(1, 0.6), ev(2, 0.6)], layer="manifestation")
    assert score == 0.84
    assert any("不是真实概率" in t for t in trace)
    assert any("独立性未核实" in t for t in trace)


def test_reliability_mean_and_prior():
    assert prior_for_type("official") == 0.65
    assert prior_for_type("nonsense") == prior_for_type("unknown")
    assert abs(reliability_mean(2.6, 1.4) - 0.65) < 1e-9
