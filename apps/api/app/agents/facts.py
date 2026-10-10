"""Deterministic checks for claims in generated resume text.

These checks catch missing provenance, invented numbers and named technologies.
They cannot prove that arbitrary natural-language outcomes follow from evidence.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.schemas import RewrittenExperience

_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_])\d+(?:[.,]\d+)*"
    r"(?:\s*(?:%|％|[a-zA-Zμµ]+|毫秒|小时|分钟|条|份|个|次|人|天|年|周|月|秒|倍|万|亿))?"
    r"(?![A-Za-z0-9_])"
)
_CHINESE_NUMBER = re.compile(
    r"(?<![A-Za-z0-9])(?P<number>[零〇一二两三四五六七八九十百千万亿]+)"
    r"\s*(?P<unit>%|％|[a-zA-Zμµ]+|毫秒|小时|分钟|条|套|份|个|次|人|天|年|周|月|秒|倍|万|亿)"
    r"(?![A-Za-z0-9])"
)
_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_SMALL_UNITS = {"十": 10, "百": 100, "千": 1000}
_CHINESE_LARGE_UNITS = {"万": 10_000, "亿": 100_000_000}
_ASCII_TERM = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z][A-Za-z0-9]*(?:[._+#-][A-Za-z0-9]+)*)(?![A-Za-z0-9])"
)
_TECHNOLOGIES = {
    "angular",
    "aws",
    "azure",
    "c#",
    "c++",
    "clickhouse",
    "css",
    "django",
    "docker",
    "elasticsearch",
    "fastapi",
    "flask",
    "gcp",
    "git",
    "golang",
    "graphql",
    "html",
    "java",
    "javascript",
    "js",
    "json",
    "k8s",
    "kafka",
    "kotlin",
    "kubernetes",
    "langchain",
    "langfuse",
    "langgraph",
    "mongodb",
    "mysql",
    "nestjs",
    "next.js",
    "node.js",
    "nodejs",
    "numpy",
    "pandas",
    "postgres",
    "postgresql",
    "pytest",
    "python",
    "rabbitmq",
    "react",
    "redis",
    "rust",
    "spark",
    "spring",
    "sql",
    "sqlite",
    "supabase",
    "swift",
    "tailwind",
    "ts",
    "typescript",
    "vue",
}
_GENERAL_ACRONYMS = {
    "AI",
    "API",
    "CI",
    "CRUD",
    "HTTP",
    "ID",
    "JD",
    "JSON",
    "LLM",
    "PDF",
    "UI",
    "UX",
}
_NEGATION = re.compile(
    r"未使用|没使用|没有使用|不使用|不具备|不会|没有.*(?:经验|经历)|无.*(?:经验|经历)|"
    r"不熟悉|不懂|不擅长|未接触|没接触|缺乏|"
    r"\b(?:no|not|never|without|lack(?:s|ed|ing)?|cannot|can't|haven't|didn't)\b",
    re.I,
)
_ALIASES = {
    "postgresql": {"postgres", "pg"},
    "javascript": {"js"},
    "typescript": {"ts"},
    "kubernetes": {"k8s"},
    "node.js": {"nodejs"},
    "golang": {"go"},
}


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _chinese_integer(value: str) -> int:
    """Convert common Chinese quantity numeral forms to an integer."""
    if all(char in _CHINESE_DIGITS for char in value):
        return int("".join(str(_CHINESE_DIGITS[char]) for char in value))
    total = 0
    section = 0
    digit = 0
    for char in value:
        if char in _CHINESE_DIGITS:
            digit = _CHINESE_DIGITS[char]
        elif char in _CHINESE_SMALL_UNITS:
            unit = _CHINESE_SMALL_UNITS[char]
            section += (digit or 1) * unit
            digit = 0
        elif char in _CHINESE_LARGE_UNITS:
            unit = _CHINESE_LARGE_UNITS[char]
            if unit == 100_000_000:
                total = (total + section + digit) * unit
            else:
                total += (section + digit) * unit
            section = 0
            digit = 0
    return total + section + digit


def _numbers(text: str) -> set[str]:
    tokens = {re.sub(r"\s+", "", match.group()) for match in _NUMBER.finditer(text)}
    for match in _CHINESE_NUMBER.finditer(text):
        number, unit = match.group("number"), match.group("unit")
        # "一条/一套" commonly describes an action or deliverable, rather than
        # a measured result. Do not turn those ordinary phrases into metric claims.
        if number == "一" and unit in {"条", "套"}:
            continue
        tokens.add(f"{_chinese_integer(number)}{unit}")
    return tokens


def _metric_sources(experience: dict[str, Any]) -> list[tuple[str, str]]:
    sources = []
    for metric in experience.get("metrics") or []:
        if not isinstance(metric, dict):
            continue
        name = str(metric.get("name") or "").strip()
        value = str(metric.get("value") or "").strip()
        context = str(metric.get("context") or "").strip()
        if not value:
            continue
        full = f"{name}：{value}" if name else value
        if context:
            full += f"（口径：{context}）"
        for part in (name, value, context, full):
            if part:
                sources.append((part, part))
    return sources


def _named_terms(text: str) -> set[str]:
    terms = set(re.findall(r"(?<![A-Za-z0-9])C(?:\+\+|#)(?![A-Za-z0-9+#])", text, re.I))
    numbers = [match.span() for match in _NUMBER.finditer(text)]
    numbers.extend(match.span() for match in _CHINESE_NUMBER.finditer(text))
    for match in _ASCII_TERM.finditer(text):
        if any(start <= match.start() < end for start, end in numbers):
            continue
        term = match.group()
        normalized = term.casefold()
        if (
            normalized in _TECHNOLOGIES
            or term in _GENERAL_ACRONYMS
            or (len(term) > 1 and any(char.isupper() for char in term))
            or "_" in term
        ):
            terms.add(term)
    return terms


def _positive_mention(evidence: list[str], term: str) -> bool:
    key = term.casefold()
    aliases = next(
        (
            names | {canonical}
            for canonical, names in _ALIASES.items()
            if key in names | {canonical}
        ),
        {key},
    )
    for snippet in evidence:
        clauses = re.split(r"[。！？!?；;]|(?<=\.)\s+|(?=但是|然而|不过|\bbut\b)", snippet)
        for clause in clauses:
            if _NEGATION.search(clause):
                continue
            if any(
                re.search(
                    r"(?<![a-z0-9+#])" + re.escape(alias) + r"(?![a-z0-9+#])", clause.casefold()
                )
                for alias in aliases
            ):
                return True
    return False


def validate_rewrite_facts(experience: dict[str, Any], rewritten: RewrittenExperience) -> list[str]:
    """Return actionable violations so a failed rewrite cannot poison sibling items."""

    ordinary_sources = [
        str(experience.get("raw_description") or ""),
        *(str(item) for item in experience.get("skill_tags") or []),
        *(str(item) for item in experience.get("highlights") or []),
    ]
    metric_sources = _metric_sources(experience)
    sources = [_norm(part) for part in ordinary_sources if _norm(part)]
    sources.extend(_norm(part) for part, _ in metric_sources)
    metric_values = [value for _, value in metric_sources]
    violations: list[str] = []

    if not rewritten.bullets:
        violations.append("至少需要一条有事实依据的要点")

    for index, bullet in enumerate(rewritten.bullets):
        text = _norm(bullet.text)
        valid_evidence: list[str] = []
        source_contexts: list[str] = []
        if not text:
            violations.append(f"bullet[{index}] 内容为空")
        if not bullet.evidence or not any(_norm(item) for item in bullet.evidence):
            violations.append(f"bullet[{index}] 缺少事实依据 evidence")
        for evidence in bullet.evidence:
            snippet = _norm(evidence)
            if not snippet:
                violations.append(f"bullet[{index}] evidence 为空")
            elif not any(snippet in source for source in sources):
                violations.append(f"bullet[{index}] evidence 无法回溯到原始素材：{snippet[:80]}")
            else:
                valid_evidence.append(snippet)
                source_contexts.extend(source for source in sources if snippet in source)

        cited_metric_values = [
            snippet
            for part, _ in metric_sources
            for snippet in valid_evidence
            if _norm(snippet) in _norm(part) and _numbers(snippet)
        ]
        for token in sorted(_numbers(text)):
            if not any(token in _numbers(_norm(value)) for value in metric_values):
                violations.append(f"bullet[{index}] 含未在量化结果中出现的数字：{token}")
            elif not any(token in _numbers(_norm(value)) for value in cited_metric_values):
                violations.append(f"bullet[{index}] 数字未引用对应量化结果：{token}")

        for term in sorted(_named_terms(text), key=str.casefold):
            if not _positive_mention(valid_evidence, term) or not _positive_mention(
                source_contexts, term
            ):
                violations.append(f"bullet[{index}] 技术或专名缺少正面事实依据：{term}")

    if rewritten.summary:
        summary = _norm(rewritten.summary)
        if summary and not any(summary in source for source in sources):
            violations.append("summary 无法逐字回溯到原始素材")
        for token in sorted(_numbers(summary)):
            if not any(token in _numbers(_norm(value)) for value in metric_values):
                violations.append(f"summary 含未在量化结果中出现的数字：{token}")
        for term in sorted(_named_terms(summary), key=str.casefold):
            if not _positive_mention(sources, term):
                violations.append(f"summary 技术或专名缺少正面事实依据：{term}")

    return violations
