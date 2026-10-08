"""测试夹具。

设计要点：**测试不依赖真实数据库。**
`get_database` 是 FastAPI 依赖，测试里用 `dependency_overrides` 换成假实现，
这样「数据库通了」和「数据库没通」两条分支都能被确定性地覆盖，
也让 CI 不必起 postgres。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db import DatabaseProbe, get_database
from app.main import create_app


class FakeDatabase:
    """最小可用的 Database 替身。"""

    def __init__(
        self,
        probe: DatabaseProbe | None = None,
        meta: list[dict[str, Any]] | None = None,
        *,
        explode: bool = False,
    ) -> None:
        self._probe = probe or DatabaseProbe(connected=True, latency_ms=1.23, server_version="16.4")
        self._meta = meta
        self._explode = explode
        self.probe_calls = 0

    async def probe(self) -> DatabaseProbe:
        self.probe_calls += 1
        if self._explode:
            raise AssertionError("本测试不允许访问数据库")
        return self._probe

    async def fetch_service_meta(self) -> list[dict[str, Any]] | None:
        if self._explode:
            raise AssertionError("本测试不允许访问数据库")
        return self._meta

    async def close(self) -> None:  # pragma: no cover - 生命周期里会被调到
        return None


SAMPLE_META: list[dict[str, Any]] = [
    {
        "key": "schema_version",
        "value": "m0_0001",
        "updated_at": datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
    },
    {
        "key": "service_name",
        "value": "resume-optimizer-api",
        "updated_at": datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
    },
]


@pytest.fixture
def app() -> Iterator[FastAPI]:
    application = create_app()
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
def override_db(app: FastAPI):
    def _apply(fake: FakeDatabase) -> FakeDatabase:
        app.dependency_overrides[get_database] = lambda: fake
        return fake

    return _apply


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client
