"""JD 解析（M1-3）。

一次 LLM 调用，把 JD 原文变成 `JobProfile`。没有规则兜底、没有二次清洗：
如果输出不稳定，那说明 prompt 要改，而不是在代码里打补丁 —— 把问题留在可观测的地方。
"""

from __future__ import annotations

from app.agents.prompts import build_jd_prompt, get_prompt_snapshot, prompt_context
from app.llm import LlmClient, StructuredResult
from app.schemas import JobProfile
from app.schemas.jd import JdImageExtraction


class JdParser:
    def __init__(self, llm: LlmClient, *, prompt_version: str | None = None) -> None:
        self._llm = llm
        configured = getattr(getattr(llm, "settings", None), "prompt_version", None)
        self.prompt = get_prompt_snapshot(prompt_version or configured)

    async def parse(self, raw_text: str) -> StructuredResult[JobProfile]:
        with prompt_context(self.prompt):
            return await self._llm.complete_json(
                build_jd_prompt(raw_text, version=self.prompt.version),
                JobProfile,
                system=self.prompt.jd_system,
            )

    async def parse_image(self, image_data_url: str) -> StructuredResult[JdImageExtraction]:
        settings = self._llm.settings
        vision = LlmClient(
            settings.model_copy(
                update={
                    "llm_provider": settings.vision_provider or settings.llm_provider,
                    "llm_base_url": settings.vision_base_url or settings.llm_base_url,
                    "llm_api_key": settings.vision_api_key or settings.llm_api_key,
                    "llm_model": settings.vision_model or settings.llm_model,
                }
            )
        )
        with prompt_context(self.prompt):
            return await vision.complete_json(
                self.prompt.jd_image,
                JdImageExtraction,
                system=self.prompt.jd_system,
                image_data_url=image_data_url,
            )


def get_jd_parser(llm: LlmClient, *, prompt_version: str | None = None) -> JdParser:
    return JdParser(llm, prompt_version=prompt_version)
