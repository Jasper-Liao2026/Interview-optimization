"""Pydantic schema —— 接口类型的单一定义源。

tech-stack.md §6.1：**任何接口改动都先改这里**，
前端类型由 `pnpm gen:types` 从 OpenAPI 自动生成，禁止手写。
"""

from app.schemas.experience import (
    ExperienceBase,
    ExperienceCreate,
    ExperienceKind,
    ExperienceListResponse,
    ExperienceMetric,
    ExperienceRead,
    ExperienceUpdate,
    ExperienceVariant,
)
from app.schemas.health import (
    DatabaseStatus,
    HealthResponse,
    ServiceMetaEntry,
    SystemInfoResponse,
)
from app.schemas.jd import (
    JdParseRequest,
    JdParseResponse,
    JdRead,
    JobProfile,
)
from app.schemas.meta import ErrorResponse
from app.schemas.observability import (
    DEFAULT_SMOKE_PROMPT,
    ObservabilityStatusResponse,
    SmokeRequest,
    SmokeResponse,
)
from app.schemas.resume import (
    GenerateRequest,
    GenerateResponse,
    ResumeBullet,
    ResumeEntry,
    ResumeHeader,
    ResumeRead,
    ResumeSection,
    RewrittenBullet,
    RewrittenExperience,
)

__all__ = [
    "DEFAULT_SMOKE_PROMPT",
    "DatabaseStatus",
    "ErrorResponse",
    "ExperienceBase",
    "ExperienceCreate",
    "ExperienceKind",
    "ExperienceListResponse",
    "ExperienceMetric",
    "ExperienceRead",
    "ExperienceUpdate",
    "ExperienceVariant",
    "GenerateRequest",
    "GenerateResponse",
    "HealthResponse",
    "JdParseRequest",
    "JdParseResponse",
    "JdRead",
    "JobProfile",
    "ObservabilityStatusResponse",
    "ResumeBullet",
    "ResumeEntry",
    "ResumeHeader",
    "ResumeRead",
    "ResumeSection",
    "RewrittenBullet",
    "RewrittenExperience",
    "ServiceMetaEntry",
    "SmokeRequest",
    "SmokeResponse",
    "SystemInfoResponse",
]
