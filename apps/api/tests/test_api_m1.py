"""M1 的接口层（M1-2 ~ M1-7）。

这里跑的是**真实的路由 + 真实的服务 + 真实的 agent**，只把仓储与浏览器换成假实现，
LLM 用 `stub` provider（默认值，不发网络请求）。

这样安排的原因：M1 的技术风险不在路由转发，而在「JD → 改写 → 组装 → 渲染 → 导出」
这条链路本身是否真的能串起来。用假仓储把外部依赖摘掉，链路本身得以在 CI 里被完整验证。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI

from app.agents.jd_parser import JdParser
from app.agents.rewriter import ExperienceRewriter
from app.config import Settings, get_settings
from app.deps import (
    get_experience_repository,
    get_generation_service,
    get_jd_repository,
    get_profile_repository,
    get_resume_repository,
)
from app.llm import LlmClient
from app.observability import get_observability
from app.pdf import PdfExportError, get_pdf_exporter
from app.services.generation import ResumeGenerationService

API = "/api/v1"
DEV_USER = UUID("00000000-0000-4000-8000-000000000001")

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
RESUME_ID = UUID("11111111-1111-4111-8111-111111111111")


# ------------------------------------------------------------------- 夹具数据
def _experience_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": uuid4(),
        "user_id": DEV_USER,
        "kind": "project",
        "org": "简历优化器",
        "role": "独立开发",
        "start_date": date(2026, 9, 1),
        "end_date": None,
        "raw_description": "独立设计与实现一个批量生成岗位适配版简历的 Web 工具。",
        "skill_tags": ["Python", "FastAPI"],
        "highlights": ["把请求 trace_id 复用为 Langfuse trace_id"],
        "sort_order": 0,
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(overrides)
    return row


def _resume_sections() -> list[dict[str, Any]]:
    return [
        {
            "title": "项目经历",
            "entries": [
                {
                    "experience_id": str(uuid4()),
                    "kind": "project",
                    "org": "简历优化器",
                    "role": "独立开发",
                    "period": "2026.09 – 至今",
                    "bullets": [{"text": "一条要点", "evidence": []}],
                }
            ],
        }
    ]


def _resume_row() -> dict[str, Any]:
    return {
        "id": RESUME_ID,
        "user_id": DEV_USER,
        "jd_id": None,
        "title": "后端开发工程师（校招）",
        "template": "classic",
        "header": {"name": "本地开发用户", "headline": "后端 / AI 应用开发"},
        "sections": _resume_sections(),
        "status": "draft",
        "generator": "stub",
        "created_at": NOW,
        "updated_at": NOW,
    }


# ---------------------------------------------------------------------- 假实现
class FakeExperienceRepository:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows if rows is not None else [_experience_row()]
        self.created: list[dict[str, Any]] = []
        self.deleted: list[UUID] = []

    async def list_for_user(self, user_id: UUID) -> list[dict[str, Any]]:
        return list(self.rows)

    async def list_by_ids(self, user_id: UUID, ids: list[UUID]) -> list[dict[str, Any]]:
        wanted = set(ids)
        return [row for row in self.rows if row["id"] in wanted]

    async def get(self, user_id: UUID, experience_id: UUID) -> dict[str, Any] | None:
        return next((row for row in self.rows if row["id"] == experience_id), None)

    async def create(self, user_id: UUID, payload: Any) -> dict[str, Any]:
        row = _experience_row(**payload.model_dump())
        self.created.append(row)
        return row

    async def delete(self, user_id: UUID, experience_id: UUID) -> bool:
        self.deleted.append(experience_id)
        return any(row["id"] == experience_id for row in self.rows)


class FakeJobDescriptionRepository:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, user_id: UUID, **fields: Any) -> dict[str, Any]:
        row = {"id": uuid4(), "user_id": user_id, **fields}
        self.created.append(row)
        return row


class FakeResumeRepository:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row
        self.created: list[dict[str, Any]] = []
        self.exported: list[UUID] = []

    async def create(self, user_id: UUID, **fields: Any) -> dict[str, Any]:
        row = {**_resume_row(), "user_id": user_id, **fields}
        self.created.append(row)
        return row

    async def get(self, user_id: UUID, resume_id: UUID) -> dict[str, Any] | None:
        return self.row

    async def mark_exported(self, user_id: UUID, resume_id: UUID) -> None:
        self.exported.append(resume_id)


class FakeProfileRepository:
    async def ensure(self, user_id: UUID, **fields: Any) -> dict[str, Any]:
        return {
            "id": user_id,
            "display_name": fields.get("display_name", "本地开发用户"),
            "headline": fields.get("headline"),
        }


class FakePdfExporter:
    """假导出器。真导出要起 Chromium 进程，不适合放在单元测试里（见 test_pdf_export.py）。"""

    FAKE_PDF = b"%PDF-1.4\n/Type /Page\n/Type /Pages\n/Type /Page\n%%EOF"

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    async def render(self, html: str, *, timeout_s: float | None = None) -> bytes:
        if self.error is not None:
            raise self.error
        return self.FAKE_PDF


# ------------------------------------------------------------------- 覆盖依赖
@pytest.fixture
def m1_wiring(app: FastAPI) -> Iterator[dict[str, Any]]:
    settings = get_settings()
    experiences = FakeExperienceRepository()
    jds = FakeJobDescriptionRepository()
    resumes = FakeResumeRepository(_resume_row())
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
        "service": service,
    }
    app.dependency_overrides.clear()


# ------------------------------------------------------------------ experiences
async def test_list_experiences(client, m1_wiring: dict[str, Any]) -> None:
    response = await client.get(f"{API}/experiences")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["org"] == "简历优化器"


async def test_create_experience_ignores_client_supplied_user(
    client, m1_wiring: dict[str, Any]
) -> None:
    payload = {
        "kind": "internship",
        "org": "某公司",
        "role": "后端实习生",
        "raw_description": "参与订单服务的接口开发。",
    }
    response = await client.post(f"{API}/experiences", json=payload)

    assert response.status_code == 201
    # user_id 由服务端按当前用户注入，客户端改不动
    assert response.json()["user_id"] == str(DEV_USER)


async def test_get_experience_404(client, m1_wiring: dict[str, Any]) -> None:
    response = await client.get(f"{API}/experiences/{uuid4()}")
    assert response.status_code == 404


# ------------------------------------------------------------------------ jd
async def test_parse_jd_with_stub_provider(client, m1_wiring: dict[str, Any]) -> None:
    response = await client.post(
        f"{API}/jd/parse",
        json={"raw_text": "招聘后端开发工程师，要求熟悉 Python 与 FastAPI。", "persist": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_stub"] is True
    assert body["jd_id"] is not None
    # stub 的输出**必须**自曝是假的，且带降级提示
    assert body["profile"]["required_skills"] == ["【fixture】required_skills"]
    assert any("stub" in warning for warning in body["warnings"])


async def test_parse_jd_without_persist(client, m1_wiring: dict[str, Any]) -> None:
    response = await client.post(
        f"{API}/jd/parse",
        json={"raw_text": "招聘后端开发工程师，要求熟悉 Python 与 FastAPI。", "persist": False},
    )

    assert response.status_code == 200
    assert response.json()["jd_id"] is None
    assert m1_wiring["jds"].created == []


async def test_parse_jd_rejects_too_short_text(client, m1_wiring: dict[str, Any]) -> None:
    response = await client.post(f"{API}/jd/parse", json={"raw_text": "太短"})
    assert response.status_code == 422


# ------------------------------------------------------------------- generate
async def test_generate_resume_end_to_end_with_stub(client, m1_wiring: dict[str, Any]) -> None:
    """M1 的主线：一条经历 + 一个 JD → 生成 → 落库 → 给出预览与导出地址。"""
    response = await client.post(
        f"{API}/resumes/generate",
        json={"jd_text": "招聘后端开发工程师，要求熟悉 Python、FastAPI 与 PostgreSQL。"},
    )

    assert response.status_code == 200
    body = response.json()

    assert body["is_stub"] is True
    assert body["preview_path"] == f"{API}/resumes/{RESUME_ID}/html"
    assert body["pdf_path"] == f"{API}/resumes/{RESUME_ID}/pdf"
    # 事实字段来自原始条目，不由模型产出
    entry = body["resume"]["sections"][0]["entries"][0]
    assert entry["org"] == "简历优化器"
    assert entry["period"] == "2026.09 – 至今"
    # 落库被调用
    assert m1_wiring["jds"].created and m1_wiring["resumes"].created


async def test_generate_without_experiences_returns_400(client, m1_wiring: dict[str, Any]) -> None:
    """素材库为空是**用户侧**问题，要回 400 并说明补救办法，而不是 500。"""
    m1_wiring["experiences"].rows = []

    response = await client.post(
        f"{API}/resumes/generate",
        json={"jd_text": "招聘后端开发工程师，要求熟悉 Python 与 FastAPI。"},
    )

    assert response.status_code == 400
    assert "经历素材" in response.json()["detail"]


async def test_generate_without_persist_has_no_preview_path(
    client, m1_wiring: dict[str, Any]
) -> None:
    response = await client.post(
        f"{API}/resumes/generate",
        json={
            "jd_text": "招聘后端开发工程师，要求熟悉 Python 与 FastAPI。",
            "persist": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["preview_path"] is None
    assert body["pdf_path"] is None
    assert any("persist=false" in warning for warning in body["warnings"])


# --------------------------------------------------------------------- resumes
async def test_get_resume(client, m1_wiring: dict[str, Any]) -> None:
    response = await client.get(f"{API}/resumes/{RESUME_ID}")

    assert response.status_code == 200
    assert response.json()["title"] == "后端开发工程师（校招）"


async def test_get_resume_404(client, m1_wiring: dict[str, Any]) -> None:
    m1_wiring["resumes"].row = None
    response = await client.get(f"{API}/resumes/{RESUME_ID}")
    assert response.status_code == 404


async def test_resume_html_is_a_complete_document(client, m1_wiring: dict[str, Any]) -> None:
    response = await client.get(f"{API}/resumes/{RESUME_ID}/html")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<!DOCTYPE html>" in response.text
    assert "本地开发用户" in response.text


async def test_resume_html_rejects_unknown_template(client, m1_wiring: dict[str, Any]) -> None:
    response = await client.get(f"{API}/resumes/{RESUME_ID}/html?template=nope")
    assert response.status_code == 400


async def test_resume_pdf_returns_bytes_and_page_count(client, m1_wiring: dict[str, Any]) -> None:
    response = await client.get(f"{API}/resumes/{RESUME_ID}/pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-resume-pages"] == "2"
    assert response.headers["content-disposition"].startswith("inline")
    assert response.content.startswith(b"%PDF")
    # 导出成功后才打标记
    assert m1_wiring["resumes"].exported == [RESUME_ID]


async def test_resume_pdf_download_uses_attachment(client, m1_wiring: dict[str, Any]) -> None:
    response = await client.get(f"{API}/resumes/{RESUME_ID}/pdf?download=1")
    assert response.headers["content-disposition"].startswith("attachment")


async def test_resume_pdf_without_browser_returns_503(
    client, m1_wiring: dict[str, Any], app: FastAPI
) -> None:
    """没有浏览器是「能力暂不可用」，回 503 而不是 500；且**不能**留下 exported 标记。"""
    app.dependency_overrides[get_pdf_exporter] = lambda: FakePdfExporter(
        error=PdfExportError("未找到可用的 Chromium 系浏览器")
    )

    response = await client.get(f"{API}/resumes/{RESUME_ID}/pdf")

    assert response.status_code == 503
    assert "Chromium" in response.json()["detail"]
    assert m1_wiring["resumes"].exported == []


async def test_settings_defaults_match_the_documented_m1_behaviour() -> None:
    """这些默认值被文档引用，改动会静默影响行为，锁定它们。"""
    settings = Settings()

    assert settings.api_prefix == API
    assert settings.llm_provider == "stub"
    assert settings.dev_user_id == str(DEV_USER)
    assert settings.llm_max_tokens >= 1024  # 256 会把结构化输出截断
