"""M4 factual guardrails: evidence, metrics, named skills and summary."""

from __future__ import annotations

import pytest

from app.agents.facts import validate_rewrite_facts
from app.schemas import RewrittenExperience


def source(**updates):
    row = {
        "org": "查询服务",
        "role": "后端开发",
        "raw_description": "使用 Python 和 PostgreSQL 维护查询服务。没有使用 Redis。",
        "skill_tags": ["Python", "PostgreSQL"],
        "highlights": ["通过增加索引改善查询性能"],
        "metrics": [{"name": "接口延迟", "value": "800ms → 120ms", "context": "同一压测数据集"}],
        "variants": [{"direction": "缓存", "text": "使用 Redis 提升吞吐 300%"}],
    }
    row.update(updates)
    return row


def rewrite(text, evidence, *, summary=None):
    return RewrittenExperience.model_validate(
        {"bullets": [{"text": text, "evidence": evidence}], "summary": summary}
    )


def violations(text, evidence, *, summary=None, row=None):
    return validate_rewrite_facts(row or source(), rewrite(text, evidence, summary=summary))


def test_valid_rewrite_with_quoted_metric_and_positive_technology():
    assert (
        violations(
            "维护 Python 查询服务，将接口延迟从 800ms 降至 120ms。",
            ["使用 Python 和 PostgreSQL 维护查询服务", "800ms → 120ms"],
        )
        == []
    )


@pytest.mark.parametrize("evidence", [[], [""], ["   "]])
def test_every_bullet_needs_nonempty_source_evidence(evidence):
    assert any("缺少事实依据" in issue for issue in violations("维护 Python 查询服务", evidence))


def test_evidence_must_exist_in_original_facts_not_variant():
    issues = violations("使用 Redis 提升吞吐", ["使用 Redis 提升吞吐 300%"])
    assert any("无法回溯" in issue for issue in issues)


def test_metrics_fields_can_be_evidence_but_name_only_cannot_support_number():
    assert violations("改善接口延迟", ["接口延迟"]) == []
    issues = violations("将延迟降至 120ms", ["接口延迟"])
    assert any("数字未引用对应量化结果" in issue for issue in issues)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("30ms", "30ms"),
        ("800", "800"),
        ("300%", "300%"),
        ("300ms", "300ms"),
        ("120s", "120s"),
    ],
)
def test_number_and_unit_require_exact_metric_token(value, expected):
    issues = violations(f"将接口延迟降至 {value}", ["800ms → 120ms"])
    assert any(expected in issue and "未在量化结果" in issue for issue in issues)


def test_number_cannot_borrow_an_uncited_metric():
    issues = violations("接口延迟降至 120ms", ["通过增加索引改善查询性能"])
    assert any("数字未引用对应量化结果" in issue for issue in issues)


def test_a_partial_metric_quote_does_not_support_other_numbers():
    issues = violations("接口延迟从 800ms 降至 120ms", ["800ms"])
    assert any("120ms" in issue and "数字未引用" in issue for issue in issues)


def test_metric_context_numbers_are_allowed_only_when_cited():
    row = source(metrics=[{"name": "接口延迟", "value": "120ms", "context": "压测 5000 QPS 下"}])
    assert violations("在 5000 QPS 下达到 120ms 延迟", ["5000 QPS", "120ms"], row=row) == []
    issues = violations("在 5000 QPS 下达到 120ms 延迟", ["120ms"], row=row)
    assert any("5000" in issue and "数字未引用" in issue for issue in issues)


@pytest.mark.parametrize("text", ["延迟达到300ms", "响应耗时为300ms", "完成30份报表"])
def test_numbers_adjacent_to_chinese_words_are_checked(text):
    assert any("未在量化结果" in issue for issue in violations(text, ["800ms → 120ms"]))


