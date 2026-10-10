"""Official LangGraph checkpointers and durable generation metadata."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection

from app.db import Database


class RunConflictError(RuntimeError):
    pass


class GenerationRunStore:
    """User-scoped run repository paired with a LangGraph checkpointer."""

    def __init__(self, database: Database | None = None, *, database_url: str = "") -> None:
        self.database = database
        self.database_url = database_url
        self.backend = "postgres" if database is not None else "memory"
        self._rows: dict[tuple[UUID, UUID], dict[str, Any]] = {}
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._saver = InMemorySaver()
        self._usage: dict[tuple[UUID, UUID], list[dict[str, Any]]] = {}

    async def record_usage(self, user_id: UUID, run_id: UUID, call: dict[str, Any]) -> None:
        call = {"id": str(uuid4()), **call}
        if self.database is None:
            self._usage.setdefault((user_id, run_id), []).append(copy.deepcopy(call))
            return
        async with self.database.connection() as conn:
            await conn.execute(
                "insert into public.generation_llm_calls(id,run_id,user_id,payload) "
                "values($1,$2,$3,$4)",
                UUID(call["id"]),
                run_id,
                user_id,
                call,
            )

    async def usage(self, user_id: UUID, run_id: UUID) -> list[dict[str, Any]]:
        if self.database is None:
            return copy.deepcopy(self._usage.get((user_id, run_id), []))
        async with self.database.connection() as conn:
            rows = await conn.fetch(
                "select payload from public.generation_llm_calls where user_id=$1 and run_id=$2 "
                "order by created_at,id",
                user_id,
                run_id,
            )
        return [row["payload"] for row in rows]

    @asynccontextmanager
    async def lock(self, run_id: UUID) -> AsyncIterator[None]:
        if self.database is None:
            async with self._locks.setdefault(run_id, asyncio.Lock()):
                yield
            return
        async with await AsyncConnection.connect(self.database_url, autocommit=True) as conn:
            key = int.from_bytes(run_id.bytes[:8], "big", signed=True)
            await conn.execute("select pg_advisory_lock(%s)", (key,))
            try:
                yield
            finally:
                await conn.execute("select pg_advisory_unlock(%s)", (key,))

    async def get(self, user_id: UUID, run_id: UUID) -> dict[str, Any] | None:
        if self.database is None:
            return copy.deepcopy(self._rows.get((user_id, run_id)))
        async with self.database.connection() as conn:
            row = await conn.fetchrow(
                "select payload from public.generation_runs where id=$1 and user_id=$2",
                run_id,
                user_id,
            )
        return row["payload"] if row else None

    async def save(self, user_id: UUID, run_id: UUID, payload: dict[str, Any]) -> None:
        payload["updated_at"] = datetime.now(UTC).isoformat()
        if self.database is None:
            self._rows[(user_id, run_id)] = copy.deepcopy(payload)
            return
        async with self.database.connection() as conn:
            row = await conn.fetchrow(
                "insert into public.generation_runs(id,user_id,payload) values($1,$2,$3) "
                "on conflict(id) do update set payload=excluded.payload,updated_at=now() "
                "where generation_runs.user_id=excluded.user_id returning id",
                run_id,
                user_id,
                payload,
            )
        if row is None:
            raise RunConflictError("运行 ID 已属于其他用户")

    @asynccontextmanager
    async def checkpointer(self) -> AsyncIterator[Any]:
        if self.database is None:
            yield self._saver
            return
        async with AsyncPostgresSaver.from_conn_string(self.database_url) as saver:
            await saver.setup()
            yield saver
