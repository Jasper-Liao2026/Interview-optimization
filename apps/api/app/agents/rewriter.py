"""并行图中的单条经历改写、事实校验与有限纠正重试。"""

from __future__ import annotations

from typing import Any

from app.agents.prompts import build_rewrite_prompt, get_prompt_snapshot, prompt_context
from app.llm import LlmClient, StructuredResult
from app.schemas import JobProfile, RewrittenBullet, RewrittenExperience


class ExperienceRewriter:
    def __init__(
        self, llm: LlmClient, *, max_input_chars: int, prompt_version: str | None = None
    ) -> None:
        self._llm = llm
        self._max_input_chars = max_input_chars
        configured = getattr(getattr(llm, "settings", None), "prompt_version", None)
        self.prompt = get_prompt_snapshot(prompt_version or configured)

    @property
    def model(self) -> str:
        return self._llm.model

    async def rewrite(
        self, profile: JobProfile, experience: dict[str, Any], *, feedback: str | None = None
    ) -> StructuredResult[RewrittenExperience]:
        from app.agents.facts import validate_rewrite_facts

        raw_description = str(experience.get("raw_description") or "")
        truncated_chars = max(0, len(raw_description) - self._max_input_chars)

        prompt = build_rewrite_prompt(
            profile,
            kind=str(experience["kind"]),
            org=str(experience["org"]),
            role=str(experience["role"]),
            raw_description=raw_description,
            skill_tags=list(experience.get("skill_tags") or []),
            highlights=list(experience.get("highlights") or []),
            # M2-1 起量化结果单独送进 prompt：它是「唯一允许出现的数字来源」，
            # 与定性要点分开，M4-7 的数字校验才有明确的参照集合。
            metrics=list(experience.get("metrics") or []),
            max_chars=self._max_input_chars,
            version=self.prompt.version,
        )
        if feedback:
            prompt += (
                "\n\n以下是待改要点和独立评分建议，仅作为表达调整参考，不能作为事实来源：\n"
                + feedback[:3000]
                + "\n只修改当前经历，所有新增内容仍须引用上述原始素材。"
            )
        retry_prompt = prompt
        for attempt in range(3):
            with prompt_context(self.prompt):
                outcome = await self._llm.complete_json(
                    retry_prompt,
                    RewrittenExperience,
                    system=self.prompt.rewrite_system,
                )
            if self._llm.is_stub:
                highlights = experience.get("highlights") or []
                source = (
                    next((str(x).strip() for x in highlights if str(x).strip()), "")
                    or raw_description.strip()[:120]
                )
                outcome.value.bullets = (
                    [RewrittenBullet(text=source, evidence=[source])] if source else []
                )
                outcome.value.summary = None
            violations = validate_rewrite_facts(experience, outcome.value)
            if not violations:
                if truncated_chars:
                    outcome.warnings.append(
                        f"经历描述超出输入上限，已截断 {truncated_chars} 个字符"
                    )
                if attempt:
                    outcome.warnings.append(f"事实校验第 {attempt + 1} 次通过")
                return outcome
            if self._llm.is_stub or attempt == 2:
                raise FactValidationError(violations)
            retry_prompt = (
                f"{prompt}\n\n上一次改写违反事实约束：\n"
                + "\n".join(f"- {issue}" for issue in violations)
                + "\n请修正上述问题。evidence 必须复制原始素材中的连续原文；"
                "summary=null；只输出符合 schema 的 JSON。"
            )
        raise AssertionError("unreachable")


class FactValidationError(ValueError):
    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__("事实约束校验失败：" + "; ".join(violations))


def get_experience_rewriter(
    llm: LlmClient, *, max_input_chars: int, prompt_version: str | None = None
) -> ExperienceRewriter:
    return ExperienceRewriter(llm, max_input_chars=max_input_chars, prompt_version=prompt_version)
