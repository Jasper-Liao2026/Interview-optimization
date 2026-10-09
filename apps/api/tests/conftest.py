"""测试夹具。

设计要点：

1. **测试不依赖真实数据库。**
   `get_database` 是 FastAPI 依赖，测试里用 `dependency_overrides` 换成假实现，
   这样「数据库通了」和「数据库没通」两条分支都能被确定性地覆盖，
   也让 CI 不必起 postgres。

2. **测试不依赖开发者本机的 `.env`。**（见下面的 `_isolate_settings`）
   否则同一份代码「我这儿过、CI 不过」——因为本地 .env 里填了 langfuse key，
   而 CI 没有。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.agents.jd_parser import JdParser
from app.agents.rewriter import ExperienceRewriter
from app.config import Settings, get_settings
from app.db import DatabaseProbe, get_database
from app.deps import (
    get_experience_repository,
    get_generation_service,
    get_jd_repository,
    get_pdf_exporter,
    get_profile_repository,
    get_resume_repository,
)
from app.llm import LlmClient
from app.main import create_app
from app.observability import get_observability
from app.services.generation import ResumeGenerationService
from tests.fakes import (
    FakeExperienceRepository,
    FakeJobDescriptionRepository,
    FakePdfExporter,
    FakeProfileRepository,
    FakeResumeRepository,
    resume_row,
)


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """把配置源与开发者本机环境彻底隔开，保证每次跑测试的起点都一致。

    为什么要动 `model_config` 而不只是删环境变量：
    `Settings` 的 `env_file=".env"` 是**相对当前工作目录**解析的，从 `apps/api`
    跑 pytest 时会被读进来。pydantic-settings 在实例化时才从
    `cls.model_config['env_file']` 取值，所以运行期改写这一项是有效的开关。

    同时必须清 `lru_cache`：`app.main` 在 import 时就 `create_app()` 过一次，
    已经把「带本机 key 的 settings」缓存住了，不清就会漏进来。
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in (
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_HOST",
        "LANGFUSE_PROJECT_ID",
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "CORS_ORIGINS",
    ):
        monkeypatch.delenv(name, raising=False)

    get_settings.cache_clear()
    get_observability.cache_clear()
    yield
    get_settings.cache_clear()
    get_observability.cache_clear()


class FakeDatabase:
    """最小可用的 Database 替身。"""

    def __init__(
        self,
        probe: DatabaseProbe | None = None,
        meta: list[dict[str, Any]] | None = None,
        *,
        explode: bool = False,
    ) -> None:
        self._probe = probe or DatabaseProbe(connected=True, latency_ms=1.23, server_version="16.4")
        self._meta = meta
        self._explode = explode
        self.probe_calls = 0

    async def probe(self) -> DatabaseProbe:
        self.probe_calls += 1
        if self._explode:
            raise AssertionError("本测试不允许访问数据库")
        return self._probe

    async def fetch_service_meta(self) -> list[dict[str, Any]] | None:
        if self._explode:
            raise AssertionError("本测试不允许访问数据库")
        return self._meta

    async def close(self) -> None:  # pragma: no cover - 生命周期里会被调到
        return None


SAMPLE_META: list[dict[str, Any]] = [
    {
        "key": "schema_version",
        "value": "m0_0001",
        "updated_at": datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
    },
    {
        "key": "service_name",
        "value": "resume-optimizer-api",
        "updated_at": datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
    },
]


@pytest.fixture
def app() -> Iterator[FastAPI]:
    application = create_app()
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
def override_db(app: FastAPI):
    def _apply(fake: FakeDatabase) -> FakeDatabase:
        app.dependency_overrides[get_database] = lambda: fake
        return fake

    return _apply


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.fixture
def api_wiring(app: FastAPI) -> Iterator[dict[str, Any]]:
    """把**外部依赖**换成假实现，路由 / 服务 / agent 全部用真的。

    这样安排的原因：本项目的技术风险不在路由转发，而在「JD → 改写 → 组装 →
    渲染 → 导出」以及「录入 → 编辑 → 分组展示」这些链路本身是否真能串起来。
    用假仓储把数据库摘掉，链路本身就能在 CI 里被完整验证（CI 不起 postgres）。

    返回 dict 而不是裸对象：测试里经常要直接摆布某一块假实现
    （例如 `wiring["experiences"].rows = []` 造出「素材库为空」的场景）。
    """
    settings = get_settings()
    experiences = FakeExperienceRepository()
    jds = FakeJobDescriptionRepository()
    resumes = FakeResumeRepository(resume_row())
    profiles = FakeProfileRepository()
    llm = LlmClient(settings)

    service = ResumeGenerationService(
        settings=settings,
        experiences=experiences,  # type: ignore[arg-type]
        job_descriptions=jds,  # type: ignore[arg-type]
        resumes=resumes,  # type: ignore[arg-type]
        profiles=profiles,  # type: ignore[arg-type]
        parser=JdParser(llm),
        rewriter=ExperienceRewriter(llm, max_input_chars=settings.rewrite_max_input_chars),
        llm=llm,
        observability=get_observability(),
    )

    app.dependency_overrides[get_experience_repository] = lambda: experiences
    app.dependency_overrides[get_jd_repository] = lambda: jds
    app.dependency_overrides[get_resume_repository] = lambda: resumes
    app.dependency_overrides[get_profile_repository] = lambda: profiles
    app.dependency_overrides[get_generation_service] = lambda: service
    app.dependency_overrides[get_pdf_exporter] = lambda: FakePdfExporter()

    yield {
        "experiences": experiences,
        "jds": jds,
        "resumes": resumes,
        "profiles": profiles,
        "service": service,
        "llm": llm,
    }
    app.dependency_overrides.clear()
