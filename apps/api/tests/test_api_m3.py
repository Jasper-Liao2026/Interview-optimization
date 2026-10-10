"""M3 API contracts with real routes and services; external storage remains offline."""

from uuid import uuid4

import pytest

from app.schemas.jd import JobProfile
from tests.fakes import API, DEV_USER, experience_row


def profile(**updates):
    return JobProfile(
        required_skills=["Python", "PostgreSQL"],
        nice_to_have=["Docker"],
        keywords=[],
        implicit_preferences=[],
        responsibilities=[],
        **updates,
    )


async def save_jd(wiring, parsed=None):
    return await wiring["jds"].create(
        DEV_USER,
        raw_text="招聘后端工程师，熟悉 Python 与 PostgreSQL。",
        title="后端工程师",
        company="示例公司",
        parsed=parsed or profile().model_dump(),
        parser_model="fixture-model",
    )


async def test_jd_management_roundtrip(client, api_wiring):
    saved = []
    for title in ("后端岗位", "前端岗位"):
        response = await client.post(
            f"{API}/jd/parse",
            json={
                "raw_text": "招聘工程师，需要熟悉软件开发和测试流程。",
                "title": title,
            },
        )
        assert response.status_code == 200
        saved.append(response.json()["jd_id"])
    listing = (await client.get(f"{API}/jd")).json()
    assert listing["total"] == 2
    assert {row["id"] for row in listing["items"]} == set(saved)
    before = (await client.get(f"{API}/jd/{saved[0]}")).json()
    edited = await client.put(f"{API}/jd/{saved[0]}", json={"title": "新标题", "company": "新公司"})
    assert edited.status_code == 200
    assert edited.json()["title"] == "新标题"
    assert edited.json()["company"] == "新公司"
    assert edited.json()["raw_text"] == before["raw_text"]
    assert edited.json()["parsed"] == before["parsed"]
    assert edited.json()["source_type"] == "text"
    assert (await client.delete(f"{API}/jd/{saved[0]}")).status_code == 204
    assert (await client.get(f"{API}/jd/{saved[0]}")).status_code == 404
    assert (await client.delete(f"{API}/jd/{saved[0]}")).status_code == 404
    assert (await client.get(f"{API}/jd")).json()["total"] == 1


async def test_saved_jd_generation_reuses_profile_without_parsing(client, api_wiring, monkeypatch):
    row = await save_jd(api_wiring)

    async def forbidden(*args, **kwargs):
        pytest.fail("Saved JD generation must not parse the JD again")

    monkeypatch.setattr(api_wiring["service"]._parser, "parse", forbidden)
    response = await client.post(f"{API}/resumes/generate", json={"jd_id": str(row["id"])})
    assert response.status_code == 200
    assert response.json()["profile"] == row["parsed"]
    assert response.json()["resume"]["jd_id"] == str(row["id"])
    assert len(api_wiring["jds"].created) == 1


@pytest.mark.parametrize(
    "body", [{}, {"jd_text": "招聘软件开发工程师，熟悉软件工程。", "jd_id": str(uuid4())}]
)
async def test_generation_requires_one_jd_source(client, api_wiring, body):
    assert (await client.post(f"{API}/resumes/generate", json=body)).status_code == 422


async def test_missing_jd_is_explicit(client, api_wiring):
    missing = uuid4()
    assert (await client.put(f"{API}/jd/{missing}", json={})).status_code == 404
    assert (await client.post(f"{API}/jd/{missing}/match", json={})).status_code == 404
    assert (
        await client.post(f"{API}/resumes/generate", json={"jd_id": str(missing)})
    ).status_code == 400


async def test_unparsed_saved_jd_is_rejected(client, api_wiring):
    row = await save_jd(api_wiring)
    row["parsed"] = None
    assert (await client.post(f"{API}/jd/{row['id']}/match", json={})).status_code == 400
    response = await client.post(f"{API}/resumes/generate", json={"jd_id": str(row["id"])})
    assert response.status_code == 400


async def test_matching_full_matrix_evidence_gaps_and_stub_disclosure(client, api_wiring):
    row = await save_jd(api_wiring)
    facts = experience_row(
        raw_description="用 Python 开发接口，使用 Postgres 存储订单。",
        skill_tags=[],
        highlights=[],
        metrics=[],
        variants=[],
    )
    unrelated = experience_row(
        org="活动",
        role="策划",
        raw_description="组织校园活动。",
        skill_tags=[],
        highlights=[],
        metrics=[],
        variants=[],
    )
    api_wiring["experiences"].rows = [facts, unrelated]
    response = await client.post(f"{API}/jd/{row['id']}/match", json={"candidate_limit": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["is_stub"] is True
    assert any("哈希" in warning for warning in body["warnings"])
    assert len(body["items"]) == 2
    assert len(body["requirements"]) == 3
    assert all(len(item["matches"]) == 3 for item in body["items"])
    matched = next(item for item in body["items"] if item["experience_id"] == str(facts["id"]))
    assert [cell["status"] for cell in matched["matches"][:2]] == ["covered", "covered"]
    assert all(
        cell["evidence"]
        and all(fragment in facts["raw_description"] for fragment in cell["evidence"])
        for cell in matched["matches"][:2]
    )
    docker = next(r for r in body["requirements"] if r["text"] == "Docker")
    assert docker["id"] in body["uncovered_requirement_ids"]


@pytest.mark.parametrize("empty", ["library", "requirements"])
async def test_empty_matching_returns_honest_matrix(client, api_wiring, empty):
    row = await save_jd(api_wiring)
    if empty == "library":
        api_wiring["experiences"].rows = []
    else:
        row["parsed"] = JobProfile(
            required_skills=[],
            nice_to_have=[],
            keywords=[],
            implicit_preferences=[],
            responsibilities=[],
        ).model_dump()
    response = await client.post(f"{API}/jd/{row['id']}/match", json={})
    assert response.status_code == 200
    body = response.json()
    if empty == "library":
        assert body["items"] == []
        assert len(body["uncovered_requirement_ids"]) == len(body["requirements"])
    else:
        assert body["requirements"] == []
        assert body["items"][0]["matches"] == []
        assert body["items"][0]["score"] == 0
    assert api_wiring["embeddings"].store_calls == 0


@pytest.mark.parametrize("limit", [0, 101])
async def test_matching_limit_is_bounded(client, api_wiring, limit):
    row = await save_jd(api_wiring)
    response = await client.post(f"{API}/jd/{row['id']}/match", json={"candidate_limit": limit})
    assert response.status_code == 422
