"""Full bundle identity, historical hash compatibility and request replay."""

import json
from dataclasses import replace

import httpx
import pytest

from app.agents import prompts
from app.agents.jd_parser import JdParser
from app.config import Settings
from app.llm import LlmClient
from app.llm.structured import fixture_payload, json_instructions, render_json_instructions
from app.schemas import JobProfile


def test_legacy_hashes_remain_compatible_with_persisted_versions():
    assert prompts.get_prompt_snapshot("m4.0").content_hash == (
        "79cbd3c2e73e257cc3fdc6b03e39278cf7ef9a1c3d0f682fdb43b956ba2c0ca4"
    )
    assert prompts.get_prompt_snapshot("m8.0").content_hash == (
        "f060df0e44e7d7913e824d629bc5af6d302cb3f7060002b3add7a0f79a8e6022"
    )
    assert not prompts.get_prompt_snapshot("m4.1").legacy_hash
    assert not prompts.get_prompt_snapshot("m8.1").legacy_hash


@pytest.mark.parametrize(
    "field",
    ["jd_user", "rewrite_user", "jd_system", "rewrite_system", "jd_image",
     "rewrite_truncated", "image_supplement", "rewrite_feedback", "rewrite_retry",
     "structured_retry", "empty_material", "bullet_prefix", "bullet_separator",
     "metric_label", "metric_context"],
)
def test_full_bundle_hash_changes_for_every_instruction_template(field):
    original = prompts.get_prompt_snapshot("m8.1")
    changed = replace(original, **{field: getattr(original, field) + " changed"})
    assert changed.content_hash != original.content_hash
    assert replace(original, version="same-content").content_hash == original.content_hash


def test_full_bundle_hash_covers_output_schema_and_profile_labels():
    original = prompts.get_prompt_snapshot("m8.1")
    changed = list(original.schema_instructions)
    name, instruction = changed[0]
    changed[0] = (name, instruction + " schema changed")
    assert (
        replace(original, schema_instructions=tuple(changed)).content_hash
        != original.content_hash
    )
    assert (
        replace(original, profile_fields=(("岗位名", "title"),)).content_hash
        != original.content_hash
    )


def test_schema_instruction_is_frozen_against_live_schema_changes(monkeypatch):
    snapshot = prompts.get_prompt_snapshot("m8.1")
    original = snapshot.schema_instruction(JobProfile)
    monkeypatch.setattr(JobProfile, "model_json_schema", lambda: {"changed": True})
    assert render_json_instructions(JobProfile) != original
    with prompts.prompt_context(snapshot):
        assert json_instructions(JobProfile) == original
    assert json_instructions(JobProfile) != original


def test_newly_registered_bundle_hash_detects_schema_definition_changes(monkeypatch):
    original = prompts.get_prompt_snapshot("m8.1")
    schema = JobProfile.model_json_schema()
    schema["properties"]["required_skills"]["description"] = "changed output contract"
    monkeypatch.setattr(JobProfile, "model_json_schema", lambda: schema)
    monkeypatch.setattr(prompts, "_PROMPT_VERSIONS", {})
    changed = prompts.get_prompt_snapshot("m8.1")
    assert changed.content_hash != original.content_hash
    assert "changed output contract" in changed.schema_instruction(JobProfile)


async def test_actual_provider_request_uses_hashed_snapshot_templates_and_schema(monkeypatch):
    original = prompts.get_prompt_snapshot("m8.1")
    changed = replace(
        original,
        jd_user="frozen input {raw_text}",
        schema_instructions=tuple(
            (name, instruction + "\nfrozen schema instruction")
            for name, instruction in original.schema_instructions
        ),
    )
    monkeypatch.setitem(prompts._PROMPT_VERSIONS, "m8.1", changed)
    requests = []
    original_client = httpx.AsyncClient

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(fixture_payload(JobProfile))}}],
        })

    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    parser = JdParser(LlmClient(Settings(
        _env_file=None, llm_provider="openai-compatible", llm_api_key="offline",
        prompt_version="m8.1",
    )))
    await parser.parse("招聘后端开发，熟悉 Python")
    assert requests[0]["messages"] == [
        {
            "role": "system",
            "content": changed.jd_system + "\n\n" + changed.schema_instruction(JobProfile),
        },
        {"role": "user", "content": "frozen input 招聘后端开发，熟悉 Python"},
    ]
    assert changed.content_hash != original.content_hash
