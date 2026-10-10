"""Opt-in independent real judge calibration using the ten real M4 sample outputs."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.scorer import ScoreAgent
from app.config import Settings
from app.llm import LlmClient
from app.schemas import JobProfile

REPO_ROOT = Path(__file__).resolve().parents[3]


async def main(output: Path) -> int:
    settings = Settings()
    if settings.judge_provider != "openai-compatible" or not settings.judge_api_key:
        raise SystemExit(
            "需要 JUDGE_PROVIDER=openai-compatible + 独立厂商 JUDGE_API_KEY；stub 不算质量验收"
        )
    root = REPO_ROOT
    evaluation = json.loads((root / "docs/M4-eval.json").read_text("utf-8"))
    if evaluation["provider"] == "stub" or not evaluation["model"].casefold().startswith(
        "deepseek"
    ):
        raise SystemExit("当前校准集需要真实 DeepSeek M4 输出，以确定原始生成厂商")
    generator_vendor = "deepseek"
    if settings.judge_vendor.strip().casefold() == generator_vendor:
        raise SystemExit("JUDGE_VENDOR 必须与校准集生成厂商 deepseek 不同")
    fixtures = json.loads((root / "apps/api/tests/fixtures/m4_experiences.json").read_text("utf-8"))
    profile = JobProfile(
        title="后端开发工程师",
        seniority="校招",
        required_skills=["Python", "FastAPI", "PostgreSQL"],
        nice_to_have=["React", "Langfuse"],
        keywords=["工程质量", "接口开发", "可观测性"],
        implicit_preferences=[],
        responsibilities=["设计、开发与维护后端服务"],
    )
    judge = ScoreAgent(
        LlmClient(
            settings.model_copy(
                update={
                    "llm_provider": settings.judge_provider,
                    "llm_base_url": settings.judge_base_url,
                    "llm_api_key": settings.judge_api_key,
                    "llm_model": settings.judge_model,
                    "llm_max_tokens": settings.judge_max_tokens,
                }
            )
        ),
        judge_vendor=settings.judge_vendor,
    )
    cases = []
    for source in fixtures:
        case = next(
            item
            for item in evaluation["cases"]
            if item["fixture"] == source["id"] and "result" in item
        )
        sections = [
            {
                "title": "经历",
                "entries": [{"experience_id": source["id"], "bullets": case["result"]["bullets"]}],
            }
        ]
        first = await judge.score(
            profile, sections, generator_provider=generator_vendor, sources=[source]
        )
        second = await judge.score(
            profile, sections, generator_provider=generator_vendor, sources=[source]
        )
        delta = round(abs(first.score - second.score), 2)
        cases.append(
            {
                "fixture": source["id"],
                "delta": delta,
                "first": first.model_dump(mode="json"),
                "second": second.model_dump(mode="json"),
            }
        )
        print(f"{source['id']}: {first.score} / {second.score}; delta={delta}", flush=True)
    report = {
        "timestamp": datetime.now(UTC).isoformat(),
        "generator_vendor": generator_vendor,
        "judge_vendor": settings.judge_vendor,
        "judge_model": settings.judge_model,
        "samples": len(cases),
        "max_delta": max(case["delta"] for case in cases),
        "stability_pass": all(case["delta"] < 0.3 for case in cases),
        "cases": cases,
    }
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(
        output.write_text, json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"M5 live judge calibration: pass={report['stability_pass']}; report={output}")
    return 0 if report["stability_pass"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("../../docs/M5-eval.json"))
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.out)))
