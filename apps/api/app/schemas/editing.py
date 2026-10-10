"""Structured editing, immutable revisions and human-approved local AI edits."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.resume import ResumeHeader, ResumeRead, ResumeSection


class ResumeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    header: ResumeHeader
    sections: list[ResumeSection] = Field(max_length=20)

    @model_validator(mode="after")
    def bounded_content(self):
        if not self.title.strip() or not self.header.name.strip():
            raise ValueError("标题和姓名不能为空")
        if len(self.header.name) > 120 or len(self.header.headline or "") > 300:
            raise ValueError("姓名或简介过长")
        for section in self.sections:
            if not section.title.strip() or len(section.title) > 120 or len(section.entries) > 60:
                raise ValueError("分区标题或经历数量无效")
            for entry in section.entries:
                if not entry.org.strip() or not entry.role.strip():
                    raise ValueError("组织和职位不能为空")
                if max(len(entry.org), len(entry.role), len(entry.period or "")) > 120:
                    raise ValueError("组织、职位或时间过长")
                if len(entry.bullets) > 40:
                    raise ValueError("每段经历最多 40 条要点")
                if any(not b.text.strip() or len(b.text) > 400 for b in entry.bullets):
                    raise ValueError("要点需要 1–400 个字符")
        return self


class SaveRevisionRequest(ResumeDraft):
    expected_revision: int = Field(ge=0)


class RestoreRevisionRequest(BaseModel):
    expected_revision: int = Field(ge=0)


class ResumeRevision(BaseModel):
    id: UUID
    revision: int
    reason: str
    draft: ResumeDraft
    created_at: datetime


class EditorResponse(BaseModel):
    resume: ResumeRead
    revision: int
    history: list[ResumeRevision]


class AiEditRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    section_index: int = Field(ge=0)
    entry_index: int = Field(ge=0)
    instruction: str = Field(min_length=1, max_length=2000)


class AiDecisionRequest(BaseModel):
    accept: bool


class AiEditResponse(BaseModel):
    run_id: UUID
    resume_id: UUID
    status: Literal["pending", "applied", "rejected"]
    base_revision: int
    section_index: int
    entry_index: int
    instruction: str
    original_bullets: list[dict]
    proposed_bullets: list[dict]
    warnings: list[str]
    is_stub: bool
    editor: EditorResponse | None = None
