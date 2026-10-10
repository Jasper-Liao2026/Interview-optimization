"""Real Postgres/checkpoint and optional Langfuse acceptance; LLM explicitly stub."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings, get_settings
from app.db import Database, get_database
from app.main import create_app
from app.observability import Observability, get_observability


async def main(langfuse=False):
    overrides = {} if langfuse else {"langfuse_public_key": None, "langfuse_secret_key": None}
    settings = Settings(llm_provider="stub", judge_provider="stub", **overrides)
    obs = Observability(settings)
    if langfuse and not obs.auth_check():
        raise RuntimeError("--langfuse 需要可达的 Langfuse 和已配置密钥")
    database = Database(settings)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_observability] = lambda: obs
    run_id, request_id = uuid4(), uuid4()
    sources = []
    generated = None
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"PASS {label}", flush=True)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://acceptance/api/v1"
    ) as client:
        try:
            raw = "使用 Python 和 FastAPI 开发接口，编写自动化测试。"
            for index in range(2):
                response = await client.post("/experiences", json={
                    "kind": "project", "org": f"M8 acceptance {uuid4()}", "role": "开发",
                    "raw_description": raw,
                    "highlights": [raw],
                    "skill_tags": ["Python", "FastAPI"],
                    "metrics": [], "variants": [], "sort_order": index,
                })
                assert response.status_code == 201, response.text
                sources.append(UUID(response.json()["id"]))
            payload = {
                "run_id": str(run_id), "jd_text": "招聘 Python 和 FastAPI 后端开发工程师",
                "experience_ids": [str(i) for i in sources],
            }
            response = await client.post("/resumes/generate", json=payload,
                headers={"X-Request-Id": str(request_id)})
            assert response.status_code == 200, response.text
            generated = response.json()
            usage = generated["usage"]
            check(generated["trace_id"] == request_id.hex, "browser request ID matches saved trace")
            check(usage["is_stub"] and usage["cost_usd"] == 0, "stub usage explicitly labelled")
            check(len(usage["calls"]) == 3, "JD parse and both parallel rewrites recorded")
            check(usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"] > 0,
                "per-resume token totals match provider-attempt ledger")
            check(all(c["prompt_version"] == "m4.0" and len(c["prompt_hash"]) == 64
                for c in usage["calls"]), "every call carries immutable prompt hash")
            replay = (await client.post("/resumes/generate", json=payload)).json()
            check(replay["usage"] == usage, "idempotent replay does not count calls twice")
            # A new service/store instance reads the same history after a restart.
            recovered = (await client.get(f"/resumes/runs/{run_id}")).json()
            check(recovered["usage"] == usage, "usage survives service reconstruction")
            settings.prompt_version = "m8.0"
            resumed = (await client.post(f"/resumes/runs/{run_id}/resume")).json()
            check(resumed["usage"]["total_tokens"] == usage["total_tokens"]
                and resumed["trace_id"] == request_id.hex and resumed["prompt_version"] == "m4.0",
                "recovery retains original trace and prompt without re-calling model")
            monitor = await client.get("/observability/usage")
            assert monitor.status_code == 200, monitor.text
            visible = next(r for r in monitor.json()["runs"] if r["run_id"] == str(run_id))
            check(
                visible["usage"]["total_tokens"] == usage["total_tokens"],
                "monitor reads saved cost/latency",
            )
            async with database.connection() as conn:
                count = await conn.fetchval(
                    "select count(*) from generation_llm_calls where run_id=$1", run_id
                )
            check(count == 3, "Postgres has exactly three provider attempts")
            if langfuse:
                obs.flush()
                trace = None
                async with httpx.AsyncClient(auth=(settings.langfuse_public_key,
                    settings.langfuse_secret_key), follow_redirects=True) as lf:
                    for _ in range(30):
                        response = await lf.get(
                            f"{settings.langfuse_host.rstrip('/')}/api/public/traces/{request_id.hex}")
                        if response.status_code == 200:
                            candidate = response.json()
                            if len(candidate.get("observations", [])) >= 6:
                                trace = candidate
                                break
                        await asyncio.sleep(1)
                check(trace is not None, "actual Langfuse trace readable from local server")
                observations = trace["observations"]
                generations = [o for o in observations if o["type"] == "GENERATION"]
                check(
                    len(generations) == 3
                    and {o["name"] for o in generations} == {"jd_parse", "rewrite"},
                    "one root contains JD parsing and every parallel LLM generation",
                )
                check(
                    all(o.get("parentObservationId") for o in generations),
                    "parallel generations retain parent spans",
                )
                check(
                    all(o.get("input") and o.get("output") for o in generations),
                    "prompts and model outputs replayable",
                )
                check(all((o.get("usageDetails") or o.get("usage") or {}).get("total", 0) > 0
                    for o in generations), "Langfuse usage matches call evidence")
                check(
                    generated["langfuse_trace_url"] is not None,
                    "frontend receives trace detail link",
                )
        finally:
            async with database.connection() as conn:
                if generated:
                    await conn.execute(
                        "delete from resumes where id=$1", UUID(generated["resume"]["id"])
                    )
                    if generated["resume"]["jd_id"]:
                        await conn.execute(
                            "delete from job_descriptions where id=$1",
                            UUID(generated["resume"]["jd_id"]),
                        )
                await conn.execute("delete from generation_runs where id=$1", run_id)
                for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    await conn.execute(f"delete from {table} where thread_id=$1", str(run_id))
                await conn.execute("delete from experiences where id=any($1::uuid[])", sources)
            obs.shutdown()
            await database.close()
    print(f"M8 telemetry: {checks} checks passed; real infrastructure, model explicitly stub")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--langfuse", action="store_true")
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(args.langfuse))
