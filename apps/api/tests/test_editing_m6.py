"""M6 public API with real service, rewriter and interrupt graph, isolated storage."""

from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.agents.checkpoint import GenerationRunStore, RunConflictError
from app.agents.editing_graph import editing_graph
from app.agents.rewriter import ExperienceRewriter
from app.config import Settings
from app.deps import get_current_user_id, get_editing_service, get_pdf_exporter
from app.llm import LlmClient
from app.repositories.editing_repo import EditingNotFoundError, draft_of
from app.services.editing import ResumeEditingService
from tests.fakes import (
    API,
    DEV_USER,
    FakeExperienceRepository,
    FakeJobDescriptionRepository,
    FakePdfExporter,
    experience_row,
    resume_row,
)


class MemoryEditingRepository:
    """Ownership and CAS semantics; mutations never alias stored revision drafts."""

    def __init__(self, row):
        self.row = copy.deepcopy(row)
        self.history = []
        self.runs = {}
        self.saves = 0
        self._snapshot("初始版本")

    def _owned(self, user_id, resume_id):
        if self.row["user_id"] != user_id or self.row["id"] != resume_id:
            raise EditingNotFoundError("简历不存在")

    def _snapshot(self, reason):
        self.history.insert(
            0,
            {
                "id": uuid4(),
                "revision": self.row["edit_revision"],
                "reason": reason,
                "draft": draft_of(self.row),
                "provenance": {key: self.row.get(key) for key in ("generator", "generator_vendor")},
                "created_at": datetime.now(UTC),
            },
        )

    async def read(self, user_id, resume_id):
        self._owned(user_id, resume_id)
        return copy.deepcopy(
            {"resume": self.row, "revision": self.row["edit_revision"], "history": self.history}
        )

    async def revision(self, user_id, resume_id, revision_id):
        self._owned(user_id, resume_id)
        for revision in self.history:
            if revision["id"] == revision_id:
                return copy.deepcopy(revision)
        raise EditingNotFoundError("历史版本不存在")

    async def save(
        self,
        user_id,
        resume_id,
        expected_revision,
        draft,
        reason,
        provenance=None,
        *,
        run_id=None,
        run_payload=None,
    ):
        self._owned(user_id, resume_id)
        if self.row["edit_revision"] != expected_revision:
            raise RunConflictError("简历已被修改")
        self.row.update(copy.deepcopy(draft))
        self.row.update(provenance or {"generator": "manual", "generator_vendor": None})
        self.row["status"] = "draft"
        self.row["edit_revision"] += 1
        self.saves += 1
        self._snapshot(reason)
        if run_id is not None:
            self.runs[(user_id, resume_id, run_id)] = copy.deepcopy(run_payload)

    async def get_run(self, user_id, resume_id, run_id):
        return copy.deepcopy(self.runs.get((user_id, resume_id, run_id)))

    async def save_run(self, user_id, resume_id, run_id, payload):
        self._owned(user_id, resume_id)
        self.runs[(user_id, resume_id, run_id)] = copy.deepcopy(payload)


class RecordingExporter(FakePdfExporter):
    async def render(self, html, *, timeout_s=None):
        self.html = html
        return await super().render(html, timeout_s=timeout_s)


