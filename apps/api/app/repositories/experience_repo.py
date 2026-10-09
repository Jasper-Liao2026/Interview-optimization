"""经历条目的读写（M1-2）。"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.db import Database
from app.schemas import ExperienceCreate

logger = logging.getLogger("app.repo.experiences")

# 显式列名而不是 select *：新增列时不会悄悄改变返回形状
_COLUMNS = (
    "id, user_id, kind, org, role, start_date, end_date, "
    "raw_description, skill_tags, highlights, sort_order, created_at, updated_at"
)


class ExperienceRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def list_for_user(self, user_id: UUID) -> list[dict[str, Any]]:
        """列出某用户的全部经历，按分类 + sort_order 排序（前端分组展示直接用这个顺序）。"""
        async with self._db.connection() as conn:
            rows = await conn.fetch(
                f"select {_COLUMNS} from public.experiences "
                "where user_id = $1 "
                "order by kind, sort_order, created_at",
                user_id,
            )
        return [dict(row) for row in rows]

    async def list_by_ids(self, user_id: UUID, ids: list[UUID]) -> list[dict[str, Any]]:
        """按给定 ID 取经历，**保持传入顺序**。

        用 `array_position` 排序而不是 `in` 之后靠数据库默认顺序：
        「用户勾选了 A、B、C 三段经历」时，简历里的顺序应当就是他勾选的顺序。
        """
        if not ids:
            return []
        async with self._db.connection() as conn:
            rows = await conn.fetch(
                f"select {_COLUMNS} from public.experiences "
                "where user_id = $1 and id = any($2::uuid[]) "
                "order by array_position($2::uuid[], id)",
                user_id,
                ids,
            )
        return [dict(row) for row in rows]

    async def get(self, user_id: UUID, experience_id: UUID) -> dict[str, Any] | None:
        async with self._db.connection() as conn:
            row = await conn.fetchrow(
                f"select {_COLUMNS} from public.experiences where user_id = $1 and id = $2",
                user_id,
                experience_id,
            )
        return dict(row) if row else None

    async def create(self, user_id: UUID, payload: ExperienceCreate) -> dict[str, Any]:
        async with self._db.connection() as conn:
            row = await conn.fetchrow(
                f"""
                insert into public.experiences
                  (user_id, kind, org, role, start_date, end_date,
                   raw_description, skill_tags, highlights, sort_order)
                values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                returning {_COLUMNS}
                """,
                user_id,
                payload.kind,
                payload.org,
                payload.role,
                payload.start_date,
                payload.end_date,
                payload.raw_description,
                list(payload.skill_tags),
                list(payload.highlights),
                payload.sort_order,
            )
        assert row is not None  # insert ... returning 必然有行
        logger.info("experience created id=%s kind=%s", row["id"], row["kind"])
        return dict(row)

    async def delete(self, user_id: UUID, experience_id: UUID) -> bool:
        async with self._db.connection() as conn:
            status = await conn.execute(
                "delete from public.experiences where user_id = $1 and id = $2",
                user_id,
                experience_id,
            )
        # asyncpg 的 execute 返回 'DELETE 1' 这样的状态串
        return status.endswith(" 1")
