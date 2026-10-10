"""M3 API + real pgvector acceptance; creates and cleans only its own IDs."""

import asyncio
import base64
import io
import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings


async def main():
    settings = Settings()
    base = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")
    conn = await asyncpg.connect(settings.database_url)
    experience_ids, jd_ids, resume_ids = [], [], []
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"PASS {label}", flush=True)

    async with httpx.AsyncClient(base_url=base + "/api/v1", timeout=120, trust_env=False) as client:

        async def call(method, path, payload=None, status=200):
            response = await client.request(method, path, json=payload)
            assert response.status_code == status, (
                f"{path}: {response.status_code} {response.text[:300]}"
            )
            return response.json() if response.content else None

        try:
            index = await conn.fetchval(
                "select indexdef from pg_indexes where indexname='idx_experience_embeddings_cosine'"
            )
            check(
                index and "hnsw" in index and "vector_cosine_ops" in index,
                "1536D HNSW cosine index",
            )
            check(
                await conn.fetchval("select value from service_meta where key='schema_version'")
                == "m3_0001",
                "M3 migration version",
            )
            initial = (await call("GET", "/jd"))["total"]
            for n in range(2):
                saved = await call(
                    "POST",
                    "/jd/parse",
                    {
                        "raw_text": "招聘后端工程师，必须熟悉 Python 和 PostgreSQL，"
                        "Docker 经验优先。",
                        "title": f"M3 verification {uuid4()}-{n}",
                    },
                )
                jd_ids.append(UUID(saved["jd_id"]))
            check((await call("GET", "/jd"))["total"] == initial + 2, "multiple saved JD list")
            await call(
                "POST",
                "/jd/parse",
                {"raw_text": "招聘前端工程师，熟悉 React 开发。", "persist": False},
            )
            check((await call("GET", "/jd"))["total"] == initial + 2, "persist=false leaves no JD")
            jd = await call("GET", f"/jd/{jd_ids[0]}")
            updated = await call(
                "PUT", f"/jd/{jd_ids[0]}", {"title": "M3 edited", "company": "local"}
            )
            check(
                updated["parsed"] == jd["parsed"]
                and updated["raw_text"] == jd["raw_text"]
                and updated["company"] == "local",
                "JD metadata edit preserves snapshot",
            )
            # Deterministic fixture isolates retrieval from the configured parser quality.
            profile = {
                "title": "backend",
                "company": None,
                "seniority": None,
                "required_skills": ["Python", "PostgreSQL", "M3AbsentSkill"],
                "nice_to_have": [],
                "business_domain": None,
                "keywords": [],
                "implicit_preferences": [],
                "responsibilities": [],
            }
            import json

            await conn.execute(
                "update job_descriptions set parsed=$2::jsonb where id=$1",
                jd_ids[0],
                json.dumps(profile),
            )
            payload = {
                "kind": "project",
                "org": "M3 local verification",
                "role": "developer",
                "raw_description": "使用 Python 开发订单接口，使用 Postgres 存储订单。",
                "skill_tags": [],
                "highlights": [],
                "metrics": [],
                "variants": [{"direction": "other", "text": "M3AbsentSkill", "note": None}],
            }
            for description in [payload["raw_description"], "组织校园活动。"]:
                row = await call(
                    "POST", "/experiences", {**payload, "raw_description": description}, status=201
                )
                experience_ids.append(UUID(row["id"]))
            result = await call("POST", f"/jd/{jd_ids[0]}/match", {"candidate_limit": 1})
            mine = next(i for i in result["items"] if i["experience_id"] == str(experience_ids[0]))
            check(
                all(len(i["matches"]) == 3 for i in result["items"]) and len(result["items"]) >= 2,
                "full experience x requirement matrix",
            )
            check(
                [c["status"] for c in mine["matches"][:2]] == ["covered", "covered"]
                and all(c["evidence"] for c in mine["matches"][:2]),
                "facts and Postgres alias evidence",
            )
            check(
                mine["matches"][2]["status"] != "covered"
                and result["requirements"][2]["id"] in result["uncovered_requirement_ids"],
                "variants excluded and gap visible",
            )
            check(
                any(
                    c["semantic_similarity"] is not None
                    for i in result["items"]
                    for c in i["matches"]
                ),
                "actual pgvector cosine retrieval",
            )
            cache = await conn.fetchrow(
                "select source_hash, model_key, created_at, vector_dims(embedding) as dims "
                "from experience_embeddings where experience_id=$1",
                experience_ids[0],
            )
            check(cache and cache["dims"] == 1536, "experience embedding persisted")
            await call("POST", f"/jd/{jd_ids[0]}/match", {})
            check(
                await conn.fetchval(
                    "select created_at from experience_embeddings where experience_id=$1",
                    experience_ids[0],
                )
                == cache["created_at"],
                "repeat match reuses cache",
            )
            await call("PUT", f"/experiences/{experience_ids[0]}", {**payload, "variants": []})
            check(
                await conn.fetchval(
                    "select created_at from experience_embeddings where experience_id=$1",
                    experience_ids[0],
                )
                == cache["created_at"],
                "wording edit preserves fact cache",
            )
            await call(
                "PUT",
                f"/experiences/{experience_ids[0]}",
                {**payload, "raw_description": payload["raw_description"] + "开发 FastAPI 服务。"},
            )
            check(
                not await conn.fetchval(
                    "select exists(select 1 from experience_embeddings where experience_id=$1)",
                    experience_ids[0],
                ),
                "fact edit trigger invalidates cache",
            )
            await call("POST", f"/jd/{jd_ids[0]}/match", {})
            check(
                await conn.fetchval(
                    "select source_hash from experience_embeddings where experience_id=$1",
                    experience_ids[0],
                )
                != cache["source_hash"],
                "updated facts rebuild vector",
            )
            generated = await call(
                "POST",
                "/resumes/generate",
                {"jd_id": str(jd_ids[0]), "experience_ids": [str(experience_ids[0])]},
            )
            resume_ids.append(UUID(generated["resume"]["id"]))
            check(
                generated["profile"] == profile
                and (await call("GET", "/jd"))["total"] == initial + 2,
                "generate reuses saved JD without duplicate",
            )
            await call("DELETE", f"/jd/{jd_ids[0]}", status=204)
            await call("GET", f"/jd/{jd_ids[0]}", status=404)
            preserved = await call("GET", f"/resumes/{resume_ids[0]}")
            check(preserved["jd_id"] is None, "deleting JD preserves resume")
            await call("DELETE", f"/experiences/{experience_ids[0]}", status=204)
            check(
                not await conn.fetchval(
                    "select exists(select 1 from experience_embeddings where experience_id=$1)",
                    experience_ids[0],
                ),
                "deleting experience cascades embedding",
            )
            await call(
                "POST",
                "/jd/parse-image",
                {"image_data_url": "data:image/png;base64,bad"},
                status=422,
            )
            check(True, "invalid screenshot rejected")
            if (settings.vision_provider or settings.llm_provider) == "stub" or generated[
                "is_stub"
            ]:
                image = io.BytesIO()
                Image.new("RGB", (8, 8), "white").save(image, "PNG")
                data_url = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()
                response = await client.post("/jd/parse-image", json={"image_data_url": data_url})
                # An independent vision provider may be active even if generation is stub.
                if response.status_code == 503:
                    check(True, "stub refuses visual recognition")
                else:
                    print("SKIP stub vision gate (server has independent vision provider)")
        finally:
            for table, ids in [
                ("resumes", resume_ids),
                ("job_descriptions", jd_ids),
                ("experiences", experience_ids),
            ]:
                await conn.execute(f"delete from {table} where id=any($1::uuid[])", ids)
            remaining = 0
            for table, ids in [
                ("resumes", resume_ids),
                ("job_descriptions", jd_ids),
                ("experiences", experience_ids),
            ]:
                remaining += await conn.fetchval(
                    f"select count(*) from {table} where id=any($1::uuid[])", ids
                )
            check(remaining == 0, "verification data cleaned")
            await conn.close()
    print(f"M3: {checks} checks passed; live vision/semantic quality: run eval:m3 separately")


if __name__ == "__main__":
    asyncio.run(main())
