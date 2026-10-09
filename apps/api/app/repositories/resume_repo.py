"""简历快照读写（M1-4 / M1-5）。"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.db import Database

logger = logging.getLogger("app.repo.resumes")

_COLUMNS = (
    "id, user_id, jd_id, title, template, header, sections, status, "
    "generator, created_at, updated_at"
)


class ResumeRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def create(
        self,
        user_id: UUID,
        *,
        jd_id: UUID | None,
        title: str,
        template: str,
        header: dict[str, Any],
        sections: list[dict[str, Any]],
        generator: str | None,
    ) -> dict[str, Any]:
        async with self._db.connection() as conn:
            row = await conn.fetchrow(
                f"""
                insert into public.resumes
                  (user_id, jd_id, title, template, header, sections, generator)
                values ($1, $2, $3, $4, $5, $6, $7)
                returning {_COLUMNS}
                """,
                user_id,
                jd_id,
                title,
                template,
                header,
                sections,
                generator,
            )
        assert row is not None
        logger.info("resume created id=%s sections=%s", row["id"], len(sections))
        return dict(row)

    async def get(self, user_id: UUID, resume_id: UUID) -> dict[str, Any] | None:
        async with self._db.connection() as conn:
            row = await conn.fetchrow(
                f"select {_COLUMNS} from public.resumes where user_id = $1 and id = $2",
                user_id,
                resume_id,
            )
        return dict(row) if row else None

    async def mark_exported(self, user_id: UUID, resume_id: UUID) -> None:
        """导出成功后打标记。

        刻意不在导出**前**标记：标记代表「确实产出过 PDF」，
        提前写会在打印失败时留下一条骗人的 exported 记录。
        """
        async with self._db.connection() as conn:
            await conn.execute(
                "update public.resumes set status = 'exported' "
                "where user_id = $1 and id = $2 and status <> 'exported'",
                user_id,
                resume_id,
            )
