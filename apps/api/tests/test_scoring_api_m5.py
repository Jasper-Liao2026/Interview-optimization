"""API integration: source mapping, bounded calls, persistence and best-version export."""

import copy
from uuid import uuid4

import pytest

from app.config import get_settings
from app.deps import get_resume_repository
from app.llm import LlmClient, LlmResult, StructuredResult
from app.schemas import JudgeOutput, RewrittenExperience
from tests.fakes import DEV_USER, RESUME_ID, FakeResumeRepository, experience_row, resume_row

API = f"/api/v1/resumes/{RESUME_ID}"


class ScoreRepository(FakeResumeRepository):
    def __init__(self, row):
        super().__init__(row)
        self.records = {}
        self.best = {}

    async def save_scoring_result(self, user_id, source, sections, result):
        run_id, best_id = uuid4(), uuid4()
        best = {**copy.deepcopy(source), "id": best_id, "sections": copy.deepcopy(sections)}
        self.records[run_id] = {"result": copy.deepcopy(result), "best_resume_id": best_id}
        self.best[best_id] = best
        return best, run_id

    async def get_scoring_result(self, user_id, resume_id, run_id):
        return self.records.get(run_id) if user_id == DEV_USER and resume_id == RESUME_ID else None

    async def get(self, user_id, resume_id):
        if user_id != DEV_USER:
            return None
        return self.row if resume_id == RESUME_ID else self.best.get(resume_id)


@pytest.fixture
def scoring_wiring(app, api_wiring):
    settings = get_settings()
    settings.judge_provider = "stub"
    settings.llm_provider = "stub"
    raw = "使用 Python 和 FastAPI 开发接口服务，并编写接口自动化测试。"
    weak = experience_row(raw_description=raw, highlights=["编写接口测试"], metrics=[])
    strong = experience_row(raw_description=raw, highlights=[], metrics=[])
    api_wiring["experiences"].rows = [strong, weak]  # storage order intentionally reversed
    jd_id = uuid4()
    profile = {
        "title": "后端工程师",
        "required_skills": ["Python", "FastAPI"],
        "nice_to_have": [],
        "keywords": ["接口"],
        "responsibilities": ["接口开发"],
        "implicit_preferences": [],
    }
    api_wiring["jds"].rows = [{"id": jd_id, "user_id": DEV_USER, "parsed": profile}]
    row = resume_row()
    row["generator_vendor"] = "stub"
    row["jd_id"] = jd_id
    row["sections"][0]["entries"][0].update(
        experience_id=str(weak["id"]),
        bullets=[{"text": "编写接口测试", "evidence": ["编写接口测试"]}],
    )
    other = copy.deepcopy(row["sections"][0]["entries"][0])
    other.update(experience_id=str(strong["id"]), bullets=[{"text": raw, "evidence": [raw]}])
    row["sections"].append({"title": "其他经历", "entries": [other]})
    repo = ScoreRepository(row)
    app.dependency_overrides[get_resume_repository] = lambda: repo
    return {**api_wiring, "repo": repo, "settings": settings, "weak": weak, "strong": strong}


async def test_score_requires_resume_and_parsed_job(client, scoring_wiring):
    assert (await client.post(f"/api/v1/resumes/{uuid4()}/score", json={})).status_code == 404
    scoring_wiring["repo"].row["jd_id"] = None
    assert (await client.post(f"{API}/score", json={})).status_code == 400


async def test_score_requires_existing_source_and_valid_request(client, scoring_wiring):
    scoring_wiring["experiences"].rows = []
    assert (await client.post(f"{API}/score", json={})).status_code == 400
    assert (await client.post(f"{API}/score", json={"max_rounds": 3})).status_code == 422
    assert (await client.post(f"{API}/score", json={"cost_limit": -1})).status_code == 422


async def test_score_improves_only_weak_item_and_saves_readable_best(client, scoring_wiring):
    original = copy.deepcopy(scoring_wiring["repo"].row)
    response = await client.post(f"{API}/score", json={"threshold": 95})
    assert response.status_code == 200, response.text
    data = response.json()
    loop = data["loop"]
    assert loop["snapshots"][0]["result"]["low_score_items"] == [0]
    assert loop["best"]["round"] == 1 and loop["stop_reason"] == "threshold"
    assert loop["best"]["result"]["score"] > loop["snapshots"][0]["result"]["score"]
    assert len(loop["best"]["result"]["dimensions"]) == 4
    assert loop["snapshots"][0]["result"]["deductions"]
    assert data["resume"]["sections"][1] == original["sections"][1]
    assert scoring_wiring["repo"].row == original
    assert data["resume"]["id"] != str(RESUME_ID)
    assert data["preview_path"] and data["pdf_path"]
    saved = await client.get(f"{API}/scores/{data['score_run_id']}")
    assert saved.status_code == 200 and saved.json() == data
    assert (await client.get(data["preview_path"])).status_code == 200
    assert (await client.get(data["pdf_path"])).content.startswith(b"%PDF")
    assert (await client.get(f"{API}/scores/{uuid4()}")).status_code == 404
    assert (
        await client.get(f"/api/v1/resumes/{uuid4()}/scores/{data['score_run_id']}")
    ).status_code == 404


