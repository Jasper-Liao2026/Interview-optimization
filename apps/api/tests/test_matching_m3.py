"""Facts, retrieval boundaries and cache invalidation for M3 matching."""

from datetime import timedelta
from uuid import uuid4

import pytest

from app.config import Settings
from app.llm.embeddings import EmbeddingClient
from app.schemas.jd import JobProfile
from app.services.matching import MatchingService, evidence_for, requirements_for
from tests.fakes import DEV_USER, FakeEmbeddingRepository, experience_row


def job(*required, nice_to_have=None):
    return JobProfile(
        required_skills=list(required),
        nice_to_have=nice_to_have or [],
        keywords=[],
        implicit_preferences=[],
        responsibilities=[],
    )


def test_requirements_dedupe_across_categories_and_keep_required_priority():
    profile = JobProfile(
        required_skills=["Python", "FastAPI"],
        nice_to_have=["Python", "Docker"],
        keywords=[],
        implicit_preferences=[],
        responsibilities=["Python", "设计接口"],
        business_domain="设计接口",
    )
    requirements = requirements_for(profile)
    assert [(item.category, item.text) for item in requirements] == [
        ("required", "Python"),
        ("required", "FastAPI"),
        ("preferred", "Docker"),
        ("responsibility", "设计接口"),
    ]


def fact_row(text, **updates):
    return experience_row(
        raw_description=text, skill_tags=[], highlights=[], metrics=[], variants=[], **updates
    )


@pytest.mark.parametrize(
    ("requirement", "fact"),
    [
        ("PostgreSQL", "使用 Postgres 存储订单"),
        ("Kubernetes", "部署 k8s 集群"),
        ("TypeScript", "使用 TS 开发页面"),
        ("JavaScript", "使用 JS 开发页面"),
        ("Node.js", "使用 Nodejs 开发接口"),
        ("C++", "使用 cpp 开发工具"),
        ("C#", "使用 csharp 编写客户端"),
        ("Golang", "使用 Go 开发接口"),
        ("Python", "使用 Ｐｙｔｈｏｎ 编写工具"),
    ],
)
def test_matching_accepts_canonical_aliases_and_fullwidth(requirement, fact):
    assert evidence_for(fact_row(fact), requirement) == [fact]


@pytest.mark.parametrize(
    ("requirement", "fact"),
    [
        ("Java", "使用 JavaScript 开发页面"),
        ("Go", "使用 Django 开发接口"),
        ("SQL", "使用 NoSQL 存储数据"),
        ("C", "使用 C++ 开发程序"),
        ("Python", "未使用 Python"),
        ("Python", "没有 Python 经验"),
        ("Python", "不熟悉 Python"),
        ("Python", "never used Python"),
        ("Python", "no experience with Python"),
        ("Python", "Not familiar with Python"),
        ("Python", "No Python experience"),
        ("Python", "I have not used Python"),
        ("Python", "I haven't used Python"),
        ("Python", "I don’t know Python"),
        ("Python", "I lack Python experience"),
        ("Python", "不懂 Python"),
        ("Python", "没用过 Python"),
        ("Python", "没有使用过 Python"),
        ("Python", "不具备 Python 经验"),
        ("Python", "无 Python 开发经历"),
        ("Python", "没有 Python 开发经历"),
        ("Python", "无 Python 基础"),
        ("Python", "不使用 Python"),
        ("Python", "不擅长 Python"),
        ("Python", "未曾使用 Python"),
        ("Python", "未学过 Python"),
        ("Python", "没学过 Python"),
        ("Python", "Not familiar with Java, Python or Go"),
        ("Go", "Not familiar with Java, Python or Go"),
        ("Python", "没有 Java、Python 或 Go 经验"),
        ("Python 和 FastAPI", "使用 Python 编写工具"),
    ],
)
def test_matching_rejects_false_skill_and_negative_claims(requirement, fact):
    assert evidence_for(fact_row(fact), requirement) == []


@pytest.mark.parametrize(
    ("fact", "evidence"),
    [
        ("使用 Python 开发接口。没有 Kubernetes 经验。", "使用 Python 开发接口。"),
        ("没有 Kubernetes 经验，但使用 Python 开发接口。", "但使用 Python 开发接口。"),
        ("Used Python for APIs. No Kubernetes experience.", "Used Python for APIs."),
        ("No Kubernetes experience, but used Python for APIs.", "but used Python for APIs."),
        ("I use not only Python but also FastAPI.", "I use not only Python"),
        ("使用 Python 开发接口，没有 Kubernetes 经验。", "使用 Python 开发接口，"),
        ("Used Python for APIs, no Kubernetes experience.", "Used Python for APIs,"),
        ("No Kubernetes experience, used Python for APIs.", "used Python for APIs."),
        ("使用 Python 开发接口，不懂 Kubernetes。", "使用 Python 开发接口，"),
        ("使用 Python 开发接口，无 Kubernetes 开发经历。", "使用 Python 开发接口，"),
    ],
)
def test_negation_in_another_clause_preserves_positive_original_evidence(fact, evidence):
    row = fact_row(fact)
    assert evidence_for(row, "Python") == [evidence]
    assert evidence in fact
    assert evidence_for(row, "Kubernetes") == []


def test_clause_boundaries_preserve_technology_names_and_complete_requirements():
    assert evidence_for(fact_row("Used Node.js for APIs. No Python experience."), "Node.js") == [
        "Used Node.js for APIs."
    ]
    assert evidence_for(fact_row("使用 Python。使用 FastAPI。"), "Python 和 FastAPI") == []
    assert evidence_for(fact_row("使用 Python 和 FastAPI 开发接口。"), "Python 和 FastAPI") == [
        "使用 Python 和 FastAPI 开发接口。"
    ]


