"""单条经历改写（M1-4）。

**刻意先做串行**（task 里写死的）：先证明「一次改写能得到可用文本」，
再去谈并行。并行只是把 N 次串行同时发出去，改写质量本身与此无关 ——
先并行会把「质量不行」和「并发有问题」两类故障混在一起，难以定位。

fan-out / fan-in 是 M4-3 / M4-4 的活。
"""

from __future__ import annotations

from typing import Any

from app.agents.prompts import REWRITE_SYSTEM, build_rewrite_prompt
from app.llm import LlmClient, StructuredResult
from app.schemas import JobProfile, RewrittenExperience


class ExperienceRewriter:
    def __init__(self, llm: LlmClient, *, max_input_chars: int) -> None:
        self._llm = llm
        self._max_input_chars = max_input_chars
        # 上一次改写因超长被截掉的字符数（0 表示没截断）。供调用方记 warning。
        self.last_truncated_chars = 0

    async def rewrite(
        self, profile: JobProfile, experience: dict[str, Any]
    ) -> StructuredResult[RewrittenExperience]:
        raw_description = str(experience.get("raw_description") or "")
        self.last_truncated_chars = max(0, len(raw_description) - self._max_input_chars)

        prompt = build_rewrite_prompt(
            profile,
            kind=str(experience["kind"]),
            org=str(experience["org"]),
            role=str(experience["role"]),
            raw_description=raw_description,
            skill_tags=list(experience.get("skill_tags") or []),
            highlights=list(experience.get("highlights") or []),
            max_chars=self._max_input_chars,
        )
        return await self._llm.complete_json(
            prompt,
            RewrittenExperience,
            system=REWRITE_SYSTEM,
        )


def get_experience_rewriter(llm: LlmClient, *, max_input_chars: int) -> ExperienceRewriter:
    return ExperienceRewriter(llm, max_input_chars=max_input_chars)
