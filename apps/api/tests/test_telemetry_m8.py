"""Retry accounting, saved snapshots and SDK failure isolation."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from app.agents.jd_parser import JdParser
from app.config import Settings
from app.llm import LlmClient, LlmError
from app.observability import Observability
from app.observability.usage import summarize_calls, usage_scope
from tests.fakes import API


async def test_structured_retries_count_every_attempt_and_price(monkeypatch):
    settings = Settings(
        llm_provider="openai-compatible",
        llm_api_key="offline",
        llm_input_price_per_million_usd=1,
        llm_output_price_per_million_usd=2,
        prompt_version="m8.0",
    )
    calls = []
    original = httpx.AsyncClient
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        payload = (
            "invalid JSON"
            if attempts == 1
            else json.dumps(
                {
                    "title": "后端",
                    "required_skills": [],
                    "nice_to_have": [],
                    "keywords": [],
                    "responsibilities": [],
                    "implicit_preferences": [],
                }
            )
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": payload}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )

    async def sink(call):
        calls.append(call)

    with usage_scope(sink=sink, operation="jd_parse"):
        await JdParser(LlmClient(settings)).parse("招聘后端开发，熟悉 Python")
    usage = summarize_calls(calls)
    assert len(calls) == 2 and usage["total_tokens"] == 300
    assert usage["cost_usd"] == pytest.approx(0.0004)
    assert {c["prompt_version"] for c in calls} == {"m8.0"}
    assert all(len(c["prompt_hash"]) == 64 for c in calls)


async def test_failed_transport_is_unknown_usage_not_free(monkeypatch):
    settings = Settings(llm_provider="openai-compatible", llm_api_key="offline")
    llm = LlmClient(settings)
    calls = []

    async def fail(*args, **kwargs):
        raise LlmError("network failed")

    async def sink(call):
        calls.append(call)

    monkeypatch.setattr(llm, "_complete", fail)
    with usage_scope(sink=sink), pytest.raises(LlmError):
        await llm.complete("test")
    usage = summarize_calls(calls)
    assert usage["unknown_usage_calls"] == 1 and usage["cost_usd"] is None
    assert calls[0]["status"] == "failed"


async def test_generation_usage_replay_does_not_double_count(client, api_wiring):
    payload = {"jd_text": "招聘后端开发，熟悉 Python 和 FastAPI"}
    result = (await client.post(f"{API}/resumes/generate", json=payload)).json()
    usage = result["usage"]
    assert usage["is_stub"] and usage["cost_usd"] == 0
    assert usage["total_tokens"] > 0
    assert {c["operation"] for c in usage["calls"]} == {"jd_parse", "rewrite"}
    replay = (
        await client.post(
            f"{API}/resumes/generate",
            json={
                **payload,
                "run_id": result["run_id"],
            },
        )
    ).json()
    assert replay["usage"] == usage
    api_wiring["service"]._settings.prompt_version = "m8.0"
    recovered = (await client.post(f"{API}/resumes/runs/{result['run_id']}/resume")).json()
    assert recovered["usage"]["total_tokens"] == usage["total_tokens"]
    assert recovered["prompt_version"] == "m4.0"


@pytest.mark.parametrize("failure", ["enter", "update", "exit"])
def test_observability_sdk_errors_do_not_mask_business(failure):
    obs = Observability(Settings(langfuse_public_key=None, langfuse_secret_key=None))

    @contextmanager
    def scope(**kwargs):
        if failure == "enter":
            raise RuntimeError("sdk enter")

        def update(**kwargs):
            if failure == "update":
                raise RuntimeError("sdk update")

        try:
            yield SimpleNamespace(update=update, update_trace=update)
        finally:
            if failure == "exit":
                raise RuntimeError("sdk exit")

    obs._client = SimpleNamespace(start_as_current_observation=scope)
    with obs.span("test") as observation:
        if observation is not None:
            observation.update(output="business succeeded")
    with pytest.raises(ValueError, match="business failed"), obs.generation("test"):
        raise ValueError("business failed")
