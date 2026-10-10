"""JD 解析相关的接口模型（M1-3）。

`JobProfile` 同时是**三处**的共同契约，这是刻意的：
  1. LLM 结构化输出的校验目标（app/agents/jd_parser.py）
  2. `job_descriptions.parsed` 这一 jsonb 列的形状
  3. 前端拿到的岗位画像类型（由 OpenAPI 生成）

一处定义、三处一致，避免「prompt 里写的字段」和「代码里读的字段」漂移。
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import datetime
from io import BytesIO
from uuid import UUID

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator


class JobProfile(BaseModel):
    """结构化岗位画像。

    列表字段必填；模型漏字段由结构化输出重试修正。
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
    model_config = ConfigDict(str_strip_whitespace=True)
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
    source_type: str = "text"
    created_at: datetime
    updated_at: datetime


class JdImageParseRequest(BaseModel):
    image_data_url: str = Field(max_length=7_000_000, description="PNG/JPEG/WebP data URL，≤5 MiB")
    title: str | None = Field(default=None, max_length=120)
    company: str | None = Field(default=None, max_length=120)
    persist: bool = True

    @field_validator("image_data_url")
    @classmethod
    def validate_image(cls, value: str) -> str:
        try:
            header, encoded = value.split(",", 1)
            formats = {
                "data:image/png;base64": "PNG",
                "data:image/jpeg;base64": "JPEG",
                "data:image/webp;base64": "WEBP",
            }
            if header not in formats:
                raise ValueError("仅支持 PNG、JPEG、WebP 的 base64 data URL")
            content = base64.b64decode(encoded, validate=True)
            if not content or len(content) > 5 * 1024 * 1024:
                raise ValueError("截图大小必须在 1 字节至 5 MiB 之间")
            with Image.open(BytesIO(content)) as img:
                if img.format != formats[header] or img.width * img.height > 20_000_000:
                    raise ValueError("截图格式不符或超过 2000 万像素")
                img.verify()
            # JPEG/WebP 的 verify 可能只检查头部，完整解码才能发现截断像素数据。
            with Image.open(BytesIO(content)) as decoded:
                decoded.load()
        except (
            binascii.Error,
            UnidentifiedImageError,
            OSError,
            Image.DecompressionBombError,
        ) as exc:
            raise ValueError("截图内容损坏或不是有效图片") from exc
        return value


class JdImageExtraction(BaseModel):
    raw_text: str = Field(
        min_length=10, max_length=20000, description="逐字读取的 JD 文本，不可补写"
    )
    profile: JobProfile


class JdImageParseResponse(JdParseResponse):
    raw_text: str


class JdListResponse(BaseModel):
    items: list[JdRead]
    total: int


class JdMetadataUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    company: str | None = Field(default=None, max_length=120)


def new_trace_id() -> str:
    """给非 HTTP 场景（脚本 / 测试）用的 trace_id。"""
    return uuid.uuid4().hex
