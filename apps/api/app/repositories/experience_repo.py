"""经历条目的读写（M1-2 建立，M2-3 补全 CRUD）。

SQL 全部收在这一层：上层（routers / agents）不写 SQL。
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.db import Database
from app.schemas import ExperienceCreate, ExperienceUpdate

logger = logging.getLogger("app.repo.experiences")

# 显式列名而不是 select *：新增列时不会悄悄改变返回形状
_COLUMNS = (
    "id, user_id, kind, org, role, start_date, end_date, "
    "raw_description, skill_tags, highlights, metrics, variants, "
    "sort_order, created_at, updated_at"
)

# 可写列的固定顺序。create / update 共用，避免两处列名与占位符各写一遍后悄悄错位。
_WRITABLE = (
    "kind",
    "org",
    "role",
    "start_date",
    "end_date",
    "raw_description",
    "skill_tags",
    "highlights",
    "metrics",
    "variants",
    "sort_order",
)

# 列表排序：**分类分组 → 组内按经历时间倒序**（M2-5）。
#
# `end_date desc nulls first`：进行中的经历 end_date 为空，排在组内最前 ——
# 「现在还在做」比「三年前做完了」更该被先看到，这和简历本身的读法一致。
# 最后的 created_at / id 是**稳定化**兜底：同一时间的两条经历不能因为
# 数据库返回顺序随机而每次刷新都换位置（否则前端列表会莫名跳动）。
_ORDER_BY = "kind, end_date desc nulls first, start_date desc nulls last, created_at desc, id"


def _to_json(payload: ExperienceCreate | ExperienceUpdate, field: str) -> list[dict[str, Any]]:
    """把 Pydantic 子模型列表转成可写进 jsonb 的纯 Python 结构。

    走 `mode="json"` 而不是直接 `model_dump()`：JSON 模式下不会留下
    `date` / `UUID` 这类 asyncpg 编码器认不出的对象，将来子结构加字段也不会踩到。
    """
    items = getattr(payload, field)
    return [item.model_dump(mode="json") for item in items]


def _write_values(payload: ExperienceCreate | ExperienceUpdate) -> tuple[Any, ...]:
    """按 `_WRITABLE` 的顺序摊平成参数元组。"""
    return (
        payload.kind,
        payload.org,
        payload.role,
        payload.start_date,
        payload.end_date,
        payload.raw_description,
        list(payload.skill_tags),
        list(payload.highlights),
        _to_json(payload, "metrics"),
        _to_json(payload, "variants"),
        payload.sort_order,
    )


class ExperienceRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def list_for_user(self, user_id: UUID) -> list[dict[str, Any]]:
        """列出某用户的全部经历。

        返回顺序**就是前端分组展示的顺序**（分类分组、组内时间倒序），
        前端不需要再排一次 —— 否则「谁负责排序」会出现两个说法。
        """
        async with self._db.connection() as conn:
            rows = await conn.fetch(
                f"select {_COLUMNS} from public.experiences "
                f"where user_id = $1 order by {_ORDER_BY}",
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
        columns = ", ".join(_WRITABLE)
        placeholders = ", ".join(f"${index}" for index in range(2, len(_WRITABLE) + 2))
        async with self._db.connection() as conn:
            row = await conn.fetchrow(
                f"""
                insert into public.experiences (user_id, {columns})
                values ($1, {placeholders})
                returning {_COLUMNS}
                """,
                user_id,
                *_write_values(payload),
            )
        assert row is not None  # insert ... returning 必然有行
        logger.info(
            "experience created id=%s kind=%s metrics=%d variants=%d",
            row["id"],
            row["kind"],
            len(row["metrics"] or []),
            len(row["variants"] or []),
        )
        return dict(row)

    async def update(
        self, user_id: UUID, experience_id: UUID, payload: ExperienceUpdate
    ) -> dict[str, Any] | None:
        """PUT 全量替换：未提交的字段按 Pydantic 默认值处理，而不是保留旧值。

        这一点与 PATCH 的差别必须在接口文档里说清楚，否则调用方会以为
        「只传 raw_description 就能只改描述」，结果把标签和量化结果清空了。
        `updated_at` 由数据库触发器维护，这里不手写。
        """
        assignments = ", ".join(
            f"{column} = ${index}" for index, column in enumerate(_WRITABLE, start=3)
        )
        async with self._db.connection() as conn:
            row = await conn.fetchrow(
                f"""
                update public.experiences
                set {assignments}
                where user_id = $1 and id = $2
                returning {_COLUMNS}
                """,
                user_id,
                experience_id,
                *_write_values(payload),
            )
        if row is None:
            return None
        logger.info("experience updated id=%s kind=%s", row["id"], row["kind"])
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
