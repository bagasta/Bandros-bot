from __future__ import annotations

from dataclasses import replace

import pytest


@pytest.fixture(autouse=True)
def sqlite_for_api_tests(monkeypatch):
    """Developer .env may set DATABASE_URL. API tests stay on SQLite unless they opt in."""
    from apps.api.app import main

    if main.settings.database_url:
        monkeypatch.setattr(main, "settings", replace(main.settings, database_url=None))
