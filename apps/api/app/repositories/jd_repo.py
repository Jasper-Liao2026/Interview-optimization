"""JD 读写（M1-3）。"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.db import Database

logger = logging.getLogger("app.repo.jd")

_COLUMNS = "id, user_id, title, company, raw_text, parsed, parser_model, created_at, updated_at"


class JobDescriptionRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def create(
        self,
        user_id: UUID,
        *,
        raw_text: str,
        title: str | None,
        company: str | None,
        parsed: dict[str, Any] | None,
        parser_model: str | None,
    ) -> dict[str, Any]:
        async with self._db.connection() as conn:
            row = await conn.fetchrow(
                f"""
                insert into public.job_descriptions
                  (user_id, title, company, raw_text, parsed, parser_model)
                values ($1, $2, $3, $4, $5, $6)
                returning {_COLUMNS}
                """,
                user_id,
                title,
                company,
                raw_text,
                parsed,
                parser_model,
            )
        assert row is not None
        logger.info("jd created id=%s parser_model=%s", row["id"], parser_model)
        return dict(row)

    async def get(self, user_id: UUID, jd_id: UUID) -> dict[str, Any] | None:
        async with self._db.connection() as conn:
            row = await conn.fetchrow(
                f"select {_COLUMNS} from public.job_descriptions where user_id = $1 and id = $2",
                user_id,
                jd_id,
            )
        return dict(row) if row else None

    async def list_for_user(self, user_id: UUID, limit: int = 50) -> list[dict[str, Any]]:
        async with self._db.connection() as conn:
            rows = await conn.fetch(
                f"select {_COLUMNS} from public.job_descriptions "
                "where user_id = $1 order by created_at desc limit $2",
                user_id,
                limit,
            )
        return [dict(row) for row in rows]
