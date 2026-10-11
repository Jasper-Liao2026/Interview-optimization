"""Coordinate model/write requests with local-data erasure across API workers."""

from collections.abc import AsyncIterator

from psycopg import AsyncConnection

from app.db import DatabaseUnavailable
from app.deps import SettingsDep
from app.repositories.local_data_repo import DATA_ACTIVITY_LOCK


async def protect_local_data(settings: SettingsDep) -> AsyncIterator[None]:
    # A dedicated connection avoids holding a pool slot while business
    # repositories borrow other connections. The shared lock allows normal
    # requests to run concurrently and survives multiple API worker processes.
    try:
        conn = await AsyncConnection.connect(
            settings.database_url, autocommit=True, connect_timeout=3
        )
    except Exception as exc:
        raise DatabaseUnavailable("无法建立本地数据保护连接") from exc
    async with conn:
        await conn.execute("select pg_advisory_lock_shared(%s)", (DATA_ACTIVITY_LOCK,))
        try:
            yield
        finally:
            await conn.execute("select pg_advisory_unlock_shared(%s)", (DATA_ACTIVITY_LOCK,))
