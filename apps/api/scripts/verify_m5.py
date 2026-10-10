"""Real Postgres API acceptance; all model calls explicitly use stub."""

from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings, get_settings
from app.db import Database, get_database
from app.main import create_app
from app.observability import Observability, get_observability
from app.repositories import JobDescriptionRepository, ResumeRepository
from app.schemas import JobProfile


async def main() -> None:
    settings = Settings(
        llm_provider="stub",
        judge_provider="stub",
        langfuse_public_key=None,
        langfuse_secret_key=None,
    )
    database = Database(settings)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_observability] = lambda: Observability(settings)
    user = UUID(settings.dev_user_id)
    experiences, resumes = [], []
    jd_id, generation_id = uuid4(), uuid4()
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"PASS {label}", flush=True)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://acceptance/api/v1"
    ) as client:

        async def call(method, path, payload=None, status=200):
            response = await client.request(method, path, json=payload)
            assert response.status_code == status, response.text[:800]
            return response.json() if response.content else None

        try:
            raw = "使用 Python 和 FastAPI 开发接口服务，并编写接口自动化测试。"
            for index in range(2):
                source = await call(
                    "POST",
                    "/experiences",
                    {
                        "kind": "project",
                        "org": f"M5 acceptance {uuid4()}",
                        "role": "开发",
                        "raw_description": raw,
                        "skill_tags": ["Python", "FastAPI"],
                        "highlights": ["编写接口测试"] if index == 0 else [raw],
                        "metrics": [],
                        "variants": [],
                        "sort_order": index,
                    },
                    status=201,
                )
                experiences.append(UUID(source["id"]))
            profile = JobProfile(
                title="后端开发",
                required_skills=["Python", "FastAPI"],
                nice_to_have=[],
                keywords=["接口"],
                implicit_preferences=[],
                responsibilities=["接口开发"],
            )
            await JobDescriptionRepository(database).create(
                user,
                jd_id=jd_id,
                raw_text="招聘 Python 和 FastAPI 后端开发工程师",
                title=profile.title,
                company=None,
                parsed=profile.model_dump(mode="json"),
                parser_model="acceptance-fixture",
            )
            generated = await call(
                "POST",
                "/resumes/generate",
                {
                    "run_id": str(generation_id),
                    "jd_id": str(jd_id),
                    "experience_ids": [str(x) for x in experiences],
                },
            )
            source_id = UUID(generated["resume"]["id"])
            resumes.append(source_id)
            check(generated["resume"]["generator_vendor"] == "stub", "generator vendor persisted")
            original = copy.deepcopy(generated["resume"]["sections"])
            scored = await call("POST", f"/resumes/{source_id}/score", {"threshold": 95})
            best_id, score_id = UUID(scored["resume"]["id"]), UUID(scored["score_run_id"])
            resumes.append(best_id)
            loop = scored["loop"]
            check(loop["snapshots"][0]["result"]["low_score_items"] == [0], "weak entry located")
            check(loop["best"]["round"] == 1, "best is improved snapshot")
            check(loop["stop_reason"] == "threshold", "threshold terminates loop")
            check(len(loop["snapshots"]) == 2, "initial and improved snapshots retained")
            check(
                loop["best"]["result"]["score"] > loop["snapshots"][0]["result"]["score"],
                "score improves",
            )
            check(len(loop["best"]["result"]["dimensions"]) == 4, "four rubric dimensions")
            check(
                loop["snapshots"][0]["result"]["deductions"]
                and loop["snapshots"][0]["result"]["recommendations"],
                "deductions and actions exposed",
            )
            check(
                scored["resume"]["sections"][0]["entries"][1] == original[0]["entries"][1],
                "strong sibling unchanged",
            )
            check(best_id != source_id, "best saved as separate exportable resume")
            saved = await call("GET", f"/resumes/{source_id}/scores/{score_id}")
            check(saved == scored, "all snapshots recover from Postgres")
            source = await call("GET", f"/resumes/{source_id}")
            check(source["sections"] == original, "source resume remains available")
            html = await client.get(f"/resumes/{best_id}/html")
            check(html.status_code == 200 and raw in html.text, "best HTML renders evidence")
            other = await ResumeRepository(database).get_scoring_result(
                uuid4(), source_id, score_id
            )
            check(other is None, "history isolates user IDs")
            await call("GET", f"/resumes/{uuid4()}/scores/{score_id}", status=404)
            check(True, "wrong source cannot read history")
            score_only = await call(
                "POST",
                f"/resumes/{source_id}/score",
                {"threshold": 95, "max_rounds": 0, "persist": False},
            )
            check(
                score_only["loop"]["stop_reason"] == "max_rounds"
                and score_only["score_run_id"] is None,
                "zero rounds and no persistence",
            )
            await call("POST", f"/resumes/{source_id}/score", {"max_rounds": 3}, status=422)
            check(True, "more than two rounds rejected")
            settings.judge_provider, settings.judge_api_key = "openai-compatible", "never-sent"
            settings.generation_vendor, settings.judge_vendor = "deepseek", "openai"
            limited = await call(
                "POST", f"/resumes/{source_id}/score", {"cost_limit": 2, "persist": False}
            )
            check(limited["loop"]["stop_reason"] == "cost_limit", "budget stops before judge call")
            settings.judge_vendor = settings.generation_vendor
            await call("POST", f"/resumes/{source_id}/score", {"persist": False}, status=400)
            check(True, "same vendor rejected before provider call")
        finally:
            async with database.connection() as conn:
                # Deleting test-owned resumes cascades scoring histories.
                await conn.execute("delete from resumes where id=any($1::uuid[])", resumes)
                await conn.execute("delete from generation_runs where id=$1", generation_id)
                for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    await conn.execute(
                        f"delete from {table} where thread_id=$1", str(generation_id)
                    )
                await conn.execute("delete from job_descriptions where id=$1", jd_id)
                await conn.execute("delete from experiences where id=any($1::uuid[])", experiences)
            await database.close()
    print(f"M5 API + loop: {checks} checks passed; real judge calibration: pnpm eval:m5")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
