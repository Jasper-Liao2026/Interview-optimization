"""M7 public API contracts with isolated storage and actual HTML rendering."""

from __future__ import annotations

import asyncio
import copy
import io
import re
import zipfile
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.deps import (
    get_editing_service,
    get_generation_service,
    get_pdf_exporter,
    get_resume_repository,
)
from app.pdf import PdfExportError
from app.render import render_resume_html
from app.repositories.editing_repo import draft_of
from app.schemas.editing import EditorResponse
from app.schemas.resume import ResumeRead
from app.services.generation import InvalidJobError
from tests.fakes import API, DEV_USER, FakePdfExporter, resume_row


class OwnedResumes:
    def __init__(self, rows):
        self.rows = {row["id"]: copy.deepcopy(row) for row in rows}
        self.exported = []

    async def get(self, user_id, resume_id):
        row = self.rows.get(resume_id)
        return copy.deepcopy(row) if row and row["user_id"] == user_id else None

    async def list_for_user(self, user_id):
        return [copy.deepcopy(row) for row in self.rows.values() if row["user_id"] == user_id]

    async def mark_exported(self, user_id, resume_id):
        assert self.rows[resume_id]["user_id"] == user_id
        self.exported.append(resume_id)


class RecordingPdf(FakePdfExporter):
    def __init__(self, fail_at=None):
        super().__init__()
        self.html = []
        self.fail_at = fail_at

    async def render(self, html, *, timeout_s=None):
        self.html.append(html)
        if len(self.html) == self.fail_at:
            raise PdfExportError("render interrupted")
        return self.FAKE_PDF + str(len(self.html)).encode()


@pytest.fixture
def exports(app):
    rows = [{**resume_row(), "id": uuid4(), "title": '../同名<>:"/\\|?*\x00'} for _ in range(2)]
    repository = OwnedResumes(rows)
    exporter = RecordingPdf()
    app.dependency_overrides[get_resume_repository] = lambda: repository
    app.dependency_overrides[get_pdf_exporter] = lambda: exporter
    return repository, exporter, rows


async def test_template_discovery_is_static_route_and_matches_pdf_html(client, exports):
    repository, exporter, rows = exports
    response = await client.get(f"{API}/resumes/templates")
    assert response.status_code == 200
    templates = response.json()["items"]
    assert {item["id"] for item in templates} >= {"classic", "modern"}
    assert {item["layout"] for item in templates} == {"single-column", "two-column"}
    original = copy.deepcopy(repository.rows)
    rendered = []
    for template in templates:
        url = f"{API}/resumes/{rows[0]['id']}"
        params = {"template": template["id"]}
        html = await client.get(f"{url}/html", params=params)
        pdf = await client.get(f"{url}/pdf", params=params)
        assert html.status_code == pdf.status_code == 200
        assert exporter.html[-1] == html.text
        assert pdf.headers["content-type"] == "application/pdf"
        rendered.append(html.text)
    assert len(set(rendered)) == len(templates)
    assert repository.rows == original


@pytest.mark.parametrize("suffix", ["html", "pdf"])
async def test_unknown_template_rejected_before_render(client, exports, suffix):
    _, exporter, rows = exports
    response = await client.get(
        f"{API}/resumes/{rows[0]['id']}/{suffix}", params={"template": "missing"}
    )
    assert response.status_code == 400
    assert not exporter.html


async def test_resume_list_excludes_other_owner(client, exports):
    repository, _, rows = exports
    repository.rows[rows[1]["id"]]["user_id"] = uuid4()
    response = await client.get(f"{API}/resumes")
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["items"]] == [str(rows[0]["id"])]


async def test_editor_preview_overrides_template_and_renders_unsaved_content(app, client, exports):
    repository, exporter, rows = exports
    row = rows[0]
    original = copy.deepcopy(repository.rows)

    class Reader:
        async def read(self, user_id, resume_id):
            assert user_id == row["user_id"] and resume_id == row["id"]
            return EditorResponse.model_validate({"resume": row, "revision": 0, "history": []})

    app.dependency_overrides[get_editing_service] = Reader
    draft = draft_of(row)
    draft["header"]["name"] = "尚未保存的姓名"
    response = await client.post(f"{API}/resumes/{row['id']}/preview?template=modern", json=draft)
    assert response.status_code == 200
    modified = ResumeRead.model_validate({**row, **draft})
    assert exporter.html[-1] == render_resume_html(modified, template="modern")
    assert exporter.html[-1] != render_resume_html(modified, template="classic")
    assert response.content == exporter.FAKE_PDF + b"1"
    assert repository.rows == original
    assert not repository.exported


