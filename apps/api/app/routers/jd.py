"""JD 解析接口（M1-3）。

截图输入是 M3-2 的事；这里只处理文本。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from app.agents.prompts import prompt_metadata
from app.deps import CurrentUserDep, JdParserDep, JdRepoDep, LlmDep, ObservabilityDep
from app.llm import LlmError
from app.observability import normalize_trace_id
from app.schemas import JdParseRequest, JdParseResponse
from app.tracing import current_trace_id

router = APIRouter(prefix="/jd", tags=["jd"])
logger = logging.getLogger("app.routers.jd")

TRACE_NAME = "m1.jd.parse"


@router.post(
    "/parse",
    response_model=JdParseResponse,
    summary="解析 JD 文本为结构化岗位画像",
    description=(
        "一次 LLM 调用把 JD 原文拆成必备技能 / 加分项 / 业务域 / 关键词 / 隐性偏好。\n\n"
        "输出经 Pydantic 校验；**校验失败会自动带错误反馈重试**，重试次数体现在 `warnings` 里。"
    ),
)
async def parse_jd(
    payload: JdParseRequest,
    user_id: CurrentUserDep,
    parser: JdParserDep,
    jd_repository: JdRepoDep,
    llm: LlmDep,
    observability: ObservabilityDep,
) -> JdParseResponse:
    request_trace_id = current_trace_id()
    trace_id = normalize_trace_id(request_trace_id)

    with observability.span(
        TRACE_NAME,
        trace_id=trace_id,
        input={"raw_text": payload.raw_text[:2000]},
        metadata=prompt_metadata(),
        tags=["m1", "jd"],
    ) as span:
        with observability.generation(
            "jd.parse",
            model=llm.model,
            input={"raw_text": payload.raw_text[:2000]},
            metadata=prompt_metadata(),
        ) as generation:
            try:
                outcome = await parser.parse(payload.raw_text)
            except LlmError as exc:
                # 失败也要留痕：观测的价值恰恰在于「失败可查」
                if generation is not None:
                    generation.update(level="ERROR", status_message=str(exc))
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"JD 解析失败：{exc}",
                ) from exc
            if generation is not None:
                generation.update(
                    output=outcome.value.model_dump(),
                    usage_details=outcome.llm.usage_details,
                )

        profile = outcome.value
        jd_id = None
        if payload.persist:
            row = await jd_repository.create(
                user_id,
                raw_text=payload.raw_text,
                title=payload.title or profile.title,
                company=payload.company or profile.company,
                parsed=profile.model_dump(),
                parser_model=outcome.llm.model,
            )
            jd_id = row["id"]

        if span is not None:
            span.update_trace(
                output={"title": profile.title, "required_skills": profile.required_skills},
                metadata={"is_stub": outcome.llm.is_stub, "warnings": outcome.warnings},
            )

    observability.flush()

    return JdParseResponse(
        jd_id=jd_id,
        profile=profile,
        provider=outcome.llm.provider,
        model=outcome.llm.model,
        is_stub=outcome.llm.is_stub,
        trace_id=trace_id,
        warnings=outcome.warnings,
    )
