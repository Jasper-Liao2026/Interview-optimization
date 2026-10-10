"""Blind scoring, factual overrides, budget guards and best-version behavior."""

import copy
from types import SimpleNamespace

import pytest

from app.agents.scorer import RUBRIC_WEIGHTS, ScoreAgent, run_score_loop, score_resume
from app.schemas import JobProfile, JudgeAssessment, JudgeDimension, JudgeOutput


def profile():
    return JobProfile(
        title="后端工程师",
        required_skills=["Python", "FastAPI"],
        nice_to_have=[],
        keywords=["接口"],
        implicit_preferences=[],
        responsibilities=["负责接口开发"],
    )


TEXT = "使用 Python 和 FastAPI 开发接口服务，提升交付效率。"


def source(**updates):
    return {
        "raw_description": TEXT,
        "skill_tags": ["Python", "FastAPI"],
        "highlights": [],
        "metrics": [],
        "variants": [],
        **updates,
    }


def sections(text="参与项目。", evidence=None):
    return [
        {
            "title": "项目经历",
            "entries": [
                {
                    "org": "项目",
                    "role": "开发",
                    "bullets": [{"text": text, "evidence": evidence or []}],
                }
            ],
        }
    ]


def assessment(index=0, value=4):
    return JudgeAssessment(
        item_index=index,
        dimensions=[
            JudgeDimension(key=key, score=value, rationale=f"{key} 尚可改进")
            for key in RUBRIC_WEIGHTS
        ],
        deductions=["表达可更具体"],
        suggestions=["在原始事实内补充做法。"],
    )


class FakeJudge:
    provider = "openai-compatible"
    model = "judge-model"
    is_stub = False

    def __init__(self, assessments=None):
        self.assessments = assessments or [assessment()]
        self.prompts = []

    async def complete_json(self, prompt, schema, *, system):
        self.prompts.append(prompt)
        return SimpleNamespace(
            value=JudgeOutput(assessments=self.assessments),
            warnings=[],
            llm=SimpleNamespace(input_tokens=10, output_tokens=20),
        )


def test_local_rule_is_explicit_and_deterministic():
    first = score_resume(profile(), sections(TEXT, [TEXT]), sources=[source()])
    second = score_resume(profile(), sections(TEXT, [TEXT]), sources=[source()])
    assert first.model_dump() == second.model_dump()
    assert RUBRIC_WEIGHTS["evidence"] == max(RUBRIC_WEIGHTS.values())
    assert first.judge_provider == "local-rubric"
    assert first.warnings


async def test_real_judge_vendor_and_blind_payload():
    judge = FakeJudge()
    result = await ScoreAgent(judge, judge_vendor="openai").score(
        profile(), sections(TEXT, [TEXT]), generator_provider="deepseek", sources=[source()]
    )
    assert result.judge_provider == "openai"
    assert result.score == 80
    assert result.usage_tokens == 30
    assert result.blind and not result.is_stub
    assert "round" not in judge.prompts[0]
    assert "deepseek" not in judge.prompts[0]
    assert "judge-model" not in judge.prompts[0]
    assert "original_facts" in judge.prompts[0]


async def test_same_vendor_rejected_before_network():
    judge = FakeJudge()
    with pytest.raises(ValueError, match="厂商"):
        await ScoreAgent(judge, judge_vendor="DeepSeek").score(
            profile(), sections(TEXT, [TEXT]), generator_provider="deepseek", sources=[source()]
        )
    assert not judge.prompts


async def test_fact_violation_overrides_inflated_judge_score():
    judge = FakeJudge([assessment(value=5)])
    result = await ScoreAgent(judge, judge_vendor="openai").score(
        profile(),
        sections("使用 Redis 提升吞吐300%。", ["使用 Redis 提升吞吐300%"]),
        generator_provider="deepseek",
        sources=[source()],
    )
    assert result.score == 55
    assert next(d for d in result.dimensions if d.key == "evidence").score == 0
    assert result.low_score_items == [0]
    assert any("无法回溯" in d.reason for d in result.deductions)


