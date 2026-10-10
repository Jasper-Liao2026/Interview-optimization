"""FastAPI 依赖。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends

from app.agents.jd_parser import JdParser, get_jd_parser
from app.agents.rewriter import ExperienceRewriter, get_experience_rewriter
from app.config import Settings, get_settings
from app.db import Database, get_database
from app.llm import LlmClient, get_llm_client
from app.llm.embeddings import EmbeddingClient
from app.observability import Observability, get_observability
from app.pdf import PdfExporter, get_pdf_exporter
from app.repositories import (
    ExperienceRepository,
    JobDescriptionRepository,
    ProfileRepository,
    ResumeRepository,
)
from app.repositories.embedding_repo import EmbeddingRepository
from app.services.generation import ResumeGenerationService
from app.services.matching import MatchingService

SettingsDep = Annotated[Settings, Depends(get_settings)]
DatabaseDep = Annotated[Database, Depends(get_database)]
LlmDep = Annotated[LlmClient, Depends(get_llm_client)]
ObservabilityDep = Annotated[Observability, Depends(get_observability)]
PdfDep = Annotated[PdfExporter, Depends(get_pdf_exporter)]


# ------------------------------------------------------------------ 仓储
def get_experience_repository(database: DatabaseDep) -> ExperienceRepository:
    return ExperienceRepository(database)


def get_jd_repository(database: DatabaseDep) -> JobDescriptionRepository:
    return JobDescriptionRepository(database)


def get_resume_repository(database: DatabaseDep) -> ResumeRepository:
    return ResumeRepository(database)


def get_profile_repository(database: DatabaseDep) -> ProfileRepository:
    return ProfileRepository(database)


ExperienceRepoDep = Annotated[ExperienceRepository, Depends(get_experience_repository)]
JdRepoDep = Annotated[JobDescriptionRepository, Depends(get_jd_repository)]
ResumeRepoDep = Annotated[ResumeRepository, Depends(get_resume_repository)]
ProfileRepoDep = Annotated[ProfileRepository, Depends(get_profile_repository)]


# ------------------------------------------------------------------ agent
def get_jd_parser_dep(llm: LlmDep) -> JdParser:
    return get_jd_parser(llm)


def get_rewriter_dep(llm: LlmDep, settings: SettingsDep) -> ExperienceRewriter:
    return get_experience_rewriter(llm, max_input_chars=settings.rewrite_max_input_chars)


JdParserDep = Annotated[JdParser, Depends(get_jd_parser_dep)]
RewriterDep = Annotated[ExperienceRewriter, Depends(get_rewriter_dep)]


# ------------------------------------------------------------- 生成服务
def get_generation_service(
    settings: SettingsDep,
    experiences: ExperienceRepoDep,
    job_descriptions: JdRepoDep,
    resumes: ResumeRepoDep,
    profiles: ProfileRepoDep,
    parser: JdParserDep,
    rewriter: RewriterDep,
    llm: LlmDep,
    observability: ObservabilityDep,
) -> ResumeGenerationService:
    return ResumeGenerationService(
        settings=settings,
        experiences=experiences,
        job_descriptions=job_descriptions,
        resumes=resumes,
        profiles=profiles,
        parser=parser,
        rewriter=rewriter,
        llm=llm,
        observability=observability,
    )


GenerationServiceDep = Annotated[ResumeGenerationService, Depends(get_generation_service)]


# ---------------------------------------------------------------- 当前用户
def get_current_user_id(settings: SettingsDep) -> UUID:
    """当前用户 ID。

    **本项目定位是本地自托管的开源工具，单实例单用户**：不做账号体系，
    所有请求都归属同一个固定的本机用户。

    之所以做成依赖而不是到处读 settings：将来真要支持多用户时，
    只需把这里换成「解析 `Authorization: Bearer <JWT>` 取 sub」，
    所有路由一行都不用改。
    """
    return UUID(settings.dev_user_id)


CurrentUserDep = Annotated[UUID, Depends(get_current_user_id)]


def get_embedding_repository(database: DatabaseDep) -> EmbeddingRepository:
    return EmbeddingRepository(database)


def get_embedding_client(settings: SettingsDep) -> EmbeddingClient:
    return EmbeddingClient(settings)


EmbeddingRepoDep = Annotated[EmbeddingRepository, Depends(get_embedding_repository)]
EmbeddingClientDep = Annotated[EmbeddingClient, Depends(get_embedding_client)]


def get_matching_service(
    embeddings: EmbeddingClientDep, repository: EmbeddingRepoDep
) -> MatchingService:
    return MatchingService(embeddings, repository)


MatchingServiceDep = Annotated[MatchingService, Depends(get_matching_service)]
