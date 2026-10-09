"""LLM 调用层。对外只暴露 `LlmClient` 与 `LlmResult`。"""

from app.llm.client import LlmClient, LlmError, LlmResult, get_llm_client

__all__ = ["LlmClient", "LlmError", "LlmResult", "get_llm_client"]
