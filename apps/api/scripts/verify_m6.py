"""Real Postgres/checkpoint/Chromium acceptance for structured editing."""

from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.checkpoint import RunConflictError
from app.config import Settings, get_settings
from app.db import Database, get_database
from app.main import create_app
from app.observability import Observability, get_observability
from app.pdf import pdf_page_count
from app.repositories import JobDescriptionRepository, ResumeRepository
from app.repositories.editing_repo import EditingRepository
from app.schemas import JobProfile

MIGRATION = Path(__file__).resolve().parents[3] / (
    "supabase/migrations/20261014000000_m6_editing.sql"
)


async def main() -> None:
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
    user = UUID(settings.dev_user_id)
    source_ids, resume_ids, edit_runs = [], [], []
    jd_id, generation_id = uuid4(), uuid4()
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"PASS {label}", flush=True)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://acceptance/api/v1",
        timeout=90,
    ) as client:

        async def call(method, path, payload=None, status=200):
            response = await client.request(method, path, json=payload)
            assert response.status_code == status, response.text[:800]
            return response.json() if response.content else None

        try:
            for index in range(2):
                source = await call(
                    "POST",
                    "/experiences",
                    {
                        "kind": "project",
                        "org": f"M6 acceptance {uuid4()}",
                        "role": "开发",
                        "raw_description": (
                            "使用 Python 和 FastAPI 开发接口服务，并编写接口自动化测试。"
                        ),
                        "skill_tags": ["Python", "FastAPI"],
                        "highlights": ["编写接口自动化测试"],
                        "metrics": [],
                        "variants": [],
                        "sort_order": index,
                    },
                    status=201,
                )
                source_ids.append(UUID(source["id"]))
            profile = JobProfile(
                title="后端开发",
                required_skills=["Python", "FastAPI"],
                nice_to_have=[],
                keywords=["接口"],
                implicit_preferences=[],
                responsibilities=["接口开发"],
            )
            await JobDescriptionRepository(database).create(
                user,
                jd_id=jd_id,
                raw_text="招聘 Python 和 FastAPI 后端开发工程师",
                title=profile.title,
                company=None,
                parsed=profile.model_dump(mode="json"),
                parser_model="acceptance-fixture",
            )
            generated = await call(
                "POST",
                "/resumes/generate",
                {
                    "run_id": str(generation_id),
                    "jd_id": str(jd_id),
                    "experience_ids": [str(x) for x in source_ids],
                },
            )
            resume_id = UUID(generated["resume"]["id"])
            resume_ids.append(resume_id)
            base = f"/resumes/{resume_id}"
            initial = await call("GET", f"{base}/editor")
            check(
                initial["revision"] == 0 and len(initial["history"]) == 1,
                "initial snapshot recorded",
            )
            await call("POST", f"/resumes/runs/{generation_id}/resume", {}, status=409)
            check(True, "generation cannot overwrite opened editor revision zero")
            # Editor initialization must also win when generation waits on its row lock.
            resumes = ResumeRepository(database)
            source = initial["resume"]
            create_args = {
                key: source[key]
                for key in (
                    "title",
                    "template",
                    "header",
                    "sections",
                    "generator",
                    "generator_vendor",
                )
            }
            concurrent_resume = await resumes.create(user, jd_id=jd_id, **create_args)
            concurrent_id = concurrent_resume["id"]
            resume_ids.append(concurrent_id)
            async with database.connection() as conn, conn.transaction():
                await EditingRepository(database)._locked(conn, user, concurrent_id)
                competing_generation = asyncio.create_task(
                    resumes.create(user, jd_id=jd_id, resume_id=concurrent_id, **create_args)
                )
                try:
                    await asyncio.wait_for(asyncio.shield(competing_generation), timeout=0.1)
                except TimeoutError:
                    pass
                else:
                    raise AssertionError("generation must wait for editor row lock")
            try:
                await competing_generation
            except RunConflictError:
                check(True, "generation cannot race editor initialization snapshot")
            else:
                raise AssertionError("generation overwrote concurrently initialized editor")
            draft = {
                key: copy.deepcopy(initial["resume"][key])
                for key in ("title", "header", "sections")
            }
            original = copy.deepcopy(draft)
            draft["header"]["name"] = "M6 验收用户"
            draft["sections"][0]["entries"][0]["bullets"][0]["text"] = "手动精简后的描述"
            saved = await call("PUT", f"{base}/editor", {**draft, "expected_revision": 0})
            check(
                saved["revision"] == 1 and len(saved["history"]) == 2,
                "edit saved atomically as revision one",
            )
            check(
                not saved["resume"]["sections"][0]["entries"][0]["bullets"][0]["evidence"],
                "server clears obsolete manual evidence",
            )
            check(saved["history"][-1]["draft"] == original, "initial snapshot immutable")
            await call("PUT", f"{base}/editor", {**draft, "expected_revision": 0}, status=409)
            check(True, "stale manual save rejected")
            # Competing saves from two clients must produce exactly one new revision.
            payload = {**draft, "expected_revision": 1}
            competitors = await asyncio.gather(
                client.put(f"{base}/editor", json=payload),
                client.put(f"{base}/editor", json=payload),
            )
            check(
                sorted(r.status_code for r in competitors) == [200, 409],
                "concurrent saves have one winner",
            )
            editor = await call("GET", f"{base}/editor")
            req = {
                "expected_revision": editor["revision"],
                "section_index": 0,
                "entry_index": 0,
                "instruction": "简洁突出接口测试",
            }
            proposal = await call("POST", f"{base}/ai-edits", req)
            edit_runs.append(UUID(proposal["run_id"]))
            check(
                proposal["status"] == "pending" and proposal["is_stub"],
                "AI pauses for human confirmation and labels demo",
            )
            unchanged = await call("GET", f"{base}/editor")
            check(unchanged == editor, "proposal never mutates resume")
            # Every HTTP dependency creates a new service and reopens Postgres saver.
            recovered = await call("GET", f"{base}/ai-edits/{proposal['run_id']}")
            check(recovered == proposal, "pending proposal recovered through new service")
            applied = await call(
                "POST", f"{base}/ai-edits/{proposal['run_id']}/decision", {"accept": True}
            )
            check(applied["status"] == "applied", "Postgres interrupt resumes on confirmation")
            ai_sections = applied["editor"]["resume"]["sections"]
            old_sections = editor["resume"]["sections"]
            check(
                ai_sections[0]["entries"][1] == old_sections[0]["entries"][1],
                "unselected sibling untouched",
            )
            check(
                ai_sections[0]["entries"][0]["bullets"] == proposal["proposed_bullets"],
                "only target bullets replaced",
            )
            repeat = await call(
                "POST", f"{base}/ai-edits/{proposal['run_id']}/decision", {"accept": True}
            )
            check(
                repeat["editor"]["revision"] == applied["editor"]["revision"],
                "confirmation retry idempotent",
            )
            req["expected_revision"] = applied["editor"]["revision"]
            rejected = await call("POST", f"{base}/ai-edits", req)
            edit_runs.append(UUID(rejected["run_id"]))
            rejection = await call(
                "POST", f"{base}/ai-edits/{rejected['run_id']}/decision", {"accept": False}
            )
            check(
                rejection["editor"]["revision"] == req["expected_revision"],
                "rejection leaves revision untouched",
            )
            stale = await call("POST", f"{base}/ai-edits", req)
            edit_runs.append(UUID(stale["run_id"]))
            initial_revision = initial["history"][0]["id"]
            restored = await call(
                "POST",
                f"{base}/revisions/{initial_revision}/restore",
                {"expected_revision": req["expected_revision"]},
            )
            check(
                all(restored["resume"][key] == original[key] for key in original),
                "arbitrary historical version restored",
            )
            check(
                restored["revision"] > req["expected_revision"],
                "restore appends revision without deleting history",
            )
            await call(
                "POST", f"{base}/ai-edits/{stale['run_id']}/decision", {"accept": True}, status=409
            )
            check(True, "stale AI confirmation cannot overwrite new revision")
            await call("POST", f"{base}/ai-edits/{stale['run_id']}/decision", {"accept": False})
            await call("GET", f"/resumes/{uuid4()}/ai-edits/{proposal['run_id']}", status=404)
            check(True, "wrong resume cannot read proposal")
            check(
                await EditingRepository(database).get_run(
                    uuid4(), resume_id, UUID(proposal["run_id"])
                )
                is None,
                "proposal scoped to owner",
            )
            # Multi-page PDF preview from an unsaved draft, then saved/exported same template.
            long_draft = copy.deepcopy(original)
            long_draft["sections"][0]["entries"] = [
                copy.deepcopy(original["sections"][0]["entries"][0]) for _ in range(35)
            ]
            for entry in long_draft["sections"][0]["entries"]:
                entry["bullets"] = [
                    {
                        "text": (
                            "中文分页验收：描述接口开发、测试与协作过程，核对中英文混排和分页效果。"
                        ),
                        "evidence": [],
                    }
                    for _ in range(4)
                ]
            preview = await client.post(f"{base}/preview", json=long_draft)
            assert preview.status_code == 200, preview.text[:800]
            page_count = pdf_page_count(preview.content)
            check(
                preview.content.startswith(b"%PDF") and page_count > 1,
                f"unsaved draft renders genuine paginated PDF ({page_count} pages)",
            )
            after_preview = await call("GET", f"{base}/editor")
            check(after_preview == restored, "PDF preview has no content or version side effects")
            await call(
                "PUT", f"{base}/editor", {**long_draft, "expected_revision": restored["revision"]}
            )
            exported = await client.get(f"{base}/pdf")
            check(
                exported.status_code == 200 and pdf_page_count(exported.content) == page_count,
                "preview/export pagination identical",
            )
            # Score historical result remains stable after its best resume is edited.
            await call(
                "POST",
                f"{base}/revisions/{initial_revision}/restore",
                {"expected_revision": restored["revision"] + 1},
            )
            score = await call("POST", f"{base}/score", {"max_rounds": 0})
            best_id = UUID(score["resume"]["id"])
            resume_ids.append(best_id)
            # Emulate a pre-M6 score record and verify the idempotent upgrade backfill.
            async with database.connection() as conn:
                await conn.execute(
                    "update resume_score_runs set result=result-'resume_snapshot' where id=$1",
                    UUID(score["score_run_id"]),
                )
                await conn.execute(await asyncio.to_thread(MIGRATION.read_text, encoding="utf-8"))
            check(
                await call("GET", f"{base}/scores/{score['score_run_id']}") == score,
                "M6 migration preserves legacy M5 scoring snapshot",
            )
            best_editor = await call("GET", f"/resumes/{best_id}/editor")
            best_draft = {key: best_editor["resume"][key] for key in original}
            best_draft["title"] = "修改后的标题"
            await call("PUT", f"/resumes/{best_id}/editor", {**best_draft, "expected_revision": 0})
            history = await call("GET", f"{base}/scores/{score['score_run_id']}")
            check(history == score, "M5 scoring history immutable after M6 editing")
        finally:
            async with database.connection() as conn:
                await conn.execute("delete from resumes where id=any($1::uuid[])", resume_ids)
                await conn.execute("delete from generation_runs where id=$1", generation_id)
                threads = [str(generation_id), *[f"edit:{run_id}" for run_id in edit_runs]]
                for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    await conn.execute(
                        f"delete from {table} where thread_id=any($1::text[])", threads
                    )
                await conn.execute("delete from job_descriptions where id=$1", jd_id)
                await conn.execute("delete from experiences where id=any($1::uuid[])", source_ids)
            await database.close()
    print(f"M6: {checks} real Postgres/checkpoint/Chromium checks passed; model explicitly stub")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
