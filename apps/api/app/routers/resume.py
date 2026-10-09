"""简历生成 / 预览 / 导出接口（M1-4 ~ M1-7）。

三个端点构成闭环：
  `POST /generate`      JD → 改写 → 组装 → 落库
  `GET  /{id}/html`     服务端渲染的 HTML（前端 iframe 直接拿来当预览）
  `GET  /{id}/pdf`      把**同一份 HTML**交给无头浏览器打印

预览与导出共用一个渲染函数，所以「所见即所得」是结构性保证，不是靠人工比对维持的。
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import HTMLResponse

from app.deps import (
    CurrentUserDep,
    GenerationServiceDep,
    ObservabilityDep,
    PdfDep,
    ResumeRepoDep,
    SettingsDep,
)
from app.llm import LlmError
from app.observability import normalize_trace_id
from app.pdf import PdfExportError, pdf_page_count
from app.render import UnknownTemplateError, render_resume_html
from app.schemas import GenerateRequest, GenerateResponse, ResumeRead
from app.services import NoExperiencesError
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
    description=(
        "M1 垂直切片的入口：解析 JD → 逐条改写经历（**串行**，并行是 M4-3）→ 组装 → 落库。\n\n"
        "同一批经历的改写共用同一份岗位画像，但每次改写是独立调用 —— "
        "为 M4 的 fan-out 并行留好了接口形状。"
    ),
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
    except NoExperiencesError as exc:
        # 素材库为空是**用户侧**问题，明确回 400 并给出补救办法，而不是 500
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LlmError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"生成失败：{exc}"
        ) from exc

    # 与观测自检接口一致：生成结束就 flush，避免刚生成的 trace 在 UI 里迟到
    observability.flush()

    resume = ResumeRead.model_validate(outcome.resume)
    prefix = settings.api_prefix.rstrip("/")
    persisted = payload.persist

    return GenerateResponse(
        resume=resume,
        profile=outcome.profile,
        preview_path=f"{prefix}/resumes/{resume.id}/html" if persisted else None,
        pdf_path=f"{prefix}/resumes/{resume.id}/pdf" if persisted else None,
        provider=outcome.provider,
        model=outcome.model,
        is_stub=outcome.is_stub,
        trace_id=outcome.trace_id,
        warnings=outcome.warnings,
    )


@router.get("/{resume_id}", response_model=ResumeRead, summary="读取一份简历")
async def get_resume(
    resume_id: UUID, user_id: CurrentUserDep, repository: ResumeRepoDep
) -> ResumeRead:
    return await _load(user_id, resume_id, repository)


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
) -> Response:
    resume = await _load(user_id, resume_id, repository)

    try:
        html = render_resume_html(resume, template=resume.template)
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