async def test_index_mapping_preserves_original_experience_indices():
    judge = FakeJudge([assessment(value=2)])
    result = await ScoreAgent(judge, judge_vendor="openai").score(
        profile(),
        sections(TEXT, [TEXT]),
        generator_provider="deepseek",
        sources=[source(), source(), source()],
        item_indices=[2],
    )
    assert result.low_score_items == [2]
    assert set(result.item_scores) == {"2"}
    assert all(d.item_index == 2 for d in result.deductions)


@pytest.mark.parametrize("bad", [[assessment(0), assessment(0)], [assessment(1)]])
async def test_judge_missing_or_duplicate_items_fail(bad):
    with pytest.raises(ValueError, match="遗漏或重复"):
        await ScoreAgent(FakeJudge(bad), judge_vendor="openai").score(
            profile(), sections(TEXT, [TEXT]), generator_provider="deepseek", sources=[source()]
        )


async def test_stub_is_explicit_and_makes_no_model_request():
    judge = FakeJudge()
    judge.is_stub = True
    result = await ScoreAgent(judge).score(
        profile(), sections(TEXT, [TEXT]), generator_provider="stub", sources=[source()]
    )
    assert result.is_stub
    assert result.judge_provider == "stub"
    assert not judge.prompts


async def test_score_budget_prevents_even_initial_network_call():
    called = []

    async def score(current):
        called.append("score")
        return score_resume(profile(), current)

    async def rewrite(indices, suggestions):
        called.append("rewrite")
        return sections(TEXT, [TEXT]), 0

    result = await run_score_loop(
        profile(), sections(), score=score, rewrite=rewrite, cost_limit=2, score_cost_estimate=3
    )
    assert not called
    assert result.stop_reason == "cost_limit"
    assert result.best.cost == 0
    assert result.best.result.judge_provider == "local-rubric"


async def test_rewrite_and_followup_score_reserve_before_network():
    calls = []

    async def score(current):
        calls.append("score")
        return score_resume(profile(), current)

    async def rewrite(indices, suggestions):
        calls.append("rewrite")
        return sections(TEXT, [TEXT]), 9

    result = await run_score_loop(
        profile(),
        sections(),
        score=score,
        rewrite=rewrite,
        cost_limit=14,
        score_cost_estimate=3,
        rewrite_cost_estimate=lambda indices: 9 * len(indices),
    )
    assert calls == ["score"]
    assert result.stop_reason == "cost_limit"
    assert result.best.cost == 3


async def test_threshold_terminates_after_targeted_rewrite():
    calls = []

    async def rewrite(indices, suggestions):
        calls.append(indices)
        assert suggestions
        return sections(TEXT, [TEXT]), 9

    result = await run_score_loop(
        profile(), sections(), rewrite=rewrite, rewrite_cost_estimate=9, cost_limit=100
    )
    assert calls == [[0]]
    assert result.stop_reason == "threshold"
    assert result.best.round == 1
    assert result.best.cost == 9


async def test_oscillation_outputs_best_and_stops_at_two_rounds():
    values = iter([60, 75, 50])
    calls = []

    async def score(current):
        value = next(values)
        result = score_resume(profile(), current)
        result.score = value
        result.low_score_items = [0]
        return result

    async def rewrite(indices, suggestions):
        calls.append(indices)
        return sections(f"第{len(calls)}轮内容。", ["素材"]), 1

    result = await run_score_loop(
        profile(),
        sections(),
        score=score,
        rewrite=rewrite,
        score_cost_estimate=1,
        rewrite_cost_estimate=1,
        threshold=90,
        max_rounds=2,
        cost_limit=10,
    )
    assert calls == [[0], [0]]
    assert result.stop_reason == "max_rounds"
    assert result.best.round == 1 and result.best.result.score == 75
    assert result.snapshots[-1].cost == 5


