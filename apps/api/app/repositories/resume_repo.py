"""简历快照读写（M1-4 / M1-5）。"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from app.db import Database

logger = logging.getLogger("app.repo.resumes")

_COLUMNS = (
    "id, user_id, jd_id, title, template, header, sections, status, "
    "generator, generator_vendor, created_at, updated_at"
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
        resume_id: UUID | None = None,
        generator_vendor: str | None = None,
    ) -> dict[str, Any]:
        async with self._db.connection() as conn:
            row = await conn.fetchrow(
                f"""
                insert into public.resumes
                  (id, user_id, jd_id, title, template, header, sections,
                   generator, generator_vendor)
                values (coalesce($8, gen_random_uuid()), $1, $2, $3, $4, $5, $6, $7, $9)
                on conflict(id) do update set sections=excluded.sections,
                  generator=excluded.generator,generator_vendor=excluded.generator_vendor,
                  updated_at=now()
                  where resumes.user_id=excluded.user_id
                returning {_COLUMNS}
                """,
                user_id,
                jd_id,
                title,
                template,
                header,
                sections,
                generator,
                resume_id,
                generator_vendor,
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

    async def save_scoring_result(
        self, user_id: UUID, source: dict[str, Any], sections: list[dict], result: dict
    ) -> tuple[dict[str, Any], UUID]:
        """Save the best resume and all score snapshots in one transaction."""
        best_id, run_id = uuid4(), uuid4()
        async with self._db.connection() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"""insert into public.resumes
                (id,user_id,jd_id,title,template,header,sections,generator,generator_vendor)
                select $1,user_id,jd_id,title,template,header,$2,$6,$5
                from public.resumes where id=$3 and user_id=$4
                returning {_COLUMNS}""",
                best_id,
                sections,
                source["id"],
                user_id,
                source.get("generator_vendor"),
                source.get("generator"),
            )
            if row is None:
                raise ValueError("source resume no longer exists")
            await conn.execute(
                "insert into public.resume_score_runs "
                "(id,user_id,source_resume_id,best_resume_id,result) values($1,$2,$3,$4,$5)",
                run_id,
                user_id,
                source["id"],
                best_id,
                result,
            )
        return dict(row), run_id

    async def get_scoring_result(
        self, user_id: UUID, resume_id: UUID, run_id: UUID
    ) -> dict[str, Any] | None:
        async with self._db.connection() as conn:
            row = await conn.fetchrow(
                "select result,best_resume_id from public.resume_score_runs "
                "where id=$1 and user_id=$2 and source_resume_id=$3",
                run_id,
                user_id,
                resume_id,
            )
        return dict(row) if row else None
