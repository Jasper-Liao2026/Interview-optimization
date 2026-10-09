"""M1 的接口层（M1-2 ~ M1-7）。

这里跑的是**真实的路由 + 真实的服务 + 真实的 agent**，只把仓储与浏览器换成假实现
（假实现见 `tests/fakes.py`，装配见 `conftest.py` 的 `api_wiring`），
LLM 用 `stub` provider（默认值，不发网络请求）。

这样安排的原因：M1 的技术风险不在路由转发，而在「JD → 改写 → 组装 → 渲染 → 导出」
这条链路本身是否真的能串起来。用假仓储把外部依赖摘掉，链路本身得以在 CI 里被完整验证。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import FastAPI

from app.config import Settings
from app.deps import get_pdf_exporter
from app.pdf import PdfExportError
from tests.fakes import API, DEV_USER, RESUME_ID, FakePdfExporter


# ------------------------------------------------------------------ experiences
async def test_list_experiences(client, api_wiring: dict[str, Any]) -> None:
    response = await client.get(f"{API}/experiences")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["org"] == "简历优化器"


async def test_create_experience_ignores_client_supplied_user(
    client, api_wiring: dict[str, Any]
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


async def test_get_experience_404(client, api_wiring: dict[str, Any]) -> None:
    response = await client.get(f"{API}/experiences/{uuid4()}")
    assert response.status_code == 404


# ------------------------------------------------------------------------ jd
async def test_parse_jd_with_stub_provider(client, api_wiring: dict[str, Any]) -> None:
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


async def test_parse_jd_without_persist(client, api_wiring: dict[str, Any]) -> None:
    response = await client.post(
        f"{API}/jd/parse",
        json={"raw_text": "招聘后端开发工程师，要求熟悉 Python 与 FastAPI。", "persist": False},
    )

    assert response.status_code == 200
    assert response.json()["jd_id"] is None
    assert api_wiring["jds"].created == []


async def test_parse_jd_rejects_too_short_text(client, api_wiring: dict[str, Any]) -> None:
    response = await client.post(f"{API}/jd/parse", json={"raw_text": "太短"})
    assert response.status_code == 422


# ------------------------------------------------------------------- generate
async def test_generate_resume_end_to_end_with_stub(client, api_wiring: dict[str, Any]) -> None:
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
    assert api_wiring["jds"].created and api_wiring["resumes"].created


async def test_generate_without_experiences_returns_400(client, api_wiring: dict[str, Any]) -> None:
    """素材库为空是**用户侧**问题，要回 400 并说明补救办法，而不是 500。"""
    api_wiring["experiences"].rows = []

    response = await client.post(
        f"{API}/resumes/generate",
        json={"jd_text": "招聘后端开发工程师，要求熟悉 Python 与 FastAPI。"},
    )

    assert response.status_code == 400
    assert "经历素材" in response.json()["detail"]


async def test_generate_without_persist_has_no_preview_path(
    client, api_wiring: dict[str, Any]
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
async def test_get_resume(client, api_wiring: dict[str, Any]) -> None:
    response = await client.get(f"{API}/resumes/{RESUME_ID}")

    assert response.status_code == 200
    assert response.json()["title"] == "后端开发工程师（校招）"


async def test_get_resume_404(client, api_wiring: dict[str, Any]) -> None:
    api_wiring["resumes"].row = None
    response = await client.get(f"{API}/resumes/{RESUME_ID}")
    assert response.status_code == 404


async def test_resume_html_is_a_complete_document(client, api_wiring: dict[str, Any]) -> None:
    response = await client.get(f"{API}/resumes/{RESUME_ID}/html")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<!DOCTYPE html>" in response.text
    assert "本地开发用户" in response.text


async def test_resume_html_rejects_unknown_template(client, api_wiring: dict[str, Any]) -> None:
    response = await client.get(f"{API}/resumes/{RESUME_ID}/html?template=nope")
    assert response.status_code == 400


async def test_resume_pdf_returns_bytes_and_page_count(client, api_wiring: dict[str, Any]) -> None:
    response = await client.get(f"{API}/resumes/{RESUME_ID}/pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-resume-pages"] == "2"
    assert response.headers["content-disposition"].startswith("inline")
    assert response.content.startswith(b"%PDF")
    # 导出成功后才打标记
    assert api_wiring["resumes"].exported == [RESUME_ID]


async def test_resume_pdf_download_uses_attachment(client, api_wiring: dict[str, Any]) -> None:
    response = await client.get(f"{API}/resumes/{RESUME_ID}/pdf?download=1")
    assert response.headers["content-disposition"].startswith("attachment")


async def test_resume_pdf_without_browser_returns_503(
    client, api_wiring: dict[str, Any], app: FastAPI
) -> None:
    """没有浏览器是「能力暂不可用」，回 503 而不是 500；且**不能**留下 exported 标记。"""
    app.dependency_overrides[get_pdf_exporter] = lambda: FakePdfExporter(
        error=PdfExportError("未找到可用的 Chromium 系浏览器")
    )

    response = await client.get(f"{API}/resumes/{RESUME_ID}/pdf")

    assert response.status_code == 503
    assert "Chromium" in response.json()["detail"]
    assert api_wiring["resumes"].exported == []


async def test_settings_defaults_match_the_documented_m1_behaviour() -> None:
    """这些默认值被文档引用，改动会静默影响行为，锁定它们。"""
    settings = Settings()

    assert settings.api_prefix == API
    assert settings.llm_provider == "stub"
    assert settings.dev_user_id == str(DEV_USER)
    assert settings.llm_max_tokens >= 1024  # 256 会把结构化输出截断
