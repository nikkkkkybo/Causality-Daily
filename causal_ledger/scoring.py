from __future__ import annotations

from dataclasses import dataclass

SOURCE_TYPE_PRIOR = {
    "primary_data": 0.80,
    "official": 0.65,
    "participant": 0.60,
    "institutional_media": 0.55,
    "analysis": 0.45,
    "anonymous": 0.25,
    "unknown": 0.30,
}

LAYER_CAP = {
    "intent": 0.50,
    "plan": 0.65,
    "formation": 0.80,
    "manifestation": 1.00,
}

PRIOR_PSEUDO_COUNT = 4.0
SINGLE_WITNESS_CAP = 0.60
HEURISTIC_CAP = 0.85


@dataclass(frozen=True)
class ChainEvidence:
    origin_source_id: int
    reliability: float


def prior_for_type(source_type: str) -> float:
    return SOURCE_TYPE_PRIOR.get(source_type, SOURCE_TYPE_PRIOR["unknown"])


def prior_pseudo_counts(source_type: str) -> tuple[float, float]:
    p = prior_for_type(source_type)
    return p * PRIOR_PSEUDO_COUNT, (1 - p) * PRIOR_PSEUDO_COUNT


def reliability_mean(alpha: float, beta: float) -> float:
    return alpha / (alpha + beta)


def normalize_layer(layer: str) -> str:
    return layer if layer in LAYER_CAP else "intent"


def credibility(
    chains: list[ChainEvidence],
    layer: str,
) -> tuple[float, list[str]]:
    trace: list[str] = []
    layer = normalize_layer(layer)

    best_by_origin: dict[int, float] = {}
    for ch in chains:
        best_by_origin[ch.origin_source_id] = max(best_by_origin.get(ch.origin_source_id, 0.0), ch.reliability)
    witnesses = list(best_by_origin.values())
    trace.append("实验性启发式评分，未经校准，不是真实概率；核查线索及预测结果不加分")
    trace.append(f"去重来源={len(witnesses)} 先验权重={[round(w, 3) for w in witnesses]}；独立性未核实")

    if not witnesses:
        trace.append("无任何证人 → 0")
        return 0.0, trace

    c = 1.0
    for w in witnesses:
        c *= 1.0 - w
    c = 1.0 - c
    trace.append(f"噪声或合成 → {c:.3f}")

    if len(witnesses) < 2 and c > SINGLE_WITNESS_CAP:
        c = SINGLE_WITNESS_CAP
        trace.append(f"单来源启发式封顶 {SINGLE_WITNESS_CAP}")

    cap = LAYER_CAP[layer]
    if c > cap:
        c = cap
        trace.append(f"陈述阶段 {layer} 启发式封顶 {cap}")

    if c > HEURISTIC_CAP:
        c = HEURISTIC_CAP
        trace.append(f"未经外部核验，启发式封顶 {HEURISTIC_CAP}")

    return round(c, 3), trace
