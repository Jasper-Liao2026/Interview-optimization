"""Portable local-data backup and explicit destructive-operation contract."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LocalDataExport(BaseModel):
    format_version: Literal[1] = 1
    exported_at: datetime
    user_id: UUID
    tables: dict[str, list[dict[str, Any]]]
    preserved_tables: list[str]
    external_stores: list[str]


class LocalDataClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: Literal["清空本地数据"]


class LocalDataClearResponse(BaseModel):
    cleared_at: datetime
    deleted_rows: dict[str, int] = Field(default_factory=dict)
    preserved_tables: list[str]
    external_stores: list[str]
