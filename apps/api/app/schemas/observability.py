"""观测自检相关的响应模型（M0-8）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.resume import GenerationUsage

DEFAULT_SMOKE_PROMPT = "用一句话说明「岗位适配版简历」是什么意思。"


class ObservabilityStatusResponse(BaseModel):
    """观测配置现状。用来回答「trace 为什么没出现」这类问题。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "langfuse_configured": True,
                "langfuse_host": "http://localhost:3000",
                "langfuse_reachable": True,
                "llm_provider": "stub",
                "llm_model": "deepseek-chat",
                "llm_is_stub": True,
            }
        }
    )

    langfuse_configured: bool = Field(description="是否配齐了 public / secret key")
    langfuse_host: str = Field(description="Langfuse 实例地址")
    langfuse_reachable: bool | None = Field(
        default=None,
        description="auth_check 结果；未配置时为 null（而不是 false，二者含义不同）",
    )
    llm_provider: str = Field(description="stub | openai-compatible")
    llm_model: str = Field(description="当前使用的模型名")
    llm_is_stub: bool = Field(description="true 表示不会发起真实网络调用")


class SmokeRequest(BaseModel):
    """一次观测自检的入参。"""

    model_config = ConfigDict(
        json_schema_extra={"example": {"prompt": DEFAULT_SMOKE_PROMPT, "system": None}}
    )

    prompt: str = Field(default=DEFAULT_SMOKE_PROMPT, min_length=1, max_length=4000)
    system: str | None = Field(default=None, max_length=1000, description="可选的 system 提示")


class SmokeResponse(BaseModel):
    """一次被观测的 LLM 调用结果。

    `trace_id` 是**规范化之后**给 Langfuse 用的 ID；`request_trace_id` 是前端下发的原始
    `X-Request-Id`。两者通常互为「去横线」关系，一并返回是为了便于人工核对。
    """

    ok: bool = Field(description="调用是否成功；失败时 trace 里同样留有记录")
    trace_id: str
    request_trace_id: str
    langfuse_enabled: bool
    langfuse_trace_url: str | None = Field(default=None, description="未启用或无 URL 时为 null")
    provider: str
    model: str
    is_stub: bool
    output: str | None = Field(default=None, description="失败时为 null")
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    error: str | None = Field(default=None, description="失败原因")


class UsageRun(BaseModel):
    run_id: str
    trace_id: str | None = None
    status: str | None = None
    updated_at: datetime | None = None
    langfuse_trace_url: str | None = None
    usage: GenerationUsage = Field(default_factory=GenerationUsage)


class UsageSummaryResponse(BaseModel):
    runs: list[UsageRun] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0
    cost_usd: float | None = None
    unknown_usage_calls: int = 0
