"""Explicit synthetic demo import response."""

from uuid import UUID

from pydantic import BaseModel


class DemoImportResponse(BaseModel):
    synthetic: bool = True
    experience_ids: list[UUID]
    jd_id: UUID
    inserted_experiences: int
    inserted_jobs: int
    message: str
