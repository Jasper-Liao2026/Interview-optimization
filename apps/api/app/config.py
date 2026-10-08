"""运行期配置。

所有配置项都从环境变量读取（本地开发走 `.env`）。
配置在这里集中定义，禁止在业务代码里散落 os.environ。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    milestone: str = Field(default="M0", description="当前里程碑，便于在自检页对照进度")
    environment: str = Field(default="local", description="local | staging | production")

    # --- HTTP ---
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"

    # --- CORS ---
    # 开发期前端默认跑在 3000；生产期改为真实域名
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
    )

    # --- 数据库 ---
    # 端口刻意选 54322：与 Supabase 本地实例默认端口一致，
    # 这样「docker compose 的 postgres」和「supabase start 的 postgres」
    # 可以共用同一份 DATABASE_URL，不必切换配置。
    database_url: str = "postgresql://postgres:postgres@localhost:54322/resume_optimizer"
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10
    db_connect_timeout_s: float = 3.0

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """支持 `CORS_ORIGINS=a,b,c` 这种逗号分隔写法，免去在 .env 里写 JSON。"""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
