"""简历组装（M1-4 → M1-5 的 fan-in）。

本文件守的是 M4-7「不可编造」红线的**最低成本实现**：
事实字段（org / role / 时间区间）根本不在改写模型的输出契约里，
所以模型连改公司名的机会都没有 —— 这里把它锁成回归测试。
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agents.assembler import SECTION_LABELS, assemble_sections, build_entry, format_period
from app.schemas import RewrittenBullet, RewrittenExperience


def _experience(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": uuid4(),
        "kind": "project",
        "org": "简历优化器",
        "role": "独立开发",
        "start_date": date(2026, 9, 1),
        "end_date": None,
        "highlights": ["原始要点一", "原始要点二"],
    }
    base.update(overrides)
    return base


def _rewritten(*texts: str) -> RewrittenExperience:
    return RewrittenExperience(
        bullets=[
            RewrittenBullet(text=text, evidence=["原始要点一"] if text else []) for text in texts
        ]
    )


# -------------------------------------------------------------- format_period
def test_format_period_ongoing() -> None:
    assert format_period(date(2026, 9, 1), None) == "2026.09 – 至今"


def test_format_period_closed_range() -> None:
    assert format_period(date(2026, 3, 1), date(2026, 6, 30)) == "2026.03 – 2026.06"


def test_format_period_single_digit_month_is_padded() -> None:
    """`2026.3` 与 `2026.03` 在简历上观感差别很大，补齐两位是硬要求。"""
    assert format_period(date(2026, 3, 1), None) == "2026.03 – 至今"


def test_format_period_without_dates_is_none() -> None:
    assert format_period(None, None) is None


def test_format_period_missing_start() -> None:
    assert format_period(None, date(2026, 6, 1)) == "? – 2026.06"


# ---------------------------------------------------------------- build_entry
def test_build_entry_takes_facts_from_the_raw_experience() -> None:
    """事实字段一律来自原始条目 —— 模型输出里压根没有这些字段。"""
    entry = build_entry(_experience(), _rewritten("改写后的一条要点"))

    assert entry.org == "简历优化器"
    assert entry.role == "独立开发"
    assert entry.period == "2026.09 – 至今"
    assert entry.kind == "project"
    assert [bullet.text for bullet in entry.bullets] == ["改写后的一条要点"]


def test_build_entry_keeps_evidence_for_later_audit() -> None:
    entry = build_entry(_experience(), _rewritten("改写后的一条要点"))
    assert entry.bullets[0].evidence == ["原始要点一"]


def test_build_entry_trims_bullet_text() -> None:
    entry = build_entry(_experience(), _rewritten("  前后有空白  "))
    assert entry.bullets[0].text == "前后有空白"


def test_build_entry_drops_whitespace_only_bullets() -> None:
    entry = build_entry(_experience(), _rewritten("   ", "有效要点"))
    assert [bullet.text for bullet in entry.bullets] == ["有效要点"]


def test_build_entry_falls_back_to_raw_highlights() -> None:
    """改写一条都没产出时，宁可展示未经润色但真实的内容，也不给用户一段空白经历。"""
    entry = build_entry(_experience(), RewrittenExperience(bullets=[]))

    assert [bullet.text for bullet in entry.bullets] == ["原始要点一", "原始要点二"]
    assert entry.bullets[0].evidence == []


def test_build_entry_without_bullets_or_highlights() -> None:
    entry = build_entry(_experience(highlights=[]), RewrittenExperience(bullets=[]))
    assert entry.bullets == []


# ----------------------------------------------------------- assemble_sections
def test_assemble_sections_orders_internship_project_campus() -> None:
    pairs = [
        (_experience(kind="campus", org="协会"), _rewritten("校园要点")),
        (_experience(kind="project", org="项目"), _rewritten("项目要点")),
        (_experience(kind="internship", org="公司"), _rewritten("实习要点")),
    ]

    sections = assemble_sections(pairs)

    assert [section.title for section in sections] == [
        SECTION_LABELS["internship"],
        SECTION_LABELS["project"],
        SECTION_LABELS["campus"],
    ]
    assert [section.entries[0].org for section in sections] == ["公司", "项目", "协会"]


def test_assemble_sections_skips_empty_kinds() -> None:
    sections = assemble_sections([(_experience(kind="project"), _rewritten("要点"))])
    assert [section.title for section in sections] == [SECTION_LABELS["project"]]


def test_assemble_sections_preserves_input_order_within_a_kind() -> None:
    pairs = [
        (_experience(kind="project", org="第一个"), _rewritten("a")),
        (_experience(kind="project", org="第二个"), _rewritten("b")),
    ]
    sections = assemble_sections(pairs)
    assert [entry.org for entry in sections[0].entries] == ["第一个", "第二个"]


def test_build_entry_rejects_kind_outside_the_declared_set() -> None:
    """非法分类被两层挡住：`ResumeEntry.kind` 是 Literal，migration 侧还有 check 约束。

    顺带记一笔现状：`assemble_sections` 里那段「兜底未知 kind」的分支因此**实际不可达**，
    属防御性代码。若是哪天把 Literal 放宽成 str，这里会立刻变成一条真路径。
    """
    with pytest.raises(ValidationError):
        build_entry(_experience(kind="award"), _rewritten("获奖"))
