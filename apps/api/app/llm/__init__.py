"""LLM 调用层。对外只暴露 `LlmClient` / `LlmResult` / `StructuredResult`。"""

from app.llm.client import (
    KNOWN_PROVIDERS,
    OPENAI_COMPATIBLE_PROVIDER,
    STUB_PROVIDER,
    LlmClient,
    LlmError,
    LlmResult,
    StructuredResult,
    get_llm_client,
)
from app.llm.structured import extract_json, fixture_payload, json_instructions

__all__ = [
    "KNOWN_PROVIDERS",
    "OPENAI_COMPATIBLE_PROVIDER",
    "STUB_PROVIDER",
    "LlmClient",
    "LlmError",
    "LlmResult",
    "StructuredResult",
    "extract_json",
    "fixture_payload",
    "get_llm_client",
    "json_instructions",
]
