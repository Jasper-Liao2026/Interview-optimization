"""M0-8 验收：观测层可降级、trace_id 规范化、自检接口可用。

测试全部离线：Langfuse 未配置时 SDK 根本不初始化，
LLM 用默认的 stub provider（不发起网络请求）。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.config import Settings
from app.llm import LlmClient, LlmError
from app.observability import Observability, normalize_trace_id
from app.observability.langfuse_client import _ZERO_TRACE_ID
from app.tracing import TRACE_ID_HEADER

# ------------------------------------------------------------- trace_id


def test_normalize_uuid_is_dash_stripped() -> None:
    """前端下发的是带横线的 UUID，去掉横线正好是 32 位十六进制。"""
    raw = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
    assert normalize_trace_id(raw) == "3f2504e04f8941d39a0c0305e82c3301"


def test_normalize_is_deterministic_for_arbitrary_input() -> None:
    """非 UUID 输入退化为 md5：同一输入永远同一 trace_id，便于按 ID 反查。"""
    first = normalize_trace_id("web-1728450000000-abcd1234")
    second = normalize_trace_id("web-1728450000000-abcd1234")
    assert first == second
    assert len(first) == 32


def test_normalize_never_returns_illegal_trace_id() -> None:
    """全 0 是非法 trace id，必须落到 md5 分支而不是原样返回。"""
    assert normalize_trace_id(_ZERO_TRACE_ID) != _ZERO_TRACE_ID
    # 空输入随机生成，不与其他任何输入冲突
    assert len(normalize_trace_id(None)) == 32
    assert len(normalize_trace_id("   ")) == 32


# --------------------------------------------------- 可降级（无 key）


def _unconfigured_settings() -> Settings:
    return Settings(langfuse_public_key=None, langfuse_secret_key=None)


def test_observability_degrades_when_keys_missing() -> None:
    observability = Observability(_unconfigured_settings())
    assert observability.enabled is False
    assert observability.auth_check() is False
    assert observability.trace_url("a" * 32) is None
    # flush / shutdown 在未启用时必须是安全 no-op
    observability.flush()
    observability.shutdown()


def test_half_configured_is_treated_as_unconfigured() -> None:
    """只给一个 key 通常是复制粘贴漏了，按未配置处理比「启用但报错」更安全。"""
    only_public = Settings(langfuse_public_key="pk-lf-x", langfuse_secret_key=None)
    only_secret = Settings(langfuse_public_key=None, langfuse_secret_key="sk-lf-x")
    assert only_public.langfuse_configured is False
    assert only_secret.langfuse_configured is False
    assert Observability(only_public).enabled is False
    assert Observability(only_secret).enabled is False


def test_span_and_generation_are_transparent_when_disabled() -> None:
    """未启用时两个上下文管理器都要能正常进出，yield None，绝不抛。"""
    observability = Observability(_unconfigured_settings())

    with observability.span("some-span", trace_id="a" * 32, input={"k": "v"}) as span:
        assert span is None
        with observability.generation("llm", model="m", input="p") as generation:
            assert generation is None


# ------------------------------------------------------------- LLM 客户端


async def test_stub_provider_marks_itself_as_stub() -> None:
    client = LlmClient(Settings(llm_provider="stub"))
    result = await client.complete("你好")

    assert result.is_stub is True
    assert "【stub】" in result.text
    assert result.usage_details["input"] > 0


async def test_unknown_provider_raises() -> None:
    client = LlmClient(Settings(llm_provider="definitely-not-a-provider"))
    with pytest.raises(LlmError, match="未知的 LLM_PROVIDER"):
        await client.complete("你好")


async def test_openai_compatible_without_key_raises() -> None:
    """选了真实 provider 却没给 key，要明确报错，而不是静默走 stub。"""
    client = LlmClient(Settings(llm_provider="openai-compatible", llm_api_key=None))
    with pytest.raises(LlmError, match="必须提供 LLM_API_KEY"):
        await client.complete("你好")


# ------------------------------------------------------------- 自检接口


async def test_status_endpoint_reports_unconfigured(client: AsyncClient) -> None:
    response = await client.get("/api/v1/observability/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["langfuse_configured"] is False
    # 未配置时是 null（没开），不是 false（开了但连不上）
    assert payload["langfuse_reachable"] is None
    assert payload["llm_is_stub"] is True


async def test_smoke_endpoint_runs_one_traced_call(client: AsyncClient) -> None:
    """stub provider + 未启用 Langfuse：接口仍要正常返回，只是没有 trace URL。"""
    response = await client.post(
        "/api/v1/observability/smoke",
        json={"prompt": "验证观测链路"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["is_stub"] is True
    assert len(payload["trace_id"]) == 32
    # trace_id 应与响应头里回写的 X-Request-Id 一致（去横线后）
    assert payload["request_trace_id"] == response.headers[TRACE_ID_HEADER]
    assert normalize_trace_id(payload["request_trace_id"]) == payload["trace_id"]
    assert payload["langfuse_enabled"] is False
    assert payload["langfuse_trace_url"] is None
    assert "验证观测链路" in payload["output"]


async def test_smoke_endpoint_rejects_empty_prompt(client: AsyncClient) -> None:
    response = await client.post("/api/v1/observability/smoke", json={"prompt": ""})
    assert response.status_code == 422
