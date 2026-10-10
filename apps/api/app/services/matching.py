"""按每条要求粗筛，再以事实命中评分。派生 variants 不参与检索或证据。"""

import hashlib
import re
import unicodedata
from typing import Any
from uuid import UUID

from app.llm.embeddings import EmbeddingClient
from app.repositories.embedding_repo import EmbeddingRepository
from app.schemas.jd import JobProfile
from app.schemas.matching import ExperienceMatch, JobRequirement, MatchResponse, RequirementMatch

ALIASES = {
    "postgresql": ["postgres", "pg"],
    "javascript": ["js"],
    "typescript": ["ts"],
    "kubernetes": ["k8s"],
    "node.js": ["nodejs"],
    "c++": ["cpp"],
    "c#": ["csharp"],
    "golang": ["go"],
}


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower().strip()


def contains(text: str, term: str) -> bool:
    text, term = normalize(text), normalize(term)
    if not term:
        return False
    if re.search(r"[a-z0-9]", term):
        return bool(re.search(r"(?<![a-z0-9+#])" + re.escape(term) + r"(?![a-z0-9+#])", text))
    return term in text


def fact_segments(row: dict[str, Any]) -> list[str]:
    return [
        str(row["org"]),
        str(row["role"]),
        str(row["raw_description"]),
        *row["skill_tags"],
        *row["highlights"],
        *[" ".join(str(v) for v in metric.values() if v) for metric in row["metrics"]],
    ]


def source_text(row: dict[str, Any]) -> str:
    return "\n".join(fact_segments(row))


def source_hash(row: dict[str, Any]) -> str:
    # schema version included: changing extraction logic invalidates old embeddings.
    return hashlib.sha256(("facts-v1\n" + source_text(row)).encode()).hexdigest()


def requirements_for(profile: JobProfile) -> list[JobRequirement]:
    result: list[JobRequirement] = []
    groups = [
        ("required", profile.required_skills, 2),
        ("preferred", profile.nice_to_have, 1),
        ("responsibility", profile.responsibilities, 1),
        ("domain", [profile.business_domain] if profile.business_domain else [], 1),
    ]
    for category, texts, weight in groups:
        seen: set[str] = set()
        for text in texts:
            key = normalize(text)
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(
                JobRequirement(
                    id=f"{category}-{len(result)}",
                    category=category,
                    text=text.strip(),
                    weight=weight,
                )
            )
    return result


def evidence_for(row: dict[str, Any], requirement: str) -> list[str]:
    terms = [requirement]
    # 整条要求仅完整命中才算覆盖；一条包含多技能的要求不能靠命中其中一个满足。
    key = normalize(requirement)
    for canonical, aliases in ALIASES.items():
        if key in [canonical, *aliases]:
            terms = [canonical, *aliases]
            break
    matches = []
    for segment in fact_segments(row):
        # 否定只作用于当前分句，不能让“没有 Kubernetes 经验”抹掉前句的 Python 事实。
        # 逗号可能分隔同一否定列表，仅在后面有独立主张时才断句。
        # 保留标点与原文；不拆 and/和，不把不同分句的技能拼成完整要求。
        clauses = re.split(
            r"(?<=[。！？!?；;\n])|(?<=\.)\s+|(?=\bbut\b)|(?=但是|但|然而|不过)|"
            r"(?<=[，,])(?=\s*(?:没有|未使用|不熟悉|使用|负责|开发|"
            r"(?:I|we)\s+(?:use|used|built|developed|have|do)\b|"
            r"(?:no|not|never|used|use|built|developed)\b))",
            segment,
            flags=re.I,
        )
        for clause in clauses:
            if not any(contains(clause, term) for term in terms):
                continue
            # 常见否定表达包括 No Python experience / not familiar / haven't used。
            # “not only”表示递进，不能当成否定。
            claim = re.sub(r"\bnot\s+only\b", "", normalize(clause))
            if re.search(
                r"未使用|未用过|没使用|没用过|没有使用|不具备|不会|没有.*经验|无.*经验|不熟悉|不懂|"
                r"未接触|没接触|不了解|未掌握|未学习|尚未|缺乏|"
                r"\b(?:no|not|never|without|lack(?:s|ed|ing)?|cannot|can't|won't|"
                r"(?:do|does|did|have|has|had|is|are|was|were|could|would)n['’]t)\b",
                claim,
            ):
                continue
            matches.append(clause.strip())
    return list(dict.fromkeys(matches))[:3]


