from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import base64
import gzip
import os
import sqlite3
import tempfile
import threading
from typing import Iterator

_LOADED_DATABASE: ContextVar[str | None] = ContextVar("bandros_loaded_database", default=None)
_SNAPSHOT_READY: ContextVar[str | None] = ContextVar("bandros_snapshot_ready", default=None)
_SNAPSHOT_LIMIT = 2_000_000
_DATABASE_BLOB_PATH = "state/workspace.db"
_HOLD_LOCK = threading.Lock()
_SNAPSHOT_HOLDS: dict[str, int] = {}


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS bots (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    instructions TEXT NOT NULL,
    model TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL REFERENCES bots(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    model TEXT,
    usage TEXT NOT NULL DEFAULT '{}',
    attachments TEXT NOT NULL DEFAULT '[]',
    citations TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL REFERENCES bots(id),
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    status TEXT NOT NULL,
    prompt TEXT NOT NULL,
    model TEXT NOT NULL,
    error TEXT,
    continuation TEXT,
    usage TEXT NOT NULL DEFAULT '{}',
    stop_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    tool_name TEXT NOT NULL,
    risk_class TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL REFERENCES bots(id),
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    content TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bot_skills (
    bot_id TEXT NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    config TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (bot_id, skill_id)
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    assignee_bot_id TEXT REFERENCES bots(id),
    created_by_bot_id TEXT REFERENCES bots(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_groups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS group_members (
    group_id TEXT NOT NULL REFERENCES work_groups(id) ON DELETE CASCADE,
    bot_id TEXT NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, bot_id)
);
CREATE TABLE IF NOT EXISTS group_messages (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL REFERENCES work_groups(id) ON DELETE CASCADE,
    sender_type TEXT NOT NULL,
    sender_bot_id TEXT REFERENCES bots(id),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    citations TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS group_run_links (
    run_id TEXT PRIMARY KEY REFERENCES runs(id),
    group_id TEXT NOT NULL REFERENCES work_groups(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS plugins (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    token TEXT,
    tools_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS group_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT NOT NULL REFERENCES work_groups(id) ON DELETE CASCADE,
    bot_id TEXT NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    sender_bot_id TEXT,
    depth INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS handoffs (
    id TEXT PRIMARY KEY,
    source_bot_id TEXT NOT NULL REFERENCES bots(id),
    target_bot_id TEXT NOT NULL REFERENCES bots(id),
    parent_run_id TEXT REFERENCES runs(id),
    child_run_id TEXT REFERENCES runs(id),
    task TEXT NOT NULL,
    status TEXT NOT NULL,
    result TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    tool_name TEXT NOT NULL,
    arguments TEXT NOT NULL,
    status TEXT NOT NULL,
    result TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS chatgpt_oauth (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    client_id TEXT,
    host_id TEXT,
    preferred_model TEXT,
    subject TEXT,
    email TEXT,
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    id_token TEXT,
    expires_at TEXT,
    scope TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_transactions (
    state TEXT PRIMARY KEY,
    code_verifier TEXT NOT NULL,
    nonce TEXT NOT NULL,
    client_id TEXT NOT NULL,
    host_id TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspace_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path, blob_path: str | None = None) -> None:
        self.path = path
        self.blob_path = blob_path or _DATABASE_BLOB_PATH

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        migrated = False
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            migrated = self._migrate(connection)
        # SQLite DDL does not increment total_changes, so connection() cannot
        # notice an ALTER TABLE and persist the migrated database to Blob.
        if migrated:
            self.push()

    @staticmethod
    def _durable() -> bool:
        return bool(os.getenv("BLOB_READ_WRITE_TOKEN") or os.getenv("VERCEL_BLOB_READ_WRITE_TOKEN"))

    def pull(self) -> None:
        if _SNAPSHOT_READY.get() == str(self.path) or not self._durable():
            return
        from vercel.blob import BlobClient
        from vercel.blob.errors import BlobNotFoundError

        try:
            with BlobClient() as client:
                result = client.get(self.blob_path, access="private", use_cache=False)
        except BlobNotFoundError:
            return
        except Exception:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(result.content)

    def push(self) -> None:
        if not self._durable() or not self.path.exists():
            return
        from vercel.blob import BlobClient

        try:
            with BlobClient() as client:
                client.put(
                    self.blob_path,
                    self.path.read_bytes(),
                    access="private",
                    content_type="application/vnd.sqlite3",
                    overwrite=True,
                )
        except Exception:
            return

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> bool:
        """Apply additive migrations for databases created by earlier versions."""
        migrations = {
            "messages": {
                "model": "TEXT",
                "usage": "TEXT NOT NULL DEFAULT '{}'",
                "attachments": "TEXT NOT NULL DEFAULT '[]'",
                "citations": "TEXT NOT NULL DEFAULT '[]'",
            },
            "runs": {
                "continuation": "TEXT",
                "usage": "TEXT NOT NULL DEFAULT '{}'",
                "stop_requested": "INTEGER NOT NULL DEFAULT 0",
            },
            "group_messages": {
                "citations": "TEXT NOT NULL DEFAULT '[]'",
            },
            "chatgpt_oauth": {
                "client_id": "TEXT",
                "host_id": "TEXT",
                "id_token": "TEXT",
                "preferred_model": "TEXT",
            },
        }
        migrated = False
        for table, columns in migrations.items():
            existing = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                    migrated = True
        return migrated

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        if _LOADED_DATABASE.get() != str(self.path):
            self.pull()
            _LOADED_DATABASE.set(str(self.path))
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            # A client snapshot from before this table existed must still accept a group turn.
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS plugins (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    url TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    token TEXT,
                    tools_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS group_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    bot_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sender_bot_id TEXT,
                    depth INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            changes = connection.total_changes
            yield connection
            if connection.total_changes != changes:
                _bump_revision(connection)
            connection.commit()
            if connection.total_changes != changes:
                self.push()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def encode_snapshot(data: bytes) -> str:
    return base64.urlsafe_b64encode(gzip.compress(data, mtime=0)).decode().rstrip("=")


def decode_snapshot(value: str) -> bytes | None:
    if not value or len(value) > 400_000:
        return None
    try:
        raw = gzip.decompress(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
    except (ValueError, OSError, EOFError):
        return None
    if len(raw) > _SNAPSHOT_LIMIT or not raw.startswith(b"SQLite format 3\x00"):
        return None
    return raw


def _mark_snapshot_ready(path: Path) -> None:
    _SNAPSHOT_READY.set(str(path))
    _LOADED_DATABASE.set(None)


def database_revision(path: Path) -> int:
    if not path.is_file():
        return -1
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return 0
    try:
        row = connection.execute("SELECT value FROM workspace_meta WHERE key = 'revision'").fetchone()
    except sqlite3.Error:
        return 0
    finally:
        connection.close()
    if row is None:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 0


def snapshot_revision(raw: bytes) -> int:
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    temp = Path(handle.name)
    try:
        temp.write_bytes(raw)
        return database_revision(temp)
    finally:
        temp.unlink(missing_ok=True)


def snapshot_held(path: Path) -> bool:
    with _HOLD_LOCK:
        return _SNAPSHOT_HOLDS.get(str(path), 0) > 0


def hold_snapshot(path: Path) -> None:
    with _HOLD_LOCK:
        key = str(path)
        _SNAPSHOT_HOLDS[key] = _SNAPSHOT_HOLDS.get(key, 0) + 1


def release_snapshot(path: Path) -> None:
    with _HOLD_LOCK:
        key = str(path)
        remaining = _SNAPSHOT_HOLDS.get(key, 0) - 1
        if remaining <= 0:
            _SNAPSHOT_HOLDS.pop(key, None)
        else:
            _SNAPSHOT_HOLDS[key] = remaining


def _bump_revision(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE TABLE IF NOT EXISTS workspace_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute(
        """
        INSERT INTO workspace_meta (key, value) VALUES ('revision', '1')
        ON CONFLICT(key) DO UPDATE SET value = CAST((CAST(value AS INTEGER) + 1) AS TEXT)
        """
    )


def stage_snapshot(path: Path, encoded: str) -> bool:
    raw = decode_snapshot(encoded)
    if raw is None:
        return False
    if snapshot_held(path):
        _mark_snapshot_ready(path)
        return False
    if path.is_file():
        disk_rev = database_revision(path)
        snap_rev = snapshot_revision(raw)
        # A legacy snapshot has no revision. Accept it when nothing is in flight.
        # A numbered snapshot must never roll the file backwards.
        if snap_rev > 0 and disk_rev > snap_rev:
            _mark_snapshot_ready(path)
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    _mark_snapshot_ready(path)
    return True
