"""M3 匹配矩阵的公共契约。分数是规则分，不是录用概率。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class MatchRequest(BaseModel):
    candidate_limit: int = Field(default=20, ge=1, le=100)


class JobRequirement(BaseModel):
    id: str
    category: Literal["required", "preferred", "responsibility", "domain"]
    text: str
    weight: int


class RequirementMatch(BaseModel):
    requirement_id: str
    score: float
    semantic_similarity: float | None
    status: Literal["covered", "related", "missing"]
    evidence: list[str]
    reason: str
    shortlisted: bool


class ExperienceMatch(BaseModel):
    experience_id: UUID
    org: str
    role: str
    score: float
    matches: list[RequirementMatch]


class MatchResponse(BaseModel):
    jd_id: UUID
    requirements: list[JobRequirement]
    items: list[ExperienceMatch]
    uncovered_requirement_ids: list[str]
    embedding_model: str
    is_stub: bool
    warnings: list[str]
    trace_id: str
