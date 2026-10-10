"""Editor API: drafts, exact PDF preview, revisions and AI confirmation."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response

from app.agents.checkpoint import RunConflictError
from app.deps import CurrentUserDep, EditingServiceDep, PdfDep
from app.llm import LlmError
from app.pdf import PdfExportError, pdf_page_count
from app.render import UnknownTemplateError, render_resume_html
from app.repositories.editing_repo import EditingNotFoundError
from app.schemas.editing import (
    AiDecisionRequest,
    AiEditRequest,
    AiEditResponse,
    EditorResponse,
    RestoreRevisionRequest,
    ResumeDraft,
    SaveRevisionRequest,
)

router = APIRouter(prefix="/resumes", tags=["editing"])


async def _call(operation):
    try:
        return await operation
    except EditingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=f"AI 微调失败：{exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{resume_id}/editor", response_model=EditorResponse)
async def get_editor(resume_id: UUID, user_id: CurrentUserDep, service: EditingServiceDep):
    return await _call(service.read(user_id, resume_id))


@router.put("/{resume_id}/editor", response_model=EditorResponse)
async def save_editor(
    resume_id: UUID,
    payload: SaveRevisionRequest,
    user_id: CurrentUserDep,
    service: EditingServiceDep,
):
    await _call(
        service.repository.save(
            user_id,
            resume_id,
            payload.expected_revision,
            ResumeDraft.model_validate(
                payload.model_dump(exclude={"expected_revision"})
            ).model_dump(mode="json"),
            "手动编辑",
        )
    )
    return await _call(service.read(user_id, resume_id))


@router.post("/{resume_id}/preview", responses={200: {"content": {"application/pdf": {}}}})
async def preview_draft(
    resume_id: UUID,
    payload: ResumeDraft,
    user_id: CurrentUserDep,
    service: EditingServiceDep,
    exporter: PdfDep,
):
    editor = await _call(service.read(user_id, resume_id))
    resume = type(editor.resume).model_validate(
        {**editor.resume.model_dump(), **payload.model_dump()}
    )
    try:
        html = render_resume_html(resume, template=resume.template)
        data = await exporter.render(html)
    except UnknownTemplateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PdfExportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"X-Resume-Pages": str(pdf_page_count(data) or ""), "Cache-Control": "no-store"},
    )


@router.post("/{resume_id}/revisions/{revision_id}/restore", response_model=EditorResponse)
async def restore_revision(
    resume_id: UUID,
    revision_id: UUID,
    payload: RestoreRevisionRequest,
    user_id: CurrentUserDep,
    service: EditingServiceDep,
):
    return await _call(service.restore(user_id, resume_id, revision_id, payload.expected_revision))


@router.post("/{resume_id}/ai-edits", response_model=AiEditResponse)
async def propose_edit(
    resume_id: UUID, payload: AiEditRequest, user_id: CurrentUserDep, service: EditingServiceDep
):
    return await _call(service.propose(user_id, resume_id, payload))


@router.get("/{resume_id}/ai-edits/{run_id}", response_model=AiEditResponse)
async def read_edit(
    resume_id: UUID, run_id: UUID, user_id: CurrentUserDep, service: EditingServiceDep
):
    return await _call(service.read_proposal(user_id, resume_id, run_id))


@router.post("/{resume_id}/ai-edits/{run_id}/decision", response_model=AiEditResponse)
async def decide_edit(
    resume_id: UUID,
    run_id: UUID,
    payload: AiDecisionRequest,
    user_id: CurrentUserDep,
    service: EditingServiceDep,
):
    return await _call(service.decide(user_id, resume_id, run_id, payload.accept))
