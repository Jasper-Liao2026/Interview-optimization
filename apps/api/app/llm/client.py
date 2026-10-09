"""最小 LLM 调用层（M0-8）。

M0 阶段这里只需证明「一次 LLM 调用能被观测到」，所以刻意做得很薄：

- **OpenAI 兼容协议 + httpx 手写请求**，不引入任何厂商专有 SDK。
  DeepSeek / Moonshot / 通义 / vLLM / Ollama 都只是换个 `LLM_BASE_URL`。
- **provider=stub 是明确标注的假实现**：在没有 API key 的环境里，
  也能把「trace → generation → usage」这条观测链路跑通并留下证据。
  它绝不伪装成真实调用（返回值带 `【stub】` 前缀，`is_stub=True`）。

M4 起，LangGraph 的节点会调用同一个 `LlmClient`，观测代码不必重写。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger("app.llm")

STUB_PROVIDER = "stub"
OPENAI_COMPATIBLE_PROVIDER = "openai-compatible"
KNOWN_PROVIDERS = (STUB_PROVIDER, OPENAI_COMPATIBLE_PROVIDER)


class LlmError(RuntimeError):
    """LLM 调用失败。观测层会把它记成 ERROR 级别的 generation，而不是静默吞掉。"""


@dataclass(frozen=True, slots=True)
class LlmResult:
    provider: str
    model: str
    text: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float
    is_stub: bool

    @property
    def usage_details(self) -> dict[str, int]:
        """转成 Langfuse 的 usage_details 形状（只放有值的项）。"""
        details: dict[str, int] = {}
        if self.input_tokens is not None:
            details["input"] = self.input_tokens
        if self.output_tokens is not None:
            details["output"] = self.output_tokens
        if self.input_tokens is not None and self.output_tokens is not None:
            details["total"] = self.input_tokens + self.output_tokens
        return details


class LlmClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def provider(self) -> str:
        return self.settings.llm_provider

    @property
    def model(self) -> str:
        return self.settings.llm_model

    @property
    def is_stub(self) -> bool:
        return self.provider == STUB_PROVIDER

    async def complete(self, prompt: str, *, system: str | None = None) -> LlmResult:
        if self.provider == STUB_PROVIDER:
            return self._stub_complete(prompt, system=system)
        if self.provider == OPENAI_COMPATIBLE_PROVIDER:
            return await self._openai_compatible_complete(prompt, system=system)
        raise LlmError(f"未知的 LLM_PROVIDER={self.provider!r}，可选：{', '.join(KNOWN_PROVIDERS)}")

    # ------------------------------------------------------------- stub
    def _stub_complete(self, prompt: str, *, system: str | None = None) -> LlmResult:
        """确定性假实现。存在的唯一目的是在无 key 环境下验证观测链路。"""
        started = time.perf_counter()
        excerpt = prompt.strip().replace("\n", " ")[:60]
        text = (
            "【stub】未发起真实网络调用——当前 LLM_PROVIDER=stub，"
            f"仅用于验证 Langfuse 观测链路。收到 prompt（{len(prompt)} 字）：{excerpt}"
        )
        if system:
            text += f"（system 前缀：{system.strip()[:20]}）"
        return LlmResult(
            provider=STUB_PROVIDER,
            model=f"{self.model}(stub)",
            text=text,
            # 粗略估算（4 字符 ≈ 1 token）。下限取 1，避免短 prompt 算出 0
            # 而让「usage 为空」看起来像没采集到数据。
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, len(text) // 4),
            latency_ms=(time.perf_counter() - started) * 1000,
            is_stub=True,
        )

    # -------------------------------------------------- openai compatible
    async def _openai_compatible_complete(self, prompt: str, *, system: str | None) -> LlmResult:
        settings = self.settings
        if not settings.llm_api_key:
            raise LlmError("LLM_PROVIDER=openai-compatible 时必须提供 LLM_API_KEY")

        url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, object] = {
            "model": settings.llm_model,
            "messages": messages,
            "max_tokens": settings.llm_max_tokens,
            "temperature": 0,
        }
        headers = {
            "Authorization": f"Bearer {settings.llm_api_key}",
            "Content-Type": "application/json",
        }

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_s) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300]
            raise LlmError(f"LLM 返回 {exc.response.status_code}：{body}") from exc
        except httpx.HTTPError as exc:
            raise LlmError(f"LLM 请求失败：{exc}") from exc

        latency_ms = (time.perf_counter() - started) * 1000
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError(f"LLM 响应结构不符合 OpenAI 兼容约定：{str(data)[:300]}") from exc

        usage = data.get("usage") or {}
        logger.info(
            "llm_complete provider=%s model=%s latency_ms=%.1f",
            OPENAI_COMPATIBLE_PROVIDER,
            settings.llm_model,
            latency_ms,
        )
        return LlmResult(
            provider=OPENAI_COMPATIBLE_PROVIDER,
            model=data.get("model") or settings.llm_model,
            text=text,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_ms=latency_ms,
            is_stub=False,
        )


def get_llm_client() -> LlmClient:
    return LlmClient(get_settings())