def test_chinese_numerals_cannot_bypass_metric_guard():
    issues = violations("提升三百%性能", ["通过增加索引改善查询性能"])
    assert any("300%" in issue and "未在量化结果" in issue for issue in issues)


def test_common_lowercase_technology_cannot_be_invented():
    issues = violations("使用 rabbitmq 构建后端服务", ["使用 Python 和 PostgreSQL 维护查询服务"])
    assert any("rabbitmq" in issue and "技术或专名" in issue for issue in issues)


def test_quantities_keep_their_original_count_unit():
    row = source(metrics=[{"name": "报表", "value": "30 份", "context": None}])
    assert violations("核对30份报表", ["30 份"], row=row) == []
    assert any("未在量化结果" in issue for issue in violations("核对30条记录", ["30 份"], row=row))


@pytest.mark.parametrize(
    ("claim", "metric"),
    [
        ("三百%", "300%"),
        ("300%", "三百%"),
        ("三百毫秒", "300毫秒"),
        ("三百ms", "300ms"),
        ("二零二四年", "2024年"),
        ("一万亿次", "1000000000000次"),
    ],
)
def test_chinese_metric_numbers_are_normalized_and_checked(claim, metric):
    row = source(metrics=[{"name": "结果", "value": metric, "context": None}])
    assert violations(f"达到{claim}", [metric], row=row) == []
    issues = violations(f"达到{claim}", ["通过增加索引改善查询性能"], row=row)
    assert any("数字未引用对应量化结果" in issue for issue in issues)
    issues = violations(f"达到{claim}", ["通过增加索引改善查询性能"])
    assert any("未在量化结果" in issue for issue in issues)


def test_generic_one_item_phrases_are_not_treated_as_metrics():
    row = source(
        raw_description="独立交付一条链路和一套服务。",
        skill_tags=[],
        highlights=["独立交付一条链路和一套服务"],
        metrics=[],
    )
    assert violations("独立交付一条链路和一套服务", ["独立交付一条链路和一套服务"], row=row) == []


@pytest.mark.parametrize(
    "term",
    ["Redis", "Docker", "Kubernetes", "React", "Memcached", "RabbitMQ", "Python3", "C++", "C#"],
)
def test_unfounded_named_technology_is_rejected(term):
    issues = violations(f"使用 {term} 维护查询服务", ["使用 Python 和 PostgreSQL 维护查询服务"])
    assert any(term in issue and "技术或专名" in issue for issue in issues)


def test_negative_technology_mentions_are_not_positive_evidence():
    issues = violations("使用 Redis 缓存数据", ["没有使用 Redis"])
    assert any("Redis" in issue and "技术或专名" in issue for issue in issues)


def test_a_short_quote_cannot_strip_a_negative_context():
    issues = violations("使用 Redis 缓存数据", ["Redis"])
    assert any("Redis" in issue and "技术或专名" in issue for issue in issues)


def test_metric_does_not_justify_invented_technology():
    issues = violations("使用 Redis 将接口延迟降至 120ms", ["800ms → 120ms"])
    assert any("Redis" in issue and "技术或专名" in issue for issue in issues)


def test_summary_must_be_a_verbatim_fact_and_cannot_hide_invention():
    assert (
        violations(
            "维护 Python 查询服务",
            ["使用 Python 和 PostgreSQL 维护查询服务"],
            summary="通过增加索引改善查询性能",
        )
        == []
    )
    issues = violations(
        "维护 Python 查询服务",
        ["使用 Python 和 PostgreSQL 维护查询服务"],
        summary="使用 Redis 实现 300% 性能提升",
    )
    assert any("summary 无法逐字回溯" in issue for issue in issues)
    assert any("summary 含未在量化结果" in issue for issue in issues)
    assert any("summary 技术或专名" in issue for issue in issues)


def test_empty_bullet_list_is_not_a_success():
    outcome = RewrittenExperience(bullets=[])
    assert any("至少需要一条" in issue for issue in validate_rewrite_facts(source(), outcome))
