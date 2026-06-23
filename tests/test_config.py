"""Config precedence: env vars override the YAML file."""

from __future__ import annotations

import pytest

from cil.config import Settings


def test_defaults_load_from_yaml() -> None:
    settings = Settings()
    assert settings.app_name == "cil"
    assert settings.port == 8000


def test_env_overrides_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CIL_PORT", "9999")
    monkeypatch.setenv("CIL_ENV", "ci")
    settings = Settings()
    assert settings.port == 9999
    assert settings.env == "ci"
