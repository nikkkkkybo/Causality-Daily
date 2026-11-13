"""Cause-source registry: the foundation of personal bearing.

This is a coverage map, not a claim that every traditional system is implemented.
`implemented` adapters produce signals; `simplified` are deliberately bounded;
`plugin` accepts normalized external signals; `gap` is visible and not faked.
"""
from __future__ import annotations

FAMILIES = {
    "time": {"label": "时间/历法", "role": "节奏、时窗、周期"},
    "traditional": {"label": "传统时间系统", "role": "时窗、活动倾向、周期关系"},
    "world": {"label": "世界信息", "role": "新闻、政策、价格等外部信号"},
    "position": {"label": "位置/主线", "role": "领域权重、目标、角色"},
    "structure": {"label": "结构/约束", "role": "硬约束、行业/组织位置"},
    "psych": {"label": "心身", "role": "负荷、改变阶段、注意力"},
    "relation": {"label": "关系", "role": "家庭、同事、合作方"},
    "action": {"label": "行动", "role": "决策、承诺、拖延"},
    "lens": {"label": "解释/透镜", "role": "半黑盒传统系统接入位"},
}

SOURCES = [
    # time / calendar
    {"id": "time.weekday", "family": "time", "label": "星期节奏", "status": "implemented",
     "adapter": "temporal_signals", "provenance": "现代工作日/周节奏"},
    {"id": "time.season", "family": "time", "label": "季节", "status": "implemented",
     "adapter": "temporal_signals", "provenance": "天文/节气近似"},
    {"id": "time.month_edge", "family": "time", "label": "月初/月末", "status": "implemented",
     "adapter": "temporal_signals", "provenance": "自然月结算节奏"},
    # traditional
    {"id": "traditional.jianchu", "family": "traditional", "label": "建除十二神", "status": "simplified",
     "adapter": "traditional_signals", "provenance": "日干支 + 节气近似 + 建除表"},
    {"id": "traditional.day_master", "family": "traditional", "label": "日主关系", "status": "simplified",
     "adapter": "traditional_signals", "provenance": "出生日干 + 当日日干五行"},
    {"id": "traditional.liuyao", "family": "traditional", "label": "六爻/小六壬", "status": "plugin",
     "adapter": "external_lens", "provenance": "外部归一化 JSON"},
    {"id": "traditional.ziwei", "family": "traditional", "label": "紫微斗数", "status": "gap",
     "adapter": "external_lens", "provenance": "需排盘数据"},
    {"id": "traditional.bazi_full", "family": "traditional", "label": "完整四柱/大运", "status": "gap",
     "adapter": "external_lens", "provenance": "需出生时刻/节气"},
    {"id": "traditional.rokuyo", "family": "traditional", "label": "六曜", "status": "plugin",
     "adapter": "external_lens", "provenance": "需旧历/六曜数据"},
    {"id": "traditional.panchang", "family": "traditional", "label": "Panchang/Muhurta", "status": "plugin",
     "adapter": "external_lens", "provenance": "需天文/历法数据"},
    {"id": "traditional.astro", "family": "traditional", "label": "占星行运/择时", "status": "plugin",
     "adapter": "external_lens", "provenance": "需星历数据"},
    # world / position / structure / psych / relation
    {"id": "world.news", "family": "world", "label": "公开信息", "status": "implemented",
     "adapter": "news_signals", "provenance": "RSS/Atom 条目"},
    {"id": "position.domains", "family": "position", "label": "领域权重", "status": "implemented",
     "adapter": "profile_signals", "provenance": "档案"},
    {"id": "position.goal", "family": "position", "label": "主目标", "status": "implemented",
     "adapter": "profile_signals", "provenance": "档案"},
    {"id": "structure.constraints", "family": "structure", "label": "硬约束", "status": "implemented",
     "adapter": "profile_signals", "provenance": "档案"},
    {"id": "psych.load", "family": "psych", "label": "负荷", "status": "implemented",
     "adapter": "profile_signals", "provenance": "档案自评"},
    {"id": "relation.relations", "family": "relation", "label": "关键关系", "status": "implemented",
     "adapter": "profile_signals", "provenance": "档案"},
    {"id": "action.notes", "family": "action", "label": "备注/承诺", "status": "implemented",
     "adapter": "notes_signals", "provenance": "末尾备注（最新 3 条）"},
]


def coverage() -> dict:
    counts = {}
    for item in SOURCES:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "families": len(FAMILIES),
        "sources": len(SOURCES),
        "counts": counts,
        "implemented": [item for item in SOURCES if item["status"] == "implemented"],
        "gaps": [item for item in SOURCES if item["status"] == "gap"],
    }


def by_family() -> dict:
    out = {key: [] for key in FAMILIES}
    for item in SOURCES:
        out[item["family"]].append(item)
    return out


def format_coverage() -> str:
    lines = ["承因因源覆盖（status: implemented / simplified / plugin / gap）"]
    for family, rows in by_family().items():
        label = FAMILIES[family]["label"]
        lines.append(f"\n[{label}]")
        for row in rows:
            lines.append(f"  - {row['label']:<16} {row['status']:<12} {row['id']}")
    info = coverage()
    lines.append(f"\n合计：{info['sources']} 个因源；状态 {info['counts']}。")
    return "\n".join(lines)
