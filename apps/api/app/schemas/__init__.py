"""Pydantic schema —— 接口类型的单一定义源。

tech-stack.md §6.1：**任何接口改动都先改这里**，
前端类型由 `pnpm gen:types` 从 OpenAPI 自动生成，禁止手写。
"""

from app.schemas.health import (
    DatabaseStatus,
    HealthResponse,
    ServiceMetaEntry,
    SystemInfoResponse,
)
from app.schemas.meta import ErrorResponse

__all__ = [
    "DatabaseStatus",
    "ErrorResponse",
    "HealthResponse",
    "ServiceMetaEntry",
    "SystemInfoResponse",
]
