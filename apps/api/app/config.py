"""运行期配置。

所有配置项都从环境变量读取（本地开发走 `.env`）。
配置在这里集中定义，禁止在业务代码里散落 os.environ。
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- 服务标识 ---
    app_name: str = "resume-optimizer-api"
    service_id: str = "resume-optimizer-api"
    version: str = "0.1.0"
    milestone: str = Field(default="M5", description="当前里程碑，便于在自检页对照进度")
    environment: str = Field(default="local", description="local | staging | production")

    # --- HTTP ---
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"

    # --- CORS ---
    # 开发期前端默认跑在 3000；生产期改为真实域名
    #
    # `NoDecode` 是必须的：pydantic-settings 会把 list[str] 当成「复杂类型」，
    # 在**读取 source 阶段**就抢先 json.loads，此时字段校验器还没机会执行，
    # 于是 `a,b,c` 这种逗号分隔写法会直接抛 SettingsError、服务起不来。
    # 加上 NoDecode 后原样把字符串交给下面的校验器自己切分。
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
    )

    # --- 数据库 ---
    # 端口刻意选 54322 而不是 5432：避开本机可能已有的 Postgres 实例。
    # 数据层就是本机 Postgres 16 + pgvector，不依赖任何云服务或 CLI。
    database_url: str = "postgresql://postgres:postgres@localhost:54322/resume_optimizer"
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10
    db_connect_timeout_s: float = 3.0

    # --- 观测：Langfuse（M0-8）---
    # 刻意不做「必填校验」：观测是横切能力，缺它也必须能跑起来。
    # 两个 key 都给齐才算配置完成，否则 SDK 不初始化，trace 落回本地日志。
    langfuse_public_key: str | None = Field(default=None, description="缺省则关闭 trace 上报")
    langfuse_secret_key: str | None = Field(default=None, description="缺省则关闭 trace 上报")
    langfuse_host: str = Field(
        default="http://localhost:3000",
        description="本地实例默认端口 3000；Langfuse Cloud 换成 https://cloud.langfuse.com",
    )
    langfuse_project_id: str = Field(
        default="resume-optimizer",
        description="Langfuse 项目 ID，用于手工拼 trace 详情页 URL（见 langfuse_client.trace_url）",
    )

    # --- LLM（M0-8 只为验证观测链路，M4 起由 LangGraph 节点调用）---
    # provider=stub 是**明确标注的假实现**，仅用于在没有 key 的环境里验证
    # 「一次调用能在 Langfuse 里看到 trace」。真实调用请设成 openai-compatible。
    llm_provider: str = Field(default="stub", description="stub | openai-compatible")
    llm_base_url: str = Field(
        default="https://api.deepseek.com/v1",
        description="OpenAI 兼容端点；DeepSeek / Moonshot / 通义 / vLLM 均适用",
    )
    llm_api_key: str | None = None
    llm_model: str = "deepseek-chat"
    llm_timeout_s: float = 30.0
    # 256 是 M0 只为验证观测链路时定的值。M1 起要输出结构化 JD 画像与多条改写要点，
    # 256 token 会被截断成不完整 JSON（表现为「解析失败 → 重试 → 仍失败」）。
    llm_max_tokens: int = 2048

    # M5: vendor 表示厂商，provider 仅表示调用协议。
    generation_vendor: str = "deepseek"
    judge_vendor: str = "openai"
    judge_provider: str = Field(default="stub", pattern="^(stub|openai-compatible)$")
    judge_base_url: str = "https://api.openai.com/v1"
    judge_api_key: str | None = None
    judge_model: str = "gpt-4.1-mini"
    judge_max_tokens: int = Field(default=4096, ge=256)

    # 视觉可独立配置；未设置时复用 LLM 的 URL/key/model（须支持视觉）。
    vision_provider: str | None = Field(default=None, pattern="^(stub|openai-compatible)$")
    vision_base_url: str | None = None
    vision_api_key: str | None = None
    vision_model: str | None = None
    embedding_provider: str = Field(default="stub", pattern="^(stub|openai-compatible)$")
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_timeout_s: float = Field(default=30.0, gt=0)

    # --- 业务（M1）---
    # 项目定位是**本地自托管的开源工具**：单实例单用户，不做账号体系，
    # 所有写操作都挂在这个固定的本机用户上。
    # 与 supabase/seed.sql 里的固定 UUID 一致，改这里必须同步改 seed。
    dev_user_id: str = Field(
        default="00000000-0000-4000-8000-000000000001",
        description="固定的本机用户；本项目单机单用户，不由登录态提供",
    )
    dev_user_name: str = Field(default="本地开发用户", description="M1 简历抬头用的姓名")
    dev_user_headline: str | None = Field(
        default="后端 / AI 应用开发", description="M1 简历抬头的一句话定位"
    )
    # 改写用的模型上下文上限：素材库条目可能很长，超出会直接截断而不是静默超时
    rewrite_max_input_chars: int = Field(
        default=6000, description="单条经历送进改写 prompt 的字符上限"
    )
    rewrite_max_concurrency: int = Field(default=4, ge=1, le=32)

    # --- PDF 导出（M1-6 / M1-7）---
    # 走「无头 Chromium 打印服务端渲染的同一份 HTML」这条路：
    # 本机装了 Edge/Chrome，无需额外下载 ~150MB 的浏览器内核。
    chromium_path: str | None = Field(
        default=None,
        description="留空则自动探测 Edge / Chrome；显式指定用于固定版本",
    )
    pdf_timeout_s: float = Field(default=60.0, description="单次无头打印超时")

    @property
    def langfuse_configured(self) -> bool:
        """两个 key 都齐才算能用。只给一个通常是复制粘贴漏了，按未配置处理更安全。"""
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """支持两种写法，避免在 .env 里手写 JSON：
        - 逗号分隔：`CORS_ORIGINS=http://a,http://b`
        - JSON 数组：`CORS_ORIGINS=["http://a","http://b"]`
        """
        if isinstance(value, str):
            raw = value.strip()
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    pass
                else:
                    return parsed
            return [item.strip() for item in raw.split(",") if item.strip()]
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
