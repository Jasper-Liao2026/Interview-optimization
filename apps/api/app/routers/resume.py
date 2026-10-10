"""简历生成、任务恢复、失败重试以及共用渲染结果的预览与导出接口。"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import HTMLResponse

from app.agents.checkpoint import RunConflictError
from app.deps import (
    CurrentUserDep,
    GenerationServiceDep,
    ObservabilityDep,
    PdfDep,
    ResumeRepoDep,
    ScoringServiceDep,
    SettingsDep,
)
from app.llm import LlmError
from app.observability import normalize_trace_id
from app.pdf import PdfExportError, pdf_page_count
from app.render import UnknownTemplateError, render_resume_html
from app.schemas import GenerateRequest, GenerateResponse, ResumeRead, ScoreRequest, ScoreResponse
from app.services import NoExperiencesError
from app.services.generation import GenerationOutcome, InvalidJobError, RunNotFoundError
from app.services.scoring import ScoringNotFoundError
from app.tracing import current_trace_id

router = APIRouter(prefix="/resumes", tags=["resumes"])
logger = logging.getLogger("app.routers.resumes")


async def _load(user_id: UUID, resume_id: UUID, repository: ResumeRepoDep) -> ResumeRead:
    row = await repository.get(user_id, resume_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="简历不存在")
    return ResumeRead.model_validate(row)


@router.post(
    "/generate",
    response_model=GenerateResponse,
    summary="按 JD 生成一份简历",
    description="LangGraph 并行改写、事实校验、失败隔离与幂等落库。",
)
async def generate_resume(
    payload: GenerateRequest,
    user_id: CurrentUserDep,
    service: GenerationServiceDep,
    settings: SettingsDep,
    observability: ObservabilityDep,
) -> GenerateResponse:
    # 必须规范化成 32 位 hex：浏览器 `crypto.randomUUID()` 生成的是带横线的 UUID，
    # 中间件原样透传，而 Langfuse SDK 会拿它做 int(id, 16)，带横线直接 ValueError → 500。
    # 与 jd.py / observability.py 的既有做法保持一致（那里早就 normalize 了，这里漏了）。
    trace_id = normalize_trace_id(current_trace_id())
    try:
        outcome = await service.generate(user_id, payload, trace_id)
    except (NoExperiencesError, InvalidJobError) as exc:
        # 素材库为空是**用户侧**问题，明确回 400 并给出补救办法，而不是 500
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LlmError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"生成失败：{exc}"
        ) from exc

    # 与观测自检接口一致：生成结束就 flush，避免刚生成的 trace 在 UI 里迟到
    observability.flush()

    return _response(outcome, settings)


def _response(outcome: GenerationOutcome, settings: SettingsDep) -> GenerateResponse:
    resume = ResumeRead.model_validate(outcome.resume)
    prefix = settings.api_prefix.rstrip("/")
    persisted = outcome.persisted

    return GenerateResponse(
        resume=resume,
        run_id=outcome.run_id,
        items=outcome.items,
        failures=outcome.failures,
        checkpoint=outcome.checkpoint,
        profile=outcome.profile,
        preview_path=f"{prefix}/resumes/{resume.id}/html" if persisted else None,
        pdf_path=f"{prefix}/resumes/{resume.id}/pdf" if persisted else None,
        provider=outcome.provider,
        model=outcome.model,
        is_stub=outcome.is_stub,
        trace_id=outcome.trace_id,
        langfuse_trace_url=(
            f"{settings.langfuse_host.rstrip('/')}/project/{settings.langfuse_project_id}/traces/{outcome.trace_id}"
            if settings.langfuse_configured
            else None
        ),
        prompt_version=(outcome.usage.get("calls") or [{}])[0].get("prompt_version"),
        usage=outcome.usage,
        warnings=outcome.warnings,
    )


@router.get("/runs/{run_id}", response_model=GenerateResponse, summary="读取生成任务")
async def get_generation_run(
    run_id: UUID, user_id: CurrentUserDep, service: GenerationServiceDep, settings: SettingsDep
) -> GenerateResponse:
    try:
        return _response(await service.read(user_id, run_id), settings)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_id}/resume", response_model=GenerateResponse, summary="恢复中断的生成任务")
async def resume_generation_run(
    run_id: UUID, user_id: CurrentUserDep, service: GenerationServiceDep, settings: SettingsDep
) -> GenerateResponse:
    try:
        return _response(await service.resume(user_id, run_id), settings)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidJobError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LlmError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"生成失败：{exc}"
        ) from exc


@router.post(
    "/runs/{run_id}/retry/{index}", response_model=GenerateResponse, summary="重试失败条目"
)
async def retry_generation_item(
    run_id: UUID,
    index: int,
    user_id: CurrentUserDep,
    service: GenerationServiceDep,
    settings: SettingsDep,
) -> GenerateResponse:
    try:
        return _response(await service.resume(user_id, run_id, retry=index), settings)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RunConflictError, IndexError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidJobError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LlmError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"生成失败：{exc}"
        ) from exc


@router.get("/{resume_id}", response_model=ResumeRead, summary="读取一份简历")
async def get_resume(
    resume_id: UUID, user_id: CurrentUserDep, repository: ResumeRepoDep
) -> ResumeRead:
    return await _load(user_id, resume_id, repository)


@router.post("/{resume_id}/score", response_model=ScoreResponse, summary="评分并定向改进简历")
async def score_resume(
    resume_id: UUID,
    payload: ScoreRequest,
    user_id: CurrentUserDep,
    service: ScoringServiceDep,
) -> ScoreResponse:
    try:
        return await service.score(user_id, resume_id, payload)
    except ScoringNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=f"评分或改写失败：{exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/{resume_id}/scores/{score_run_id}",
    response_model=ScoreResponse,
    summary="读取评分历史与最佳版本",
)
async def get_score_run(
    resume_id: UUID,
    score_run_id: UUID,
    user_id: CurrentUserDep,
    service: ScoringServiceDep,
) -> ScoreResponse:
    try:
        return await service.read(user_id, resume_id, score_run_id)
    except ScoringNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/{resume_id}/html",
    response_class=HTMLResponse,
    summary="渲染简历 HTML（预览用）",
    description=(
        "返回完整 HTML 文档，样式已内联。前端用 iframe 直接指向本地址即为所见即所得预览；"
        "服务端导出 PDF 时用的也是这份内容。"
    ),
)
async def get_resume_html(
    resume_id: UUID,
    user_id: CurrentUserDep,
    repository: ResumeRepoDep,
    template: str | None = Query(default=None, description="覆盖模板；缺省用简历自身记录的模板"),
) -> HTMLResponse:
    resume = await _load(user_id, resume_id, repository)
    try:
        html = render_resume_html(resume, template=template or resume.template)
    except UnknownTemplateError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return HTMLResponse(content=html)


@router.get(
    "/{resume_id}/pdf",
    summary="导出简历 PDF",
    description=(
        "用无头 Chromium 打印 `/{id}/html` 的同一份内容，默认 A4。\n\n"
        "`download=1` 时以附件形式下载，否则浏览器内联预览。\n"
        "响应头 `X-Resume-Pages` 给出页数，便于自动化校验分页。"
    ),
    responses={200: {"content": {"application/pdf": {}}}},
)
async def export_resume_pdf(
    resume_id: UUID,
    user_id: CurrentUserDep,
    repository: ResumeRepoDep,
    exporter: PdfDep,
    download: bool = Query(default=False, description="true 时强制下载而非内联预览"),
    template: str | None = Query(default=None, description="覆盖导出模板，不修改简历内容"),
) -> Response:
    resume = await _load(user_id, resume_id, repository)

    try:
        html = render_resume_html(resume, template=template or resume.template)
    except UnknownTemplateError as exc:  # 简历存的模板名不在注册表里（改过注册表才会出现）
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        pdf_bytes = await exporter.render(html)
    except PdfExportError as exc:
        # 503 而不是 500：这是「能力暂不可用」（没装浏览器 / 超时），不是代码错误
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    # 导出成功后才改状态，见 resume_repo.mark_exported 的注释
    await repository.mark_exported(user_id, resume_id)

    disposition = "attachment" if download else "inline"
    headers = {
        "Content-Disposition": f'{disposition}; filename="resume-{resume_id}.pdf"',
        "X-Resume-Pages": str(pdf_page_count(pdf_bytes) or ""),
    }
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)
