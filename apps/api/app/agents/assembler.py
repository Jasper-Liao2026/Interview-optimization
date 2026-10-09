"""简历组装（fan-in 的雏形，M4-4 会把它升级成图里的一个节点）。

**本文件最重要的一条规则**：事实字段（org / role / 时间区间）**只从原始经历条目取**，
绝不采用模型输出。模型只负责 `bullets` 的文本。

这条规则是 M4-7「不可编造」红线的最低成本实现 —— 与其事后校验模型有没有改公司名，
不如根本不把公司名交给模型去写。成本为零，效果是确定的。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.schemas import (
    ResumeBullet,
    ResumeEntry,
    ResumeSection,
    RewrittenExperience,
)

# 分区顺序：实习 > 项目 > 校园。学生简历里实习经历的权重通常最高。
SECTION_ORDER: tuple[str, ...] = ("internship", "project", "campus")
SECTION_LABELS: dict[str, str] = {
    "internship": "实习经历",
    "project": "项目经历",
    "campus": "校园经历",
}


def format_period(start: date | None, end: date | None) -> str | None:
    """把时间区间格式化成简历上的写法：`2026.09 – 至今`。"""
    if start is None and end is None:
        return None
    left = f"{start.year}.{start.month:02d}" if start is not None else "?"
    right = f"{end.year}.{end.month:02d}" if end is not None else "至今"
    return f"{left} – {right}"


def build_entry(experience: dict[str, Any], rewritten: RewrittenExperience) -> ResumeEntry:
    """原始条目 + 改写结果 → 一段简历经历。"""
    bullets = [
        ResumeBullet(text=bullet.text.strip(), evidence=list(bullet.evidence))
        for bullet in rewritten.bullets
        if bullet.text.strip()
    ]
    # 改写一条都没产出时，退回原始量化要点：
    # 宁可展示未经润色但真实的内容，也不要给用户一段空白经历。
    if not bullets:
        bullets = [
            ResumeBullet(text=str(item).strip(), evidence=[])
            for item in (experience.get("highlights") or [])
            if str(item).strip()
        ]

    return ResumeEntry(
        experience_id=experience.get("id"),
        kind=experience["kind"],
        org=str(experience["org"]),
        role=str(experience["role"]),
        period=format_period(experience.get("start_date"), experience.get("end_date")),
        bullets=bullets,
    )


def assemble_sections(
    pairs: list[tuple[dict[str, Any], RewrittenExperience]],
) -> list[ResumeSection]:
    """按 kind 分组、按 SECTION_ORDER 排序，组内保持传入顺序。"""
    grouped: dict[str, list[ResumeEntry]] = {kind: [] for kind in SECTION_ORDER}
    for experience, rewritten in pairs:
        kind = str(experience["kind"])
        grouped.setdefault(kind, []).append(build_entry(experience, rewritten))

    sections: list[ResumeSection] = []
    for kind in SECTION_ORDER:
        entries = grouped.get(kind) or []
        if entries:
            sections.append(ResumeSection(title=SECTION_LABELS[kind], entries=entries))

    # 兜底：出现 SECTION_ORDER 之外的 kind（migration 的 check 约束理论上挡住了，
    # 但这里不能假设两边永远同步），单独成区而不是静默丢弃
    for kind, entries in grouped.items():
        if kind not in SECTION_ORDER and entries:
            sections.append(ResumeSection(title=kind, entries=entries))

    return sections
