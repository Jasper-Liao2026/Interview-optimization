"""M0 自检相关的响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """进程级健康检查结果。不依赖数据库，永远能返回。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "ok",
                "service": "resume-optimizer-api",
                "version": "0.1.0",
                "environment": "local",
                "uptime_seconds": 12.5,
                "checked_at": "2026-10-08T12:00:00Z",
                "trace_id": "8f1c0b2e6a4d4a1f9c3e7b5d2a0f6c11",
            }
        }
    )

    status: Literal["ok"] = Field(description="进程存活即返回 ok")
    service: str = Field(description="服务标识")
    version: str = Field(description="后端版本号")
    environment: str = Field(description="运行环境：local | staging | production")
    uptime_seconds: float = Field(description="进程已运行秒数")
    checked_at: datetime = Field(description="本次检查时间（UTC）")
    trace_id: str = Field(description="贯穿前后端的请求 ID")


class DatabaseStatus(BaseModel):
    """数据库探活结果。连不上不算接口失败，只降级。"""

    connected: bool
    latency_ms: float | None = Field(default=None, description="探活往返耗时")
    server_version: str | None = Field(default=None, description="Postgres 版本")
    error: str | None = Field(default=None, description="连接失败原因，成功时为 null")


class ServiceMetaEntry(BaseModel):
    """`service_meta` 表的一行。"""

    key: str
    value: str
    updated_at: datetime | None = None


class SystemInfoResponse(BaseModel):
    """穿透到数据库的系统信息。

    M0-7 的验收就落在这个接口上：前端能把它渲染出来，
    就说明 UI → FastAPI → Postgres 这一条链路是通的。
    """

    service: str
    version: str
    milestone: str = Field(description="当前里程碑，用于自检页对照进度")
    environment: str
    trace_id: str
    database: DatabaseStatus
    # 注意：这里**不给默认值**。契约是「永远存在，数据库不可用时为空数组」，
    # 若给 default_factory，OpenAPI 会把它标成非必填，前端类型随之变成可选，
    # 逼着每个调用点写 `?? []`。让后端声明为必填，类型才与契约一致。
    meta: list[ServiceMetaEntry] = Field(
        description="来自 service_meta 表；数据库不可用时为空数组",
    )
