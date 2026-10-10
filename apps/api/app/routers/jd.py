"""JD 解析接口（M1-3）。

M3：文本 / 截图解析、持久化管理与匹配矩阵。
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from app.agents.prompts import prompt_metadata
from app.deps import (
    CurrentUserDep,
    ExperienceRepoDep,
    JdParserDep,
    JdRepoDep,
    LlmDep,
    MatchingServiceDep,
    ObservabilityDep,
)
from app.llm import LlmError
from app.observability import normalize_trace_id
from app.schemas import JdParseRequest, JdParseResponse
from app.schemas.jd import (
    JdImageParseRequest,
    JdImageParseResponse,
    JdListResponse,
    JdMetadataUpdate,
    JdRead,
    JobProfile,
)
from app.schemas.matching import MatchRequest, MatchResponse
from app.tracing import current_trace_id

router = APIRouter(prefix="/jd", tags=["jd"])
logger = logging.getLogger("app.routers.jd")

TRACE_NAME = "m3.jd.parse"


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
        tags=["m3", "jd"],
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


@router.post("/parse-image", response_model=JdImageParseResponse, summary="视觉模型直读 JD 截图")
async def parse_image(
    payload: JdImageParseRequest,
    user_id: CurrentUserDep,
    parser: JdParserDep,
    jd_repository: JdRepoDep,
    observability: ObservabilityDep,
    llm: LlmDep,
) -> JdImageParseResponse:
    if (llm.settings.vision_provider or llm.settings.llm_provider) == "stub":
        raise HTTPException(503, "演示模型不识别截图，请配置支持视觉的模型后重试，或粘贴 JD 文本。")
    trace_id = normalize_trace_id(current_trace_id())
    with observability.span(
        "m3.jd.parse-image",
        trace_id=trace_id,
        input={"source_type": "image"},
        metadata=prompt_metadata(),
        tags=["m3", "jd", "vision"],
    ):
        with observability.generation(
            "jd.parse-image",
            model=llm.settings.vision_model or llm.model,
            input={"source_type": "image"},
            metadata=prompt_metadata(),
        ) as generation:
            try:
                outcome = await parser.parse_image(payload.image_data_url)
            except LlmError as exc:
                if generation is not None:
                    generation.update(level="ERROR", status_message=str(exc))
                raise HTTPException(502, f"截图解析失败：{exc}") from exc
            if generation is not None:
                generation.update(
                    output=outcome.value.model_dump(), usage_details=outcome.llm.usage_details
                )
        extraction = outcome.value
        jd_id = None
        if payload.persist:
            row = await jd_repository.create(
                user_id,
                raw_text=extraction.raw_text,
                title=payload.title or extraction.profile.title,
                company=payload.company or extraction.profile.company,
                parsed=extraction.profile.model_dump(),
                parser_model=outcome.llm.model,
                source_type="image",
            )
            jd_id = row["id"]
    observability.flush()
    return JdImageParseResponse(
        jd_id=jd_id,
        profile=extraction.profile,
        raw_text=extraction.raw_text,
        provider=outcome.llm.provider,
        model=outcome.llm.model,
        is_stub=outcome.llm.is_stub,
        trace_id=trace_id,
        warnings=outcome.warnings,
    )


@router.get("", response_model=JdListResponse)
async def list_jds(user_id: CurrentUserDep, repository: JdRepoDep) -> JdListResponse:
    items = [JdRead.model_validate(row) for row in await repository.list_for_user(user_id)]
    return JdListResponse(items=items, total=len(items))


@router.get("/{jd_id}", response_model=JdRead)
async def get_jd(jd_id: UUID, user_id: CurrentUserDep, repository: JdRepoDep) -> JdRead:
    row = await repository.get(user_id, jd_id)
    if not row:
        raise HTTPException(404, "JD 不存在")
    return JdRead.model_validate(row)


@router.put(
    "/{jd_id}", response_model=JdRead, summary="修改 JD 标题和公司；原文与画像作为解析快照保留"
)
async def update_jd(
    jd_id: UUID, payload: JdMetadataUpdate, user_id: CurrentUserDep, repository: JdRepoDep
) -> JdRead:
    row = await repository.update_metadata(user_id, jd_id, payload)
    if not row:
        raise HTTPException(404, "JD 不存在")
    return JdRead.model_validate(row)


@router.delete("/{jd_id}", status_code=204)
async def delete_jd(jd_id: UUID, user_id: CurrentUserDep, repository: JdRepoDep) -> Response:
    if not await repository.delete(user_id, jd_id):
        raise HTTPException(404, "JD 不存在")
    return Response(status_code=204)


@router.post("/{jd_id}/match", response_model=MatchResponse)
async def match_jd(
    jd_id: UUID,
    payload: MatchRequest,
    user_id: CurrentUserDep,
    repository: JdRepoDep,
    experiences: ExperienceRepoDep,
    service: MatchingServiceDep,
    observability: ObservabilityDep,
) -> MatchResponse:
    row = await repository.get(user_id, jd_id)
    if not row:
        raise HTTPException(404, "JD 不存在")
    if not row["parsed"]:
        raise HTTPException(400, "该 JD 尚未解析，请先解析岗位内容")
    trace_id = normalize_trace_id(current_trace_id())
    with observability.span(
        "m3.jd.match",
        trace_id=trace_id,
        input={"jd_id": str(jd_id)},
        metadata={"embedding_model": service.embeddings.model_key},
        tags=["m3", "matching"],
    ) as span:
        try:
            result = await service.match(
                user_id,
                jd_id,
                JobProfile.model_validate(row["parsed"]),
                await experiences.list_for_user(user_id),
                payload.candidate_limit,
                trace_id,
            )
        except LlmError as exc:
            raise HTTPException(502, str(exc)) from exc
        if span is not None:
            span.update_trace(
                output={
                    "experiences": len(result.items),
                    "gaps": len(result.uncovered_requirement_ids),
                }
            )
    observability.flush()
    return result
