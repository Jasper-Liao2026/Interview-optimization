"""Local-data API boundary tests; database acceptance uses an isolated database."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.deps import get_current_user_id
from app.repositories.local_data_repo import EXTERNAL_STORES, LocalDataBusyError, _portable
from app.routers import local_data
from app.schemas.local_data import LocalDataClearResponse, LocalDataExport

API = "/api/v1/system/data"


class RepositorySpy:
    users: list[UUID]
    busy = False

    def __init__(self, database):
        self.users = []

    async def export(self, user_id):
        self.users.append(user_id)
        return LocalDataExport(
            exported_at=datetime.now(UTC),
            user_id=user_id,
            tables={"checkpoint_blobs": [{"blob": _portable(b"\xffprivate\x00")}]},
            preserved_tables=["service_meta"],
            external_stores=EXTERNAL_STORES,
        )

    async def clear(self, user_id):
        self.users.append(user_id)
        if self.busy:
            raise LocalDataBusyError("请求正在运行")
        return LocalDataClearResponse(
            cleared_at=datetime.now(UTC),
            deleted_rows={"experiences": 2},
            preserved_tables=["service_meta"],
            external_stores=EXTERNAL_STORES,
        )


@pytest.fixture
def repository(app, override_db, monkeypatch):
    from tests.conftest import FakeDatabase

    override_db(FakeDatabase())
    spy = RepositorySpy(None)
    monkeypatch.setattr(local_data, "LocalDataRepository", lambda database: spy)
    return spy


async def test_export_uses_server_user_and_binary_backup_is_json(client, app, repository):
    user = uuid4()
    app.dependency_overrides[get_current_user_id] = lambda: user
    response = await client.get(f"{API}/export?user_id={uuid4()}")
    assert response.status_code == 200
    assert response.json()["user_id"] == str(user)
    assert repository.users == [user]
    assert response.headers["cache-control"] == "no-store"
    assert "attachment" in response.headers["content-disposition"]
    assert response.json()["tables"]["checkpoint_blobs"][0]["blob"] == {
        "encoding": "base64",
        "data": "/3ByaXZhdGUA",
    }
    assert response.json()["external_stores"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"confirmation": "yes"},
        {
            "confirmation": "清空本地数据",
            "user_id": str(uuid4()),
        },
    ],
)
async def test_clear_requires_exact_confirmation_and_rejects_user_override(
    client, repository, body
):
    response = await client.post(f"{API}/clear", json=body)
    assert response.status_code == 422
    assert not repository.users


async def test_busy_clear_returns_conflict(client, repository):
    repository.busy = True
    response = await client.post(f"{API}/clear", json={"confirmation": "清空本地数据"})
    assert response.status_code == 409
    assert response.json()["detail"] == "请求正在运行"


async def test_clear_returns_counts_and_external_store_boundary(client, repository):
    response = await client.post(f"{API}/clear", json={"confirmation": "清空本地数据"})
    assert response.status_code == 200
    assert response.json()["deleted_rows"] == {"experiences": 2}
    assert response.json()["preserved_tables"] == ["service_meta"]
    assert response.json()["external_stores"] == EXTERNAL_STORES
    assert response.headers["cache-control"] == "no-store"
