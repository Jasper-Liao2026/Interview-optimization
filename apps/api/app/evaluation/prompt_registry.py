"""Prompt registry with immutable snapshots and selection rollback."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from app.agents.prompts import (
    PromptSnapshot,
    available_prompt_versions,
)
from app.agents.prompts import get_prompt_snapshot as _get_snapshot
from app.agents.prompts import rollback_prompt_version as _rollback
from app.agents.prompts import select_prompt_version as _select
from app.config import Settings, get_settings


def get_prompt_snapshot(version: str | None = None) -> PromptSnapshot:
    return _get_snapshot(version)


def list_prompt_versions() -> list[dict[str, Any]]:
    return [
        {
            "version": version,
            "snapshot": asdict(_get_snapshot(version)),
            "content_hash": _get_snapshot(version).content_hash,
            "immutable": True,
        }
        for version in available_prompt_versions()
    ]


def select_prompt_version(version: str) -> PromptSnapshot:
    return _select(version)


def rollback_prompt_version(version: str) -> PromptSnapshot:
    return _rollback(version)


async def persist_prompt_snapshot(conn: Any, item: dict[str, Any]) -> None:
    """Persist a complete bundle; callers provide a transaction for the lock."""
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended($1,0))", "prompt:" + item["version"]
    )
    snapshot = item["snapshot"]
    existing = await conn.fetchrow(
        "select content_hash,snapshot from public.prompt_versions where version=$1",
        item["version"],
    )
    if existing is not None:
        if str(existing["content_hash"]) != item["content_hash"]:
            raise ValueError(f"prompt 版本已存在但内容冲突: {item['version']}")
        stored = existing["snapshot"]
        # JSON normalizes tuple fields to arrays. Compare canonical JSON so
        # a read-back matches asdict(), while legacy partial hashes still
        # cannot authorize changes to an already persisted complete bundle.
        if stored is not None:
            if json.dumps(stored, sort_keys=True) != json.dumps(snapshot, sort_keys=True):
                raise ValueError(f"prompt snapshot 已存在但内容冲突: {item['version']}")
            return
        await conn.execute(
            "update public.prompt_versions set snapshot=$2 "
            "where version=$1 and snapshot is null",
            item["version"], snapshot,
        )
        return
    await conn.execute(
        "insert into public.prompt_versions("
        "version,jd_system,rewrite_system,jd_image,content_hash,snapshot) "
        "values($1,$2,$3,$4,$5,$6)",
        item["version"], snapshot["jd_system"], snapshot["rewrite_system"],
        snapshot["jd_image"], item["content_hash"], snapshot,
    )


class PromptVersionRepository:
    """Persistence adapter for prompt snapshots/settings.

    The registry remains the source of shipped code snapshots.  This adapter
    records the selected version and immutable snapshot in Postgres, allowing
    each evaluation/run to be traced and rolled back.
    """

    def __init__(self, database: Any, *, settings: Settings | None = None) -> None:
        self.database = database
        # Keep persistence changes connected to the same mutable settings object
        # used by FastAPI dependencies.  This makes a selection effective for
        # subsequent requests without requiring a process restart.
        self.settings = settings or get_settings()

    async def ensure_shipped(self) -> None:
        async with self.database.connection() as conn, conn.transaction():
            for item in list_prompt_versions():
                await persist_prompt_snapshot(conn, item)

    async def selected(self) -> str:
        async with self.database.connection() as conn:
            row = await conn.fetchrow(
                "select selected_version from public.prompt_settings where key='default'"
            )
        # A persisted choice wins; a fresh database must honor PROMPT_VERSION.
        version = str(row["selected_version"]) if row else self.settings.prompt_version
        snapshot = _select(version)
        self.settings.prompt_version = snapshot.version
        return snapshot.version

    async def select(self, version: str, *, actor: str = "system") -> dict[str, Any]:
        snapshot = _get_snapshot(version)
        await self.ensure_shipped()
        async with self.database.connection() as conn, conn.transaction():
            await conn.execute(
                "select pg_advisory_xact_lock(hashtextextended($1,0))", "prompt:default"
            )
            current = await conn.fetchrow(
                "select selected_version,previous_version from public.prompt_settings "
                "where key='default' for update"
            )
            selected = str(current["selected_version"]) if current else self.settings.prompt_version
            # Preserve rollback on an idempotent selection. The first persisted
            # change can roll back to the configured version that preceded it.
            previous = (
                current["previous_version"] if current else None
            ) if selected == version else selected
            row = await conn.fetchrow(
                "insert into public.prompt_settings("
                "key,selected_version,previous_version,changed_by) "
                "values('default',$1,$2,$3) "
                "on conflict(key) do update set "
                "previous_version=excluded.previous_version,"
                "selected_version=excluded.selected_version,"
                "changed_by=excluded.changed_by,changed_at=now() "
                "returning key,selected_version,previous_version,changed_at",
                version,
                previous,
                actor,
            )
        _select(snapshot.version)
        self.settings.prompt_version = snapshot.version
        return dict(row)

    async def rollback(self, *, actor: str = "system") -> dict[str, Any]:
        current = await self.selected()
        async with self.database.connection() as conn:
            row = await conn.fetchrow(
                "select previous_version from public.prompt_settings where key='default'"
            )
        previous = row["previous_version"] if row else None
        if not previous or previous == current:
            raise ValueError("没有可回滚的 prompt 版本")
        return await self.select(str(previous), actor=actor)
