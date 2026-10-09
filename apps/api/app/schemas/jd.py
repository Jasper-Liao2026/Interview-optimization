"""JD 解析相关的接口模型（M1-3）。

`JobProfile` 同时是**三处**的共同契约，这是刻意的：
  1. LLM 结构化输出的校验目标（app/agents/jd_parser.py）
  2. `job_descriptions.parsed` 这一 jsonb 列的形状
  3. 前端拿到的岗位画像类型（由 OpenAPI 生成）

一处定义、三处一致，避免「prompt 里写的字段」和「代码里读的字段」漂移。
"""

from __future__ import annotations

import uuid
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class JobProfile(BaseModel):
    """结构化岗位画像。

    **列表字段一律给默认值**：LLM 输出偶尔会漏字段，缺一个就该降级成空列表，
    而不是让整次解析失败重试。幂等的重试留给「JSON 根本解析不出来」那种情况。
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "后端开发工程师（校招）",
                "company": "某互联网公司",
                "seniority": "校招",
                "required_skills": ["Python", "FastAPI", "PostgreSQL"],
                "nice_to_have": ["LangGraph", "Docker"],
                "business_domain": "AI 应用 / 效率工具",
                "keywords": ["高并发", "agent 编排", "可观测性"],
                "implicit_preferences": ["偏好有从 0 到 1 独立交付经验的候选人"],
                "responsibilities": ["负责后端服务的设计与实现", "参与 agent 流程编排"],
            }
        }
    )

    title: str | None = Field(default=None, description="岗位名称")
    company: str | None = Field(default=None, description="公司名")
    seniority: str | None = Field(
        default=None, description="级别：实习 / 校招 / 社招-初级 / 社招-资深"
    )
    # 以下 list 字段刻意**不给默认值**：后端一定会给出（LLM 漏了会被重试机制修正），
    # 不给默认值才能让生成的 TS 类型是必填，前端才能直接 `profile.required_skills.map(...)`。
    required_skills: list[str] = Field(description="必备技能（硬性要求），改写时优先覆盖")
    nice_to_have: list[str] = Field(description="加分项")
    business_domain: str | None = Field(default=None, description="业务域")
    keywords: list[str] = Field(description="JD 里的高频关键词，用于对齐措辞")
    implicit_preferences: list[str] = Field(
        description="没明说但能读出来的偏好（例如「抗压」「独立交付」）"
    )
    responsibilities: list[str] = Field(description="岗位职责条目")


class JdParseRequest(BaseModel):
    raw_text: str = Field(min_length=10, max_length=20000, description="JD 原文，直接粘贴即可")
    title: str | None = Field(default=None, max_length=120, description="可选的岗位标题补充")
    company: str | None = Field(default=None, max_length=120, description="可选的公司名补充")
    persist: bool = Field(default=True, description="是否落库；false 时只解析不保存")


class JdParseResponse(BaseModel):
    """一次 JD 解析的结果。"""

    jd_id: UUID | None = Field(
        default=None, description="落库后的记录 ID；persist=false 时为 null（前端应避免依赖它）"
    )
    profile: JobProfile
    provider: str = Field(description="本次使用的 LLM provider")
    model: str = Field(description="本次使用的模型名")
    is_stub: bool = Field(description="true 表示未发起真实网络调用（stub provider）")
    trace_id: str = Field(description="贯穿前后端的请求 ID，可在 Langfuse 按它回放")
    warnings: list[str] = Field(
        description="解析过程中的降级提示，例如「JSON 首次解析失败，已重试」"
    )


class JdRead(BaseModel):
    """落库后的 JD 记录。"""

    id: UUID
    user_id: UUID
    title: str | None = None
    company: str | None = None
    raw_text: str
    parsed: JobProfile | None = None
    parser_model: str | None = None


def new_trace_id() -> str:
    """给非 HTTP 场景（脚本 / 测试）用的 trace_id。"""
    return uuid.uuid4().hex