@pytest.fixture
def editing_wiring(app):
    settings = Settings(_env_file=None, llm_provider="stub")
    sources = [experience_row(org="首个项目"), experience_row(org="第二个项目")]
    row = resume_row()
    row.update(edit_revision=0, jd_id=uuid4(), generator_vendor=None)
    first = row["sections"][0]["entries"][0]
    first.update(experience_id=str(sources[0]["id"]), org=sources[0]["org"])
    second = copy.deepcopy(first)
    second.update(experience_id=str(sources[1]["id"]), org=sources[1]["org"])
    second["bullets"] = [{"text": "第二条独立要点", "evidence": []}]
    row["sections"][0]["entries"].append(second)
    repository = MemoryEditingRepository(row)
    experiences = FakeExperienceRepository(sources)
    jobs = FakeJobDescriptionRepository(
        [
            {
                "id": row["jd_id"],
                "user_id": DEV_USER,
                "parsed": {
                    "title": "后端开发",
                    "required_skills": ["Python"],
                    "nice_to_have": [],
                    "keywords": [],
                    "implicit_preferences": [],
                    "responsibilities": [],
                },
            }
        ]
    )
    rewriter = ExperienceRewriter(LlmClient(settings), max_input_chars=10000)
    runs = GenerationRunStore()
    exporter = RecordingExporter()

    def rebuild():
        service = ResumeEditingService(repository, experiences, jobs, rewriter, runs, settings)
        app.dependency_overrides[get_editing_service] = lambda: service
        return service

    service = rebuild()
    app.dependency_overrides[get_pdf_exporter] = lambda: exporter
    return {
        "repository": repository,
        "experiences": experiences,
        "jobs": jobs,
        "service": service,
        "rebuild": rebuild,
        "runs": runs,
        "rewriter": rewriter,
        "exporter": exporter,
        "url": f"{API}/resumes/{row['id']}",
    }


def save_payload(wiring, *, revision=0, title="手动更新标题"):
    return {
        **draft_of(wiring["repository"].row),
        "title": title,
        "expected_revision": revision,
    }