async def test_unpenalized_sibling_cannot_be_rewritten():
    original = sections()
    original[0]["entries"].append(copy.deepcopy(original[0]["entries"][0]))

    async def score(current):
        result = score_resume(profile(), current)
        result.low_score_items = [0]
        return result

    async def rewrite(indices, suggestions):
        changed = copy.deepcopy(original)
        changed[0]["entries"][1]["org"] = "改坏的高分条目"
        return changed, 0

    with pytest.raises(ValueError, match="未扣分"):
        await run_score_loop(profile(), original, rewrite=rewrite, score=score)


async def test_more_than_two_rounds_is_rejected():
    async def rewrite(indices, suggestions):
        return sections(), 0

    with pytest.raises(ValueError, match="0-2"):
        await run_score_loop(profile(), sections(), rewrite=rewrite, max_rounds=3)


def four_entries_with_one_violation():
    candidate = sections(TEXT, [TEXT])
    candidate[0]["entries"] *= 3
    candidate[0]["entries"].append(
        sections("使用 Redis 提升吞吐300%。", ["Redis"])[0]["entries"][0]
    )
    return copy.deepcopy(candidate)


@pytest.mark.parametrize("threshold", [0, 80])
async def test_high_average_cannot_hide_one_fact_violation(threshold):
    original = four_entries_with_one_violation()
    calls = []
    judge = FakeJudge([assessment(index=i, value=5) for i in range(4)])
    agent = ScoreAgent(judge, judge_vendor="openai", low_score_threshold=threshold)

    async def score(current):
        if calls:
            # The safe candidate scores less than the unsafe initial candidate.
            judge.assessments = [assessment(index=i, value=4) for i in range(4)]
        return await agent.score(
            profile(), current, generator_provider="deepseek", sources=[source() for _ in range(4)]
        )

    async def rewrite(indices, suggestions):
        calls.append(indices)
        assert indices == [3]
        safe = copy.deepcopy(original)
        safe[0]["entries"][3]["bullets"] = [{"text": TEXT, "evidence": [TEXT]}]
        return safe, 0

    result = await run_score_loop(
        profile(),
        original,
        score=score,
        rewrite=rewrite,
        sources=[source() for _ in range(4)],
        threshold=threshold,
    )
    assert result.snapshots[0].result.score == 88.75
    assert result.snapshots[0].result.factual_violations == [3]
    assert result.snapshots[0].result.low_score_items == [3]
    assert calls == [[3]]
    assert result.best.round == 1
    assert result.best.result.score == 80
    assert result.best.result.factual_violations == []


async def test_no_safe_snapshot_refuses_save_even_with_zero_threshold():
    async def rewrite(indices, suggestions):
        raise AssertionError("max_rounds=0 不应改写")

    with pytest.raises(ValueError, match="不能保存或导出"):
        await run_score_loop(
            profile(),
            four_entries_with_one_violation(),
            rewrite=rewrite,
            sources=[source() for _ in range(4)],
            threshold=0,
            max_rounds=0,
        )


async def test_fallback_and_stub_respect_requested_item_threshold():
    # Full coverage + evidence but short expression yields 96; 99 should target it.
    brief = sections("Python FastAPI 接口", ["Python", "FastAPI"])
    assert score_resume(profile(), brief, low_score_threshold=99).low_score_items == [0]
    assert score_resume(profile(), brief, low_score_threshold=80).low_score_items == []
    judge = FakeJudge()
    judge.is_stub = True
    scored = await ScoreAgent(judge, low_score_threshold=99).score(
        profile(), brief, generator_provider="stub"
    )
    assert scored.low_score_items == [0]

    async def rewrite(indices, suggestions):
        assert indices == [0]
        return sections(TEXT, [TEXT]), 0

    result = await run_score_loop(profile(), brief, rewrite=rewrite, threshold=99)
    assert result.best.round == 1
    assert result.stop_reason == "threshold"
