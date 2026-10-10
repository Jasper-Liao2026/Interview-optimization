"""测试用的假实现 —— 仓储 / 导出器 / 夹具数据。

**为什么单独成模块而不是放在某个 test_*.py 里**：
M1 与 M2 的接口测试跑的是同一套「真路由 + 真服务 + 真 agent，只把外部依赖换掉」的装配，
假实现放在 `conftest.py` 之外的普通模块里，两个测试模块可以各自 import 而不互相依赖
（`test_health.py` / `test_system.py` 从 `tests.conftest` 取 `FakeDatabase` 也是同一个思路）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

API = "/api/v1"
DEV_USER = UUID("00000000-0000-4000-8000-000000000001")

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
RESUME_ID = UUID("11111111-1111-4111-8111-111111111111")


# ------------------------------------------------------------------- 夹具数据
def experience_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": uuid4(),
        "user_id": DEV_USER,
        "kind": "project",
        "org": "简历优化器",
        "role": "独立开发",
        "start_date": date(2026, 9, 1),
        "end_date": None,
        "raw_description": "独立设计与实现一个批量生成岗位适配版简历的 Web 工具。",
        "skill_tags": ["Python", "FastAPI"],
        "highlights": ["把请求 trace_id 复用为 Langfuse trace_id"],
        "metrics": [{"name": "单元测试", "value": "24 → 82 条", "context": None}],
        "variants": [
            {"direction": "后端开发", "text": "用 FastAPI 承载全部业务逻辑。", "note": None}
        ],
        "sort_order": 0,
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(overrides)
    return row


def resume_sections() -> list[dict[str, Any]]:
    return [
        {
            "title": "项目经历",
            "entries": [
                {
                    "experience_id": str(uuid4()),
                    "kind": "project",
                    "org": "简历优化器",
                    "role": "独立开发",
                    "period": "2026.09 – 至今",
                    "bullets": [{"text": "一条要点", "evidence": []}],
                }
            ],
        }
    ]


def resume_row() -> dict[str, Any]:
    return {
        "id": RESUME_ID,
        "user_id": DEV_USER,
        "jd_id": None,
        "title": "后端开发工程师（校招）",
        "template": "classic",
        "header": {"name": "本地开发用户", "headline": "后端 / AI 应用开发"},
        "sections": resume_sections(),
        "status": "draft",
        "generator": "stub",
        "created_at": NOW,
        "updated_at": NOW,
    }


# ---------------------------------------------------------------------- 假实现
class FakeExperienceRepository:
    """内存版经历仓储。

    `create` / `update` 都走 `payload.model_dump()`，因此**接口层新增字段会自动流到假仓储**，
    不需要在这里逐个补 —— 这也正是它能把「Pydantic 是唯一定义源」这条约定验证到位的原因。
    """

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows if rows is not None else [experience_row()]
        self.created: list[dict[str, Any]] = []
        self.updated: list[UUID] = []
        self.deleted: list[UUID] = []

    async def list_for_user(self, user_id: UUID) -> list[dict[str, Any]]:
        return list(self.rows)

    async def list_by_ids(self, user_id: UUID, ids: list[UUID]) -> list[dict[str, Any]]:
        wanted = set(ids)
        return [row for row in self.rows if row["id"] in wanted]

    async def get(self, user_id: UUID, experience_id: UUID) -> dict[str, Any] | None:
        return next((row for row in self.rows if row["id"] == experience_id), None)

    async def create(self, user_id: UUID, payload: Any) -> dict[str, Any]:
        row = experience_row(**payload.model_dump())
        self.rows.append(row)
        self.created.append(row)
        return row

    async def update(
        self, user_id: UUID, experience_id: UUID, payload: Any
    ) -> dict[str, Any] | None:
        for index, row in enumerate(self.rows):
            if row["id"] != experience_id:
                continue
            # 全量替换语义：以 payload 为准重建，只保留不可写的元字段
            replaced = experience_row(**payload.model_dump())
            replaced["id"] = experience_id
            replaced["user_id"] = row["user_id"]
            self.rows[index] = replaced
            self.updated.append(experience_id)
            return replaced
        return None

    async def delete(self, user_id: UUID, experience_id: UUID) -> bool:
        self.deleted.append(experience_id)
        # 真的把行摘掉，而不是只记一笔 —— 否则「删完再查应当 404」这类断言会假通过
        before = len(self.rows)
        self.rows = [row for row in self.rows if row["id"] != experience_id]
        return len(self.rows) < before


class FakeJobDescriptionRepository:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows: list[dict[str, Any]] = rows or []
        self.created: list[dict[str, Any]] = []
        self.updated: list[UUID] = []
        self.deleted: list[UUID] = []

    async def create(self, user_id: UUID, **fields: Any) -> dict[str, Any]:
        row = {
            "id": uuid4(),
            "user_id": user_id,
            "created_at": NOW,
            "updated_at": NOW,
            "source_type": "text",
            **fields,
        }
        self.rows.append(row)
        self.created.append(row)
        return row

    async def get(self, user_id: UUID, jd_id: UUID) -> dict[str, Any] | None:
        return next(
            (row for row in self.rows if row["id"] == jd_id and row["user_id"] == user_id), None
        )

    async def list_for_user(self, user_id: UUID) -> list[dict[str, Any]]:
        return [row for row in self.rows if row["user_id"] == user_id]

    async def update_metadata(
        self, user_id: UUID, jd_id: UUID, payload: Any
    ) -> dict[str, Any] | None:
        row = await self.get(user_id, jd_id)
        if row is None:
            return None
        row["title"] = payload.title
        row["company"] = payload.company
        row["updated_at"] = NOW
        self.updated.append(jd_id)
        return row

    async def delete(self, user_id: UUID, jd_id: UUID) -> bool:
        before = len(self.rows)
        self.rows = [
            row for row in self.rows if not (row["id"] == jd_id and row["user_id"] == user_id)
        ]
        deleted = len(self.rows) < before
        if deleted:
            self.deleted.append(jd_id)
        return deleted


class FakeEmbeddingRepository:
    """Deterministic in-memory counterpart to the pgvector repository."""

    def __init__(self) -> None:
        self.records: dict[UUID, dict[str, Any]] = {}
        self.store_calls = 0
        self.search_calls = 0

    async def fingerprints(self, user_id: UUID) -> dict[UUID, tuple[str, str]]:
        return {
            key: (value["source_hash"], value["model_key"])
            for key, value in self.records.items()
            if value["user_id"] == user_id
        }

    async def store(
        self,
        user_id: UUID,
        experience_id: UUID,
        updated_at: datetime,
        source_hash: str,
        model_key: str,
        vector: list[float],
    ) -> bool:
        row = next(
            (r for r in getattr(self, "experience_rows", []) if r["id"] == experience_id), None
        )
        if row is not None and row.get("updated_at") != updated_at:
            return False
        self.records[experience_id] = {
            "user_id": user_id,
            "updated_at": updated_at,
            "source_hash": source_hash,
            "model_key": model_key,
            "vector": vector,
        }
        self.store_calls += 1
        return True

    async def search(
        self, user_id: UUID, vector: list[float], model_key: str, limit: int
    ) -> dict[UUID, float]:
        self.search_calls += 1
        candidates = [
            (key, value)
            for key, value in self.records.items()
            if value["user_id"] == user_id and value["model_key"] == model_key
        ]

        def similarity(stored: list[float]) -> float:
            return sum(a * b for a, b in zip(stored, vector, strict=False))

        ranked = sorted(candidates, key=lambda item: (-similarity(item[1]["vector"]), str(item[0])))
        return {key: similarity(value["vector"]) for key, value in ranked[:limit]}


class FakeResumeRepository:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row
        self.created: list[dict[str, Any]] = []
        self.exported: list[UUID] = []

    async def create(self, user_id: UUID, **fields: Any) -> dict[str, Any]:
        row = {**resume_row(), "user_id": user_id, **fields}
        self.created.append(row)
        return row

    async def get(self, user_id: UUID, resume_id: UUID) -> dict[str, Any] | None:
        return self.row

    async def mark_exported(self, user_id: UUID, resume_id: UUID) -> None:
        self.exported.append(resume_id)


class FakeProfileRepository:
    async def ensure(self, user_id: UUID, **fields: Any) -> dict[str, Any]:
        return {
            "id": user_id,
            "display_name": fields.get("display_name", "本地开发用户"),
            "headline": fields.get("headline"),
        }


class FakePdfExporter:
    """假导出器。真导出要起 Chromium 进程，不适合放在单元测试里（见 test_pdf_export.py）。"""

    FAKE_PDF = b"%PDF-1.4\n/Type /Page\n/Type /Pages\n/Type /Page\n%%EOF"

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    async def render(self, html: str, *, timeout_s: float | None = None) -> bytes:
        if self.error is not None:
            raise self.error
        return self.FAKE_PDF
