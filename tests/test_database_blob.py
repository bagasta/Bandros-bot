from pathlib import Path
from types import SimpleNamespace

from vercel.blob.errors import BlobNotFoundError

from apps.api.app.database import Database


class FakeBlob:
    def __init__(self, store: dict[str, bytes]) -> None:
        self.store = store

    def __enter__(self) -> "FakeBlob":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, key: str, **_kwargs: object) -> SimpleNamespace:
        if key not in self.store:
            raise BlobNotFoundError()
        return SimpleNamespace(content=self.store[key])

    def put(self, key: str, body: bytes, **_kwargs: object) -> None:
        self.store[key] = body


def test_bot_survives_a_fresh_database_file(tmp_path: Path, monkeypatch) -> None:
    store: dict[str, bytes] = {}
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_token")
    monkeypatch.setattr("vercel.blob.BlobClient", lambda: FakeBlob(store))

    first = Database(tmp_path / "one" / "workspace.db")
    first.initialize()
    with first.connection() as connection:
        connection.execute(
            "INSERT INTO bots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("bot-1", "Riset", "", "", None, "active", "2026-10-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00"),
        )

    second = Database(tmp_path / "two" / "workspace.db")
    second.initialize()
    with second.connection() as connection:
        row = connection.execute("SELECT name FROM bots WHERE id = ?", ("bot-1",)).fetchone()

    assert row["name"] == "Riset"
