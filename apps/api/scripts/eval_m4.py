"""Opt-in live M4 rewrite evaluation; stub output is never counted as quality evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.facts import validate_rewrite_facts
from app.agents.rewriter import ExperienceRewriter, FactValidationError
from app.config import Settings
from app.llm import LlmClient, LlmError
from app.schemas import JobProfile


async def main(samples: int, concurrency: int, output: Path) -> int:
    settings = Settings()
    if settings.llm_provider != "openai-compatible" or not settings.llm_api_key:
        raise SystemExit(
            "M4 live evaluation requires LLM_PROVIDER=openai-compatible and LLM_API_KEY"
        )
    fixtures = json.loads(
        (Path(__file__).parents[1] / "tests/fixtures/m4_experiences.json").read_text("utf-8")
    )
    profile = JobProfile(
        title="后端开发工程师",
        company=None,
        seniority="校招",
        required_skills=["Python", "FastAPI", "PostgreSQL"],
        nice_to_have=["React", "Langfuse"],
        business_domain=None,
        keywords=["工程质量", "接口开发", "可观测性"],
        implicit_preferences=[],
        responsibilities=["设计、开发与维护后端服务"],
    )
    gate = asyncio.Semaphore(concurrency)
    llm = LlmClient(settings)
    rewriter = ExperienceRewriter(llm, max_input_chars=settings.rewrite_max_input_chars)

    async def evaluate(index: int) -> dict:
        experience = fixtures[index % len(fixtures)]
        async with gate:
            entry = {"index": index, "fixture": experience["id"]}
            try:
                result = await rewriter.rewrite(profile, experience)
                entry["schema_ok"] = True
                entry["warnings"] = result.warnings
                entry["fact_violations"] = validate_rewrite_facts(experience, result.value)
                entry["result"] = result.value.model_dump(mode="json")
            except Exception as exc:
                entry["schema_ok"] = not (isinstance(exc, LlmError) and "结构化输出" in str(exc))
                entry["fact_violations"] = (
                    exc.violations if isinstance(exc, FactValidationError) else []
                )
                entry["transport_error"] = not isinstance(exc, (FactValidationError, LlmError)) or (
                    isinstance(exc, LlmError) and "结构化输出" not in str(exc)
                )
                entry["error"] = f"{type(exc).__name__}: {exc}"
            return entry

    cases = await asyncio.gather(*(evaluate(index) for index in range(samples)))
    failures = sum(not item["schema_ok"] for item in cases)
    fact_failures = sum(bool(item.get("fact_violations")) for item in cases)
    transport_failures = sum(bool(item.get("transport_error")) for item in cases)
    accepted = sum("result" in item and not item.get("fact_violations") for item in cases)
    report = {
        "timestamp": datetime.now(UTC).isoformat(),
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "samples": samples,
        "schema_failures": failures,
        "schema_failure_rate": failures / samples,
        "fact_rejections": fact_failures,
        "transport_failures": transport_failures,
        "accepted_rewrites": accepted,
        "accepted_rate": accepted / samples,
        "schema_gate_evaluable": samples >= 100,
        "schema_gate_pass": samples >= 100 and failures / samples < 0.01,
        "cases": cases,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        output.write_text, json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"M4 live evaluation: {samples} samples, {failures} schema failures "
        f"({failures / samples:.1%}), {fact_failures} fact rejections, "
        f"{transport_failures} transport/other failures, {accepted} accepted; report={output}"
    )
    if samples < 100:
        print("Exploratory sample only; run --samples 100 to evaluate the <1% schema gate.")
        return 0
    return 0 if report["schema_gate_pass"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--out", type=Path, default=Path("../../docs/M4-eval.json"))
    args = parser.parse_args()
    if args.samples < 1 or args.concurrency < 1:
        parser.error("--samples and --concurrency must be positive")
    raise SystemExit(asyncio.run(main(args.samples, args.concurrency, args.out)))
