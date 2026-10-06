from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.app.main import app


def test_health_reports_vercel_git_commit_sha(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "34f08aaa77408c4b3ccffd36e941f5f697892b73")
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["commit"] == "34f08aaa77408c4b3ccffd36e941f5f697892b73"
    assert "environment" in body


def test_health_commit_is_unknown_when_sha_missing(monkeypatch) -> None:
    monkeypatch.delenv("VERCEL_GIT_COMMIT_SHA", raising=False)
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["commit"] == "unknown"


def test_health_commit_is_unknown_when_sha_blank(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "   ")
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["commit"] == "unknown"
    assert response.json()["status"] == "ok"
