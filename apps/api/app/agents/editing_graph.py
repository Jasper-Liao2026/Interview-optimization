"""Local rewrite followed by a durable LangGraph human confirmation interrupt."""

import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.rewriter import ExperienceRewriter
from app.schemas import JobProfile


class EditState(TypedDict, total=False):
    source: dict
    profile: dict
    instruction: str
    original_bullets: list[dict]
    proposed_bullets: list[dict]
    warnings: list[str]
    accepted: bool


def editing_graph(rewriter: ExperienceRewriter, checkpointer: Any):
    async def propose(state: EditState) -> dict:
        outcome = await rewriter.rewrite(
            JobProfile.model_validate(state["profile"]),
            state["source"],
            feedback=json.dumps(
                {"current_bullets": state["original_bullets"], "instruction": state["instruction"]},
                ensure_ascii=False,
            ),
        )
        return {
            "proposed_bullets": [
                bullet.model_dump(mode="json") for bullet in outcome.value.bullets
            ],
            "warnings": outcome.warnings,
        }

    def confirm(state: EditState) -> dict:
        accepted = interrupt(
            {"original": state["original_bullets"], "proposed": state["proposed_bullets"]}
        )
        return {"accepted": accepted is True}

    graph = StateGraph(EditState)
    graph.add_node("propose", propose)
    graph.add_node("confirm", confirm)
    graph.add_edge(START, "propose")
    graph.add_edge("propose", "confirm")
    graph.add_edge("confirm", END)
    return graph.compile(checkpointer=checkpointer)
