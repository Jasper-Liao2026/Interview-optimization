"""经历条目的接口模型（M1-2）。

沿用 M0 定下的约定：**这里是接口类型的唯一定义源**，
前端 TS 类型由 `pnpm gen:types` 从 OpenAPI 生成，禁止手写。

M1 只做到「够垂直切片用」的程度；M2-1 会补「多版本表述」「量化结果细分」等字段。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# 与 migration 里的 check 约束一致（project / internship / campus）
ExperienceKind = Literal["project", "internship", "campus"]


class ExperienceBase(BaseModel):
    kind: ExperienceKind = Field(description="经历类型：项目 / 实习 / 校园")
    org: str = Field(min_length=1, max_length=120, description="组织、公司或项目名")
    role: str = Field(min_length=1, max_length=120, description="角色或职位")
    start_date: date | None = Field(default=None, description="开始时间")
    end_date: date | None = Field(default=None, description="结束时间；进行中留空")
    raw_description: str = Field(
        min_length=1,
        max_length=8000,
        description="原始描述。**事实基线** —— 后续改写的内容必须可回溯到这里",
    )
    skill_tags: list[str] = Field(default_factory=list, max_length=40, description="技能标签")
    highlights: list[str] = Field(
        default_factory=list,
        max_length=40,
        description="量化结果 / 要点。改写只允许引用，不允许模型凭空生成",
    )
    sort_order: int = Field(default=0, description="同一分类内的展示顺序")


class ExperienceCreate(ExperienceBase):
    """新增经历的入参。

    刻意**不含 user_id**：M1 没有登录体系，用户由服务端按配置注入，
    不能让客户端指定（否则就是一个越权写入的洞）。
    """


class ExperienceRead(ExperienceBase):
    """读出的经历条目。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "00000000-0000-4000-8000-000000000101",
                "user_id": "00000000-0000-4000-8000-000000000001",
                "kind": "project",
                "org": "简历优化器",
                "role": "独立开发",
                "start_date": "2026-09-01",
                "end_date": None,
                "raw_description": "独立设计与实现一个批量生成岗位适配版简历的 Web 工具……",
                "skill_tags": ["Python", "FastAPI", "LangGraph"],
                "highlights": ["把请求 trace_id 复用为 Langfuse trace_id"],
                "sort_order": 0,
                "created_at": "2026-10-09T10:00:00Z",
                "updated_at": "2026-10-09T10:00:00Z",
            }
        }
    )

    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime


class ExperienceListResponse(BaseModel):
    items: list[ExperienceRead] = Field(description="当前用户的经历条目，按分类与 sort_order 排序")
    total: int = Field(description="条目总数")
