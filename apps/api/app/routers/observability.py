"""观测自检路由（M0-8）。

这两个接口不是业务接口，只回答两个问题：
1. **观测配好了吗** —— `GET /observability/status`
2. **配好后真的有 trace 吗** —— `POST /observability/smoke` 跑一次真实调用并落 trace

刻意做成「跑一次真调用」而不是「伪造一条 trace」：只有真调用才能证明
SDK → 上报 → ClickHouse → UI 这条链路是通的。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.deps import CurrentUserDep, DatabaseDep, LlmDep, ObservabilityDep, SettingsDep
from app.llm import LlmError, LlmResult
from app.observability import normalize_trace_id
from app.schemas import (
    ObservabilityStatusResponse,
    SmokeRequest,
    SmokeResponse,
    UsageSummaryResponse,
)
from app.tracing import current_trace_id

router = APIRouter(tags=["observability"])

logger = logging.getLogger("app.observability")

TRACE_NAME = "m0-8-smoke"


@router.get(
    "/observability/status",
    response_model=ObservabilityStatusResponse,
    summary="观测配置现状",
    description="回答「trace 为什么没出现」：key 是否配齐、实例是否可达、LLM 是不是 stub。",
)
async def observability_status(
    settings: SettingsDep,
    observability: ObservabilityDep,
) -> ObservabilityStatusResponse:
    return ObservabilityStatusResponse(
        langfuse_configured=settings.langfuse_configured,
        langfuse_host=settings.langfuse_host,
        # 未配置时给 null 而不是 false —— 二者含义不同：
        # null 是「没开」，false 是「开了但连不上」
        langfuse_reachable=observability.auth_check() if observability.enabled else None,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        llm_is_stub=settings.llm_provider == "stub",
    )


@router.get(
    "/observability/usage",
    response_model=UsageSummaryResponse,
    summary="生成成本与延迟汇总",
)
async def observability_usage(
    user_id: CurrentUserDep,
    database: DatabaseDep,
    observability: ObservabilityDep,
) -> UsageSummaryResponse:
    rows = await database.fetch_generation_usage(str(user_id))
    for row in rows:
        row["langfuse_trace_url"] = (
            observability.trace_url(row["trace_id"]) if row["trace_id"] else None
        )
    usages = [row["usage"] for row in rows]

    def total(name: str) -> int:
        return sum(int(item.get(name) or 0) for item in usages)

    costs = [item.get("cost_usd") for item in usages]
    return UsageSummaryResponse(
        runs=rows,
        input_tokens=total("input_tokens"),
        output_tokens=total("output_tokens"),
        total_tokens=total("total_tokens"),
        latency_ms=round(sum(float(item.get("latency_ms") or 0) for item in usages), 2),
        cost_usd=round(sum(costs), 8) if costs and all(c is not None for c in costs) else None,
        unknown_usage_calls=total("unknown_usage_calls"),
    )


@router.post(
    "/observability/smoke",
    response_model=SmokeResponse,
    summary="观测自检：一次被完整 trace 的 LLM 调用",
    description=(
        "发起一次 LLM 调用并按 trace → generation 两级结构上报 Langfuse。\n\n"
        "**失败也算通过**：调用异常时 trace 照常落库，只是 observation 标记为 ERROR —— "
        "观测的价值恰恰在于失败可查。"
    ),
)
async def observability_smoke(
    payload: SmokeRequest,
    settings: SettingsDep,
    observability: ObservabilityDep,
    llm: LlmDep,
) -> SmokeResponse:
    request_trace_id = current_trace_id()
    trace_id = normalize_trace_id(request_trace_id)

    result: LlmResult | None = None
    error: str | None = None

    with observability.span(
        TRACE_NAME,
        trace_id=trace_id,
        input={"prompt": payload.prompt},
        metadata={"milestone": settings.milestone, "llm_provider": llm.provider},
        tags=["m0-8", "smoke"],
    ) as span:
        with observability.generation(
            "llm.complete",
            model=llm.model,
            input={"prompt": payload.prompt, "system": payload.system},
            model_parameters={"temperature": 0, "max_tokens": settings.llm_max_tokens},
        ) as generation:
            try:
                result = await llm.complete(payload.prompt, system=payload.system)
            except LlmError as exc:
                error = str(exc)
                logger.warning("llm smoke 失败 trace_id=%s: %s", trace_id, error)
                if generation is not None:
                    generation.update(level="ERROR", status_message=error)
            else:
                if generation is not None:
                    generation.update(output=result.text, usage_details=result.usage_details)

        if span is not None:
            span.update_trace(
                output=result.text if result is not None else None,
                metadata={"ok": result is not None, "error": error},
            )

    # 只有在「证明链路」这种场景下才强制 flush：SDK 默认批量异步上报，
    # 不 flush 的话刚跑完的 trace 要等一会儿才在 UI 出现，容易误判成上报失败。
    observability.flush()

    return SmokeResponse(
        ok=result is not None,
        trace_id=trace_id,
        request_trace_id=request_trace_id,
        langfuse_enabled=observability.enabled,
        langfuse_trace_url=observability.trace_url(trace_id),
        provider=result.provider if result is not None else llm.provider,
        model=result.model if result is not None else llm.model,
        is_stub=result.is_stub if result is not None else llm.is_stub,
        output=result.text if result is not None else None,
        input_tokens=result.input_tokens if result is not None else None,
        output_tokens=result.output_tokens if result is not None else None,
        latency_ms=result.latency_ms if result is not None else None,
        error=error,
    )
