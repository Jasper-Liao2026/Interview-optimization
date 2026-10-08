"""通用响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """统一错误体。M0 只在文档里声明，业务错误从 M1 开始使用。"""

    detail: str = Field(description="人类可读的错误说明")
    code: str | None = Field(default=None, description="机器可读的错误码")
    trace_id: str | None = Field(default=None, description="便于按 ID 捞日志")
