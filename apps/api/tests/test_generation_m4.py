"""M4 fan-out, failure isolation and targeted retry through the public API."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.generation_graph import GenerationGraph
from app.config import Settings
from app.llm import LlmClient, LlmError
from app.observability import Observability
from app.schemas import JobProfile
from tests.fakes import API, experience_row


async def test_parallel_failure_is_isolated_and_retry_preserves_siblings(client, api_wiring):
    first = experience_row(org="first")
    second = experience_row(org="second")
    third = experience_row(org="third")
    api_wiring["experiences"].rows = [first, second, third]
    original = api_wiring["service"]._rewriter
    active = 0
    peak = 0
    calls = {row["org"]: 0 for row in (first, second, third)}

    class FlakyRewriter:
        model = original.model

        async def rewrite(self, profile, row):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            calls[row["org"]] += 1
            try:
                await asyncio.sleep(0.01)
                if row["org"] == "second" and calls["second"] == 1:
                    raise RuntimeError("temporary model failure")
                return await original.rewrite(profile, row)
            finally:
                active -= 1

    api_wiring["service"]._rewriter = FlakyRewriter()
    run_id = uuid4()
    payload = {
        "run_id": str(run_id),
        "jd_text": "招聘后端工程师，熟悉 Python 开发。",
        "experience_ids": [str(row["id"]) for row in (first, second, third)],
    }
    generated = (await client.post(f"{API}/resumes/generate", json=payload)).json()
    assert peak > 1
    assert generated["checkpoint"]["status"] == "partial"
    assert [item["status"] for item in generated["items"]] == ["succeeded", "failed", "succeeded"]
    assert len(generated["resume"]["sections"][0]["entries"]) == 2
    assert generated["failures"][0]["index"] == 1

    retry = await client.post(f"{API}/resumes/runs/{run_id}/retry/1")
    assert retry.status_code == 200
    body = retry.json()
    assert body["checkpoint"]["status"] == "completed"
    assert not body["failures"]
    assert [entry["org"] for entry in body["resume"]["sections"][0]["entries"]] == [
        "first",
        "second",
        "third",
    ]
    assert body["resume"]["id"] == generated["resume"]["id"]
    assert calls == {"first": 1, "second": 2, "third": 1}

    read = await client.get(f"{API}/resumes/runs/{run_id}")
    assert read.status_code == 200
    assert read.json()["resume"]["id"] == body["resume"]["id"]
    assert (await client.post(f"{API}/resumes/runs/{run_id}/retry/1")).status_code == 409
    assert (
        await client.post(f"{API}/resumes/generate", json={**payload, "title": "different"})
    ).status_code == 409


async def test_missing_selected_experience_rejected(client, api_wiring):
    response = await client.post(
        f"{API}/resumes/generate",
        json={"jd_text": "招聘后端工程师，熟悉 Python 开发。", "experience_ids": [str(uuid4())]},
    )
    assert response.status_code == 400


@pytest.mark.parametrize("action", ["resume", "retry/0"])
@pytest.mark.parametrize("failure", ["missing_jd", "parser_unavailable"])
async def test_recovery_preparation_errors_are_mapped(client, api_wiring, action, failure):
    run_id = uuid4()
    payload = {"run_id": str(run_id)}
    if failure == "missing_jd":
        payload["jd_id"] = str(uuid4())
        expected_status = 400
        expected_detail = "JD 不存在"
    else:
        payload["jd_text"] = "招聘后端工程师，熟悉 Python 开发。"

        class UnavailableParser:
            async def parse(self, text):
                raise LlmError("provider unavailable")

        api_wiring["service"]._parser = UnavailableParser()
        expected_status = 502
        expected_detail = "provider unavailable"

    initial = await client.post(f"{API}/resumes/generate", json=payload)
    assert initial.status_code == expected_status
    recovered = await client.post(f"{API}/resumes/runs/{run_id}/{action}")
    assert recovered.status_code == expected_status
    assert expected_detail in recovered.json()["detail"]


async def test_graph_checkpoint_resumes_completed_sibling_after_interruption():
    settings = Settings(_env_file=None)
    llm = LlmClient(settings)
    from app.agents.rewriter import ExperienceRewriter

    original = ExperienceRewriter(llm, max_input_chars=settings.rewrite_max_input_chars)
    waiting = asyncio.Event()
    release = asyncio.Event()
    calls = {"first": 0, "second": 0}

    class InterruptedRewriter:
        model = original.model

        async def rewrite(self, profile, row):
            calls[row["org"]] += 1
            if row["org"] == "second" and calls["second"] == 1:
                waiting.set()
                await release.wait()
            return await original.rewrite(profile, row)

    saver = InMemorySaver()
    obs = Observability(settings)
    graph = GenerationGraph(InterruptedRewriter(), checkpointer=saver, observability=obs)
    config = graph.config(str(uuid4()))
    profile = JobProfile(
        title="后端开发",
        required_skills=[],
        nice_to_have=[],
        keywords=[],
        implicit_preferences=[],
        responsibilities=[],
    )
    rows = [experience_row(org="first"), experience_row(org="second")]
    for row in rows:
        row["id"] = str(row["id"])
        row["start_date"] = row["start_date"].isoformat()
    task = asyncio.create_task(
        graph.graph.ainvoke(
            {
                "profile": profile.model_dump(mode="json"),
                "experiences": rows,
                "selected": [0, 1],
                "items": {},
            },
            config=config,
            durability="sync",
        )
    )
    await waiting.wait()
    await asyncio.sleep(0.02)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    restarted = GenerationGraph(InterruptedRewriter(), checkpointer=saver, observability=obs)
    completed = await restarted.graph.ainvoke(None, config=config, durability="sync")
    assert [entry["org"] for entry in completed["sections"][0]["entries"]] == ["first", "second"]
    assert calls == {"first": 1, "second": 2}
