"""Persistence for golden datasets and evaluation runs."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from app.db import Database


class EvaluationStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def save_dataset(self, version: str, cases: list[dict[str, Any]]) -> UUID:
        payload = json.dumps(cases, ensure_ascii=False, sort_keys=True)
        dataset_id = uuid4()
        async with self._db.connection() as conn, conn.transaction():
            await conn.execute(
                "select pg_advisory_xact_lock(hashtextextended($1,0))", "dataset:" + version
            )
            row = await conn.fetchrow(
                "select id,cases from public.evaluation_datasets where version=$1 for update",
                version,
            )
            if row:
                existing = json.dumps(row["cases"], ensure_ascii=False, sort_keys=True)
                if existing != payload:
                    raise ValueError(f"golden dataset version 已存在但内容冲突: {version}")
                return UUID(str(row["id"]))
            await conn.execute(
                "insert into public.evaluation_datasets("
                "id,version,synthetic,cases) values($1,$2,$3,$4)",
                dataset_id,
                version,
                True,
                cases,
            )
        return dataset_id

    async def save_prompt_snapshots(self, versions: list[dict[str, Any]]) -> None:
        async with self._db.connection() as conn, conn.transaction():
            for item in versions:
                await conn.execute(
                    "select pg_advisory_xact_lock(hashtextextended($1,0))",
                    "prompt:" + item["version"],
                )
                snapshot = item["snapshot"]
                existing = await conn.fetchval(
                    "select content_hash from public.prompt_versions where version=$1",
                    item["version"],
                )
                if existing is not None and str(existing) != item["content_hash"]:
                    raise ValueError(f"prompt 版本已存在但内容冲突: {item['version']}")
                await conn.execute(
                    "insert into public.prompt_versions("
                    "version,jd_system,rewrite_system,jd_image,content_hash) "
                    "values($1,$2,$3,$4,$5) on conflict(version) do nothing",
                    item["version"],
                    snapshot["jd_system"],
                    snapshot["rewrite_system"],
                    snapshot["jd_image"],
                    item["content_hash"],
                )

    async def save_run(self, dataset_id: UUID, report: dict[str, Any]) -> UUID:
        run_id = uuid4()
        async with self._db.connection() as conn:
            await conn.execute(
                "insert into public.evaluation_runs(id,dataset_id,prompt_a,prompt_b,mode,result) "
                "values($1,$2,$3,$4,$5,$6)",
                run_id,
                dataset_id,
                report["prompt_versions"][0],
                report["prompt_versions"][1],
                report["mode"],
                report,
            )
        return run_id
