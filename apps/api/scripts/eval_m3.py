"""Opt-in live model evaluation. No stub or mocked outputs count as quality passes."""

import argparse
import asyncio
import base64
import io
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agents.jd_parser import JdParser
from app.config import Settings
from app.llm.client import LlmClient
from app.llm.embeddings import EmbeddingClient


def skill_errors(profile, expected):
    errors = []
    required = " ".join(profile.required_skills).lower()
    preferred = " ".join(profile.nice_to_have).lower()
    for skill in expected["required_skills"]:
        if skill.lower() not in required:
            errors.append(f"missing required: {skill}")
    for skill in expected["nice_to_have"]:
        if skill.lower() not in preferred or skill.lower() in required:
            errors.append(f"preferred lost/upgraded: {skill}")
    if not expected["required_skills"] and profile.required_skills:
        errors.append("invented required skills")
    if profile.company is not None:
        errors.append("invented company")
    if expected["business_domain"] is None and profile.business_domain is not None:
        errors.append("inferred business domain without explicit source")
    return errors


def screenshot(text, font_path):
    font = ImageFont.truetype(font_path, 28)
    lines = [text[i : i + 30] for i in range(0, len(text), 30)]
    image = Image.new("RGB", (1050, 80 + len(lines) * 48), "white")
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        draw.text((30, 30 + i * 48), line, font=font, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


async def main(args):
    settings = Settings()
    parser = JdParser(LlmClient(settings))
    if settings.llm_provider == "stub":
        raise SystemExit("Real text evaluation requires LLM_PROVIDER=openai-compatible")
    if args.images and (settings.vision_provider or settings.llm_provider) == "stub":
        raise SystemExit("Image evaluation requires a real vision provider")
    fixtures = json.loads(
        (Path(__file__).parents[1] / "tests/fixtures/m3_jds.json").read_text("utf-8")
    )
    report = {
        "timestamp": datetime.now(UTC).isoformat(),
        "text_model": settings.llm_model,
        "prompt_version": "m3.0",
        "cases": [],
        "vision": "not run",
        "embeddings": "not run",
    }
    gate = asyncio.Semaphore(2)

    async def evaluate(case):
        async with gate:
            entry = {"id": case["id"], "errors": []}
            try:
                outcome = await parser.parse(case["raw_text"])
                entry["profile"] = outcome.value.model_dump()
                entry["warnings"] = outcome.warnings
                entry["errors"] = skill_errors(outcome.value, case["expected_profile"])
                if args.images:
                    image = await parser.parse_image(screenshot(case["raw_text"], args.font))
                    entry["image_profile"] = image.value.profile.model_dump()
                    entry["image_raw_text"] = image.value.raw_text
                    entry["errors"] += [
                        "image: " + e
                        for e in skill_errors(image.value.profile, case["expected_profile"])
                    ]
                    # Wording is allowed to vary; hard/preferred skill assignment must agree.
                    for field in ("required_skills", "nice_to_have"):
                        expected = case["expected_profile"][field]
                        for skill in expected:
                            if (
                                skill.lower() in " ".join(getattr(outcome.value, field)).lower()
                            ) != (
                                skill.lower()
                                in " ".join(getattr(image.value.profile, field)).lower()
                            ):
                                entry["errors"].append(f"text/image mismatch: {field} {skill}")
            except Exception as exc:
                # Provider exceptions may contain their response body; no credentials are printed.
                entry["errors"].append(type(exc).__name__)
            print(
                f"{'FAIL' if entry['errors'] else 'PASS'} {case['id']}: {entry['errors']}",
                flush=True,
            )
            return entry

    if args.retry_failed:
        previous = json.loads(await asyncio.to_thread(Path(args.out).read_text, "utf-8"))
        failed_ids = {c["id"] for c in previous["cases"] if c["errors"]}
        rerun = await asyncio.gather(*(evaluate(c) for c in fixtures if c["id"] in failed_ids))
        replacements = {c["id"]: c for c in rerun}
        report["previous_failures"] = [c for c in previous["cases"] if c["errors"]]
        report["cases"] = [replacements.get(c["id"], c) for c in previous["cases"]]
    else:
        report["cases"] = await asyncio.gather(*(evaluate(case) for case in fixtures))
    if args.images:
        report["vision"] = settings.vision_model or settings.llm_model
    if args.embeddings:
        embeddings = EmbeddingClient(settings)
        if embeddings.is_stub:
            raise SystemExit(
                "Real semantic evaluation requires EMBEDDING_PROVIDER=openai-compatible"
            )
        vector = await embeddings.embed(
            ["优化数据库查询速度", "通过索引设计降低 SQL 检索延迟", "组织校园文艺活动"]
        )

        def cosine(a, b):
            import math

            return sum(x * y for x, y in zip(a, b, strict=True)) / (
                math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
            )

        related, unrelated = cosine(vector[0], vector[1]), cosine(vector[0], vector[2])
        report["embeddings"] = {
            "model": settings.embedding_model,
            "related": related,
            "unrelated": unrelated,
            "pass": related > unrelated,
        }
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        output.write_text, json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    case_failures = sum(bool(c["errors"]) for c in report["cases"])
    failures = case_failures
    if isinstance(report["embeddings"], dict) and not report["embeddings"]["pass"]:
        failures += 1
    print(
        f"Real model evaluation: {len(report['cases']) - case_failures}/{len(report['cases'])} "
        f"{'text/image' if args.images else 'text'} cases passed; report: {output}"
    )
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    options = argparse.ArgumentParser()
    options.add_argument("--images", action="store_true")
    options.add_argument("--embeddings", action="store_true")
    options.add_argument("--retry-failed", action="store_true")
    options.add_argument(
        "--font", default=os.environ.get("M3_CJK_FONT", "C:/Windows/Fonts/msyh.ttc")
    )
    options.add_argument("--out", default="../../docs/M3-eval.json")
    asyncio.run(main(options.parse_args()))
