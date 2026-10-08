"""系统信息路由 —— M0-7「前后端联通」的落点。

这个接口刻意设计成**必须读库**：它返回的 `meta` 来自 migration 创建的
`service_meta` 表。前端能把数据渲染出来，等于同时证明了
「前端 → 后端 → 数据库」与「migration 可执行」两件事。
"""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.deps import DatabaseDep, SettingsDep
from app.schemas import DatabaseStatus, ServiceMetaEntry, SystemInfoResponse
from app.tracing import current_trace_id

router = APIRouter(tags=["system"])


@router.get(
    "/system/info",
    response_model=SystemInfoResponse,
    summary="系统信息",
    description="穿透到 Postgres 的读库校验。数据库不可用时降级返回，接口本身不报错。",
)
async def system_info(settings: SettingsDep, database: DatabaseDep) -> SystemInfoResponse:
    probe = await database.probe()

    rows = await database.fetch_service_meta() if probe.connected else None
    meta = [ServiceMetaEntry.model_validate(row) for row in (rows or [])]

    return SystemInfoResponse(
        service=settings.service_id,
        version=__version__,
        milestone=settings.milestone,
        environment=settings.environment,
        trace_id=current_trace_id(),
        database=DatabaseStatus(
            connected=probe.connected,
            latency_ms=probe.latency_ms,
            server_version=probe.server_version,
            error=probe.error,
        ),
        meta=meta,
    )
