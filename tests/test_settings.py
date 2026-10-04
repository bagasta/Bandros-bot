from __future__ import annotations

from pathlib import Path

from apps.api.app.settings import Settings


def test_placeholder_paths_use_local_data_dir(monkeypatch) -> None:
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.setenv("DATABASE_PATH", "[REDACTED]")
    monkeypatch.setenv("WORKSPACE_ROOT", "[REDACTED]")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "[REDACTED]")
    monkeypatch.setenv("MODEL_GATEWAY", "openrouter")
    settings = Settings.from_environment()
    assert settings.database_path == Path(__file__).resolve().parents[1] / "data" / "workspace.db"
    assert settings.workspace_root == Path(__file__).resolve().parents[1] / "data" / "workspace"
    assert settings.openrouter_base_url.startswith("https://")
    assert "REDACTED" not in settings.openrouter_base_url
    assert settings.await_runs is False
    assert settings.environment_name == "local"


def test_vercel_defaults_use_tmp_and_mock_gateway(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("DATABASE_PATH", "[REDACTED]")
    monkeypatch.setenv("WORKSPACE_ROOT", "[REDACTED]")
    monkeypatch.delenv("MODEL_GATEWAY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    settings = Settings.from_environment()
    assert settings.database_path == Path("/tmp/bandros/workspace.db")
    assert settings.workspace_root == Path("/tmp/bandros/workspace")
    assert settings.model_gateway == "mock"
    assert settings.await_runs is True
    assert settings.cors_allow_all is True
    assert settings.environment_name == "production"
