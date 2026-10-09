"""结构化输出工具（M1-3 / M1-4 的地基）。

这一层是「调 API」与「做工程」的分界线：真实模型会返回 ```json 围栏、会在 JSON 前后
多说一句话、会漏字段。这些脏活必须有确定性覆盖，否则它会以「线上偶发解析失败」的形式回来。
"""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel

from app.llm.structured import extract_json, fixture_payload, json_instructions


class Inner(BaseModel):
    label: str


class Sample(BaseModel):
    name: str
    count: int
    ratio: float
    ok: bool
    tags: list[str]
    kind: Literal["a", "b"]
    inner: Inner


# --------------------------------------------------------------- extract_json
def test_extract_json_plain() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_strips_json_fence() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_strips_bare_fence() -> None:
    assert extract_json('```\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_survives_surrounding_prose() -> None:
    """模型最常见的「多说一句话」。"""
    text = '好的，解析结果如下：\n{"a": 1}\n如果需要调整请告诉我。'
    assert extract_json(text) == {"a": 1}


def test_extract_json_survives_fence_plus_prose() -> None:
    text = '好的：\n```json\n{"a": 1}\n```\n以上。'
    assert extract_json(text) == {"a": 1}


def test_extract_json_no_object_raises() -> None:
    with pytest.raises(ValueError, match="找不到 JSON 对象"):
        extract_json("这里一个对象都没有")


def test_extract_json_broken_fragment_raises() -> None:
    """抠出来的片段仍不合法时要明确抛错，而不是返回一个半成品。"""
    with pytest.raises(ValueError, match="仍不是合法 JSON"):
        extract_json("{这不是 json}")


# ------------------------------------------------------------ json_instructions
def test_json_instructions_inlines_schema() -> None:
    """刻意内联完整 schema：字段名与类型都摆出来，模型漏字段的概率显著下降。"""
    text = json_instructions(Sample)
    assert "只输出一个 JSON 对象" in text
    for field in ("name", "tags", "inner", "kind"):
        assert field in text


# -------------------------------------------------------------- fixture_payload
def test_fixture_payload_passes_its_own_schema() -> None:
    """桩数据的唯一硬指标：必然通过校验，否则「无 key 也能跑通链路」就是空话。"""
    payload = fixture_payload(Sample)
    obj = Sample.model_validate(payload)

    assert obj.name.startswith("【fixture】")
    assert obj.count == 0
    assert obj.ratio == 0.0
    assert obj.ok is False
    assert obj.kind == "a"
    assert obj.tags == ["【fixture】tags"]
    assert obj.inner.label == "【fixture】label"


def test_fixture_payload_marks_every_string_as_fake() -> None:
    """桩数据绝不伪装成真实输出 —— 自由文本一律带标记。

    唯一的例外是 `Literal` 字段：它只能取枚举里的真实值，加了前缀就不合法了。
    """
    payload = fixture_payload(Sample)

    def walk(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [s for v in value.values() for s in walk(v)]
        if isinstance(value, list):
            return [s for v in value for s in walk(v)]
        return []

    strings = walk(payload)
    assert strings

    marked = [item for item in strings if item != "a"]  # "a" 来自 Literal 字段
    assert marked
    assert all(item.startswith("【fixture】") for item in marked)
