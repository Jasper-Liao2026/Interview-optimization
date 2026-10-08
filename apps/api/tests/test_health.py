"""M0-3 验收：/health 返回 200。"""

from __future__ import annotations

from httpx import AsyncClient

from app import __version__
from app.tracing import TRACE_ID_HEADER
from tests.conftest import FakeDatabase


async def test_health_returns_200(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "resume-optimizer-api"
    assert payload["version"] == __version__
    assert payload["uptime_seconds"] >= 0
    assert payload["trace_id"]


async def test_health_available_under_api_prefix(client: AsyncClient) -> None:
    """/api/v1/health 与 /health 是同一个 handler，两处都要能用。"""
    assert (await client.get("/api/v1/health")).status_code == 200


async def test_health_does_not_touch_database(client: AsyncClient, override_db) -> None:
    """数据库炸了 /health 也必须 200 —— 否则 postgres 慢启动会把 api 拖死。"""
    override_db(FakeDatabase(explode=True))

    response = await client.get("/health")

    assert response.status_code == 200


async def test_trace_id_echoes_incoming_header(client: AsyncClient) -> None:
    """前端下发的 trace_id 必须原样回写，这是跨进程排查的前提。"""
    response = await client.get("/health", headers={TRACE_ID_HEADER: "trace-from-web-001"})

    assert response.headers[TRACE_ID_HEADER] == "trace-from-web-001"
    assert response.json()["trace_id"] == "trace-from-web-001"


async def test_trace_id_generated_when_absent(client: AsyncClient) -> None:
    response = await client.get("/health")

    generated = response.headers[TRACE_ID_HEADER]
    assert generated
    assert len(generated) == 32  # uuid4().hex


async def test_openapi_schema_is_served(client: AsyncClient) -> None:
    """M0-6 类型管线依赖这个端点，不能关掉。"""
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/health" in paths
    assert "/api/v1/health" in paths
    assert "/api/v1/system/info" in paths
