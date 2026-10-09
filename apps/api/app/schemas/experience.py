"""经历条目的接口模型（M1-2 建立，M2-1 / M2-6 补全）。

沿用 M0 定下的约定：**这里是接口类型的唯一定义源**，
前端 TS 类型由 `pnpm gen:types` 从 OpenAPI 生成，禁止手写。

M1 只做到「够垂直切片用」的程度；M2 补上两样东西：
  - `metrics`  —— 量化结果细分，让 M4-7「不得编造数字」变成可自动校验的规则
  - `variants` —— 多版本表述，同一条目按岗位方向存多份写法（M2-6）
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 与 migration 里的 check 约束一致（project / internship / campus）
ExperienceKind = Literal["project", "internship", "campus"]


# ============================================================ 子结构
class ExperienceMetric(BaseModel):
    """一条**量化结果**：这段经历里可测量的产出。

    量化的是「成果」，不是过程、也不是技能。三要素缺一不可：

        name    = 指标名，如「接口 P99 延迟」
        value   = 数值原文，如「800ms → 120ms」
        context = 口径，如「压测 5000 QPS 下」

    `value` 刻意保持**字符串**而不是解析成数字 + 单位：
    简历里的量化写法千奇百怪（`24 → 82 条`、`3000+`、`下降 40%`），
    强行结构化只会让用户在录入时跟表单较劲。保持原样同样能满足
    「改写可回溯」——校验时做的是**子串匹配**，不需要理解数值语义。
    """

    name: str = Field(min_length=1, max_length=60, description="指标名，如「接口 P99 延迟」")
    value: str = Field(min_length=1, max_length=120, description="数值原文，如「800ms → 120ms」")
    context: str | None = Field(
        default=None,
        max_length=200,
        description="口径：怎么算的 / 什么范围，如「压测 5000 QPS 下」",
    )


class ExperienceVariant(BaseModel):
    """同一条经历在**某个岗位方向**下的表述版本（M2-6）。

    典型用法：同一段「简历优化器」经历，投后端岗时强调 FastAPI / LangGraph 编排，
    投 AI 应用岗时强调 prompt 设计与评测。两份表述都真实，只是侧重不同。

    变体是**派生内容**，事实基线仍是 `raw_description`。M2 阶段由用户手工维护，
    M4 起可由改写流程自动产出并回填。
    """

    direction: str = Field(
        min_length=1, max_length=60, description="岗位方向，如「后端开发」「AI 应用」"
    )
    text: str = Field(min_length=1, max_length=2000, description="该方向下的表述")
    note: str | None = Field(default=None, max_length=200, description="备注，如来源 JD / 使用建议")


# ============================================================ 经历条目
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
        description="**定性**要点。改写只允许引用，不允许模型凭空生成",
    )
    metrics: list[ExperienceMetric] = Field(
        default_factory=list,
        max_length=40,
        description="**量化**结果。与 highlights 分开存放，便于自动校验「数字不可编造」（M4-7）",
    )
    variants: list[ExperienceVariant] = Field(
        default_factory=list,
        max_length=20,
        description="按岗位方向保存的多版本表述；同一条目同方向只允许一份",
    )
    sort_order: int = Field(
        default=0,
        description=(
            "同一分类内的展示顺序。**M2 起 UI 不再维护**：列表按经历时间倒序，"
            "本字段降级为同时间条目的稳定排序兜底"
        ),
    )

    @field_validator("variants")
    @classmethod
    def _unique_direction(cls, value: list[ExperienceVariant]) -> list[ExperienceVariant]:
        """同一条目同方向只允许一份表述。

        这条约束放在校验层而不是数据库：jsonb 里做唯一性要靠表达式索引，
        收益不抵复杂度；而「同方向两份表述」本身就是用户填错了，
        应该在提交时直接报 422，而不是安静地存成一个谁也说不清哪个生效的数组。
        """
        seen: set[str] = set()
        for variant in value:
            key = variant.direction.strip()
            if key in seen:
                raise ValueError(f"岗位方向「{key}」重复：同一条经历同一方向只允许一份表述")
            seen.add(key)
        return value

    @model_validator(mode="after")
    def _check_date_order(self) -> ExperienceBase:
        """结束时间不得早于开始时间。

        migration 里有一条同名 check 约束，这里**刻意重复一遍**：
        数据库抛的 CheckViolation 到接口层会变成 500，而用户填错日期是**用户侧**问题，
        应该在提交时就得到 422 与一句人话。两边都留着，谁也替代不了谁。
        """
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("结束时间不能早于开始时间（进行中的经历请把结束时间留空）")
        return self


class ExperienceCreate(ExperienceBase):
    """新增经历的入参。

    刻意**不含 user_id**：本项目是本地单机工具，用户由服务端按配置注入，
    不能让客户端指定（否则就是一个越权写入的洞）。
    """


class ExperienceUpdate(ExperienceBase):
    """编辑经历的入参 —— **PUT 全量替换**语义（M2-3）。

    为什么是 PUT 而不是 PATCH：编辑表单提交的就是整条记录，
    全量语义下「必填字段缺失」直接被 Pydantic 拦成 422，不需要在服务层
    区分「字段没传」和「显式置空」这两种 PATCH 特有的歧义。
    与「Pydantic model 是接口类型唯一定义源」这条硬约定也最契合。

    同样不含 `id` / `user_id` / 时间戳：这些不是用户能改的东西。
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
                "highlights": ["接入 Langfuse 观测，每次 LLM 调用可按 trace_id 回放"],
                "metrics": [
                    {
                        "name": "单元测试",
                        "value": "24 → 82 条",
                        "context": "后端 ruff + pytest 全绿",
                    }
                ],
                "variants": [
                    {
                        "direction": "后端开发",
                        "text": "用 FastAPI 承载全部业务逻辑，用 LangGraph 编排多步流程……",
                        "note": "投后端岗时强调编排与工程化",
                    }
                ],
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
    items: list[ExperienceRead] = Field(
        description="当前用户的经历条目，按分类分组、组内按经历时间倒序"
    )
    total: int = Field(description="条目总数")
