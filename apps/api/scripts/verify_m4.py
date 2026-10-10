"""M4 routes with real Postgres and official durable LangGraph checkpoints."""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import suppress
from pathlib import Path
from uuid import UUID, uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.checkpoint import GenerationRunStore
from app.agents.generation_graph import GenerationGraph
from app.agents.rewriter import ExperienceRewriter
from app.config import Settings, get_settings
from app.db import Database, get_database
from app.llm import LlmClient
from app.main import create_app
from app.observability import Observability, get_observability


async def main() -> None:
    settings = Settings(llm_provider="stub", langfuse_public_key=None, langfuse_secret_key=None)
    database = Database(settings)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_observability] = lambda: Observability(settings)
    ids: list[UUID] = []
    runs: list[UUID] = []
    resumes: list[UUID] = []
    jds: list[UUID] = []
    checks = 0

    def check(condition: bool, label: str) -> None:
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"PASS {label}", flush=True)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://acceptance/api/v1"
    ) as client:

        async def call(method: str, path: str, payload=None, status=200):
            response = await client.request(method, path, json=payload)
            assert response.status_code == status, (
                f"{path}: {response.status_code} {response.text[:500]}"
            )
            return response.json() if response.content else None

        try:
            for index in range(2):
                row = await call(
                    "POST",
                    "/experiences",
                    {
                        "kind": "project",
                        "org": f"M4 acceptance {uuid4()}",
                        "role": "开发者",
                        "raw_description": "使用 Python 实现接口并编写测试。",
                        "skill_tags": ["Python"],
                        "highlights": ["编写接口测试"],
                        "metrics": [],
                        "variants": [],
                        "sort_order": index,
                    },
                    status=201,
                )
                ids.append(UUID(row["id"]))
            run_id = uuid4()
            runs.append(run_id)
            payload = {
                "run_id": str(run_id),
                "jd_text": "招聘后端工程师，熟悉 Python 开发。",
                "experience_ids": [str(item) for item in ids],
                "persist": True,
            }
            generated = await call("POST", "/resumes/generate", payload)
            resumes.append(UUID(generated["resume"]["id"]))
            if generated["resume"]["jd_id"]:
                jds.append(UUID(generated["resume"]["jd_id"]))
            check(generated["run_id"] == str(run_id), "client run ID retained")
            check(generated["checkpoint"]["backend"] == "postgres", "Postgres checkpointer used")
            check(generated["checkpoint"]["status"] == "completed", "fan-in completed")
            check(len(generated["items"]) == 2 and not generated["failures"], "all items succeed")
            entries = [e for section in generated["resume"]["sections"] for e in section["entries"]]
            check(len(entries) == 2, "full resume contains both experiences")
            check(all(e["bullets"][0]["evidence"] for e in entries), "source evidence retained")
            repeat = await call("POST", "/resumes/generate", payload)
            check(
                repeat["resume"]["id"] == generated["resume"]["id"], "repeat generation idempotent"
            )
            read = await call("GET", f"/resumes/runs/{run_id}")
            check(
                read["resume"]["id"] == generated["resume"]["id"], "run read from durable storage"
            )
            resumed = await call("POST", f"/resumes/runs/{run_id}/resume", {})
            check(resumed["resume"]["id"] == generated["resume"]["id"], "resume retains saved ID")
            html = await client.get(f"/resumes/{generated['resume']['id']}/html")
            check(
                html.status_code == 200 and "编写接口测试" in html.text,
                "preview renders source facts",
            )
            await call("GET", f"/resumes/runs/{uuid4()}", status=404)
            check(True, "missing run returns 404")
            async with database.connection() as conn:
                count = await conn.fetchval(
                    "select count(*) from checkpoints where thread_id=$1", str(run_id)
                )
            check(count > 0, "official checkpoints persisted")

            store = GenerationRunStore(database, database_url=settings.database_url)
            checkpoint_id = uuid4()
            runs.append(checkpoint_id)
            waiting = asyncio.Event()
            calls = {0: 0, 1: 0}
            original = ExperienceRewriter(LlmClient(settings), max_input_chars=6000)

            class InterruptedRewriter:
                model = original.model

                async def rewrite(self, profile, row):
                    index = row["sort_order"]
                    calls[index] += 1
                    if index == 1 and calls[index] == 1:
                        waiting.set()
                        await asyncio.Event().wait()
                    return await original.rewrite(profile, row)

            sources = [await call("GET", f"/experiences/{item}") for item in ids]
            async with store.checkpointer() as saver:
                graph = GenerationGraph(
                    InterruptedRewriter(),
                    checkpointer=saver,
                    observability=Observability(settings),
                )
                config = graph.config(str(checkpoint_id))
                task = asyncio.create_task(
                    graph.graph.ainvoke(
                        {
                            "profile": generated["profile"],
                            "experiences": sources,
                            "selected": [0, 1],
                            "items": {},
                        },
                        config=config,
                        durability="sync",
                    )
                )
                await asyncio.wait_for(waiting.wait(), timeout=5)
                await asyncio.sleep(0.1)
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            async with store.checkpointer() as saver:
                restarted = GenerationGraph(
                    InterruptedRewriter(),
                    checkpointer=saver,
                    observability=Observability(settings),
                )
                state = await restarted.graph.ainvoke(None, config=config, durability="sync")
            check(calls == {0: 1, 1: 2}, "reopened Postgres saver preserves completed sibling")
            check(
                len(state["sections"][0]["entries"]) == 2, "interrupted graph resumes full output"
            )
        finally:
            async with database.connection() as conn:
                for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    await conn.execute(
                        f"delete from {table} where thread_id=any($1::text[])",
                        [str(run) for run in runs],
                    )
                for table, values in (
                    ("generation_runs", runs),
                    ("resumes", resumes),
                    ("job_descriptions", jds),
                    ("experiences", ids),
                ):
                    await conn.execute(f"delete from {table} where id=any($1::uuid[])", values)
            await database.close()
    print(f"M4: {checks} checks passed; live quality: pnpm eval:m4 --samples 100")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
    asyncio.run(main())
