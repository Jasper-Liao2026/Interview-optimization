"""M5 评分循环的数据契约。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.jd import JobProfile
from app.schemas.resume import ResumeRead


class ScoreDimension(BaseModel):
    """一个 rubric 维度的得分。"""

    model_config = ConfigDict(extra="forbid")

    key: Literal["relevance", "coverage", "evidence", "clarity"]
    label: str
    score: float = Field(ge=0, le=100)
    weight: float = Field(gt=0, le=1)
    weighted_score: float = Field(ge=0, le=100)
    rationale: str


class ScoreDeduction(BaseModel):
    """一处可执行的扣分和改进建议。"""

    model_config = ConfigDict(extra="forbid")

    item_index: int = Field(ge=0)
    bullet_index: int | None = Field(default=None, ge=0)
    dimension: Literal["relevance", "coverage", "evidence", "clarity"]
    points: float = Field(gt=0)
    reason: str
    suggestion: str


class ScoreResult(BaseModel):
    """一轮评分的完整结果。"""

    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0, le=100)
    dimensions: list[ScoreDimension]
    deductions: list[ScoreDeduction]
    recommendations: list[str]
    low_score_items: list[int] = Field(description="需要定向重写的经历下标")
    factual_violations: list[int] = Field(
        default_factory=list, description="原始素材核验失败的经历下标；不能作为最佳版本保存或导出"
    )
    judge_provider: str = Field(description="评分者 provider；与生成者分开记录")
    judge_model: str
    blind: bool = Field(description="评分输入已移除轮次和生成者信息")
    item_scores: dict[str, float] = Field(default_factory=dict)
    is_stub: bool = False
    usage_tokens: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)


class JudgeDimension(BaseModel):
    """LLM judge 对单条经历一个维度的 0-5 锚点评分。"""

    model_config = ConfigDict(extra="forbid")

    key: Literal["relevance", "coverage", "evidence", "clarity"]
    score: float = Field(ge=0, le=5)
    rationale: str = Field(min_length=1, max_length=500)


class JudgeAssessment(BaseModel):
    """LLM judge 对单条经历的结构化判断。"""

    model_config = ConfigDict(extra="forbid")

    item_index: int = Field(ge=0)
    dimensions: list[JudgeDimension] = Field(min_length=4, max_length=4)
    deductions: list[str] = Field(default_factory=list, max_length=8)
    suggestions: list[str] = Field(default_factory=list, max_length=8)


class JudgeOutput(BaseModel):
    """异构模型 judge 的最小输出契约。"""

    model_config = ConfigDict(extra="forbid")

    assessments: list[JudgeAssessment]


class ScoreSnapshot(BaseModel):
    """循环中的不可变历史快照。"""

    round: int = Field(ge=0)
    result: ScoreResult
    sections: list[dict]
    cost: float = Field(ge=0)


class ScoreLoopResult(BaseModel):
    """评分循环停止后的最佳版本和完整历史。"""

    best: ScoreSnapshot
    snapshots: list[ScoreSnapshot]
    stop_reason: Literal["threshold", "max_rounds", "cost_limit", "no_low_score_items"]


class ScoreRequest(BaseModel):
    """对已生成简历评分并按低分条目执行有限改进。"""

    threshold: float = Field(default=80, ge=0, le=100)
    max_rounds: int = Field(default=2, ge=0, le=2)
    cost_limit: float = Field(
        default=100, ge=0, description="最大模型调用预算单位，含重试预留；不是货币"
    )
    persist: bool = True


class ScoreResponse(BaseModel):
    resume_id: str
    profile: JobProfile
    loop: ScoreLoopResult
    resume: ResumeRead
    score_run_id: UUID | None = None
    preview_path: str | None = None
    pdf_path: str | None = None
