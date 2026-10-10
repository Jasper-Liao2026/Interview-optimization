"""垂直切片编排：JD → 解析 → 改写 → 组装 → 落库（M1-3 / M1-4）。

这是 M1 的主线，也是后续接 LangGraph 前**先用一条显式顺序流程**把链路跑通的地方。

为什么要先手写顺序流程，而不是一上来就上 StateGraph：
图的形状取决于「节点边界在哪、状态要带什么」。先把流程真正跑一遍，
节点边界自然就清楚了，那时再搬进图里是一次机械改写。
反过来先画图，往往要返工 —— 因为会发现状态设计得不对。

M4-2 会把这个函数拆成 StateGraph 的节点。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.agents.assembler import assemble_sections
from app.agents.jd_parser import JdParser
from app.agents.prompts import prompt_metadata
from app.agents.rewriter import ExperienceRewriter
from app.config import Settings
from app.llm import LlmClient
from app.observability import Observability
from app.repositories import (
    ExperienceRepository,
    JobDescriptionRepository,
    ProfileRepository,
    ResumeRepository,
)
from app.schemas import GenerateRequest, JobProfile

logger = logging.getLogger("app.services.generation")

TRACE_NAME = "m1.generate"

DEFAULT_TEMPLATE = "classic"


class GenerationError(RuntimeError):
    """生成失败的基类。router 会把它翻译成 4xx/5xx。"""


class NoExperiencesError(GenerationError):
    """一条经历都没有 —— 用户还没录入素材。这是**用户侧**的问题，应回 400。"""


class InvalidJobError(GenerationError):
    """保存的 JD 不存在或没有解析画像。"""


@dataclass(slots=True)
class GenerationOutcome:
    resume: dict[str, Any]
    profile: JobProfile
    jd_id: UUID | None
    provider: str
    model: str
    is_stub: bool
    trace_id: str
    warnings: list[str] = field(default_factory=list)


class ResumeGenerationService:
    def __init__(
        self,
        *,
        settings: Settings,
        experiences: ExperienceRepository,
        job_descriptions: JobDescriptionRepository,
        resumes: ResumeRepository,
        profiles: ProfileRepository,
        parser: JdParser,
        rewriter: ExperienceRewriter,
        llm: LlmClient,
        observability: Observability,
    ) -> None:
        self._settings = settings
        self._experiences = experiences
        self._jds = job_descriptions
        self._resumes = resumes
        self._profiles = profiles
        self._parser = parser
        self._rewriter = rewriter
        self._llm = llm
        self._obs = observability

    # ------------------------------------------------------------ 内部步骤
    async def _load_experiences(
        self, user_id: UUID, experience_ids: list[UUID]
    ) -> list[dict[str, Any]]:
        """选定素材。带 ID 时按 ID 取（保持用户勾选顺序），否则取该用户全部。"""
        if experience_ids:
            return await self._experiences.list_by_ids(user_id, experience_ids)
        return await self._experiences.list_for_user(user_id)

    # ---------------------------------------------------------------- 主流程
    async def generate(
        self, user_id: UUID, request: GenerateRequest, trace_id: str
    ) -> GenerationOutcome:
        warnings: list[str] = []

        with self._obs.span(
            TRACE_NAME,
            trace_id=trace_id,
            input={
                "jd_text": (request.jd_text or "")[:2000],
                "jd_id": str(request.jd_id) if request.jd_id else None,
                "experience_ids": [str(item) for item in request.experience_ids],
            },
            metadata={**prompt_metadata(), "milestone": self._settings.milestone},
            tags=["m1", "generate"],
        ) as span:
            # ---- 1. 取素材 -------------------------------------------------
            experiences = await self._load_experiences(user_id, request.experience_ids)
            if not experiences:
                raise NoExperiencesError(
                    "没有任何经历素材，无法生成简历。先通过 POST /api/v1/experiences 录入，"
                    "或执行 `node scripts/seed-m1.mjs` 写入示例数据。"
                )

            # ---- 2. 解析 JD（M1-3）----------------------------------------
            if request.jd_id:
                saved_jd = await self._jds.get(user_id, request.jd_id)
                if not saved_jd or not saved_jd["parsed"]:
                    raise InvalidJobError("JD 不存在或尚未解析，请重新选择已保存的岗位。")
                profile = JobProfile.model_validate(saved_jd["parsed"])
            else:
                assert request.jd_text is not None
                with self._obs.generation(
                    "jd.parse",
                    model=self._llm.model,
                    input={"raw_text": request.jd_text[:2000]},
                    metadata=prompt_metadata(),
                ) as generation:
                    parse_outcome = await self._parser.parse(request.jd_text)
                    profile = parse_outcome.value
                    warnings.extend(parse_outcome.warnings)
                    if generation is not None:
                        generation.update(
                            output=profile.model_dump(),
                            usage_details=parse_outcome.llm.usage_details,
                        )

            # ---- 3. 串行改写（M1-4）---------------------------------------
            # 先串行，不并行。并行是 M4-3 的事，这里只证明「改写本身可用」。
            pairs: list[tuple[dict[str, Any], Any]] = []
            for experience in experiences:
                with self._obs.generation(
                    "llm.rewrite_experience",
                    model=self._llm.model,
                    input={
                        "kind": experience["kind"],
                        "org": experience["org"],
                        "role": experience["role"],
                    },
                    metadata=prompt_metadata(),
                ) as generation:
                    rewrite_outcome = await self._rewriter.rewrite(profile, experience)
                    if generation is not None:
                        generation.update(
                            output=rewrite_outcome.value.model_dump(),
                            usage_details=rewrite_outcome.llm.usage_details,
                        )
                warnings.extend(rewrite_outcome.warnings)
                if self._rewriter.last_truncated_chars:
                    warnings.append(
                        f"经历「{experience['org']}」的原始描述超长，"
                        f"已截断 {self._rewriter.last_truncated_chars} 字后送进改写"
                    )
                pairs.append((experience, rewrite_outcome.value))

            # ---- 4. 组装（事实字段来自原始条目，不来自模型）-----------------
            sections = assemble_sections(pairs)

            # ---- 5. 抬头与落库 --------------------------------------------
            profile_row = await self._profiles.ensure(
                user_id,
                display_name=self._settings.dev_user_name,
                headline=self._settings.dev_user_headline,
            )
            header = {
                "name": profile_row["display_name"],
                "headline": profile_row["headline"],
            }
            title = request.title or profile.title or "未命名简历"
            generator = f"{self._llm.provider}:{self._llm.model}"

            jd_id: UUID | None = request.jd_id
            if request.persist:
                if not jd_id:
                    jd_row = await self._jds.create(
                        user_id,
                        raw_text=request.jd_text,
                        title=profile.title,
                        company=profile.company,
                        parsed=profile.model_dump(),
                        parser_model=parse_outcome.llm.model,
                    )
                    jd_id = jd_row["id"]

                resume_row = await self._resumes.create(
                    user_id,
                    jd_id=jd_id,
                    title=title,
                    template=DEFAULT_TEMPLATE,
                    header=header,
                    sections=[section.model_dump(mode="json") for section in sections],
                    generator=generator,
                )
            else:
                # persist=false：不落库，只能返回结构本身。
                # id 是临时生成的，**不可持久引用**（html/pdf 端点会 404），
                # 调用方（router）会据此把 preview/pdf 路径置空并给出 warning。
                now = datetime.now(UTC)
                resume_row = {
                    "id": uuid4(),
                    "user_id": user_id,
                    "jd_id": None,
                    "title": title,
                    "template": DEFAULT_TEMPLATE,
                    "header": header,
                    "sections": [section.model_dump(mode="json") for section in sections],
                    "status": "draft",
                    "generator": generator,
                    "created_at": now,
                    "updated_at": now,
                }
                warnings.append("persist=false：未落库，返回的简历 id 无法用于预览或导出")

            if span is not None:
                span.update_trace(
                    output={"title": title, "sections": len(sections)},
                    metadata={"warnings": warnings, "is_stub": self._llm.is_stub},
                )

        logger.info(
            "generate done trace_id=%s experiences=%d sections=%d stub=%s",
            trace_id,
            len(experiences),
            len(sections),
            self._llm.is_stub,
        )

        return GenerationOutcome(
            resume=resume_row,
            profile=profile,
            jd_id=jd_id,
            provider=self._llm.provider,
            model=self._llm.model,
            is_stub=self._llm.is_stub,
            trace_id=trace_id,
            warnings=warnings,
        )
