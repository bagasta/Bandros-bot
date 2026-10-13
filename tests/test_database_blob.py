import sqlite3
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


def test_existing_runs_table_gets_continuation_and_is_persisted(tmp_path: Path, monkeypatch) -> None:
    store: dict[str, bytes] = {}
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_token")
    monkeypatch.setattr("vercel.blob.BlobClient", lambda: FakeBlob(store))

    legacy_path = tmp_path / "legacy.db"
    with sqlite3.connect(legacy_path) as connection:
        connection.executescript(
            """
            CREATE TABLE bots (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                instructions TEXT NOT NULL,
                model TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY,
                bot_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE runs (
                id TEXT PRIMARY KEY,
                bot_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                status TEXT NOT NULL,
                prompt TEXT NOT NULL,
                model TEXT NOT NULL,
                error TEXT,
                usage TEXT NOT NULL DEFAULT '{}',
                stop_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            );
            INSERT INTO bots VALUES ('bot-1', 'Riset', '', '', NULL, 'active', 'now', 'now');
            INSERT INTO conversations VALUES ('conversation-1', 'bot-1', 'now');
            INSERT INTO runs (
                id, bot_id, conversation_id, status, prompt, model, error,
                usage, stop_requested, created_at, started_at, completed_at
            ) VALUES (
                'run-1', 'bot-1', 'conversation-1', 'cancelled', 'Keep data',
                'test-model', NULL, '{}', 0, 'now', NULL, 'now'
            );
            """
        )
    store["state/workspace.db"] = legacy_path.read_bytes()

    database = Database(tmp_path / "migrated" / "workspace.db")
    database.initialize()

    with database.connection() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(runs)")}
        row = connection.execute("SELECT prompt FROM runs WHERE id = 'run-1'").fetchone()

    assert "continuation" in columns
    assert row["prompt"] == "Keep data"

    persisted_path = tmp_path / "persisted.db"
    persisted_path.write_bytes(store["state/workspace.db"])
    with sqlite3.connect(persisted_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
        row = connection.execute("SELECT prompt FROM runs WHERE id = 'run-1'").fetchone()

    assert "continuation" in columns
    assert row[0] == "Keep data"
