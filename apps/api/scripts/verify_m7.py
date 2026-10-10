"""Real Postgres/checkpoint/Chromium acceptance for M7, without model keys."""

from __future__ import annotations

import asyncio
import copy
import io
import sys
import zipfile
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings, get_settings
from app.db import Database, get_database
from app.main import create_app
from app.observability import Observability, get_observability
from app.pdf import pdf_page_count


async def main():
    settings = Settings(
        llm_provider="stub",
        judge_provider="stub",
        langfuse_public_key=None,
        langfuse_secret_key=None,
    )
    database = Database(settings)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_observability] = lambda: Observability(settings)
    experience_ids, jd_ids, resume_ids = [], [], []
    batch_id = uuid4()
    missing_jd = uuid4()
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"PASS {label}", flush=True)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://acceptance/api/v1", timeout=180
    ) as client:

        async def call(method, path, body=None, status=200):
            response = await client.request(method, path, json=body)
            assert response.status_code == status, response.text[:800]
            return response.json()

        try:
            templates = await call("GET", "/resumes/templates")
            check(
                {t["id"] for t in templates["items"]} == {"classic", "modern"},
                "two discoverable templates",
            )
            source = await call(
                "POST",
                "/experiences",
                {
                    "kind": "project",
                    "org": f"M7 验收 {uuid4()}",
                    "role": "开发",
                    "raw_description": (
                        "使用 Python 和 FastAPI 开发接口，编写自动化测试并维护 PDF 导出。"
                    ),
                    "skill_tags": ["Python", "FastAPI"],
                    "highlights": ["编写自动化测试"],
                },
                status=201,
            )
            experience_ids.append(UUID(source["id"]))
            for title in ("M7后端岗位", "M7平台岗位"):
                jd = await call(
                    "POST",
                    "/jd/parse",
                    {
                        "raw_text": "后端开发工程师，熟悉 Python 和 FastAPI，负责接口开发与测试。",
                        "title": title,
                        "persist": True,
                    },
                )
                jd_ids.append(UUID(jd["jd_id"]))
            payload = {
                "batch_id": str(batch_id),
                "jd_ids": [str(i) for i in jd_ids] + [str(missing_jd)],
                "experience_ids": [str(i) for i in experience_ids],
            }
            batch = await call("POST", "/resumes/batch-generate", payload)
            resume_ids.extend(UUID(i["resume"]["id"]) for i in batch["items"] if i["resume"])
            check(
                [i["status"] for i in batch["items"]] == ["completed", "completed", "failed"],
                "multi-JD generation isolates missing job and preserves order",
            )
            check(len(set(resume_ids)) == 2, "each JD gets its own persisted resume")
            check(
                all(any("stub" in w.lower() for w in i["warnings"]) for i in batch["items"][:2]),
                "stub model explicitly labeled",
            )
            repeated = await call("POST", "/resumes/batch-generate", payload)
            check(
                [i["run_id"] for i in repeated["items"]] == [i["run_id"] for i in batch["items"]]
                and [i["resume"]["id"] for i in repeated["items"][:2]]
                == [str(i) for i in resume_ids],
                "same batch request does not duplicate resumes",
            )
            listed = await call("GET", "/resumes")
            check(
                set(resume_ids).issubset({UUID(i["id"]) for i in listed["items"]}),
                "generated resumes discoverable for filtering",
            )
            first = resume_ids[0]
            base = f"/resumes/{first}"
            editor = await call("GET", f"{base}/editor")
            draft = {k: editor["resume"][k] for k in ("title", "header", "sections")}
            long_draft = copy.deepcopy(draft)
            entry = long_draft["sections"][0]["entries"][0]
            long_draft["sections"][0]["entries"] = [copy.deepcopy(entry) for _ in range(22)]
            for index, item in enumerate(long_draft["sections"][0]["entries"]):
                item["org"] = f"M7中英文分页验证 {index}"
                item["bullets"] = [
                    {"text": "中文排版与 Python FastAPI 开发测试。" * 6, "evidence": []}
                    for _ in range(4)
                ]
            saved = await call("PUT", f"{base}/editor", {**long_draft, "expected_revision": 0})
            for template in ("classic", "modern"):
                html = await client.get(f"{base}/html?template={template}")
                check(
                    html.status_code == 200 and f'data-template="{template}"' in html.text,
                    f"{template} HTML uses selected template",
                )
                preview = await client.post(f"{base}/preview?template={template}", json=long_draft)
                pdf = await client.get(f"{base}/pdf?template={template}")
                pages = pdf_page_count(preview.content)
                check(
                    preview.status_code == pdf.status_code == 200
                    and pages > 1
                    and pages == pdf_page_count(pdf.content),
                    f"{template} long Chinese preview/export share pagination ({pages} pages)",
                )
            after = await call("GET", f"{base}/editor")
            check(
                after["revision"] == saved["revision"] and after["resume"]["template"] == "classic",
                "template selection never edits resume revisions",
            )
            retried = await call("POST", "/resumes/batch-generate", payload)
            current = await call("GET", f"{base}/editor")
            check(
                retried["items"][0]["status"] == "completed"
                and current["resume"]["sections"] == saved["resume"]["sections"],
                "repeating batch after manual editing preserves edited content",
            )
            changed = await call(
                "POST", "/resumes/batch-generate", {**payload, "experience_ids": []}
            )
            check(
                all(
                    i["status"] == "failed" and "run_id" in i["error"] for i in changed["items"][:2]
                ),
                "same batch with changed sources rejects conflicting run requests",
            )
            archive = await client.post(
                "/resumes/export",
                json={"resume_ids": [str(i) for i in resume_ids], "template": "modern"},
            )
            check(
                archive.status_code == 200 and archive.headers["content-type"] == "application/zip",
                "selected resumes download as ZIP",
            )
            with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
                check(
                    len(zipped.namelist()) == 2 and len(set(zipped.namelist())) == 2,
                    "ZIP keeps unique filenames for duplicate titles",
                )
                check(
                    all(zipped.read(name).startswith(b"%PDF") for name in zipped.namelist()),
                    "every ZIP member is a Chromium PDF",
                )
                check(
                    pdf_page_count(zipped.read(zipped.namelist()[0]))
                    == pdf_page_count(preview.content),
                    "ZIP modern pagination equals selected preview",
                )
            await call(
                "POST", "/resumes/export", {"resume_ids": [str(first)], "template": "missing"}, 400
            )
            check(True, "unknown template rejected")
            await call("POST", "/resumes/export", {"resume_ids": [str(first), str(uuid4())]}, 404)
            check(True, "missing resume rejects whole archive")
            await call("POST", "/resumes/export", {"resume_ids": [str(first), str(first)]}, 422)
            check(True, "duplicate export selection rejected")
            await call("POST", "/resumes/batch-generate", {"jd_ids": []}, 422)
            check(True, "empty batch rejected")
        finally:
            async with database.connection() as conn:
                runs = [uuid5(batch_id, str(i)) for i in [*jd_ids, missing_jd]]
                await conn.execute("delete from resumes where id=any($1::uuid[])", resume_ids)
                await conn.execute("delete from generation_runs where id=any($1::uuid[])", runs)
                for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    await conn.execute(
                        f"delete from {table} where thread_id=any($1::text[])",
                        [str(i) for i in runs],
                    )
                await conn.execute("delete from job_descriptions where id=any($1::uuid[])", jd_ids)
                await conn.execute(
                    "delete from experiences where id=any($1::uuid[])", experience_ids
                )
            await database.close()
    print(f"M7: {checks} real Postgres/checkpoint/Chromium checks passed; model explicitly stub")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
