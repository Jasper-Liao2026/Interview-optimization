"""Consistent JSON snapshots and atomic erasure of the single-user local store."""

from __future__ import annotations

import base64
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from app.db import Database
from app.schemas.local_data import LocalDataClearResponse, LocalDataExport

# Session advisory lock shared with every protected API request. A clear tries
# the exclusive form, so model calls cannot recreate data after deletion.
DATA_ACTIVITY_LOCK = 720_091_006
USER_TABLES = (
    "experiences",
    "job_descriptions",
    "resumes",
    "generation_runs",
    "generation_llm_calls",
    "resume_score_runs",
    "resume_revisions",
    "resume_edit_runs",
)
INSTANCE_TABLES = ("evaluation_runs", "evaluation_datasets")
CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")
PRESERVED_TABLES = ["service_meta", "prompt_versions", "prompt_settings", "checkpoint_migrations"]
EXTERNAL_STORES = [
    "Langfuse traces are separate; export or delete them in the configured project.",
    "Model providers may retain request logs; this operation cannot delete provider logs.",
]


class LocalDataBusyError(ValueError):
    """A write/model request is still using the local store."""


def _portable(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _portable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_portable(item) for item in value]
    return value


class LocalDataRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def _tables(self, conn: Any) -> list[str]:
        # LangGraph tables are lazy-created on the first generation, so a fresh
        # no-key installation must work before any graph has run.
        return [
            table
            for table in CHECKPOINT_TABLES
            if await conn.fetchval("select to_regclass($1)", f"public.{table}")
        ]

    async def export(self, user_id: UUID) -> LocalDataExport:
        tables: dict[str, list[dict[str, Any]]] = {}
        async with (
            self.database.connection() as conn,
            conn.transaction(isolation="repeatable_read", readonly=True),
        ):
            tables["profiles"] = [
                _portable(dict(row))
                for row in await conn.fetch(
                    "select * from public.profiles where id=$1 order by id", user_id
                )
            ]
            for table in USER_TABLES:
                tables[table] = [
                    _portable(dict(row))
                    for row in await conn.fetch(
                        f"select * from public.{table} where user_id=$1 order by id", user_id
                    )
                ]
            tables["experience_embeddings"] = [
                _portable(dict(row))
                for row in await conn.fetch(
                    "select embedding.* from public.experience_embeddings embedding "
                    "join public.experiences experience on experience.id=embedding.experience_id "
                    "where experience.user_id=$1 order by embedding.experience_id",
                    user_id,
                )
            ]
            for table in INSTANCE_TABLES:
                tables[table] = [
                    _portable(dict(row))
                    for row in await conn.fetch(f"select * from public.{table} order by id")
                ]
            present = await self._tables(conn)
            for table in PRESERVED_TABLES:
                exists = await conn.fetchval("select to_regclass($1)", f"public.{table}")
                tables[table] = (
                    [
                        _portable(dict(row))
                        for row in await conn.fetch(f"select * from public.{table}")
                    ]
                    if exists
                    else []
                )
            for table in CHECKPOINT_TABLES:
                tables[table] = (
                    [
                        _portable(dict(row))
                        for row in await conn.fetch(
                            f"select * from public.{table} order by thread_id,checkpoint_ns"
                        )
                    ]
                    if table in present
                    else []
                )
        return LocalDataExport(
            exported_at=datetime.now(UTC),
            user_id=user_id,
            tables=tables,
            preserved_tables=PRESERVED_TABLES,
            external_stores=EXTERNAL_STORES,
        )

    async def clear(self, user_id: UUID) -> LocalDataClearResponse:
        deleted: dict[str, int] = {}
        async with self.database.connection() as conn, conn.transaction():
            locked = await conn.fetchval(
                "select pg_try_advisory_xact_lock($1::bigint)", DATA_ACTIVITY_LOCK
            )
            if not locked:
                raise LocalDataBusyError("仍有生成、编辑或数据请求正在运行，请完成后再清空。")
            # Stop unguarded database clients (for example an evaluation CLI)
            # racing our delete transaction. Do not wait behind long writers.
            await conn.execute("set local lock_timeout='1s'")
            present = await self._tables(conn)
            lock_tables = [
                "profiles",
                *USER_TABLES,
                "experience_embeddings",
                *INSTANCE_TABLES,
                *present,
            ]
            await conn.execute(
                "lock table "
                + ", ".join(f"public.{table}" for table in lock_tables)
                + " in share row exclusive mode"
            )
            # Count before cascading profile deletion; counts cover all data,
            # including embeddings which have no user_id column.
            for table in USER_TABLES:
                deleted[table] = await conn.fetchval(
                    f"select count(*) from public.{table} where user_id=$1", user_id
                )
            deleted["experience_embeddings"] = await conn.fetchval(
                "select count(*) from public.experience_embeddings embedding "
                "join public.experiences experience on experience.id=embedding.experience_id "
                "where experience.user_id=$1",
                user_id,
            )
            for table in (*present, *INSTANCE_TABLES):
                result = await conn.execute(f"delete from public.{table}")
                deleted[table] = int(result.rsplit(" ", 1)[-1])
            result = await conn.execute("delete from public.profiles where id=$1", user_id)
            deleted["profiles"] = int(result.rsplit(" ", 1)[-1])
        return LocalDataClearResponse(
            cleared_at=datetime.now(UTC),
            deleted_rows=deleted,
            preserved_tables=PRESERVED_TABLES,
            external_stores=EXTERNAL_STORES,
        )
