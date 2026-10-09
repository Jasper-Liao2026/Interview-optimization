"""JD 解析（M1-3）。

一次 LLM 调用，把 JD 原文变成 `JobProfile`。没有规则兜底、没有二次清洗：
如果输出不稳定，那说明 prompt 要改，而不是在代码里打补丁 —— 把问题留在可观测的地方。
"""

from __future__ import annotations

from app.agents.prompts import JD_PARSE_SYSTEM, build_jd_prompt
from app.llm import LlmClient, StructuredResult
from app.schemas import JobProfile


class JdParser:
    def __init__(self, llm: LlmClient) -> None:
        self._llm = llm

    async def parse(self, raw_text: str) -> StructuredResult[JobProfile]:
        return await self._llm.complete_json(
            build_jd_prompt(raw_text),
            JobProfile,
            system=JD_PARSE_SYSTEM,
        )


def get_jd_parser(llm: LlmClient) -> JdParser:
    return JdParser(llm)
