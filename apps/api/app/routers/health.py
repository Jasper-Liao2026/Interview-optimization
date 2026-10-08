"""健康检查路由。

同一个 router 被挂载两次（见 app/main.py）：
  - `/health`            → 给 docker healthcheck / 负载均衡用，无版本前缀
  - `/api/v1/health`     → 给前端业务侧用，走统一的 api_prefix
"""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.deps import SettingsDep
from app.runtime import uptime_seconds, utcnow
from app.schemas import HealthResponse
from app.tracing import current_trace_id

router = APIRouter(tags=["system"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="健康检查",
    description="进程级探活。**不访问数据库**，因此数据库挂掉时本接口仍返回 200。",
)
async def health(settings: SettingsDep) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.service_id,
        version=__version__,
        environment=settings.environment,
        uptime_seconds=uptime_seconds(),
        checked_at=utcnow(),
        trace_id=current_trace_id(),
    )
