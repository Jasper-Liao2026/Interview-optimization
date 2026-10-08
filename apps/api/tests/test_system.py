"""M0-5 / M0-7 验收：system/info 能读回 migration 落下的数据。"""

from __future__ import annotations

from httpx import AsyncClient

from app.db import DatabaseProbe
from tests.conftest import SAMPLE_META, FakeDatabase


async def test_system_info_returns_meta_from_database(client: AsyncClient, override_db) -> None:
    override_db(FakeDatabase(meta=SAMPLE_META))

    response = await client.get("/api/v1/system/info")

    assert response.status_code == 200
    payload = response.json()
    assert payload["database"]["connected"] is True
    assert payload["database"]["server_version"] == "16.4"
    assert [entry["key"] for entry in payload["meta"]] == ["schema_version", "service_name"]
    assert payload["milestone"]
    assert payload["trace_id"]


async def test_system_info_degrades_when_database_is_down(client: AsyncClient, override_db) -> None:
    """数据库连不上时降级，而不是 500 —— 自检页要能显示「哪一段断了」。"""
    override_db(FakeDatabase(probe=DatabaseProbe(connected=False, error="ConnectionRefusedError")))

    response = await client.get("/api/v1/system/info")

    assert response.status_code == 200
    payload = response.json()
    assert payload["database"]["connected"] is False
    assert payload["database"]["error"] == "ConnectionRefusedError"
    assert payload["meta"] == []
