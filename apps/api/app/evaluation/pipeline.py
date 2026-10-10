"""Run two prompt snapshots over the same golden set and compare scores."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from app.agents.jd_parser import JdParser
from app.agents.prompts import get_prompt_snapshot
from app.agents.rewriter import ExperienceRewriter
from app.agents.scorer import ScoreAgent
from app.config import Settings
from app.llm import LlmClient
from app.schemas import JobProfile

from .golden import GoldenCase, golden_set


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    prompt_version: str
    score: float
    is_stub: bool
    profile: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    expected_profile: dict[str, Any] | None = None
    expected_bullets: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    created_at: str
    dataset_version: str
    prompt_versions: tuple[str, str]
    mode: str
    cases: tuple[CaseResult, ...]
    passed: bool = True
    prompt_hashes: dict[str, str] | None = None
    models: dict[str, str] | None = None

    @property
    def averages(self) -> dict[str, float]:
        values: dict[str, list[float]] = {version: [] for version in self.prompt_versions}
        for case in self.cases:
            values.setdefault(case.prompt_version, []).append(case.score)
        return {
            version: round(sum(items) / len(items), 2) if items else 0.0
            for version, items in values.items()
        }

    @property
    def delta(self) -> float:
        a, b = self.prompt_versions
        return round(self.averages.get(b, 0) - self.averages.get(a, 0), 2)

    def model_dump(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "dataset_version": self.dataset_version,
            "prompt_versions": list(self.prompt_versions),
            "mode": self.mode,
            "averages": self.averages,
            "delta": self.delta,
            "passed": self.passed,
            "prompt_hashes": self.prompt_hashes or {},
            "models": self.models or {},
            "cases": [asdict(case) for case in self.cases],
        }


def _sections(case: GoldenCase, bullets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"title": "项目经历", "entries": [{"experience_id": case.source["id"], "bullets": bullets}]}
    ]


def _same_skill_list(actual: Any, expected: Any) -> bool:
    """Compare skill lists as unordered normalized values."""
    def normalize(values: Any) -> set[str]:
        return {
            str(value).strip().casefold()
            for value in (values if isinstance(values, list) else [])
        }

    return normalize(actual) == normalize(expected)


async def _run_case(settings: Settings, case: GoldenCase, version: str) -> CaseResult:
    generator = LlmClient(settings)
    parser = JdParser(generator, prompt_version=version)
    rewriter = ExperienceRewriter(
        generator, max_input_chars=settings.rewrite_max_input_chars, prompt_version=version
    )
    try:
        parsed = await parser.parse(case.raw_jd)
        profile = parsed.value
        rewritten = await rewriter.rewrite(profile, case.source)
    except Exception as exc:
        return CaseResult(
            case.id,
            version,
            0,
            settings.llm_provider == "stub",
            expected_profile=case.expected_profile,
            expected_bullets=case.expected_bullets,
            failures=(f"{type(exc).__name__}: {exc}",),
        )
    judge_settings = settings.model_copy(
        update={
            "llm_provider": settings.judge_provider,
            "llm_base_url": settings.judge_base_url,
            "llm_api_key": settings.judge_api_key,
            "llm_model": settings.judge_model,
            "llm_max_tokens": settings.judge_max_tokens,
        }
    )
    judge = ScoreAgent(LlmClient(judge_settings), judge_vendor=settings.judge_vendor)
    bullets = [bullet.model_dump(mode="json") for bullet in rewritten.value.bullets]
    try:
        result = await judge.score(
            JobProfile.model_validate(case.expected_profile),
            _sections(case, bullets),
            generator_provider=settings.generation_vendor,
            sources=[case.source],
        )
    except Exception as exc:
        return CaseResult(
            case.id,
            version,
            0,
            settings.llm_provider == "stub" or settings.judge_provider == "stub",
            profile=profile.model_dump(mode="json"),
            output={"bullets": bullets},
            expected_profile=case.expected_profile,
            expected_bullets=case.expected_bullets,
            failures=(f"{type(exc).__name__}: {exc}",),
            warnings=tuple(parsed.warnings + rewritten.warnings),
        )
    failures: list[str] = []
    profile_dump = profile.model_dump(mode="json")
    if settings.llm_provider != "stub":
        if profile_dump.get("title") != case.expected_profile.get("title"):
            failures.append("profile.title mismatch")
        for key in ("required_skills", "nice_to_have"):
            if not _same_skill_list(profile_dump.get(key), case.expected_profile.get(key)):
                failures.append(f"profile.{key} mismatch")
        text = " ".join(str(item.get("text", "")) for item in bullets).casefold()
        for expected in case.expected_bullets:
            if expected.casefold() not in text:
                failures.append(f"missing expected bullet: {expected}")
    warnings = tuple(parsed.warnings + rewritten.warnings + result.warnings)
    return CaseResult(
        case.id,
        version,
        result.score,
        result.is_stub,
        profile=profile_dump,
        output={"bullets": bullets},
        expected_profile=case.expected_profile,
        expected_bullets=case.expected_bullets,
        failures=tuple(failures),
        warnings=warnings,
    )


async def compare_prompts(
    settings: Settings,
    *,
    prompt_a: str = "m4.0",
    prompt_b: str = "m8.0",
    cases: tuple[GoldenCase, ...] | None = None,
    concurrency: int = 2,
    allow_stub: bool = False,
    min_score: float = 0,
    max_regression: float = 0,
) -> EvaluationReport:
    """Evaluate both versions against identical cases.

    Stub runs are useful for pipeline smoke tests but are explicitly marked and
    rejected unless ``allow_stub`` is set. Real quality reports must use both a
    real generator and a real, independent judge.
    """
    if prompt_a == prompt_b:
        raise ValueError("prompt_a 与 prompt_b 必须是两个版本")
    get_prompt_snapshot(prompt_a)
    get_prompt_snapshot(prompt_b)
    if concurrency < 1 or min_score < 0 or min_score > 100 or max_regression < 0:
        raise ValueError("concurrency 必须为正数，min_score 为 0-100，max_regression 非负")
    if settings.llm_provider == "stub" or settings.judge_provider == "stub":
        if not allow_stub:
            raise ValueError("真实 M8 评测拒绝 stub；仅流水线冒烟测试可显式 allow_stub=True")
        mode = "stub"
    else:
        if (
            settings.llm_provider != "openai-compatible"
            or settings.judge_provider != "openai-compatible"
        ):
            raise ValueError("真实评测需使用 openai-compatible provider")
        if not settings.llm_api_key or not settings.judge_api_key:
            raise ValueError("真实 M8 评测需要 LLM_API_KEY 与 JUDGE_API_KEY")
        if (
            settings.generation_vendor.strip().casefold()
            == settings.judge_vendor.strip().casefold()
        ):
            raise ValueError("真实 M8 评测要求生成与评分厂商不同")
        if not settings.generation_vendor.strip() or not settings.judge_vendor.strip():
            raise ValueError("真实 M8 评测必须明确模型厂商")
        mode = "real"
    selected = cases or golden_set()
    gate = asyncio.Semaphore(concurrency)

    async def run(case: GoldenCase, version: str) -> CaseResult:
        async with gate:
            return await _run_case(settings, case, version)

    results = await asyncio.gather(
        *(run(case, version) for version in (prompt_a, prompt_b) for case in selected)
    )
    by_version = {
        version: [item for item in results if item.prompt_version == version]
        for version in (prompt_a, prompt_b)
    }
    failures = any(item.failures or item.score < min_score for item in results)
    if by_version[prompt_b] and by_version[prompt_a]:
        failures = failures or (
            sum(item.score for item in by_version[prompt_b]) / len(by_version[prompt_b])
            < (
                sum(item.score for item in by_version[prompt_a]) / len(by_version[prompt_a])
                - max_regression
            )
        )
    return EvaluationReport(
        created_at=datetime.now(UTC).isoformat(),
        dataset_version="m8-golden-v1",
        prompt_versions=(prompt_a, prompt_b),
        mode=mode,
        cases=tuple(results),
        passed=not failures,
        prompt_hashes={
            prompt_a: get_prompt_snapshot(prompt_a).content_hash,
            prompt_b: get_prompt_snapshot(prompt_b).content_hash,
        },
        models={
            "generation_provider": settings.llm_provider,
            "generation_model": settings.llm_model,
            "generation_vendor": settings.generation_vendor,
            "judge_provider": settings.judge_provider,
            "judge_model": settings.judge_model,
            "judge_vendor": settings.judge_vendor,
        },
    )
