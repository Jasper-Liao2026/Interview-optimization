"""Golden cases are explicit synthetic fixtures, never production user data."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.schemas import JobProfile


@dataclass(frozen=True, slots=True)
class GoldenCase:
    case_id: str
    jd_text: str
    expected_profile: JobProfile
    source_experience: dict[str, Any]
    expected_bullets: tuple[str, ...]
    synthetic: bool = True


def load_golden_dataset(path: str | Path | None = None) -> list[GoldenCase]:
    path = Path(path) if path else Path(__file__).with_name("golden_dataset.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not 20 <= len(payload) <= 50:
        raise ValueError("M8 golden dataset 必须包含 20-50 条记录")
    cases: list[GoldenCase] = []
    for item in payload:
        if item.get("synthetic") is not True:
            raise ValueError("golden dataset 只允许明确标记 synthetic=true 的合成数据")
        profile = JobProfile.model_validate(item["expected_profile"])
        cases.append(
            GoldenCase(
                case_id=str(item["case_id"]),
                jd_text=str(item["jd_text"]),
                expected_profile=profile,
                source_experience=dict(item["source_experience"]),
                expected_bullets=tuple(str(x) for x in item.get("expected_bullets", [])),
            )
        )
    ids = [case.case_id for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("golden dataset case_id 必须唯一")
    return cases
