"""用户档案读写（当前只读：本地单机单用户，档案由 seed.sql 固定）。"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.db import Database

logger = logging.getLogger("app.repo.profiles")


class ProfileRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def get(self, user_id: UUID) -> dict[str, Any] | None:
        async with self._db.connection() as conn:
            row = await conn.fetchrow(
                "select id, display_name, headline, created_at, updated_at "
                "from public.profiles where id = $1",
                user_id,
            )
        return dict(row) if row else None

    async def ensure(
        self, user_id: UUID, *, display_name: str, headline: str | None
    ) -> dict[str, Any]:
        """确保档案存在。

        正常路径上 seed.sql 已经建好了；这里兜一手，
        避免「忘了跑 seed」表现为一个难懂的 500（简历抬头取不到）。
        """
        async with self._db.connection() as conn:
            row = await conn.fetchrow(
                """
                insert into public.profiles (id, display_name, headline)
                values ($1, $2, $3)
                on conflict (id) do update set display_name = excluded.display_name
                returning id, display_name, headline, created_at, updated_at
                """,
                user_id,
                display_name,
                headline,
            )
        assert row is not None
        logger.info("profile ensured id=%s", user_id)
        return dict(row)
