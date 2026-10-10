"""Prompt registry with immutable snapshots and selection rollback."""

from __future__ import annotations

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
        async with self.database.connection() as conn:
            for item in list_prompt_versions():
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
                    "values($1,$2,$3,$4,$5) "
                    "on conflict(version) do nothing",
                    item["version"],
                    snapshot["jd_system"],
                    snapshot["rewrite_system"],
                    snapshot["jd_image"],
                    _get_snapshot(item["version"]).content_hash,
                )

    async def selected(self) -> str:
        async with self.database.connection() as conn:
            row = await conn.fetchrow(
                "select selected_version from public.prompt_settings where key='default'"
            )
        version = str(row["selected_version"]) if row else available_prompt_versions()[0]
        snapshot = _select(version)
        self.settings.prompt_version = snapshot.version
        return snapshot.version

    async def select(self, version: str, *, actor: str = "system") -> dict[str, Any]:
        snapshot = _get_snapshot(version)
        async with self.database.connection() as conn, conn.transaction():
            existing = await conn.fetchval(
                "select content_hash from public.prompt_versions where version=$1", version
            )
            if existing is not None and str(existing) != snapshot.content_hash:
                raise ValueError(f"prompt 版本已存在但内容冲突: {version}")
            await conn.execute(
                "insert into public.prompt_versions("
                "version,jd_system,rewrite_system,jd_image,content_hash) "
                "values($1,$2,$3,$4,$5) "
                "on conflict(version) do nothing",
                version,
                snapshot.jd_system,
                snapshot.rewrite_system,
                snapshot.jd_image,
                snapshot.content_hash,
            )
            row = await conn.fetchrow(
                "insert into public.prompt_settings("
                "key,selected_version,previous_version,changed_by) "
                "values('default',$1,(select selected_version from "
                "public.prompt_settings where key='default'),$2) "
                "on conflict(key) do update set "
                "previous_version=prompt_settings.selected_version,"
                "selected_version=excluded.selected_version,"
                "changed_by=excluded.changed_by,changed_at=now() "
                "returning key,selected_version,previous_version,changed_at",
                version,
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
