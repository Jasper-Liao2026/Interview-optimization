"""Bounded batch generation and all-or-error PDF ZIP export."""

import asyncio
import io
import logging
import re
import zipfile
from uuid import UUID, uuid5

from app.agents.checkpoint import RunConflictError
from app.llm import LlmError
from app.render import UnknownTemplateError, available_templates, render_resume_html
from app.schemas.exporting import (
    BatchExportRequest,
    BatchGenerateItem,
    BatchGenerateRequest,
    BatchGenerateResponse,
)
from app.schemas.resume import GenerateRequest, ResumeRead
from app.services.generation import InvalidJobError, NoExperiencesError

logger = logging.getLogger(__name__)


class ExportNotFoundError(ValueError):
    pass


def validate_template(template: str) -> None:
    if template not in available_templates():
        raise UnknownTemplateError(f"未知模板 {template!r}")


async def generate_batch(
    user_id, payload: BatchGenerateRequest, generation, trace_id, observability=None
):
    # Individual run IDs survive interrupted HTTP requests and process restarts.
    # Each run validates its stored request before reusing its checkpoint.
    semaphore = asyncio.Semaphore(2)

    async def generate(jd_id):
        run_id = uuid5(payload.batch_id, str(jd_id))
        async with semaphore:
            try:
                outcome = await generation.generate(
                    user_id,
                    GenerateRequest(
                        run_id=run_id,
                        jd_id=jd_id,
                        experience_ids=payload.experience_ids,
                        persist=True,
                    ),
                    trace_id,
                )
                return BatchGenerateItem(
                    jd_id=jd_id,
                    run_id=run_id,
                    status=outcome.checkpoint["status"],
                    resume=ResumeRead.model_validate(outcome.resume),
                    warnings=outcome.warnings,
                    usage=getattr(outcome, "usage", {}),
                    trace_id=getattr(outcome, "trace_id", None),
                    prompt_version=(getattr(outcome, "usage", {}).get("calls") or [{}])[0].get(
                        "prompt_version"
                    ),
                    langfuse_trace_url=(
                        observability.trace_url(outcome.trace_id)
                        if observability and getattr(outcome, "trace_id", None)
                        else None
                    ),
                    error="全部经历改写失败，请检查素材并重试失败条目"
                    if outcome.checkpoint["status"] == "failed"
                    else None,
                )
            except (InvalidJobError, NoExperiencesError, RunConflictError, LlmError) as exc:
                return BatchGenerateItem(
                    jd_id=jd_id, run_id=run_id, status="failed", error=str(exc)
                )
            except Exception:
                logger.exception("batch generation failed run_id=%s", run_id)
                return BatchGenerateItem(
                    jd_id=jd_id,
                    run_id=run_id,
                    status="failed",
                    error="岗位生成失败，可重试此批次或查看服务日志",
                )

    return BatchGenerateResponse(
        batch_id=payload.batch_id,
        items=await asyncio.gather(*(generate(i) for i in payload.jd_ids)),
    )


async def export_archive(user_id: UUID, payload: BatchExportRequest, repository, exporter) -> bytes:
    validate_template(payload.template)
    # Resolve ownership and take content snapshots before expensive rendering.
    resumes = []
    for resume_id in payload.resume_ids:
        row = await repository.get(user_id, resume_id)
        if row is None:
            raise ExportNotFoundError("所选简历不存在，请刷新列表")
        resume = ResumeRead.model_validate(row)
        if not any(entry.bullets for section in resume.sections for entry in section.entries):
            raise ValueError(f"简历「{resume.title}」没有可导出的要点")
        resumes.append(resume)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for resume in resumes:
            data = await exporter.render(render_resume_html(resume, template=payload.template))
            # UUID guarantees uniqueness even when titles collide. Strip path
            # separators and platform-invalid characters from user titles.
            title = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", resume.title).strip(" .")[:70] or "简历"
            archive.writestr(f"{title}-{resume.id}.pdf", data)
    # Render failure never returns a misleading incomplete ZIP or marks output.
    for resume in resumes:
        await repository.mark_exported(user_id, resume.id)
    return output.getvalue()
