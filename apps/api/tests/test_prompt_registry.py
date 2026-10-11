from contextlib import asynccontextmanager

import pytest

from app.config import Settings
from app.evaluation.prompt_registry import PromptVersionRepository


class _Conn:
    def __init__(self, row=None):
        self.row = row

    async def fetchrow(self, query, *args):
        return self.row


class _Db:
    def __init__(self, row=None):
        self.conn = _Conn(row)

    @asynccontextmanager
    async def connection(self):
        yield self.conn


@pytest.mark.asyncio
async def test_fresh_registry_uses_configured_prompt_version():
    settings = Settings(_env_file=None, prompt_version="m8.0")
    selected = await PromptVersionRepository(_Db(), settings=settings).selected()
    assert selected == "m8.0"
    assert settings.prompt_version == "m8.0"


@pytest.mark.asyncio
async def test_persisted_selection_wins_over_configured_default():
    settings = Settings(_env_file=None, prompt_version="m8.0")
    selected = await PromptVersionRepository(
        _Db({"selected_version": "m4.0"}), settings=settings
    ).selected()
    assert selected == "m4.0"
    assert settings.prompt_version == "m4.0"


@pytest.mark.asyncio
async def test_unknown_configured_prompt_version_is_rejected():
    settings = Settings(_env_file=None, prompt_version="does-not-exist")
    with pytest.raises(ValueError, match="未知 prompt"):
        await PromptVersionRepository(_Db(), settings=settings).selected()