class MatchingService:
    def __init__(self, embeddings: EmbeddingClient, repository: EmbeddingRepository):
        self.embeddings = embeddings
        self.repository = repository

    async def match(
        self,
        user_id: UUID,
        jd_id: UUID,
        profile: JobProfile,
        rows: list[dict[str, Any]],
        limit: int,
        trace_id: str,
    ) -> MatchResponse:
        requirements = requirements_for(profile)
        warnings = []
        if self.embeddings.is_stub:
            warnings.append(
                "当前为哈希向量演示，不代表真实语义检索质量；"
                "请配置 EMBEDDING_PROVIDER=openai-compatible。"
            )
        if not requirements:
            warnings.append("该 JD 没有可匹配的要求，请补充岗位内容后重新解析。")
        if not rows:
            warnings.append("素材库为空，请先录入经历。")
        searches: list[dict[UUID, float]] = [{} for _ in requirements]
        if rows and requirements:
            cached = await self.repository.fingerprints(user_id)
            pending = [
                r
                for r in rows
                if cached.get(r["id"]) != (source_hash(r), self.embeddings.model_key)
            ]
            for start in range(0, len(pending), 16):
                batch = pending[start : start + 16]
                vectors = await self.embeddings.embed([source_text(r) for r in batch])
                for row, vector in zip(batch, vectors, strict=True):
                    stored = await self.repository.store(
                        user_id,
                        row["id"],
                        row["updated_at"],
                        source_hash(row),
                        self.embeddings.model_key,
                        vector,
                    )
                    if not stored:
                        warnings.append("素材在匹配期间发生变化，请重新匹配以获得最新结果。")
            vectors = await self.embeddings.embed([r.text for r in requirements])
            searches = [
                await self.repository.search(user_id, vector, self.embeddings.model_key, limit)
                for vector in vectors
            ]
        items = []
        total_weight = sum(r.weight for r in requirements)
        for row in rows:
            cells = []
            for requirement, search in zip(requirements, searches, strict=True):
                evidence = evidence_for(row, requirement.text)
                similarity = search.get(row["id"])
                shortlisted = row["id"] in search or bool(evidence)
                if evidence:
                    score, status, reason = (
                        85.0,
                        "covered",
                        "原始素材完整命中该要求（含规范化别名）；请核对事实依据。",
                    )
                elif similarity is not None and similarity >= 0.35:
                    score = round(min(49.0, max(0, similarity) * 49), 1)
                    status, reason = (
                        "related",
                        "语义相关，但缺少完整事实依据，不能据此认定满足要求。",
                    )
                else:
                    score, status, reason = 0.0, "missing", "未找到完整事实依据。"
                cells.append(
                    RequirementMatch(
                        requirement_id=requirement.id,
                        score=score,
                        semantic_similarity=round(similarity, 4)
                        if similarity is not None
                        else None,
                        status=status,
                        evidence=evidence,
                        reason=reason,
                        shortlisted=shortlisted,
                    )
                )
            score = sum(c.score * r.weight for c, r in zip(cells, requirements, strict=True))
            items.append(
                ExperienceMatch(
                    experience_id=row["id"],
                    org=row["org"],
                    role=row["role"],
                    score=round(score / total_weight, 1) if total_weight else 0,
                    matches=cells,
                )
            )
        items.sort(key=lambda item: (-item.score, str(item.experience_id)))
        uncovered = [
            r.id
            for r in requirements
            if not any(
                c.requirement_id == r.id and c.status == "covered"
                for item in items
                for c in item.matches
            )
        ]
        return MatchResponse(
            jd_id=jd_id,
            requirements=requirements,
            items=items,
            uncovered_requirement_ids=uncovered,
            embedding_model=self.embeddings.model_key,
            is_stub=self.embeddings.is_stub,
            warnings=list(dict.fromkeys(warnings)),
            trace_id=trace_id,
        )
