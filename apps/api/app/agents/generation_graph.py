"""LangGraph fan-out/fan-in with isolated outcomes and durable pending writes."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.agents.assembler import assemble_sections
from app.agents.rewriter import ExperienceRewriter
from app.observability import Observability
from app.schemas import JobProfile, RewrittenExperience


def merge_items(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {**left, **right}


class GenerationGraphState(TypedDict, total=False):
    profile: dict[str, Any]
    experiences: list[dict[str, Any]]
    selected: list[int]
    items: Annotated[dict[str, Any], merge_items]
    sections: list[dict[str, Any]]


class GenerationGraph:
    def __init__(
        self,
        rewriter: ExperienceRewriter,
        *,
        checkpointer: Any,
        observability: Observability,
        max_concurrency: int = 4,
    ) -> None:
        self.rewriter = rewriter
        self.observability = observability
        self.max_concurrency = max_concurrency
        builder = StateGraph(GenerationGraphState)
        builder.add_node("fan_out", lambda state: {})
        builder.add_node("rewrite_item", self._rewrite_item)
        builder.add_node("fan_in", self._fan_in)
        builder.add_edge(START, "fan_out")
        builder.add_conditional_edges("fan_out", self._dispatch, ["rewrite_item", "fan_in"])
        builder.add_edge("rewrite_item", "fan_in")
        builder.add_edge("fan_in", END)
        self.graph = builder.compile(checkpointer=checkpointer)

    def config(self, run_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": run_id}, "max_concurrency": self.max_concurrency}

    def _dispatch(self, state: GenerationGraphState) -> list[Send] | str:
        selected = state.get("selected", list(range(len(state["experiences"]))))
        if not selected:
            return "fan_in"
        return [
            Send(
                "rewrite_item",
                {
                    "profile": state["profile"],
                    "experience": state["experiences"][i],
                    "index": i,
                },
            )
            for i in selected
        ]

    async def _rewrite_item(self, state: dict[str, Any]) -> dict[str, Any]:
        experience, index = state["experience"], state["index"]
        item = {"index": index, "experience_id": experience["id"], "org": experience["org"]}
        with self.observability.generation(
            "m4.rewrite",
            model=self.rewriter.model,
            input={"experience_id": experience["id"], "org": experience["org"]},
        ) as observation:
            try:
                outcome = await self.rewriter.rewrite(
                    JobProfile.model_validate(state["profile"]), experience
                )
                item.update(
                    status="succeeded",
                    result=outcome.value.model_dump(mode="json"),
                    warnings=outcome.warnings,
                )
                if observation is not None:
                    observation.update(
                        output=item["result"], usage_details=outcome.llm.usage_details
                    )
            except Exception as exc:
                # Cancellation propagates and leaves resumable tasks; item failures
                # are data so other tasks can finish and checkpoint their writes.
                item.update(
                    status="failed",
                    error=str(exc),
                    details=getattr(exc, "violations", []),
                    warnings=[],
                )
                if observation is not None:
                    observation.update(level="ERROR", status_message=str(exc))
        return {"items": {str(index): item}}

    async def _fan_in(self, state: GenerationGraphState) -> dict[str, Any]:
        experiences = []
        for row in state["experiences"]:
            source = dict(row)
            for key in ("start_date", "end_date"):
                if isinstance(source.get(key), str):
                    source[key] = date.fromisoformat(source[key])
            experiences.append(source)
        pairs = [
            (experiences[i], RewrittenExperience.model_validate(item["result"]))
            for i in range(len(state["experiences"]))
            if (item := state.get("items", {}).get(str(i))) and item["status"] == "succeeded"
        ]
        return {"sections": [s.model_dump(mode="json") for s in assemble_sections(pairs)]}