async def propose(client, wiring):
    response = await client.post(
        f"{wiring['url']}/ai-edits",
        json={
            "expected_revision": wiring["repository"].row["edit_revision"],
            "section_index": 0,
            "entry_index": 0,
            "instruction": "精简要点，并突出后端能力",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_manual_save_preserves_history_and_rejects_stale_revision(client, editing_wiring):
    w = editing_wiring
    original = copy.deepcopy(w["repository"].history[0])
    payload = save_payload(w)
    payload["header"]["name"] = "更新姓名"
    payload["sections"][0]["entries"][0]["bullets"][0]["text"] = "更新要点"
    saved = await client.put(f"{w['url']}/editor", json=payload)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert saved.json()["resume"]["header"]["name"] == "更新姓名"
    stale = await client.put(f"{w['url']}/editor", json={**payload, "title": "过期覆盖"})
    assert stale.status_code == 409
    assert w["repository"].row["title"] == payload["title"]
    assert w["repository"].history[-1] == original
    assert w["repository"].saves == 1


async def test_competing_saves_only_commit_once(client, editing_wiring):
    w = editing_wiring
    results = await asyncio.gather(
        client.put(f"{w['url']}/editor", json=save_payload(w, title="版本甲")),
        client.put(f"{w['url']}/editor", json=save_payload(w, title="版本乙")),
    )
    assert sorted(result.status_code for result in results) == [200, 409]
    assert w["repository"].saves == 1


async def test_restore_any_revision_creates_new_immutable_snapshot(client, editing_wiring):
    w = editing_wiring
    for revision in range(3):
        saved = await client.put(
            f"{w['url']}/editor",
            json=save_payload(w, revision=revision, title=f"标题 {revision + 1}"),
        )
        assert saved.status_code == 200
    originals = copy.deepcopy(w["repository"].history)
    for offset, target in enumerate((originals[2], originals[3], originals[0])):
        restored = await client.post(
            f"{w['url']}/revisions/{target['id']}/restore",
            json={"expected_revision": 3 + offset},
        )
        assert restored.status_code == 200
        body = restored.json()
        assert body["revision"] == 4 + offset
        assert body["resume"]["title"] == target["draft"]["title"]
        assert body["history"][0]["id"] != str(target["id"])
    assert w["repository"].history[-4:] == originals
    stale = await client.post(
        f"{w['url']}/revisions/{originals[-1]['id']}/restore",
        json={"expected_revision": 0},
    )
    assert stale.status_code == 409


async def test_preview_renders_unsaved_draft_without_mutating_resume(client, editing_wiring):
    w = editing_wiring
    original = copy.deepcopy(w["repository"].row)
    draft = draft_of(original)
    draft["header"]["name"] = "预览姓名"
    draft["sections"][0]["entries"][0]["bullets"][0]["text"] = "尚未保存的预览要点"
    response = await client.post(f"{w['url']}/preview", json=draft)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-resume-pages"] == "2"
    assert "预览姓名" in w["exporter"].html
    assert "尚未保存的预览要点" in w["exporter"].html
    assert w["repository"].row == original
    assert w["repository"].saves == 0


async def test_proposal_interrupt_survives_rebuilding_service_and_only_changes_selection(
    client, editing_wiring
):
    w = editing_wiring
    original = copy.deepcopy(w["repository"].row)
    proposal = await propose(client, w)
    assert proposal["status"] == "pending"
    assert proposal["is_stub"] is True
    assert proposal["proposed_bullets"] != proposal["original_bullets"]
    assert w["repository"].row == original
    assert w["repository"].saves == 0
    config = {"configurable": {"thread_id": f"edit:{proposal['run_id']}"}}
    async with w["runs"].checkpointer() as saver:
        state = await editing_graph(w["rewriter"], saver).aget_state(config)
    assert state.next == ("confirm",)
    assert state.tasks[0].interrupts
    restarted = w["rebuild"]()
    assert restarted is not w["service"]
    url = f"{w['url']}/ai-edits/{proposal['run_id']}"
    read = await client.get(url)
    assert read.status_code == 200
    assert read.json()["status"] == "pending"
    applied = await client.post(f"{url}/decision", json={"accept": True})
    assert applied.status_code == 200
    body = applied.json()
    assert body["status"] == "applied"
    assert body["editor"]["revision"] == 1
    expected_sections = copy.deepcopy(original["sections"])
    expected_sections[0]["entries"][0]["bullets"] = proposal["proposed_bullets"]
    assert body["editor"]["resume"]["sections"] == expected_sections
    assert body["editor"]["resume"]["header"] == original["header"]
    assert body["editor"]["resume"]["generator"] == "stub:local-edit"
    repeated = await client.post(f"{url}/decision", json={"accept": True})
    assert repeated.status_code == 200
    assert repeated.json()["editor"]["revision"] == 1
    assert w["repository"].saves == 1
    assert (await client.post(f"{url}/decision", json={"accept": False})).status_code == 409
    async with w["runs"].checkpointer() as saver:
        state = await editing_graph(w["rewriter"], saver).aget_state(config)
    assert not state.next
    assert state.values["accepted"] is True


async def test_reject_is_idempotent_and_leaves_content_unchanged(client, editing_wiring):
    w = editing_wiring
    original = copy.deepcopy(w["repository"].row)
    proposal = await propose(client, w)
    w["rebuild"]()
    url = f"{w['url']}/ai-edits/{proposal['run_id']}/decision"
    for _ in range(2):
        response = await client.post(url, json={"accept": False})
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
    assert w["repository"].row == original
    assert w["repository"].saves == 0
    assert (await client.post(url, json={"accept": True})).status_code == 409


async def test_stale_proposal_cannot_overwrite_manual_edit_and_can_be_rejected(
    client, editing_wiring
):
    w = editing_wiring
    proposal = await propose(client, w)
    saved = await client.put(f"{w['url']}/editor", json=save_payload(w))
    assert saved.status_code == 200
    url = f"{w['url']}/ai-edits/{proposal['run_id']}/decision"
    assert (await client.post(url, json={"accept": True})).status_code == 409
    assert w["repository"].saves == 1
    assert w["repository"].row["title"] == "手动更新标题"
    rejection = await client.post(url, json={"accept": False})
    assert rejection.status_code == 200
    assert rejection.json()["status"] == "rejected"


@pytest.mark.parametrize(
    "action", ["read", "save", "preview", "restore", "propose", "run", "decide"]
)
async def test_other_user_cannot_read_or_mutate_editor(app, client, editing_wiring, action):
    w = editing_wiring
    proposal = await propose(client, w)
    original = copy.deepcopy(w["repository"].row)
    app.dependency_overrides[get_current_user_id] = lambda: uuid4()
    if action == "read":
        response = await client.get(f"{w['url']}/editor")
    elif action == "save":
        response = await client.put(f"{w['url']}/editor", json=save_payload(w))
    elif action == "preview":
        response = await client.post(f"{w['url']}/preview", json=draft_of(original))
    elif action == "restore":
        revision_id = w["repository"].history[0]["id"]
        response = await client.post(
            f"{w['url']}/revisions/{revision_id}/restore", json={"expected_revision": 0}
        )
    elif action == "propose":
        response = await client.post(
            f"{w['url']}/ai-edits",
            json={
                "expected_revision": 0,
                "section_index": 0,
                "entry_index": 0,
                "instruction": "精简",
            },
        )
    elif action == "run":
        response = await client.get(f"{w['url']}/ai-edits/{proposal['run_id']}")
    else:
        response = await client.post(
            f"{w['url']}/ai-edits/{proposal['run_id']}/decision", json={"accept": True}
        )
    assert response.status_code == 404
    assert w["repository"].row == original


@pytest.mark.parametrize("missing", ["jd_link", "jd", "parsed", "source_link", "source"])
async def test_missing_source_or_job_prevents_proposal(client, editing_wiring, missing):
    w = editing_wiring
    if missing == "jd_link":
        w["repository"].row["jd_id"] = None
    elif missing == "jd":
        w["jobs"].rows = []
    elif missing == "parsed":
        w["jobs"].rows[0]["parsed"] = None
    elif missing == "source_link":
        w["repository"].row["sections"][0]["entries"][0]["experience_id"] = None
    else:
        w["experiences"].rows = []
    response = await client.post(
        f"{w['url']}/ai-edits",
        json={"expected_revision": 0, "section_index": 0, "entry_index": 0, "instruction": "精简"},
    )
    assert response.status_code == 400
    assert not w["repository"].runs
    assert w["repository"].saves == 0


async def test_ai_fact_violation_is_rejected_before_pending_proposal(client, editing_wiring):
    w = editing_wiring
    original_llm = w["rewriter"]._llm

    class FabricatingLlm:
        is_stub = False
        model = "fabricating-test"
        calls = 0

        async def complete_json(self, *args, **kwargs):
            self.calls += 1
            result = await original_llm.complete_json(*args, **kwargs)
            result.value.bullets[0].text = "使用 Redis 提升性能 999%"
            result.value.bullets[0].evidence = ["把请求 trace_id 复用为 Langfuse trace_id"]
            return result

    llm = FabricatingLlm()
    w["rewriter"]._llm = llm
    original = copy.deepcopy(w["repository"].row)
    response = await client.post(
        f"{w['url']}/ai-edits",
        json={
            "expected_revision": 0,
            "section_index": 0,
            "entry_index": 0,
            "instruction": "新增 Redis 和 999% 性能成果",
        },
    )
    assert response.status_code == 400
    assert "事实约束" in response.json()["detail"]
    assert llm.calls == 3
    assert not w["repository"].runs
    assert w["repository"].row == original


async def test_proposal_request_requires_current_revision(client, editing_wiring):
    w = editing_wiring
    response = await client.post(
        f"{w['url']}/ai-edits",
        json={"expected_revision": 1, "section_index": 0, "entry_index": 0, "instruction": "精简"},
    )
    assert response.status_code == 409
    assert not w["repository"].runs


async def test_unknown_revision_and_run_return_404(client, editing_wiring):
    w = editing_wiring
    assert (
        await client.post(f"{w['url']}/revisions/{uuid4()}/restore", json={"expected_revision": 0})
    ).status_code == 404
    assert (await client.get(f"{w['url']}/ai-edits/{uuid4()}")).status_code == 404
    assert (
        await client.post(f"{w['url']}/ai-edits/{uuid4()}/decision", json={"accept": True})
    ).status_code == 404


async def test_missing_checkpoint_does_not_apply_proposal(client, editing_wiring):
    w = editing_wiring
    proposal = await propose(client, w)
    # New memory store simulates losing checkpoint storage, not restarting a service.
    w["service"].runs = GenerationRunStore()
    response = await client.post(
        f"{w['url']}/ai-edits/{proposal['run_id']}/decision", json={"accept": True}
    )
    assert response.status_code == 409
    assert "checkpoint" in response.json()["detail"]
    assert w["repository"].saves == 0
    assert (
        w["repository"].runs[(DEV_USER, w["repository"].row["id"], UUID(proposal["run_id"]))][
            "status"
        ]
        == "pending"
    )
