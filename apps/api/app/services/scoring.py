"""M5: score saved resumes and improve only weak entries from their sources."""

from __future__ import annotations

import copy
import json
from typing import Any
from uuid import UUID

from app.agents.facts import validate_rewrite_facts
from app.agents.rewriter import ExperienceRewriter
from app.agents.scorer import ScoreAgent, run_score_loop
from app.config import Settings
from app.llm import LlmClient
from app.repositories import ExperienceRepository, JobDescriptionRepository, ResumeRepository
from app.schemas import (
    JobProfile,
    ResumeRead,
    RewrittenBullet,
    RewrittenExperience,
    ScoreRequest,
    ScoreResponse,
)


class ScoringNotFoundError(ValueError):
    pass


class ResumeScoringService:
    def __init__(
        self,
        settings: Settings,
        experiences: ExperienceRepository,
        jobs: JobDescriptionRepository,
        resumes: ResumeRepository,
        rewriter: ExperienceRewriter,
    ) -> None:
        self.settings, self.experiences, self.jobs = settings, experiences, jobs
        self.resumes, self.rewriter = resumes, rewriter

    async def score(self, user_id: UUID, resume_id: UUID, request: ScoreRequest) -> ScoreResponse:
        row = await self.resumes.get(user_id, resume_id)
        if row is None:
            raise ScoringNotFoundError("简历不存在")
        if not row.get("jd_id"):
            raise ValueError("该简历没有绑定岗位，无法评分")
        jd = await self.jobs.get(user_id, UUID(str(row["jd_id"])))
        if not jd or not jd.get("parsed"):
            raise ValueError("岗位画像不存在或尚未解析")
        profile = JobProfile.model_validate(jd["parsed"])
        resume = ResumeRead.model_validate(row)
        working = [section.model_dump(mode="json") for section in resume.sections]
        entries = [entry for section in working for entry in section["entries"]]
        source_ids = [UUID(entry["experience_id"]) for entry in entries if entry["experience_id"]]
        source_rows = await self.experiences.list_by_ids(user_id, source_ids)
        by_id = {str(source["id"]): source for source in source_rows}
        if any(entry["experience_id"] not in by_id for entry in entries):
            raise ValueError("简历引用的原始素材不存在，无法校验或定向改写")
        sources = [by_id[entry["experience_id"]] for entry in entries]
        settings = self.settings
        is_local = settings.judge_provider == "stub"
        if not is_local:
            if not settings.judge_api_key:
                raise ValueError("真实评分需要配置 JUDGE_API_KEY")
            if (
                settings.generation_vendor.strip().casefold()
                == settings.judge_vendor.strip().casefold()
            ):
                raise ValueError("JUDGE_VENDOR 必须与 GENERATION_VENDOR 不同")
            if not settings.judge_vendor.strip() or not settings.generation_vendor.strip():
                raise ValueError("必须明确配置生成与评分模型的厂商")
            original_vendor = str(row.get("generator_vendor") or "").strip().casefold()
            original_is_stub = (
                original_vendor == "stub"
                and str(row.get("generator") or "").split(":")[0] == "stub"
            )
            if not original_is_stub and not original_vendor:
                raise ValueError("旧简历缺少生成厂商记录，请重新生成后再使用真实评分")
            if not original_is_stub and original_vendor == settings.judge_vendor.strip().casefold():
                raise ValueError("评分厂商必须与原始简历的生成厂商不同")
        judge_settings = settings.model_copy(
            update={
                "llm_provider": settings.judge_provider,
                "llm_base_url": settings.judge_base_url,
                "llm_api_key": settings.judge_api_key,
                "llm_model": settings.judge_model,
                "llm_max_tokens": settings.judge_max_tokens,
            }
        )
        judge = ScoreAgent(
            LlmClient(judge_settings),
            judge_vendor=settings.judge_vendor,
            low_score_threshold=request.threshold,
        )

        async def evaluate(sections: list[dict]):
            return await judge.score(
                profile, sections, generator_provider=settings.generation_vendor, sources=sources
            )

        async def rewrite(indices: list[int], suggestions: list[str]) -> tuple[list[dict], float]:
            nonlocal working
            updated = copy.deepcopy(working)
            flattened = [entry for section in updated for entry in section["entries"]]
            for index in indices:
                source = sources[index]
                if settings.llm_provider == "stub":
                    candidates = [
                        str(source.get("raw_description") or ""),
                        *[str(x) for x in source.get("highlights") or []],
                    ]
                    valid = []
                    for candidate in candidates:
                        candidate = candidate.strip()[:400]
                        if not candidate:
                            continue
                        bullet = RewrittenBullet(text=candidate, evidence=[candidate])
                        if not validate_rewrite_facts(
                            source, RewrittenExperience(bullets=[bullet])
                        ):
                            valid.append(bullet)
                    if valid:
                        flattened[index]["bullets"] = [
                            max(valid, key=lambda bullet: len(bullet.text)).model_dump(mode="json")
                        ]
                else:
                    feedback = json.dumps(
                        {
                            "current_bullets": flattened[index]["bullets"],
                            "suggestions": suggestions,
                        },
                        ensure_ascii=False,
                    )
                    outcome = await self.rewriter.rewrite(profile, source, feedback=feedback)
                    flattened[index]["bullets"] = [
                        bullet.model_dump(mode="json") for bullet in outcome.value.bullets
                    ]
            working = updated
            # Budget units reserve every possible structured/fact-validation retry.
            return updated, 0.0 if settings.llm_provider == "stub" else 9.0 * len(indices)

        loop = await run_score_loop(
            profile,
            working,
            rewrite=rewrite,
            score=evaluate,
            sources=sources,
            threshold=request.threshold,
            max_rounds=request.max_rounds,
            cost_limit=request.cost_limit,
            score_cost_estimate=0.0 if is_local else 3.0,
            rewrite_cost_estimate=lambda indices: (
                0.0 if settings.llm_provider == "stub" else 9.0 * len(indices)
            ),
        )
        best_row = {**row, "sections": loop.best.sections}
        if loop.best.round and settings.llm_provider != "stub":
            # A best version may include both original and newly rewritten entries.
            vendors = {row.get("generator_vendor"), settings.generation_vendor}
            best_row["generator_vendor"] = settings.generation_vendor if len(vendors) == 1 else None
            best_row["generator"] = f"{settings.llm_provider}:{settings.llm_model}"
        run_id = None
        if request.persist:
            best_row, run_id = await self.resumes.save_scoring_result(
                user_id,
                best_row,
                loop.best.sections,
                {"profile": profile.model_dump(mode="json"), "loop": loop.model_dump(mode="json")},
            )
        best_resume = ResumeRead.model_validate(best_row)
        prefix = settings.api_prefix.rstrip("/")
        return ScoreResponse(
            resume_id=str(resume_id),
            profile=profile,
            loop=loop,
            resume=best_resume,
            score_run_id=run_id,
            preview_path=f"{prefix}/resumes/{best_resume.id}/html" if request.persist else None,
            pdf_path=f"{prefix}/resumes/{best_resume.id}/pdf" if request.persist else None,
        )

    async def read(self, user_id: UUID, resume_id: UUID, run_id: UUID) -> ScoreResponse:
        saved = await self.resumes.get_scoring_result(user_id, resume_id, run_id)
        if saved is None:
            raise ScoringNotFoundError("评分记录不存在")
        result = dict(saved["result"])
        row: dict[str, Any] | None = result.pop("resume_snapshot", None)
        if row is None:
            row = await self.resumes.get(user_id, saved["best_resume_id"])
        if row is None:
            raise ScoringNotFoundError("最佳简历不存在")
        prefix = self.settings.api_prefix.rstrip("/")
        return ScoreResponse(
            resume_id=str(resume_id),
            resume=ResumeRead.model_validate(row),
            score_run_id=run_id,
            preview_path=f"{prefix}/resumes/{row['id']}/html",
            pdf_path=f"{prefix}/resumes/{row['id']}/pdf",
            **result,
        )
