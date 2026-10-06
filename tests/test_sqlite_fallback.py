"""Production without Neon must boot on SQLite.

Run just this path:

    pytest tests/test_sqlite_fallback.py

Run the whole suite the same way (Postgres cases skip unless BANDROS_TEST_POSTGRES is set):

    pytest
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.app import main
from apps.api.app.settings import Settings
from apps.api.app.tenancy import tenant_locations


def test_health_and_bots_use_sqlite_when_database_url_is_blank(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://bandros:bandros@127.0.0.1:1/no_such_db")
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.delenv("VERCEL_BLOB_READ_WRITE_TOKEN", raising=False)
    configured = Settings.from_environment()
    assert configured.database_url is None

    monkeypatch.setattr(
        main,
        "settings",
        replace(
            configured,
            database_path=tmp_path / "workspace.db",
            workspace_root=tmp_path / "workspace",
            await_runs=True,
            database_url=None,
            daytona_api_key=None,
        ),
    )
    main._workspaces.clear()
    asyncio.run(
        main.credential_store.put(
            "sessions",
            "token-sqlite-boot",
            {"auth_mode": "codex", "account_id": "acct_sqlite_boot", "access_token": "a"},
        )
    )
    headers = {"X-Bandros-Session": "token-sqlite-boot"}

    with TestClient(main.app) as client:
        health = client.get("/health")
        anonymous = client.get("/api/v1/bots")
        listed = client.get("/api/v1/bots", headers=headers)
        created = client.post(
            "/api/v1/bots",
            headers=headers,
            json={"name": "Riset", "description": "", "instructions": "Jawab singkat."},
        )

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert anonymous.status_code == 401
    assert listed.status_code == 200
    assert any(bot["name"] == "Bandros" for bot in listed.json())
    assert created.status_code == 201
    assert created.json()["name"] == "Riset"

    database_path, _, _ = tenant_locations("acct_sqlite_boot", tmp_path, tmp_path / "workspace")
    assert database_path.is_file()
    with sqlite3.connect(database_path) as connection:
        names = [row[0] for row in connection.execute("SELECT name FROM bots ORDER BY name")]
    assert names == ["Bandros", "Riset"]
    main._workspaces.clear()


def test_health_starts_when_database_url_points_at_an_unreachable_host(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "settings",
        replace(
            main.settings,
            database_url="postgresql://bandros:bandros@127.0.0.1:1/no_such_db",
        ),
    )
    with TestClient(main.app) as client:
        health = client.get("/health")
        anonymous = client.get("/api/v1/bots")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert anonymous.status_code == 401


def test_a_configured_dsn_is_not_silently_replaced_by_sqlite(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.delenv("VERCEL_BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.setattr(
        main,
        "settings",
        replace(
            main.settings,
            database_path=tmp_path / "workspace.db",
            workspace_root=tmp_path / "workspace",
            await_runs=True,
            database_url="postgresql://bandros:bandros@127.0.0.1:1/no_such_db",
            daytona_api_key=None,
        ),
    )
    main._workspaces.clear()
    asyncio.run(
        main.credential_store.put(
            "sessions",
            "token-no-fallback",
            {"auth_mode": "codex", "account_id": "acct_no_fallback", "access_token": "a"},
        )
    )
    with TestClient(main.app, raise_server_exceptions=False) as client:
        listed = client.get("/api/v1/bots", headers={"X-Bandros-Session": "token-no-fallback"})
    database_path, _, _ = tenant_locations("acct_no_fallback", tmp_path, tmp_path / "workspace")
    assert listed.status_code == 500
    assert not database_path.exists()
    main._workspaces.clear()