async def test_negative_experience_remains_a_gap_even_with_high_vector_similarity():
    row = fact_row("Not familiar with Python. Used PostgreSQL for storage.")

    class SemanticShortlist(FakeEmbeddingRepository):
        async def search(self, *args, **kwargs):
            return {row["id"]: 0.99}

    result = await MatchingService(RecordingEmbedding(), SemanticShortlist()).match(
        DEV_USER, uuid4(), job("Python", "PostgreSQL"), [row], 1, "a" * 32
    )
    negative, positive = result.items[0].matches
    assert negative.status == "related"
    assert negative.evidence == []
    assert negative.score < 50
    assert result.requirements[0].id in result.uncovered_requirement_ids
    assert positive.status == "covered"
    assert positive.evidence == ["Used PostgreSQL for storage."]


async def test_variants_do_not_become_facts_or_embeddings():
    row = fact_row("负责订单服务。")
    row["variants"] = [{"direction": "后端", "text": "Python Docker", "note": None}]
    embeddings = RecordingEmbedding()
    repository = FakeEmbeddingRepository()
    result = await MatchingService(embeddings, repository).match(
        DEV_USER,
        uuid4(),
        job("Python"),
        [row],
        1,
        "a" * 32,
    )
    assert "Docker" not in embeddings.calls[0][0]
    assert result.items[0].matches[0].status != "covered"
    assert result.items[0].matches[0].evidence == []


class RecordingEmbedding(EmbeddingClient):
    def __init__(self):
        super().__init__(Settings(embedding_provider="stub"))
        self.calls = []
        self.version = "first"

    @property
    def model_key(self):
        return f"test:{self.version}:1536"

    async def embed(self, texts):
        self.calls.append(list(texts))
        return await super().embed(texts)


async def test_cache_reuses_facts_and_invalidates_edit_and_model_changes():
    embeddings = RecordingEmbedding()
    repository = FakeEmbeddingRepository()
    service = MatchingService(embeddings, repository)
    row = fact_row("使用 Python 开发接口。")
    args = (DEV_USER, uuid4(), job("Python"), [row], 1, "a" * 32)
    await service.match(*args)
    assert repository.store_calls == 1
    assert len(embeddings.calls) == 2
    await service.match(*args)
    assert repository.store_calls == 1
    assert len(embeddings.calls) == 3  # requirements still need a query embedding
    row["variants"] = [{"direction": "前端", "text": "润色稿", "note": None}]
    await service.match(*args)
    assert repository.store_calls == 1
    row["raw_description"] = "使用 Python 开发接口及性能优化。"
    row["updated_at"] += timedelta(seconds=1)
    await service.match(*args)
    assert repository.store_calls == 2
    embeddings.version = "second"
    await service.match(*args)
    assert repository.store_calls == 3
    assert repository.records[row["id"]]["model_key"] == embeddings.model_key


async def test_exact_fact_outside_vector_shortlist_still_covered():
    class EmptyShortlist(FakeEmbeddingRepository):
        async def search(self, *args, **kwargs):
            return {}

    row = fact_row("使用 Python 开发订单服务。")
    result = await MatchingService(RecordingEmbedding(), EmptyShortlist()).match(
        DEV_USER,
        uuid4(),
        job("Python"),
        [row],
        1,
        "a" * 32,
    )
    cell = result.items[0].matches[0]
    assert cell.status == "covered"
    assert cell.shortlisted is True
    assert cell.semantic_similarity is None
    assert cell.evidence == [row["raw_description"]]


async def test_semantic_similarity_is_not_proof_of_coverage():
    row = fact_row("负责服务维护。")

    class SemanticShortlist(FakeEmbeddingRepository):
        async def search(self, *args, **kwargs):
            return {row["id"]: 0.99}

    result = await MatchingService(RecordingEmbedding(), SemanticShortlist()).match(
        DEV_USER,
        uuid4(),
        job("Python"),
        [row],
        1,
        "a" * 32,
    )
    cell = result.items[0].matches[0]
    assert cell.status == "related"
    assert cell.score < 50
    assert cell.evidence == []
    assert result.requirements[0].id in result.uncovered_requirement_ids


async def test_matrix_preserves_all_rows_and_requirements_and_searches_each_requirement():
    repository = FakeEmbeddingRepository()
    rows = [fact_row("使用 Python"), fact_row("参与校园活动")]
    result = await MatchingService(RecordingEmbedding(), repository).match(
        DEV_USER,
        uuid4(),
        job("Python", "Rust", nice_to_have=["Docker"]),
        rows,
        1,
        "a" * 32,
    )
    assert repository.search_calls == 3
    assert len(result.items) == 2
    expected = {requirement.id for requirement in result.requirements}
    assert all({cell.requirement_id for cell in item.matches} == expected for item in result.items)
    assert result.items[0].experience_id == rows[0]["id"]


async def test_concurrent_fact_edit_does_not_silently_cache_old_embedding():
    class RejectStore(FakeEmbeddingRepository):
        async def store(self, *args, **kwargs):
            return False

    repository = RejectStore()
    result = await MatchingService(RecordingEmbedding(), repository).match(
        DEV_USER,
        uuid4(),
        job("Python"),
        [fact_row("使用 Python")],
        1,
        "a" * 32,
    )
    assert repository.records == {}
    assert any("发生变化" in warning for warning in result.warnings)
