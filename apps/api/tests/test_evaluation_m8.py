from dataclasses import FrozenInstanceError

import pytest

from app.agents.prompts import (
    available_prompt_versions,
    get_prompt_snapshot,
    prompt_context,
    prompt_metadata,
    select_prompt_version,
)
from app.config import Settings
from app.evaluation.golden import golden_set
from app.evaluation.pipeline import CaseResult, compare_prompts


def test_golden_set_is_20_synthetic_cases_with_expected_outputs():
    cases = golden_set()
    assert len(cases) == 20
    assert all(case.synthetic for case in cases)
    assert len({case.id for case in cases}) == 20
    assert all(case.expected_profile["required_skills"] for case in cases)
    assert all(case.expected_bullets for case in cases)


def test_prompt_snapshots_are_selectable_and_rollbackable():
    versions = available_prompt_versions()
    assert {"m4.0", "m8.0"}.issubset(versions)
    original = get_prompt_snapshot("m4.0")
    selected = select_prompt_version("m8.0")
    assert selected.version == "m8.0"
    assert get_prompt_snapshot().version == "m8.0"
    assert select_prompt_version(original.version).version == "m4.0"
    with pytest.raises(ValueError, match="未知 prompt"):
        get_prompt_snapshot("does-not-exist")


@pytest.mark.asyncio
async def test_stub_comparison_is_explicit_and_has_both_versions():
    settings = Settings(_env_file=None, llm_provider="stub", judge_provider="stub")
    report = await compare_prompts(settings, cases=golden_set()[:1], allow_stub=True)
    assert report.mode == "stub"
    assert report.prompt_versions == ("m4.0", "m8.0")
    assert len(report.cases) == 2
    assert all(case.is_stub for case in report.cases)


@pytest.mark.asyncio
async def test_real_comparison_rejects_stub_without_opt_in():
    settings = Settings(_env_file=None, llm_provider="stub", judge_provider="stub")
    with pytest.raises(ValueError, match="拒绝 stub"):
        await compare_prompts(settings, cases=golden_set()[:1])


def test_snapshot_frozen_and_context_restored():
    baseline = get_prompt_snapshot("m4.0")
    candidate = get_prompt_snapshot("m8.0")
    assert baseline.content_hash != candidate.content_hash
    with pytest.raises(FrozenInstanceError):
        baseline.version = "changed"
    with prompt_context(candidate):
        assert prompt_metadata()["prompt_version"] == "m8.0"
    assert prompt_metadata()["prompt_version"] == "m4.0"


async def test_same_vendor_rejected_before_generation(monkeypatch):
    from app.evaluation import pipeline

    async def forbidden(*args, **kwargs):
        pytest.fail("不得发起生成")

    monkeypatch.setattr(pipeline, "_run_case", forbidden)
    settings = Settings(
        _env_file=None,
        llm_provider="openai-compatible",
        judge_provider="openai-compatible",
        llm_api_key="fake",
        judge_api_key="fake",
        generation_vendor="one",
        judge_vendor="ONE",
    )
    with pytest.raises(ValueError, match="厂商不同"):
        await compare_prompts(settings, cases=golden_set()[:1])


async def test_zero_tolerance_regression_fails(monkeypatch):
    from app.evaluation import pipeline

    async def output(settings, case, version):
        return CaseResult(case.id, version, 90 if version == "m4.0" else 89, True)

    monkeypatch.setattr(pipeline, "_run_case", output)
    settings = Settings(_env_file=None, llm_provider="stub", judge_provider="stub")
    report = await compare_prompts(settings, cases=golden_set()[:1], allow_stub=True)
    assert report.delta == -1 and not report.passed


async def test_failed_sample_recorded_without_aborting_sibling(monkeypatch):
    from app.evaluation import pipeline

    async def failed_parse(self, text):
        raise RuntimeError("transport unavailable")

    monkeypatch.setattr(pipeline.JdParser, "parse", failed_parse)
    settings = Settings(_env_file=None, llm_provider="stub", judge_provider="stub")
    report = await compare_prompts(settings, cases=golden_set()[:2], allow_stub=True)
    assert len(report.cases) == 4 and not report.passed
    assert all(case.failures and case.expected_profile for case in report.cases)
    assert "fake" not in str(report.model_dump())
