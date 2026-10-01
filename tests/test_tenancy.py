from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.app import main
from apps.api.app.tenancy import tenant_locations


def test_tenant_locations_are_separate(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    workspace_root = tmp_path / "files"
    first = tenant_locations("acct_a", data_root, workspace_root)
    second = tenant_locations("acct_b", data_root, workspace_root)

    assert first[0] != second[0]
    assert first[1] != second[1]
    assert first[2] != second[2]
    assert first[2].startswith("state/users/")


def test_bots_require_chatgpt_login() -> None:
    with TestClient(main.app) as client:
        response = client.get("/api/v1/bots")

    assert response.status_code == 401
    assert response.json()["detail"] == "Masuk dengan ChatGPT dulu."


def test_two_chatgpt_accounts_do_not_share_bots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.delenv("VERCEL_BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.setattr(
        main,
        "settings",
        replace(main.settings, database_path=tmp_path / "workspace.db", workspace_root=tmp_path / "workspace"),
    )
    main._workspaces.clear()
    asyncio.run(main.credential_store.put("sessions", "token-a", {"auth_mode": "codex", "account_id": "acct_a", "access_token": "a"}))
    asyncio.run(main.credential_store.put("sessions", "token-b", {"auth_mode": "codex", "account_id": "acct_b", "access_token": "b"}))

    with TestClient(main.app) as client:
        created = client.post(
            "/api/v1/bots",
            headers={"X-Bandros-Session": "token-a"},
            json={"name": "Riset A", "description": "milik A", "instructions": ""},
        )
        own = client.get("/api/v1/bots", headers={"X-Bandros-Session": "token-a"})
        other = client.get("/api/v1/bots", headers={"X-Bandros-Session": "token-b"})

    assert created.status_code == 201
    assert [bot["name"] for bot in own.json()] == ["Bandros", "Riset A"]
    assert [bot["name"] for bot in other.json()] == ["Bandros"]
    main._workspaces.clear()
