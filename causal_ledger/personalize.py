"""Personal causal ranking: a deliberately small, explainable prior.

No profile -> the caller should run the general line. With a profile, this
module maps feed items to life domains with keyword heuristics, then re-ranks
them by domain weight and adds one plain-language "why it relates to you" line.
It is a heuristic, not a causal claim; every line keeps that boundary.
"""
from __future__ import annotations

from .profile import DOMAIN_LABELS

DOMAIN_KEYWORDS = {
    "career": ("事业", "工作", "职业", "公司", "招聘", "裁员", "创业", "产品", "技术",
               "工程", "行业", "管理", "组织", "职位", "升职", "同事", "老板",
               "work", "career", "job", "hiring", "layoff", "company", "product",
               "tech", "industry", "manager", "startup"),
    "wealth": ("钱", "收入", "工资", "投资", "股票", "基金", "房价", "经济",
               "消费", "价格", "财务", "资产", "负债", "市场", "税",
               "money", "income", "salary", "invest", "stock", "housing",
               "economy", "price", "finance", "asset", "debt", "market"),
    "health": ("健康", "医疗", "疾病", "睡眠", "运动", "疫苗", "疫情", "医院",
               "身体", "心理", "药", "医生", "体检",
               "health", "medical", "disease", "sleep", "exercise", "vaccine",
               "hospital", "doctor", "mental"),
    "relationship": ("恋爱", "婚姻", "伴侣", "亲密", "情感", "约会", "分手",
                     "结婚", "对象", "love", "marriage", "partner", "dating", "relationship"),
    "family": ("家庭", "父母", "孩子", "子女", "家人", "养老", "亲戚", "长辈",
               "family", "parents", "children", "child", "eldercare"),
    "learning": ("学习", "教育", "课程", "研究", "论文", "知识", "技能", "读书",
                 "考试", "学校", "专业", "learn", "education", "course", "research",
                 "paper", "skill", "school", "study"),
    "social": ("社交", "朋友", "社群", "社区", "人脉", "网络", "圈层", "同行",
               "social", "friend", "community", "network", "peer"),
    "inner": ("意义", "焦虑", "情绪", "精神", "信仰", "自我", "内心", "价值",
              "存在", "孤独", "meaning", "anxiety", "emotion", "spirit", "faith",
              "self", "value", "loneliness"),
}


def _text(item: dict) -> str:
    return ((item.get("title") or "") + " " + (item.get("summary") or "")).casefold()


def classify_domains(text: str) -> dict[str, float]:
    """Return a 0/1 hit map. Multiple keywords still count once per domain."""
    folded = (text or "").casefold()
    hits = {}
    for domain, words in DOMAIN_KEYWORDS.items():
        if any(word.casefold() in folded for word in words):
            hits[domain] = 1.0
    return hits


def _personal_score(item: dict, features: dict) -> tuple[float, dict[str, float]]:
    hits = classify_domains(_text(item))
    weights = features.get("domains") or {}
    score = sum(float(weights.get(domain, 0.0)) for domain in hits)
    if score > 0:
        score = min(1.0, score / 2.0)  # two strong domains already saturate
    return score, hits


def _why(item: dict, features: dict, hits: dict[str, float]) -> str:
    weights = features.get("domains") or {}
    ranked = [domain for domain in hits if float(weights.get(domain, 0.0)) > 0]
    ranked.sort(key=lambda domain: float(weights.get(domain, 0.0)), reverse=True)
    if not ranked:
        return "暂无明显领域匹配（按通用链排序）"
    labels = "、".join(f"{DOMAIN_LABELS[d]}（{float(weights.get(d, 0.0)):.1f}）"
                      for d in ranked[:2])
    return f"落在「{labels}」；按你的领域权重排序（启发式，不是因果证明）。"


def _segment(features: dict) -> str:
    role = features.get("role") or ""
    constraints = features.get("constraints") or []
    force = features.get("force") or ""
    parts = []
    if role:
        parts.append(f"你的位置：{role}")
    if force:
        parts.append(f"这件事对你更像：{force}")
    if constraints:
        parts.append("注意约束：" + "、".join(str(item) for item in constraints[:3]))
    return "；".join(parts)


def annotate(item: dict, features: dict, mode: str) -> dict:
    if mode not in ("light", "individual"):
        return dict(item)
    score, hits = _personal_score(item, features)
    entry = dict(item)
    entry["personal_relevance"] = round(score, 3)
    if score > 0:
        entry["personal_why"] = _why(item, features, hits)
        if mode == "individual":
            entry["personal_segment"] = _segment(features)
    return entry


def rank_items(items: list[dict], features: dict, mode: str) -> list[dict]:
    if mode not in ("light", "individual"):
        return list(items)
    annotated = [annotate(item, features, mode) for item in items]
    annotated.sort(key=lambda item: item.get("personal_relevance", 0.0), reverse=True)
    return annotated


def lines_for(item: dict, mode: str) -> list[str]:
    """Plain-text lines for the text brief; empty for the general line."""
    if mode not in ("light", "individual"):
        return []
    out = []
    why = item.get("personal_why")
    if why:
        out.append(f"与你有关：{why}")
    segment = item.get("personal_segment")
    if mode == "individual" and segment:
        out.append(f"你在哪一段：{segment}")
    return out