async def test_archive_contains_unique_safe_pdf_names_and_selected_template(client, exports):
    repository, exporter, rows = exports
    ids = [str(row["id"]) for row in rows]
    response = await client.post(
        f"{API}/resumes/export", json={"resume_ids": ids, "template": "modern"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert len(set(names)) == len(rows)
        for index, name in enumerate(names):
            assert not re.search(r'[\x00-\x1f<>:"/\\|?*]', name)
            assert name.endswith(f"{rows[index]['id']}.pdf")
            assert archive.read(name) == exporter.FAKE_PDF + str(index + 1).encode()
    assert repository.exported == [row["id"] for row in rows]
    for row, html in zip(rows, exporter.html, strict=True):
        preview = await client.get(f"{API}/resumes/{row['id']}/html?template=modern")
        assert preview.text == html


@pytest.mark.parametrize("invalid", ["missing", "foreign", "no_entries", "no_bullets"])
async def test_archive_prechecks_entire_selection_before_rendering(client, exports, invalid):
    repository, exporter, rows = exports
    second = rows[1]["id"]
    expected = 400
    if invalid == "missing":
        repository.rows.pop(second)
        expected = 404
    elif invalid == "foreign":
        repository.rows[second]["user_id"] = uuid4()
        expected = 404
    elif invalid == "no_entries":
        repository.rows[second]["sections"] = []
    else:
        for section in repository.rows[second]["sections"]:
            for entry in section["entries"]:
                entry["bullets"] = []
    response = await client.post(
        f"{API}/resumes/export", json={"resume_ids": [str(row["id"]) for row in rows]}
    )
    assert response.status_code == expected
    assert not exporter.html
    assert not repository.exported


async def test_render_failure_returns_error_without_partial_archive_or_export_marks(
    client, exports
):
    repository, exporter, rows = exports
    exporter.fail_at = 2
    response = await client.post(
        f"{API}/resumes/export", json={"resume_ids": [str(row["id"]) for row in rows]}
    )
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/json"
    assert not response.content.startswith(b"PK")
    assert len(exporter.html) == 2
    assert not repository.exported


async def test_unknown_archive_template_prevents_render_and_marks(client, exports):
    repository, exporter, rows = exports
    response = await client.post(
        f"{API}/resumes/export",
        json={"resume_ids": [str(rows[0]["id"])], "template": "missing"},
    )
    assert response.status_code == 400
    assert not exporter.html
    assert not repository.exported


@pytest.mark.parametrize(
    ("endpoint", "field", "count"),
    [
        ("batch-generate", "jd_ids", 0),
        ("batch-generate", "jd_ids", 21),
        ("batch-generate", "experience_ids", 61),
        ("export", "resume_ids", 0),
        ("export", "resume_ids", 21),
    ],
)
async def test_batch_count_boundaries_reject_invalid_requests(client, endpoint, field, count):
    payload = {"jd_ids": [str(uuid4())]} if endpoint == "batch-generate" else {}
    payload[field] = [str(uuid4()) for _ in range(count)]
    response = await client.post(f"{API}/resumes/{endpoint}", json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("endpoint", "field"),
    [("batch-generate", "jd_ids"), ("batch-generate", "experience_ids"), ("export", "resume_ids")],
)
async def test_batch_duplicate_ids_rejected(client, endpoint, field):
    payload = {"jd_ids": [str(uuid4())]} if endpoint == "batch-generate" else {}
    repeated = str(uuid4())
    payload[field] = [repeated, repeated]
    response = await client.post(f"{API}/resumes/{endpoint}", json=payload)
    assert response.status_code == 422


async def test_batch_isolates_job_failure_preserves_order_and_reuses_run_ids(app, client):
    jobs = [uuid4() for _ in range(3)]
    calls = []
    released = asyncio.Event()
    active = 0
    peak = 0

    class Generation:
        async def generate(self, user_id, request, trace_id):
            nonlocal active, peak
            assert user_id == DEV_USER
            calls.append(request)
            active += 1
            peak = max(peak, active)
            try:
                if request.jd_id == jobs[0]:
                    await released.wait()
                elif request.jd_id == jobs[1]:
                    released.set()
                else:
                    raise InvalidJobError("JD 不存在")
                return SimpleNamespace(
                    checkpoint={"status": "completed"},
                    resume={**resume_row(), "id": request.run_id, "jd_id": request.jd_id},
                    warnings=[],
                )
            finally:
                active -= 1

    app.dependency_overrides[get_generation_service] = Generation
    payload = {"batch_id": str(uuid4()), "jd_ids": [str(job) for job in jobs]}
    responses = [await client.post(f"{API}/resumes/batch-generate", json=payload) for _ in range(2)]
    assert all(response.status_code == 200 for response in responses)
    first, repeated = [response.json() for response in responses]
    assert [item["jd_id"] for item in first["items"]] == payload["jd_ids"]
    assert [item["status"] for item in first["items"]] == ["completed", "completed", "failed"]
    assert first["items"][2]["resume"] is None
    assert "JD 不存在" in first["items"][2]["error"]
    assert [item["run_id"] for item in first["items"]] == [
        item["run_id"] for item in repeated["items"]
    ]
    assert len({item["run_id"] for item in first["items"]}) == 3
    assert peak == 2
    assert all(request.persist for request in calls)


async def test_batch_accepts_maximum_job_and_source_counts(app, client):
    seen = []

    class Generation:
        async def generate(self, user_id, request, trace_id):
            seen.append(request)
            return SimpleNamespace(
                checkpoint={"status": "completed"},
                resume={**resume_row(), "id": request.run_id},
                warnings=[],
            )

    app.dependency_overrides[get_generation_service] = Generation
    response = await client.post(
        f"{API}/resumes/batch-generate",
        json={
            "jd_ids": [str(uuid4()) for _ in range(20)],
            "experience_ids": [str(uuid4()) for _ in range(60)],
        },
    )
    assert response.status_code == 200
    assert len(response.json()["items"]) == len(seen) == 20
    assert all(len(request.experience_ids) == 60 for request in seen)


async def test_archive_accepts_maximum_resume_count(client, exports):
    repository, exporter, rows = exports
    for _ in range(18):
        row = {**resume_row(), "id": uuid4()}
        rows.append(row)
        repository.rows[row["id"]] = copy.deepcopy(row)
    response = await client.post(
        f"{API}/resumes/export", json={"resume_ids": [str(row["id"]) for row in rows]}
    )
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert len(archive.namelist()) == 20
    assert len(exporter.html) == len(repository.exported) == 20


async def test_completed_generation_replay_does_not_write_over_editor(
    client, api_wiring, monkeypatch
):
    payload = {"run_id": str(uuid4()), "jd_text": "招聘后端开发工程师，熟悉 Python 开发。"}
    initial = await client.post(f"{API}/resumes/generate", json=payload)
    assert initial.status_code == 200
    assert initial.json()["checkpoint"]["status"] == "completed"
    repository = api_wiring["resumes"]
    edited = {**repository.created[0], "title": "人工编辑后标题", "edit_revision": 1}
    repository.row = copy.deepcopy(edited)

    async def forbid_upsert(*args, **kwargs):
        raise AssertionError("Completed generation must not upsert an edited resume")

    monkeypatch.setattr(repository, "create", forbid_upsert)
    repeated = await client.post(f"{API}/resumes/generate", json=payload)
    assert repeated.status_code == 200
    assert repeated.json()["resume"] == initial.json()["resume"]
    assert repository.row == edited
    assert len(repository.created) == 1
    conflict = await client.post(
        f"{API}/resumes/generate", json={**payload, "title": "changed request"}
    )
    assert conflict.status_code == 409
