"""数据库访问层。

M0 阶段只需要证明「后端真的能连上库、能读回 migration 落下的数据」，
因此这里是**最小实现**：asyncpg 连接池 + 两个只读查询。
M1-1 引入正式数据模型后，这里会被 SQLAlchemy / 仓储层替换，
但对上层暴露的接口（`get_database()`）保持不变。

一条刻意的设计：**连接是惰性的，不在应用启动时建立。**
数据库没起来时后端仍应能启动并让 `/health` 返回 200，
否则 `docker compose up` 里 postgres 慢启动就会把 api 拖死。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DatabaseProbe:
    """一次数据库探活的结果。"""

    connected: bool
    latency_ms: float | None = None
    server_version: str | None = None
    error: str | None = None


class Database:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()
        self._last_error: str | None = None

    # ------------------------------------------------------------ pool

    async def _get_pool(self) -> asyncpg.Pool | None:
        """惰性建池。失败返回 None，由调用方决定降级策略。"""
        if self._pool is not None:
            return self._pool

        async with self._lock:
            if self._pool is not None:
                return self._pool
            try:
                self._pool = await asyncpg.create_pool(
                    dsn=self._settings.database_url,
                    min_size=self._settings.db_pool_min_size,
                    max_size=self._settings.db_pool_max_size,
                    timeout=self._settings.db_connect_timeout_s,
                    command_timeout=5.0,
                )
                self._last_error = None
                logger.info("database pool created")
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("database pool creation failed: %s", self._last_error)
                return None
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("database pool closed")

    # ----------------------------------------------------------- probes

    async def probe(self) -> DatabaseProbe:
        """`SELECT 1` + 读 server_version，用于 /system/info 的健康展示。"""
        pool = await self._get_pool()
        if pool is None:
            return DatabaseProbe(connected=False, error=self._last_error)

        started = time.perf_counter()
        try:
            async with pool.acquire() as conn:
                await conn.execute("select 1")
                version = await conn.fetchval("show server_version")
        except Exception as exc:
            # 池可能已失效（例如 postgres 被重建），丢弃以便下次重连
            await self._discard_pool()
            message = f"{type(exc).__name__}: {exc}"
            logger.warning("database probe failed: %s", message)
            return DatabaseProbe(connected=False, error=message)

        return DatabaseProbe(
            connected=True,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            server_version=str(version),
        )

    async def fetch_service_meta(self) -> list[dict[str, Any]] | None:
        """读 `service_meta` 表 —— 这张表由 migration 创建并 seed。

        它是 M0-5「migration 流程可执行」的可观测证据：
        接口能读出它，就说明 migration 真的作用到了这个库上。
        """
        pool = await self._get_pool()
        if pool is None:
            return None
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "select key, value, updated_at from service_meta order by key"
                )
        except Exception as exc:
            logger.warning("fetch_service_meta failed: %s: %s", type(exc).__name__, exc)
            return None

        return [
            {
                "key": row["key"],
                "value": row["value"],
                "updated_at": _as_datetime(row["updated_at"]),
            }
            for row in rows
        ]

    async def _discard_pool(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            try:
                await pool.close()
            except Exception:  # 关闭失败无所谓
                logger.debug("pool close failed during discard", exc_info=True)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return None


_db: Database | None = None


def get_database() -> Database:
    """FastAPI 依赖入口。"""
    global _db
    if _db is None:
        _db = Database(get_settings())
    return _db
