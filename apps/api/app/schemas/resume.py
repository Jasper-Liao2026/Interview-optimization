"""简历生成与渲染相关的接口模型（M1-4 / M1-5）。

这里有两组模型，职责不同，刻意分开：

1. **改写模型的输出**（`RewrittenExperience`）
   只包含「模型可以决定的东西」—— 要点文本。像 org / role / 时间区间这类
   **事实字段一律不由模型产出**，而是从原始条目原样带过来。
   这是 M4-7「不可编造」红线的第一道防线：模型连改公司名的机会都没有。

2. **简历结构**（`ResumeEntry` / `ResumeSection` / `ResumeRead`）
   面向渲染与前端的数据形状，落进 `resumes.sections` 这列 jsonb。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.experience import ExperienceKind
from app.schemas.jd import JobProfile


# ============================================================ 改写（M1-4）
class RewrittenBullet(BaseModel):
    """一条改写后的要点。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=400, description="改写后的要点文本")
    evidence: list[str] = Field(
        description=("事实来源：逐字引用原始描述、技能标签、定性要点或量化结果。"),
    )


class RewrittenExperience(BaseModel):
    """单条经历的改写结果 —— 即 LLM 的输出契约。"""

    model_config = ConfigDict(extra="forbid")

    bullets: list[RewrittenBullet] = Field(description="改写后的要点，按重要性排序，建议 2–5 条")
    summary: str | None = Field(
        default=None, max_length=300, description="可选的一句话概述，用于简历摘要区"
    )


# ============================================================ 简历结构
class ResumeBullet(BaseModel):
    """简历里的一条要点。"""

    text: str
    evidence: list[str]


class ResumeEntry(BaseModel):
    """简历里的一段经历。

    `org` / `role` / `period` 全部**来自原始经历条目**，不由模型产出。
    """

    experience_id: UUID | None = Field(description="来源经历条目 ID")
    kind: ExperienceKind
    org: str
    role: str
    period: str | None = Field(description="形如 2026.09 – 至今；无时间信息时为 null")
    bullets: list[ResumeBullet]


class ResumeSection(BaseModel):
    """简历的一个分区，例如「项目经历」「实习经历」。"""

    title: str
    entries: list[ResumeEntry]


class ResumeHeader(BaseModel):
    """简历抬头。M1 从 profiles 表取；M2-7 起可编辑。"""

    name: str
    headline: str | None = None


class ResumeRead(BaseModel):
    """一份生成的简历快照。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "11111111-1111-4111-8111-111111111111",
                "user_id": "00000000-0000-4000-8000-000000000001",
                "jd_id": None,
                "title": "后端开发工程师（校招）",
                "template": "classic",
                "header": {"name": "本地开发用户", "headline": "后端 / AI 应用开发"},
                "sections": [],
                "status": "draft",
                "generator": "stub:deepseek-chat(stub)",
                "created_at": "2026-10-09T10:00:00Z",
                "updated_at": "2026-10-09T10:00:00Z",
            }
        }
    )

    id: UUID
    user_id: UUID
    jd_id: UUID | None = None
    title: str
    template: str = "classic"
    header: ResumeHeader
    sections: list[ResumeSection]
    status: str = Field(description="draft | exported")
    generator: str | None = Field(default=None, description="产出该简历的 provider:model")
    generator_vendor: str | None = Field(
        default=None, description="生成时记录的厂商；旧数据可能为空"
    )
    created_at: datetime
    updated_at: datetime


# ============================================================ 生成入参
class GenerateRequest(BaseModel):
    """一次「JD → 简历」生成的入参。"""

    run_id: UUID | None = Field(default=None, description="可由客户端指定，用于幂等生成和恢复")

    jd_text: str | None = Field(
        default=None, min_length=10, max_length=20000, description="JD 原文，与 jd_id 二选一"
    )
    jd_id: UUID | None = Field(default=None, description="使用已保存的 JD，复用解析画像")
    experience_ids: list[UUID] = Field(
        default_factory=list,
        description="要用哪些经历。留空表示用当前用户的全部经历（走 M1-2 的素材库）",
    )
    title: str | None = Field(
        default=None, max_length=120, description="简历标题；留空则由岗位名推导"
    )
    persist: bool = Field(default=True, description="是否落库；false 时只生成不保存")

    @model_validator(mode="after")
    def unique_experiences(self) -> GenerateRequest:
        if len(self.experience_ids) != len(set(self.experience_ids)):
            raise ValueError("experience_ids 不得重复")
        return self

    @model_validator(mode="after")
    def validate_jd_source(self) -> GenerateRequest:
        if (self.jd_text is None) == (self.jd_id is None):
            raise ValueError("必须且只能提供 jd_text 或 jd_id 之一")
        return self


class GenerationFailure(BaseModel):
    index: int
    experience_id: UUID
    error: str
    details: list[str] = Field(default_factory=list)
    retryable: bool = True


class GenerationItem(BaseModel):
    index: int
    experience_id: UUID
    org: str
    status: Literal["pending", "succeeded", "failed"]
    entry: ResumeEntry | None = None


class GenerationCheckpointRead(BaseModel):
    status: Literal["pending", "running", "completed", "partial", "failed"]
    updated_at: datetime
    backend: Literal["postgres", "memory"]
    resumable: bool


class GenerateResponse(BaseModel):
    """一次生成的结果。"""

    run_id: UUID
    resume: ResumeRead
    items: list[GenerationItem]
    failures: list[GenerationFailure]
    checkpoint: GenerationCheckpointRead
    profile: JobProfile = Field(description="本次使用的岗位画像，便于前端展示「为什么这样改写」")
    preview_path: str | None = Field(
        default=None, description="服务端渲染的 HTML 预览地址；persist=false 时为 null"
    )
    pdf_path: str | None = Field(default=None, description="PDF 下载地址；persist=false 时为 null")
    provider: str
    model: str
    is_stub: bool
    trace_id: str
    langfuse_trace_url: str | None = None
    prompt_version: str | None = None
    usage: GenerationUsage = Field(
        default_factory=lambda: GenerationUsage(),
        description="本次生成的 token、延迟与调用明细",
    )
    warnings: list[str] = Field(description="生成过程中的降级提示（stub、截断、JSON 重试等）")


class UsageCall(BaseModel):
    id: str | None = None
    operation: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int = 0
    latency_ms: float = 0
    prompt_version: str | None = None
    prompt_hash: str | None = None
    cost_usd: float | None = None
    is_stub: bool = False
    status: str = "succeeded"


class GenerationUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0
    cost_usd: float | None = None
    unknown_usage_calls: int = 0
    is_stub: bool = False
    calls: list[UsageCall] = Field(default_factory=list)
