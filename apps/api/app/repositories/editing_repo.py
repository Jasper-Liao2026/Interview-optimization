"""Atomic editor saves with optimistic revision checks and immutable history."""

from typing import Any
from uuid import UUID

from app.agents.checkpoint import RunConflictError
from app.db import Database
from app.schemas.editing import ResumeDraft


class EditingNotFoundError(ValueError):
    pass


def draft_of(row: dict) -> dict:
    return ResumeDraft.model_validate(
        {key: row[key] for key in ("title", "header", "sections")}
    ).model_dump(mode="json")


class EditingRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def _locked(self, conn: Any, user_id: UUID, resume_id: UUID) -> dict:
        row = await conn.fetchrow(
            "select * from public.resumes where id=$1 and user_id=$2 for update",
            resume_id,
            user_id,
        )
        if row is None:
            raise EditingNotFoundError("简历不存在")
        row = dict(row)
        await self._snapshot(conn, row, "初始版本")
        return row

    async def _snapshot(self, conn: Any, row: dict, reason: str) -> None:
        await conn.execute(
            "insert into public.resume_revisions "
            "(user_id,resume_id,revision,reason,draft,provenance) values($1,$2,$3,$4,$5,$6) "
            "on conflict(resume_id,revision) do nothing",
            row["user_id"],
            row["id"],
            row["edit_revision"],
            reason,
            draft_of(row),
            {key: row.get(key) for key in ("generator", "generator_vendor")},
        )

    async def read(self, user_id: UUID, resume_id: UUID) -> dict:
        async with self.database.connection() as conn, conn.transaction():
            row = await self._locked(conn, user_id, resume_id)
            history = await conn.fetch(
                "select * from public.resume_revisions "
                "where resume_id=$1 and user_id=$2 order by revision desc",
                resume_id,
                user_id,
            )
        return {
            "resume": row,
            "revision": row["edit_revision"],
            "history": [dict(item) for item in history],
        }

    async def revision(self, user_id: UUID, resume_id: UUID, revision_id: UUID) -> dict:
        async with self.database.connection() as conn:
            row = await conn.fetchrow(
                "select * from public.resume_revisions where id=$1 and resume_id=$2 and user_id=$3",
                revision_id,
                resume_id,
                user_id,
            )
        if row is None:
            raise EditingNotFoundError("历史版本不存在")
        return dict(row)

    async def save(
        self,
        user_id: UUID,
        resume_id: UUID,
        expected_revision: int,
        draft: dict,
        reason: str,
        provenance: dict | None = None,
        *,
        run_id: UUID | None = None,
        run_payload: dict | None = None,
    ) -> None:
        async with self.database.connection() as conn, conn.transaction():
            row = await self._locked(conn, user_id, resume_id)
            if row["edit_revision"] != expected_revision:
                raise RunConflictError("简历已被修改，请刷新后重试；当前改动未覆盖新版本")
            if provenance is None:
                # Manual text changes invalidate old provenance even for direct API clients.
                draft = ResumeDraft.model_validate(draft).model_dump(mode="json")
                old_evidence = {
                    (entry.get("experience_id"), bullet["text"]): bullet.get("evidence", [])
                    for section in row["sections"]
                    for entry in section["entries"]
                    for bullet in entry["bullets"]
                }
                for section in draft["sections"]:
                    for entry in section["entries"]:
                        for bullet in entry["bullets"]:
                            bullet["evidence"] = old_evidence.get(
                                (entry.get("experience_id"), bullet["text"]), []
                            )
                provenance = {"generator": "manual", "generator_vendor": None}
            updated = await conn.fetchrow(
                "update public.resumes set title=$3,header=$4,sections=$5,status='draft',"
                "edit_revision=edit_revision+1,generator=$6,generator_vendor=$7,updated_at=now() "
                "where id=$1 and user_id=$2 returning *",
                resume_id,
                user_id,
                draft["title"],
                draft["header"],
                draft["sections"],
                provenance.get("generator"),
                provenance.get("generator_vendor"),
            )
            await self._snapshot(conn, dict(updated), reason)
            if run_id is not None:
                await conn.execute(
                    "update public.resume_edit_runs set payload=$4,updated_at=now() "
                    "where id=$1 and user_id=$2 and resume_id=$3",
                    run_id,
                    user_id,
                    resume_id,
                    run_payload,
                )

    async def get_run(self, user_id: UUID, resume_id: UUID, run_id: UUID) -> dict | None:
        async with self.database.connection() as conn:
            row = await conn.fetchrow(
                "select payload from public.resume_edit_runs "
                "where id=$1 and user_id=$2 and resume_id=$3",
                run_id,
                user_id,
                resume_id,
            )
        return row["payload"] if row else None

    async def save_run(self, user_id: UUID, resume_id: UUID, run_id: UUID, payload: dict) -> None:
        async with self.database.connection() as conn:
            await conn.execute(
                "insert into public.resume_edit_runs(id,user_id,resume_id,payload) "
                "values($1,$2,$3,$4) "
                "on conflict(id) do update set payload=excluded.payload,updated_at=now() "
                "where resume_edit_runs.user_id=excluded.user_id "
                "and resume_edit_runs.resume_id=excluded.resume_id",
                run_id,
                user_id,
                resume_id,
                payload,
            )
