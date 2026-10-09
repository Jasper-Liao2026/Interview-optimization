"""配置解析的回归测试。

背景：`cors_origins` 曾经只靠 `field_validator` 处理逗号分隔，但 pydantic-settings
把 `list[str]` 当复杂类型、**在读取 source 阶段就抢跑 json.loads**，校验器根本没机会执行，
于是 `.env` 里写 `CORS_ORIGINS=http://a,http://b` 会直接抛 SettingsError。
修法是给字段加 `NoDecode`。这几条用例把行为锁住，防止以后有人把 NoDecode 删掉。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings


def _settings_from_env_file(
    tmp_path: Path, content: str, monkeypatch: pytest.MonkeyPatch
) -> Settings:
    # 真实环境变量优先级高于 .env，先清掉确保读到的是文件里的值
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")
    return Settings(_env_file=env_file)


def test_cors_origins_accepts_comma_separated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings_from_env_file(
        tmp_path, "CORS_ORIGINS=http://a.example,http://b.example\n", monkeypatch
    )
    assert settings.cors_origins == ["http://a.example", "http://b.example"]


def test_cors_origins_accepts_json_array(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings_from_env_file(
        tmp_path, 'CORS_ORIGINS=["http://a.example","http://b.example"]\n', monkeypatch
    )
    assert settings.cors_origins == ["http://a.example", "http://b.example"]


def test_cors_origins_trims_blanks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings_from_env_file(
        tmp_path, "CORS_ORIGINS= http://a.example , , http://b.example \n", monkeypatch
    )
    assert settings.cors_origins == ["http://a.example", "http://b.example"]


def test_langfuse_configured_requires_both_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """只给 public key 不算配置完成——复制粘贴漏一个是最常见的失误。"""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-x")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert Settings(_env_file=None).langfuse_configured is False

    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-x")
    assert Settings(_env_file=None).langfuse_configured is True
