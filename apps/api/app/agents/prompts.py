"""Prompt 集中管理。

**为什么不把 prompt 写在调用处**：改 prompt 等于改业务逻辑。集中放一个文件，
才能一眼看出「这次输出变差是不是哪个 prompt 被改了」，也是 M8-4 prompt 版本管理的地基。

`PROMPT_VERSION` 会写进 Langfuse 的 metadata，于是线上每条 trace 都能对上是哪版 prompt 产的。
"""

from __future__ import annotations

import json
from typing import Any

from app.schemas import JobProfile

PROMPT_VERSION = "m2.0"

# ============================================================ JD 解析（M1-3）
JD_PARSE_SYSTEM = (
    "你是一名资深技术招聘顾问。你的任务是把一段招聘 JD 原文拆解成结构化的岗位画像。\n"
    "\n"
    "要求：\n"
    "1. 只依据 JD 原文，不要补充任何原文没有的信息。原文没写的字段就留空或返回空数组。\n"
    "2. `required_skills` 只放**硬性要求**（「必须」「熟悉」「精通」这类措辞下的技术栈与能力）；\n"
    "   把「有…经验优先」「加分项」这类放到 `nice_to_have`。\n"
    "3. `keywords` 抽取 JD 里反复出现、能体现岗位重点的词（技术名词、业务术语、能力词）。\n"
    "4. `implicit_preferences` 是**没明说但能读出来的偏好**，例如「能独立交付」「抗压」\n"
    "   「偏好有从 0 到 1 经验的人」。这一项是你的增值判断，但必须有原文依据，不要凭空发挥。\n"
    "5. 技能名统一写法（例如 'Python' 而不是 'python'/'PYTHON'）。\n"
)


def build_jd_prompt(raw_text: str) -> str:
    return f"请解析下面这份 JD：\n\n<JD>\n{raw_text.strip()}\n</JD>"


# ============================================================ 经历改写（M1-4）
REWRITE_SYSTEM = (
    "你是一名简历撰写专家。你的任务是把一段**真实的**经历素材，改写成面向目标岗位的简历要点。\n"
    "\n"
    "硬性约束（违反即为失败）：\n"
    "1. **绝不编造事实**。不得新增原文没有的公司、项目、数字、技术栈或成果。\n"
    "   原文说「负责用户增长」，你不能写成「使用户增长 300%」。\n"
    "2. 每条要点必须能在**原文描述、定性要点或量化结果**里找到依据，并把依据片段填进 `evidence`。\n"
    "3. 每条要点以**动词开头**，用「做了什么 + 怎么做的 + 带来什么结果」的结构，\n"
    "   优先量化；但**只在原始素材确实有数字时**才写数字。\n"
    "4. **凡写到数字，必须逐字来自「量化结果」区块**。不得换算单位、不得四舍五入、\n"
    "   不得把「24 → 82 条」写成「增长 240%」。「量化结果」为空时，全文不许出现任何数字成果。\n"
    "5. 措辞向目标岗位的关键词靠拢（在事实不变的前提下换用 JD 里的说法）。\n"
    "6. 输出 2–5 条要点，按与岗位的相关度从高到低排序；无关的内容直接不写。\n"
    "7. 全部用中文，不要 markdown 标记，不要编号前缀。\n"
)


def _bullet_block(items: list[str]) -> str:
    """把字符串列表渲染成 `\\n  - a\\n  - b`；空列表返回「（无）」。

    单独抽出来是因为「原样保留用户写法」很重要：这里**不做任何规整或截断**，
    否则「24 → 82 条」这类数字写法会在进 prompt 的路上被改掉，
    M4-7 的编号校验就失去参照物了。
    """
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        return "（无）"
    return "\n  - " + "\n  - ".join(cleaned)


def _metric_block(metrics: list[dict[str, Any]]) -> str:
    """把量化结果渲染成 `指标名：数值（口径：…）`。

    `metrics` 是 jsonb 读回的 dict 列表而不是 `ExperienceMetric` 对象：
    调用方（rewriter）拿到的就是仓储返回的原始行，不额外做一次模型转换 ——
    少一层转换就少一处「字段改名后这里悄悄丢数据」的机会。
    """
    lines: list[str] = []
    for item in metrics or []:
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if not (name or value):
            continue
        text = f"{name}：{value}" if name and value else (name or value)
        context = str(item.get("context") or "").strip()
        if context:
            text += f"（口径：{context}）"
        lines.append(text)
    return _bullet_block(lines)


def build_rewrite_prompt(
    profile: JobProfile,
    *,
    kind: str,
    org: str,
    role: str,
    raw_description: str,
    skill_tags: list[str],
    highlights: list[str],
    metrics: list[dict[str, Any]] | None = None,
    max_chars: int,
) -> str:
    """拼改写 prompt。

    `max_chars` 用于截断超长素材：宁可截断并如实告知（调用方会记 warning），
    也不要让请求悄悄超时 —— 那种失败最难查。
    """
    description = raw_description[:max_chars]
    truncated = len(raw_description) > len(description)

    profile_view = {
        "岗位名称": profile.title,
        "级别": profile.seniority,
        "必备技能": profile.required_skills,
        "加分项": profile.nice_to_have,
        "业务域": profile.business_domain,
        "关键词": profile.keywords,
        "隐性偏好": profile.implicit_preferences,
    }

    parts = [
        "## 目标岗位画像",
        json.dumps(profile_view, ensure_ascii=False, indent=2),
        "",
        "## 待改写的经历素材（事实基线，不得超出）",
        f"- 类型：{kind}",
        f"- 组织/项目：{org}",
        f"- 角色：{role}",
        f"- 原始描述：{description}",
        f"- 技能标签：{', '.join(skill_tags) if skill_tags else '（无）'}",
        "- 定性要点：" + _bullet_block(highlights),
        # 量化结果单独成块：模型要写数字时只许看这里，校验时也只查这里
        "- 量化结果（唯一允许出现的数字来源）：" + _metric_block(metrics or []),
        "",
        "## 任务",
        "把这段经历改写成面向上述岗位的简历要点。",
    ]
    if truncated:
        parts.append(
            f"（注意：原始描述过长，已截取前 {max_chars} 字；不要为被截掉的部分编造内容。）"
        )
    return "\n".join(parts)


def prompt_metadata() -> dict[str, str]:
    """写进 Langfuse 的 prompt 版本标记。"""
    return {"prompt_version": PROMPT_VERSION}
