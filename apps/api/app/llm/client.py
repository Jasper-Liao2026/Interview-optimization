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
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings
from app.llm.structured import extract_json, fixture_payload, json_instructions

logger = logging.getLogger("app.llm")

ModelT = TypeVar("ModelT", bound=BaseModel)

STUB_PROVIDER = "stub"
OPENAI_COMPATIBLE_PROVIDER = "openai-compatible"
KNOWN_PROVIDERS = (STUB_PROVIDER, OPENAI_COMPATIBLE_PROVIDER)


class LlmError(RuntimeError):
    """LLM 调用失败。观测层会把它记成 ERROR 级别的 generation，而不是静默吞掉。"""


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    """一次结构化调用的结果。`warnings` 记录降级与重试，向上透传到接口响应里。"""

    value: T
    llm: LlmResult
    warnings: list[str] = field(default_factory=list)


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

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_mode: bool = False,
        image_data_url: str | None = None,
    ) -> LlmResult:
        """单次补全。

        `json_mode=True` 时向 OpenAI 兼容端点声明 `response_format={"type": "json_object"}`，
        由**服务端**保证返回的是合法 JSON。这只解决「不是合法 JSON」，
        不解决「字段缺失/类型不对」—— 后者仍靠 `complete_json` 的 schema 校验 + 重试。
        两道防线各管一件事，不要用其中一个替代另一个。
        """
        if self.provider == STUB_PROVIDER:
            if image_data_url:
                raise LlmError("stub 不具备视觉识别能力，请配置支持视觉的模型")
            return self._stub_complete(prompt, system=system)
        if self.provider == OPENAI_COMPATIBLE_PROVIDER:
            return await self._openai_compatible_complete(
                prompt, system=system, json_mode=json_mode, image_data_url=image_data_url
            )
        raise LlmError(f"未知的 LLM_PROVIDER={self.provider!r}，可选：{', '.join(KNOWN_PROVIDERS)}")

    # ------------------------------------------------------- 结构化输出
    async def complete_json(
        self,
        prompt: str,
        schema: type[ModelT],
        *,
        system: str | None = None,
        max_retries: int = 2,
        image_data_url: str | None = None,
    ) -> StructuredResult[ModelT]:
        """要求模型返回 JSON，按 `schema` 校验；失败则带错误反馈重试。

        为什么需要重试而不是「一次不行就报错」：真实模型偶发返回带 markdown 围栏、
        或漏一个字段的输出，这类失败**重试一次基本就好**，属于可恢复抖动。
        把可恢复抖动和真正的失败分开处理，是让整条链路可用的关键。
        """
        warnings: list[str] = []

        if self.is_stub:
            if image_data_url:
                raise LlmError("stub 不具备视觉识别能力，请配置支持视觉的模型")
            # 无 key 环境：返回确定性桩，形状合法但内容显然是假的，用于打通工程链路
            stub_result = self._stub_complete(prompt, system=system)
            warnings.append(
                "LLM_PROVIDER=stub：返回的是确定性桩数据（字段值带【fixture】前缀），不是真实模型输出。"
                "要看真实效果请配置 LLM_PROVIDER=openai-compatible + LLM_API_KEY。"
            )
            return StructuredResult(
                value=schema.model_validate(fixture_payload(schema)),
                llm=stub_result,
                warnings=warnings,
            )

        instruction = json_instructions(schema)
        merged_system = f"{system}\n\n{instruction}" if system else instruction

        last_error = "未知错误"
        last_result: LlmResult | None = None

        for attempt in range(max_retries + 1):
            current_prompt = (
                prompt
                if attempt == 0
                else (
                    f"{prompt}\n\n---\n上一次的输出无法通过校验：{last_error}\n"
                    "请只重新输出一个符合 Schema 的 JSON 对象，不要解释。"
                )
            )
            kwargs: dict[str, Any] = {"system": merged_system}
            if image_data_url:
                kwargs["image_data_url"] = image_data_url
            last_result = await self.complete(current_prompt, **kwargs)
            try:
                parsed = schema.model_validate(extract_json(last_result.text))
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)[:400]
                logger.warning(
                    "structured output 第 %d 次校验失败 model=%s: %s",
                    attempt + 1,
                    schema.__name__,
                    last_error,
                )
                warnings.append(f"第 {attempt + 1} 次结构化输出校验失败，已重试")
                continue
            if attempt > 0:
                warnings.append(f"第 {attempt + 1} 次重试成功")
            return StructuredResult(value=parsed, llm=last_result, warnings=warnings)

        raise LlmError(
            f"结构化输出连续 {max_retries + 1} 次校验失败（{schema.__name__}）：{last_error}"
        )

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
    async def _openai_compatible_complete(
        self,
        prompt: str,
        *,
        system: str | None,
        json_mode: bool = False,
        image_data_url: str | None = None,
    ) -> LlmResult:
        settings = self.settings
        if not settings.llm_api_key:
            raise LlmError("LLM_PROVIDER=openai-compatible 时必须提供 LLM_API_KEY")

        url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        content: Any = prompt
        if image_data_url:
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ]
        messages.append({"role": "user", "content": content})

        payload: dict[str, object] = {
            "model": settings.llm_model,
            "messages": messages,
            "max_tokens": settings.llm_max_tokens,
            "temperature": 0,
        }
        if json_mode:
            # DeepSeek / OpenAI / Moonshot 等均支持；要求 prompt 里出现 "json" 字样，
            # 我们的 system 提示里已包含，因此两边是配合的。
            payload["response_format"] = {"type": "json_object"}
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
