"""Template discovery, durable batch generation and PDF archive contracts."""

from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.resume import ResumeRead


class TemplateRead(BaseModel):
    id: str
    name: str
    description: str
    layout: Literal["single-column", "two-column"]


class TemplateListResponse(BaseModel):
    items: list[TemplateRead]


class ResumeListResponse(BaseModel):
    items: list[ResumeRead]


class BatchGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: UUID = Field(default_factory=uuid4)
    jd_ids: list[UUID] = Field(min_length=1, max_length=20)
    experience_ids: list[UUID] = Field(default_factory=list, max_length=60)

    @model_validator(mode="after")
    def unique_ids(self):
        for ids in (self.jd_ids, self.experience_ids):
            if len(ids) != len(set(ids)):
                raise ValueError("岗位和素材 ID 不得重复")
        return self


class BatchGenerateItem(BaseModel):
    jd_id: UUID
    run_id: UUID
    status: Literal["completed", "partial", "failed"]
    resume: ResumeRead | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)


class BatchGenerateResponse(BaseModel):
    batch_id: UUID
    items: list[BatchGenerateItem]


class BatchExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_ids: list[UUID] = Field(min_length=1, max_length=20)
    template: str = "classic"

    @model_validator(mode="after")
    def unique_ids(self):
        if len(self.resume_ids) != len(set(self.resume_ids)):
            raise ValueError("简历 ID 不得重复")
        return self
