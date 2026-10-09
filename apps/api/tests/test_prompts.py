"""改写 prompt 的契约（M2-1 起）。

为什么要给 prompt 写测试：prompt 是**业务逻辑**，改动会让输出质量悄悄变化。
M2-1 把「量化结果」从 highlights 里拆成单独一块，模型从此有了一个明确的
「唯一允许出现的数字来源」—— 这个区块一旦被谁顺手删掉，M4-7 的数字校验就没有参照集合了。
"""

from __future__ import annotations

from app.agents.prompts import build_rewrite_prompt
from app.schemas import JobProfile


def _profile() -> JobProfile:
    return JobProfile(
        title="后端开发工程师",
        seniority="校招",
        required_skills=["Python", "FastAPI"],
        nice_to_have=["Docker"],
        business_domain="AI 应用",
        keywords=["agent 编排"],
        implicit_preferences=["能独立交付"],
        responsibilities=[],
    )


def _prompt(**overrides: object) -> str:
    kwargs: dict[str, object] = {
        "kind": "project",
        "org": "简历优化器",
        "role": "独立开发",
        "raw_description": "独立实现一个批量生成简历的工具。",
        "skill_tags": ["Python"],
        "highlights": ["接入 Langfuse 观测"],
        "metrics": [
            {"name": "单元测试", "value": "24 → 82 条", "context": "ruff + pytest 全绿"},
            {"name": "CI 时长", "value": "3min → 55s", "context": None},
        ],
        "max_chars": 6000,
    }
    kwargs.update(overrides)
    return build_rewrite_prompt(_profile(), **kwargs)  # type: ignore[arg-type]


def test_prompt_separates_qualitative_and_quantified_material() -> None:
    prompt = _prompt()

    assert "定性要点：\n  - 接入 Langfuse 观测" in prompt
    assert "量化结果（唯一允许出现的数字来源）：" in prompt


def test_metrics_are_rendered_with_context() -> None:
    prompt = _prompt()

    assert "单元测试：24 → 82 条（口径：ruff + pytest 全绿）" in prompt
    # 没有口径的指标只渲染指标名与数值，不留下一个空括号
    assert "CI 时长：3min → 55s" in prompt
    assert "CI 时长：3min → 55s（" not in prompt


def test_metric_text_is_never_reformatted() -> None:
    """数字写法必须原样保留。

    一旦 prompt 组装阶段把「24 → 82 条」规整成别的东西，
    M4-7 的「改写里的数字必须能在 metrics 里找到」就失去了可比对的参照物。
    """
    prompt = _prompt(metrics=[{"name": "覆盖模块", "value": "12 个", "context": None}])
    assert "覆盖模块：12 个" in prompt


def test_empty_metrics_block_is_explicitly_empty() -> None:
    """空块要写成「（无）」而不是消失：prompt 里少一整块时，模型更容易自己编数字。"""
    prompt = _prompt(metrics=[])

    assert "量化结果（唯一允许出现的数字来源）：（无）" in prompt
    assert "定性要点：\n  - 接入 Langfuse 观测" in prompt


def test_metrics_default_to_empty_when_not_passed() -> None:
    """`metrics` 有默认值，M1 时代只传 highlights 的调用点不会炸。"""
    prompt = build_rewrite_prompt(
        _profile(),
        kind="project",
        org="某项目",
        role="开发",
        raw_description="描述。",
        skill_tags=[],
        highlights=[],
        max_chars=6000,
    )

    assert "量化结果（唯一允许出现的数字来源）：（无）" in prompt
    assert "定性要点：（无）" in prompt


def test_malformed_metric_rows_are_skipped_not_rendered_as_none() -> None:
    """jsonb 是弱类型的：历史数据里可能出现缺字段的行，不能渲染出「None：None」。"""
    prompt = _prompt(metrics=[{"name": "", "value": ""}, {"name": "服务可用性", "value": "99.9%"}])

    assert "None" not in prompt
    assert "服务可用性：99.9%" in prompt
