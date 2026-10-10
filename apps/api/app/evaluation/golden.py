"""Compatibility names for the single versioned golden JSON dataset."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.evaluation.dataset import load_golden_dataset


@dataclass(frozen=True, slots=True)
class GoldenCase:
    id: str
    raw_jd: str
    expected_profile: dict[str, Any]
    source: dict[str, Any]
    expected_bullets: tuple[str, ...]
    synthetic: bool = True

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


def golden_set() -> tuple[GoldenCase, ...]:
    return tuple(
        GoldenCase(
            case.case_id,
            case.jd_text,
            case.expected_profile.model_dump(mode="json"),
            {**case.source_experience, "id": case.case_id},
            case.expected_bullets,
        )
        for case in load_golden_dataset()
    )


def golden_set_json() -> list[dict[str, Any]]:
    return [case.model_dump() for case in golden_set()]
