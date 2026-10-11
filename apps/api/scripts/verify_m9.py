"""M9-5/6 acceptance in an isolated Postgres database.

Creates a throwaway database, replays the checked-in migrations and seed, then
exercises demo import, generation, scoring, editing, PDF export and destructive
data controls through the real ASGI app. The database is always dropped.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import psycopg
from fastapi import Depends
from fastapi.responses import StreamingResponse
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings, get_settings  # noqa: E402
from app.data_activity import protect_local_data  # noqa: E402
from app.db import Database, get_database  # noqa: E402
from app.main import create_app  # noqa: E402
from app.observability import Observability, get_observability  # noqa: E402

CHECK_COUNT = 0


def check(condition: bool, label: str) -> None:
    global CHECK_COUNT
    if not condition:
        raise AssertionError(label)
    CHECK_COUNT += 1
    print(f"PASS {label}", flush=True)


def create_database(base_url: str, name: str) -> str:
    params = conninfo_to_dict(base_url)
    params.pop("dbname", None)
    maintenance = make_conninfo(**params, dbname="postgres")
    with psycopg.connect(maintenance, autocommit=True) as conn:
        conn.execute(f'create database "{name}"')
    target = make_conninfo(**params, dbname=name)
    migration_dir = ROOT / "supabase" / "migrations"
    with psycopg.connect(target, autocommit=True) as conn:
        for path in sorted(migration_dir.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
        conn.execute((ROOT / "supabase" / "seed.sql").read_text(encoding="utf-8"))
    return urlsplit(base_url)._replace(path=f"/{name}").geturl()


def drop_database(base_url: str, name: str) -> None:
    params = conninfo_to_dict(base_url)
    params.pop("dbname", None)
    maintenance = make_conninfo(**params, dbname="postgres")
    with psycopg.connect(maintenance, autocommit=True) as conn:
        conn.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity "
            "where datname=%s and pid <> pg_backend_pid()",
            (name,),
        )
        conn.execute(f'drop database if exists "{name}"')


async def main() -> None:
    base = Settings().database_url
    name = f"resume_optimizer_m9_{os.getpid()}_{uuid4().hex[:8]}"
    try:
        target = create_database(base, name)
    except Exception:
        drop_database(base, name)
        raise
    settings = Settings(
        database_url=target,
        llm_provider="stub",
        judge_provider="stub",
        langfuse_public_key=None,
        langfuse_secret_key=None,
    )
    database = Database(settings)
    application = create_app()
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_observability] = lambda: Observability(settings)
    stream_started, finish_stream = asyncio.Event(), asyncio.Event()

    @application.get(
        "/api/v1/acceptance/activity",
        dependencies=[Depends(protect_local_data, scope="request")],
    )
    async def activity():
        async def stream():
            stream_started.set()
            yield b"started"
            await finish_stream.wait()
            yield b"completed"

        return StreamingResponse(stream())

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://m9.test/api/v1",
            timeout=120,
        ) as client:

            async def call(method: str, path: str, payload=None, expected: int = 200):
                response = await client.request(method, path, json=payload)
                check(response.status_code == expected, f"{method} {path} -> {expected}")
                return response.json() if response.content else None

            first = await call("POST", "/system/demo", {})
            check(
                first["inserted_experiences"] == 3 and first["inserted_jobs"] == 1,
                "first demo import inserts three experiences and one JD",
            )
            second = await call("POST", "/system/demo", {})
            check(
                second["inserted_experiences"] == 0 and second["inserted_jobs"] == 0,
                "second demo import is idempotent",
            )
            experience_id = first["experience_ids"][0]
            original = await call("GET", f"/experiences/{experience_id}")
            edited = {**original, "raw_description": "用户手动编辑的真实内容"}
            edited.pop("created_at", None)
            edited.pop("updated_at", None)
            edited.pop("user_id", None)
            await call("PUT", f"/experiences/{experience_id}", edited)
            third = await call("POST", "/system/demo", {})
            after = await call("GET", f"/experiences/{experience_id}")
            check(
                third["inserted_experiences"] == 0
                and after["raw_description"] == edited["raw_description"],
                "repeat demo import preserves user edits",
            )

            generated = await call(
                "POST",
                "/resumes/generate",
                {
                    "run_id": str(uuid4()),
                    "jd_id": first["jd_id"],
                    "experience_ids": first["experience_ids"],
                    "persist": True,
                },
            )
            check(generated["usage"]["is_stub"], "generation is explicitly stub")
            resume_id = generated["resume"]["id"]
            scored = await call(
                "POST",
                f"/resumes/{resume_id}/score",
                {
                    "threshold": 80,
                    "max_rounds": 0,
                    "persist": True,
                },
            )
            check(scored["loop"]["best"]["result"]["is_stub"], "scoring is explicitly stub")
            editor = await call("GET", f"/resumes/{resume_id}/editor")
            draft = {key: editor["resume"][key] for key in ("title", "header", "sections")}
            draft["title"] = "演示数据编辑后的简历"
            draft["expected_revision"] = editor["revision"]
            await call("PUT", f"/resumes/{resume_id}/editor", draft)
            pdf = await client.get(f"/resumes/{resume_id}/pdf")
            check(
                pdf.status_code == 200 and pdf.content.startswith(b"%PDF"),
                "stub generation -> score -> edit -> PDF export",
            )

            backup = await call("GET", "/system/data/export")
            check(
                backup["tables"]["experiences"] and backup["tables"]["resumes"],
                "full JSON export contains demo and generated data",
            )
            check(
                backup["tables"]["checkpoints"] and backup["tables"]["checkpoint_blobs"],
                "backup contains real graph checkpoints and binary blobs",
            )
            preserved = {key: backup["tables"][key] for key in backup["preserved_tables"]}
            # Evaluation rows can contain custom private samples despite having
            # no user_id. They must be exported and erased with instance data.
            async with database.connection() as conn:
                dataset = uuid4()
                await conn.execute(
                    "insert into public.evaluation_datasets(id,version,cases) values($1,$2,$3)",
                    dataset,
                    "m9-private-fixture",
                    {"raw_text": "private evaluation sample"},
                )
                await conn.execute(
                    "insert into public.evaluation_runs"
                    "(id,dataset_id,prompt_a,prompt_b,mode,result) values($1,$2,$3,$4,'stub',$5)",
                    uuid4(),
                    dataset,
                    settings.prompt_version,
                    settings.prompt_version,
                    {},
                )
            activity_task = asyncio.create_task(client.get("/acceptance/activity"))
            await asyncio.wait_for(stream_started.wait(), timeout=5)
            try:
                await call(
                    "POST", "/system/data/clear", {"confirmation": "清空本地数据"}, expected=409
                )
                check(not activity_task.done(), "clear rejects while response stream still runs")
            finally:
                finish_stream.set()
                await activity_task
            busy_backup = await call("GET", "/system/data/export")
            check(
                busy_backup["tables"]["resumes"] and busy_backup["tables"]["evaluation_runs"],
                "busy clear leaves existing and evaluation data intact",
            )
            await call("POST", "/system/data/clear", {"confirmation": "清空本地数据"})
            empty = await call("GET", "/system/data/export")
            check(
                not empty["tables"]["experiences"] and not empty["tables"]["resumes"],
                "clear removes user data",
            )
            check(
                not any(
                    empty["tables"][key]
                    for key in (
                        "evaluation_runs",
                        "evaluation_datasets",
                        "checkpoints",
                        "checkpoint_blobs",
                        "checkpoint_writes",
                        "generation_runs",
                        "generation_llm_calls",
                    )
                ),
                "clear erases evaluation, graph state, usage and generation recovery",
            )
            check(
                all(empty["tables"][key] == value for key, value in preserved.items()),
                "clear preserves prompt catalog, service metadata and graph schema",
            )
            restored = await call("POST", "/system/demo", {})
            check(
                restored["inserted_experiences"] == 3 and restored["inserted_jobs"] == 1,
                "demo import restores data after clear",
            )
    finally:
        await database.close()
        drop_database(base, name)
    print(f"M9: {CHECK_COUNT} isolated Postgres/API checks passed; model explicitly stub")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
