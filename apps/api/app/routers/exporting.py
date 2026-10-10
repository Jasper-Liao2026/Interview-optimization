"""M7 template selection, multi-JD generation and archive download."""

from fastapi import APIRouter, HTTPException, Response

from app.deps import CurrentUserDep, GenerationServiceDep, ObservabilityDep, PdfDep, ResumeRepoDep
from app.observability import normalize_trace_id
from app.pdf import PdfExportError
from app.render import UnknownTemplateError, template_definitions
from app.schemas.exporting import (
    BatchExportRequest,
    BatchGenerateRequest,
    BatchGenerateResponse,
    ResumeListResponse,
    TemplateListResponse,
)
from app.services.exporting import ExportNotFoundError, export_archive, generate_batch
from app.tracing import current_trace_id

router = APIRouter(prefix="/resumes", tags=["exporting"])


@router.get("/templates", response_model=TemplateListResponse)
async def list_templates():
    return TemplateListResponse(items=template_definitions())


@router.get("", response_model=ResumeListResponse)
async def list_resumes(user_id: CurrentUserDep, repository: ResumeRepoDep):
    return ResumeListResponse(items=await repository.list_for_user(user_id))


@router.post("/batch-generate", response_model=BatchGenerateResponse)
async def batch_generate(
    payload: BatchGenerateRequest,
    user_id: CurrentUserDep,
    service: GenerationServiceDep,
    observability: ObservabilityDep,
):
    result = await generate_batch(
        user_id, payload, service, normalize_trace_id(current_trace_id()), observability
    )
    observability.flush()
    return result


@router.post("/export", responses={200: {"content": {"application/zip": {}}}})
async def batch_export(
    payload: BatchExportRequest,
    user_id: CurrentUserDep,
    repository: ResumeRepoDep,
    exporter: PdfDep,
):
    try:
        data = await export_archive(user_id, payload, repository, exporter)
    except ExportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (UnknownTemplateError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PdfExportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        data,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="resumes.zip"',
            "Cache-Control": "no-store",
        },
    )
