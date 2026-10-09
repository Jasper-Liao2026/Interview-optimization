"""结构化输出工具：JSON 抽取 + 确定性桩数据。

**为什么要单独一层**：让模型返回 JSON 是「调 API」和「做工程」的分界线。
真实模型会返回 ```json 围栏、会多说一句话、会漏字段。把这些脏活收在一个文件里，
业务代码就只面对「一个校验通过的 Pydantic 对象」。

**关于 `fixture_payload`**：本机没有真实 LLM key 时，整条垂直切片（JD → 改写 → HTML → PDF）
仍然要被验证 —— 因为 M1 最大的技术风险是 PDF 链路，而不是模型输出。
因此给 stub provider 配了一个**确定性桩**：按 schema 生成形状合法、内容显然为假的占位数据。
它绝不伪装成真实输出（所有字符串都带 `【fixture】` 前缀），只用于打通工程链路。
"""

from __future__ import annotations

import json
import re
import types
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

_FENCE_OPEN = re.compile(r"^```[a-zA-Z0-9_-]*\s*")
_FENCE_CLOSE = re.compile(r"\s*```$")


def extract_json(text: str) -> Any:
    """从模型返回的文本里抠出 JSON 对象。

    依次尝试：直接用 → 去掉 markdown 围栏 → 截取第一个 `{` 到最后一个 `}`。
    三种都失败才抛错。
    """
    cleaned = text.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    if cleaned.startswith("```"):
        unfenced = _FENCE_CLOSE.sub("", _FENCE_OPEN.sub("", cleaned))
        try:
            return json.loads(unfenced)
        except json.JSONDecodeError:
            cleaned = unfenced

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"截取出的片段仍不是合法 JSON：{exc}") from exc

    raise ValueError("响应里找不到 JSON 对象")


def json_instructions(schema: type[BaseModel]) -> str:
    """拼给模型的输出约束说明。

    刻意**内联完整 JSON Schema**，而不是只说「按这个结构返回」：
    字段名、类型、哪些必填都摆出来，模型漏字段的概率显著下降。
    """
    schema_text = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
    return (
        "只输出一个 JSON 对象，不要任何解释文字，不要 markdown 代码围栏。\n"
        "输出必须满足下面的 JSON Schema：\n"
        f"{schema_text}"
    )


# --------------------------------------------------------------- 桩数据
def _unwrap_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        return args[0] if args else str
    return annotation


def _fixture_value(field_name: str, annotation: Any) -> Any:
    annotation = _unwrap_optional(annotation)
    origin = get_origin(annotation)

    if origin is Literal:
        args = get_args(annotation)
        return args[0] if args else None

    if origin in (list, tuple, set, frozenset):
        args = get_args(annotation)
        inner = args[0] if args else str
        return [_fixture_value(field_name, inner)]

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return fixture_payload(annotation)

    if annotation is bool:
        return False
    if annotation is int:
        return 0
    if annotation is float:
        return 0.0
    return f"【fixture】{field_name}"


def fixture_payload(schema: type[BaseModel]) -> dict[str, Any]:
    """按 schema 造一个必然通过校验的占位对象。"""
    return {
        name: _fixture_value(name, field.annotation) for name, field in schema.model_fields.items()
    }