async def test_score_only_does_not_persist(client, scoring_wiring):
    response = await client.post(
        f"{API}/score", json={"persist": False, "max_rounds": 0, "threshold": 95}
    )
    data = response.json()
    assert response.status_code == 200
    assert len(data["loop"]["snapshots"]) == 1
    assert data["loop"]["stop_reason"] == "max_rounds"
    assert data["score_run_id"] is None and data["preview_path"] is None
    assert not scoring_wiring["repo"].records


async def test_real_judge_vendor_and_budget_checked_before_call(
    client, scoring_wiring, monkeypatch
):
    settings = scoring_wiring["settings"]
    settings.judge_provider = "openai-compatible"
    settings.judge_api_key = "test-only"
    settings.generation_vendor = settings.judge_vendor = "same"
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("must not call provider")

    monkeypatch.setattr(LlmClient, "complete_json", forbidden)
    assert (await client.post(f"{API}/score", json={})).status_code == 400
    assert not calls
    settings.judge_vendor = "another"
    response = await client.post(f"{API}/score", json={"cost_limit": 2, "persist": False})
    assert response.status_code == 200, response.text
    assert response.json()["loop"]["stop_reason"] == "cost_limit"
    assert not calls


async def test_real_judge_is_called_through_api_and_blind(client, scoring_wiring, monkeypatch):
    settings = scoring_wiring["settings"]
    settings.judge_provider = "openai-compatible"
    settings.judge_api_key = "test-only"
    settings.generation_vendor, settings.judge_vendor = "deepseek", "openai"
    seen = []

    async def complete(self, prompt, schema, **kwargs):
        assert schema is JudgeOutput
        seen.append((self.settings.llm_base_url, prompt))
        values = {
            "assessments": [
                {
                    "item_index": i,
                    "dimensions": [
                        {"key": key, "score": 4, "rationale": "具体动作有对应事实"}
                        for key in ("relevance", "coverage", "evidence", "clarity")
                    ],
                    "suggestions": ["引用原文补充具体做法"],
                    "deductions": [],
                }
                for i in range(2)
            ]
        }
        return StructuredResult(
            value=schema.model_validate(values),
            llm=LlmResult(
                provider=self.provider,
                model=self.model,
                text="",
                input_tokens=20,
                output_tokens=40,
                latency_ms=1,
                is_stub=False,
            ),
        )

    monkeypatch.setattr(LlmClient, "complete_json", complete)
    response = await client.post(f"{API}/score", json={"max_rounds": 0, "persist": False})
    assert response.status_code == 200, response.text
    data = response.json()["loop"]
    assert len(seen) == 1 and seen[0][0] == settings.judge_base_url
    assert "deepseek" not in seen[0][1] and '"round"' not in seen[0][1]
    assert data["best"]["result"]["judge_provider"] == "openai"
    assert data["best"]["result"]["usage_tokens"] == 60
    assert data["best"]["cost"] == 3


async def test_real_rewriter_receives_only_weak_source_and_feedback(
    client, scoring_wiring, monkeypatch
):
    from app.agents.rewriter import ExperienceRewriter

    settings = scoring_wiring["settings"]
    settings.llm_provider = "openai-compatible"
    calls = []

    async def rewrite(self, profile, source, *, feedback=None):
        calls.append((source["id"], feedback))
        return StructuredResult(
            value=RewrittenExperience.model_validate(
                {
                    "bullets": [
                        {"text": source["raw_description"], "evidence": [source["raw_description"]]}
                    ]
                }
            ),
            llm=LlmResult(
                provider="openai-compatible",
                model="test",
                text="",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                is_stub=False,
            ),
        )

    monkeypatch.setattr(ExperienceRewriter, "rewrite", rewrite)
    response = await client.post(f"{API}/score", json={"persist": False, "threshold": 95})
    assert response.status_code == 200, response.text
    assert [call[0] for call in calls] == [scoring_wiring["weak"]["id"]]
    assert "suggestions" in calls[0][1]
    assert response.json()["loop"]["best"]["cost"] == 9


async def test_historical_vendor_cannot_be_bypassed_by_current_settings(client, scoring_wiring):
    settings, row = scoring_wiring["settings"], scoring_wiring["repo"].row
    settings.judge_provider, settings.judge_api_key = "openai-compatible", "not-sent"
    settings.generation_vendor, settings.judge_vendor = "deepseek", "openai"
    row["generator"] = "openai-compatible:gpt-4.1-mini"
    row["generator_vendor"] = "openai"
    same = await client.post(f"{API}/score", json={"persist": False})
    assert same.status_code == 400 and "原始简历" in same.text
    row["generator_vendor"] = None
    unknown = await client.post(f"{API}/score", json={"persist": False})
    assert unknown.status_code == 400 and "缺少生成厂商" in unknown.text
    row["generator"] = "stub:old-model"
    mixed = await client.post(f"{API}/score", json={"persist": False})
    assert mixed.status_code == 400 and "缺少生成厂商" in mixed.text


async def test_factual_violations_never_save_an_exportable_best(client, scoring_wiring):
    row = scoring_wiring["repo"].row
    row["sections"][0]["entries"][0]["bullets"][0]["text"] = "使用 Python 提升接口吞吐 300%"
    response = await client.post(f"{API}/score", json={"threshold": 0, "max_rounds": 0})
    assert response.status_code == 400 and "安全版本" in response.text
    assert not scoring_wiring["repo"].records
