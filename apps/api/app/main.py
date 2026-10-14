from __future__ import annotations

from collections.abc import Awaitable
from contextlib import asynccontextmanager
from contextvars import ContextVar
import asyncio
import json
import base64
import hashlib
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any, AsyncIterator
from uuid import UUID
from urllib.parse import quote, urlencode

import httpx
import jwt
from jwt import PyJWKClient

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel

from .database import Database, database_revision, encode_snapshot, hold_snapshot, release_snapshot, stage_snapshot
from .computer import DaytonaComputer
from .credential_store import CredentialStore
from .domain import AssignSkill, Approval, ApprovalProposal, ApprovalStatus, Bot, BotActivity, BotStatus, ClawHubInstall, ClawHubListing, CreateBot, GroupActivity, GroupInput, GroupMemberInput, GroupMessage, GroupMessageInput, Handoff, HandoffInput, Job, JobInput, JobUpdate, Memory, Message, MessageEditInput, MessageInput, Plugin, PluginInput, RegenerateInput, Run, RunEvent, RunStatus, Skill, SkillInput, UpdateBot, WorkGroup
from .model_gateway import ChatGPTGateway, CompositeGateway, MockGateway, OpenRouterGateway
from .policy import PolicyEngine
from .orchestrator import ORCHESTRATOR_DESCRIPTION, ORCHESTRATOR_INSTRUCTIONS, ORCHESTRATOR_NAME
from .mentions import is_resume_request
from .repository import Repository
from .runtime import RunRuntime
from .settings import Settings, running_on_vercel
from .tenancy import tenant_digest, tenant_locations


settings = Settings.from_environment()
credential_store = CredentialStore()
current_chatgpt_connection: ContextVar[dict[str, object] | None] = ContextVar(
    "current_chatgpt_connection", default=None
)
current_repository: ContextVar[Repository | None] = ContextVar("current_repository", default=None)
current_runtime: ContextVar[RunRuntime | None] = ContextVar("current_runtime", default=None)
_workspaces: dict[str, tuple[Repository, RunRuntime]] = {}


def _chatgpt_connection() -> dict[str, object] | None:
    return current_chatgpt_connection.get()


class _Active:
    """Resolve the workspace bound to the ChatGPT account of this request."""

    def __init__(self, var: ContextVar[Repository | None] | ContextVar[RunRuntime | None]) -> None:
        self._var = var

    def __getattr__(self, name: str):
        target = self._var.get()
        if target is None:
            raise RuntimeError("ChatGPT session required")
        return getattr(target, name)


repository = _Active(current_repository)
runtime = _Active(current_runtime)
_gateway = CompositeGateway(
    MockGateway()
    if settings.model_gateway == "mock"
    else OpenRouterGateway(settings.openrouter_api_key, settings.openrouter_base_url),
    ChatGPTGateway(
        _chatgpt_connection,
        lambda connection: _refresh_chatgpt_token(connection),
    ),
)
policy = PolicyEngine()
openai_jwks = PyJWKClient("https://auth.openai.com/.well-known/jwks.json")
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_CLIENT_VERSION = "0.157.1"
LATEST_CODEX_MODEL = {"id": "gpt-6-luna", "display_name": "GPT-6 Luna"}
CODEX_DEVICE_CODE_URL = "https://auth.openai.com/api/accounts/deviceauth/usercode"
CODEX_DEVICE_TOKEN_URL = "https://auth.openai.com/api/accounts/deviceauth/token"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_DEVICE_REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"


def _jwt_payload(token: str) -> dict[str, Any]:
    try:
        encoded = token.split(".")[1]
        encoded += "=" * (-len(encoded) % 4)
        value = json.loads(base64.urlsafe_b64decode(encoded).decode())
        return value if isinstance(value, dict) else {}
    except (IndexError, ValueError, json.JSONDecodeError):
        return {}


def _openai_token_headers(client_id: str) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if settings.openai_client_secret:
        credentials = f"{quote(client_id, safe='')}:{quote(settings.openai_client_secret, safe='')}"
        encoded = base64.b64encode(credentials.encode()).decode()
        headers["Authorization"] = f"Basic {encoded}"
    return headers


async def _refresh_chatgpt_token(connection: dict[str, object]) -> str | None:
    refresh_token = connection.get("refresh_token")
    client_id = connection.get("client_id")
    if not isinstance(refresh_token, str) or not isinstance(client_id, str):
        return None
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            CODEX_TOKEN_URL
            if connection.get("auth_mode") == "codex"
            else "https://auth.openai.com/api/accounts/oauth/token",
            headers=_openai_token_headers(str(client_id)),
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
                **(
                    {}
                    if connection.get("auth_mode") == "codex"
                    else {"resource": "https://api.openai.com/v1"}
                ),
            },
        )
    if response.is_error:
        return None
    tokens = response.json()
    access_token = tokens.get("access_token")
    if not access_token:
        return None
    expires = tokens.get("expires_in")
    expires_at = datetime.now(UTC) + timedelta(seconds=int(expires)) if expires else None
    if connection.get("auth_mode") == "codex" and connection.get("_session_token"):
        updated = {
            **connection,
            "access_token": access_token,
            "refresh_token": tokens.get("refresh_token") or refresh_token,
            "id_token": tokens.get("id_token") or connection.get("id_token"),
            "expires_at": expires_at.isoformat() if expires_at else None,
        }
        await credential_store.put(
            "sessions", str(connection["_session_token"]), updated
        )
        current_chatgpt_connection.set(updated)
        return access_token
    repository.update_chatgpt_tokens(
        access_token=access_token,
        refresh_token=tokens.get("refresh_token"),
        expires_at=expires_at,
        scope=tokens.get("scope", str(connection.get("scope") or "")),
    )
    return access_token

DEFAULT_SKILLS = (
    ("workspace_admin", "Membuat, mengubah, dan mengarsipkan Bot sesuai struktur tim.", "Kelola identitas Bot dan tanggung jawabnya secara jelas."),
    ("job_manager", "Membuat dan memperbarui job yang terukur untuk anggota tim.", "Setiap job punya pemilik, prioritas, dan status."),
    ("coordination", "Mendelegasikan pekerjaan dan mengirim pembaruan ke grup kerja.", "Delegasi harus spesifik dan diberikan ke Bot yang tepat."),
)


def _seed_workspace(repo: Repository) -> None:
    known_names = {skill.name for skill in repo.list_skills()}
    for name, description, content in DEFAULT_SKILLS:
        if name not in known_names:
            repo.create_skill(name, description, content)
    default_skill_ids = {skill.name: skill.id for skill in repo.list_skills()}
    for bot in repo.list_bots():
        if "manager" in bot.name.lower() or bot.name.lower() == ORCHESTRATOR_NAME.lower():
            assigned = {skill.name for skill in repo.list_bot_skills(bot.id)}
            for skill_name in ("workspace_admin", "job_manager", "coordination"):
                skill_id = default_skill_ids.get(skill_name)
                if skill_id and skill_name not in assigned:
                    repo.assign_skill(bot.id, skill_id)
    existing = next((bot for bot in repo.list_bots() if bot.name.lower() == ORCHESTRATOR_NAME.lower()), None)
    if existing is None:
        orchestrator = repo.create_bot(ORCHESTRATOR_NAME, ORCHESTRATOR_DESCRIPTION, ORCHESTRATOR_INSTRUCTIONS, None)
        for skill_name in ("workspace_admin", "job_manager", "coordination"):
            skill_id = default_skill_ids.get(skill_name)
            if skill_id:
                repo.assign_skill(orchestrator.id, skill_id)
    else:
        if existing.status is not BotStatus.ACTIVE:
            repo.set_bot_status(existing.id, BotStatus.ACTIVE)
            existing = repo.get_bot(existing.id)
        if "jangan menulis (diam)" not in existing.instructions.lower():
            repo.update_bot(existing.id, {"description": ORCHESTRATOR_DESCRIPTION, "instructions": ORCHESTRATOR_INSTRUCTIONS})
    repo.drop_copied_group_context()


def activate_account(account_id: str) -> tuple[object, object]:
    digest = tenant_digest(account_id)
    pair = _workspaces.get(digest)
    if pair is None:
        database_path, files, blob_path = tenant_locations(
            account_id,
            settings.database_path.parent,
            settings.workspace_root,
        )
        database = Database(database_path, blob_path=blob_path)
        database.initialize()
        repo = Repository(database)
        _seed_workspace(repo)
        workspace_runtime = RunRuntime(
            repository=repo,
            model_gateway=_gateway,
            default_model=settings.default_model,
            max_model_calls=settings.max_model_calls_per_run,
            workspace_root=files,
            computer=DaytonaComputer(
                api_key=settings.daytona_api_key,
                api_url=settings.daytona_api_url,
                account_id=digest,
                target=settings.daytona_target,
            ) if settings.daytona_api_key else None,
        )
        workspace_runtime.recover(resume=not settings.await_runs)
        pair = (repo, workspace_runtime)
        _workspaces[digest] = pair
    return current_repository.set(pair[0]), current_runtime.set(pair[1])


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    yield


class SnapshotEnvelope:
    """Carry the workspace snapshot in the JSON body so a large copy survives Vercel's header limit."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        header_map = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
        if header_map.get("x-bandros-envelope") != "1":
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                continue
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        try:
            envelope = json.loads(b"".join(chunks) or b"{}")
        except json.JSONDecodeError:
            envelope = {}
        method = envelope.get("method") if isinstance(envelope, dict) else None
        if isinstance(method, str) and method.strip():
            scope["method"] = method.strip().upper()
        snapshot = envelope.get("snapshot") if isinstance(envelope, dict) else None
        if isinstance(snapshot, str) and snapshot:
            scope.setdefault("state", {})["bandros_snapshot"] = snapshot
        payload = envelope.get("payload") if isinstance(envelope, dict) else None
        payload_body = b"" if payload is None else json.dumps(payload).encode()
        scope["headers"] = [
            (key, value)
            for key, value in scope["headers"]
            if key.lower() != b"content-length"
        ]
        scope["headers"].append((b"content-length", str(len(payload_body)).encode()))
        sent = False

        async def inner_receive():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": payload_body, "more_body": False}

        started: dict[str, Any] = {}
        body_parts: list[bytes] = []

        async def inner_send(message):
            if message["type"] == "http.response.start":
                started.update(message)
                return
            if message["type"] != "http.response.body":
                await send(message)
                return
            body_parts.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            await self._send_wrapped(send, started, b"".join(body_parts))

        await self.app(scope, inner_receive, inner_send)

    async def _send_wrapped(self, send, started: dict[str, Any], body: bytes) -> None:
        headers = list(started.get("headers") or [])
        snapshot = ""
        revision = "0"
        kept: list[tuple[bytes, bytes]] = []
        for key, value in headers:
            lowered = key.lower()
            if lowered == b"x-bandros-snapshot":
                snapshot = value.decode()
                continue
            if lowered == b"x-bandros-snapshot-rev":
                revision = value.decode()
                continue
            if lowered == b"content-length":
                continue
            kept.append((key, value))
        status_code = int(started.get("status", 200))
        content_type = ""
        for key, value in kept:
            if key.lower() == b"content-type":
                content_type = value.decode()
        if status_code < 300 and "application/json" in content_type and body:
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = None
            else:
                wrapped = json.dumps({"data": data, "snapshot": snapshot, "revision": int(revision or "0")}).encode()
                kept.append((b"content-length", str(len(wrapped)).encode()))
                await send({**started, "headers": kept})
                await send({"type": "http.response.body", "body": wrapped, "more_body": False})
                return
        kept.append((b"content-length", str(len(body)).encode()))
        if snapshot:
            kept.append((b"x-bandros-snapshot", snapshot.encode()))
            kept.append((b"x-bandros-snapshot-rev", revision.encode()))
        await send({**started, "headers": kept})
        await send({"type": "http.response.body", "body": body, "more_body": False})


app = FastAPI(title="Persistent Agent Workspace", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.cors_allow_all else ["http://localhost:3000", "http://127.0.0.1:3000", settings.web_origin],
    allow_origin_regex=None if settings.cors_allow_all else r"https://.*\.vercel\.app",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Bandros-Session", "X-Bandros-Snapshot", "X-Bandros-Envelope"],
    expose_headers=["X-Bandros-Snapshot", "X-Bandros-Snapshot-Rev"],
)


_PUBLIC_PATHS = {
    "/",
    "/health",
    "/api/v1/auth/chatgpt/device/start",
    "/api/v1/auth/chatgpt/device/poll",
    "/api/v1/auth/chatgpt/status",
    "/api/v1/auth/chatgpt/start",
    "/api/v1/auth/chatgpt/callback",
}


_STAGE_LOCK = threading.Lock()


def _account_database_path(account_id: str):
    return tenant_locations(account_id, settings.database_path.parent, settings.workspace_root)[0]


def _active_account_id() -> str:
    connection = _chatgpt_connection()
    account_id = connection.get("account_id") if connection else None
    if not isinstance(account_id, str) or not account_id.strip():
        raise HTTPException(status_code=401, detail="Masuk dengan ChatGPT dulu.")
    return account_id


@app.middleware("http")
async def optional_auth(request: Request, call_next):
    if request.url.path.startswith("/api/v1/computer/view/"):
        return await call_next(request)
    session_token = request.headers.get("X-Bandros-Session")
    connection = (
        await credential_store.get("sessions", session_token) if session_token else None
    )
    if connection is not None and session_token:
        connection["_session_token"] = session_token
    context_token = current_chatgpt_connection.set(connection)
    repository_token = None
    runtime_token = None
    public_path = request.url.path in _PUBLIC_PATHS
    account_id = None
    held_path = None
    try:
        if settings.api_auth_token and not public_path and request.method != "OPTIONS":
            expected = f"Bearer {settings.api_auth_token}"
            provided = request.headers.get("Authorization")
            if provided != expected:
                return JSONResponse({"detail": "authentication required"}, status_code=401)
        if not public_path and request.method != "OPTIONS":
            account_id = connection.get("account_id") if connection else None
            if not isinstance(account_id, str) or not account_id.strip():
                return JSONResponse({"detail": "Masuk dengan ChatGPT dulu."}, status_code=401)
            database_path = _account_database_path(account_id)
            snapshot = request.headers.get("X-Bandros-Snapshot") or request.scope.get("state", {}).get("bandros_snapshot")
            with _STAGE_LOCK:
                if snapshot:
                    stage_snapshot(database_path, snapshot)
                hold_snapshot(database_path)
                held_path = database_path
            incoming_rev = database_revision(database_path) if database_path.is_file() else -1
            repository_token, runtime_token = activate_account(account_id)
        response = await call_next(request)
        if not public_path and request.method != "OPTIONS" and isinstance(account_id, str) and account_id.strip():
            database_path = _account_database_path(account_id)
            if database_path.is_file():
                revision = database_revision(database_path)
                response.headers["X-Bandros-Snapshot-Rev"] = str(revision)
                if revision != incoming_rev:
                    encoded = encode_snapshot(database_path.read_bytes())
                    if len(encoded) <= 400_000:
                        response.headers["X-Bandros-Snapshot"] = encoded
        return response
    finally:
        if held_path is not None:
            release_snapshot(held_path)
        if repository_token is not None:
            current_repository.reset(repository_token)
        if runtime_token is not None:
            current_runtime.reset(runtime_token)
        current_chatgpt_connection.reset(context_token)


app.add_middleware(SnapshotEnvelope)


def not_found(error: KeyError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))


def _model_for_bot(bot: Bot, requested: str | None = None) -> str:
    if requested:
        return requested
    if bot.model:
        return bot.model
    connection = _chatgpt_connection()
    if (
        connection
        and (
            connection.get("auth_mode") == "codex"
            or "chatgpt.tokens.use.direct" in str(connection.get("scope") or "").split()
        )
        and connection.get("preferred_model")
    ):
        return f"chatgpt/{connection['preferred_model']}"
    return settings.default_model


class DevicePollInput(BaseModel):
    flow_id: str


def listed_codex_models(payload: object) -> list[dict[str, str]]:
    raw_models: object = []
    if isinstance(payload, dict):
        raw_models = payload.get("models") or payload.get("data") or []
    elif isinstance(payload, list):
        raw_models = payload
    ranked: list[tuple[bool, bool, int, int, dict[str, str]]] = []
    if not isinstance(raw_models, list):
        return []
    for index, item in enumerate(raw_models):
        if not isinstance(item, dict):
            continue
        slug = item.get("slug") or item.get("id")
        if not isinstance(slug, str) or not slug.strip():
            continue
        priority = item.get("priority")
        rank = priority if isinstance(priority, int) else 10_000
        visible = item.get("visibility", "list") == "list"
        supported = item.get("supported_in_api") is not False
        ranked.append(
            (
                visible,
                supported,
                rank,
                index,
                {"id": slug, "display_name": str(item.get("display_name") or slug)},
            )
        )
    chosen = [item for item in ranked if item[0] and item[1]] or [item for item in ranked if item[1]]
    chosen.sort(key=lambda item: (item[2], item[3]))
    return [item[4] for item in chosen]


def ensure_latest_codex_models(models: list[dict[str, str]]) -> list[dict[str, str]]:
    rest = [item for item in models if item.get("id") != LATEST_CODEX_MODEL["id"]]
    return [LATEST_CODEX_MODEL, *rest]


async def _list_codex_models(connection: dict[str, object]) -> list[dict[str, str]]:
    """Return the Codex catalog, or GPT-6 Luna when ChatGPT's catalog is unreachable.

    Login must not depend on this call. From Vercel the catalog often times out or
    returns 401 even after a valid device login, while the same call works locally.
    """
    headers = {
        "Authorization": f"Bearer {connection['access_token']}",
        "ChatGPT-Account-Id": str(connection["account_id"]),
        "originator": "codex_cli_rs",
        "User-Agent": f"codex_cli_rs/{CODEX_CLIENT_VERSION}",
    }
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            for version in (CODEX_CLIENT_VERSION, "0.157.0", "0.149.0"):
                try:
                    response = await client.get(
                        "https://chatgpt.com/backend-api/codex/models",
                        params={"client_version": version},
                        headers=headers,
                    )
                except httpx.HTTPError:
                    continue
                if response.is_error:
                    continue
                try:
                    models = ensure_latest_codex_models(listed_codex_models(response.json()))
                except ValueError:
                    continue
                if models:
                    return models
    except Exception:
        return [dict(LATEST_CODEX_MODEL)]
    return [dict(LATEST_CODEX_MODEL)]


async def continue_after_response(work: Awaitable[object]) -> None:
    """Return the HTTP response before long group work, when Vercel can keep the invocation alive."""
    from vercel.cache.context import get_context
    from vercel.functions import wait_until

    if get_context().wait_until is None:
        await work
        return
    wait_until(work)


async def _start_run(run_id: UUID) -> None:
    if settings.await_runs:
        await runtime.start_and_wait(run_id)
    else:
        runtime.start(run_id)


@app.get("/api/v1/computer")
async def computer_status() -> dict[str, str | None]:
    computer = _account_computer()
    if computer is None:
        return {"state": "unavailable", "screen_url": None}
    return await asyncio.to_thread(computer.status)


@app.post("/api/v1/computer/start")
async def computer_start() -> dict[str, str | None]:
    raise HTTPException(
        status_code=409,
        detail="Desktop hanya diaktifkan saat Bot memanggil alat komputer.",
    )


@app.post("/api/v1/computer/stop")
async def computer_stop() -> dict[str, str | None]:
    computer = _account_computer()
    if computer is not None:
        await asyncio.to_thread(computer.park)
    return {"state": "off", "screen_url": None}


_SCREEN_TICKETS: dict[str, tuple[str, float]] = {}


@app.get("/api/v1/computer/ticket")
async def computer_ticket() -> dict[str, str]:
    account_id = _active_account_id()
    ticket = secrets.token_urlsafe(24)
    _SCREEN_TICKETS[ticket] = (account_id, time.time() + 3600)
    return {"ticket": ticket}


@app.get("/api/v1/computer/view/{ticket}/{asset_path:path}")
async def computer_view(ticket: str, asset_path: str) -> Response:
    if ".." in asset_path.split("/"):
        raise HTTPException(status_code=400, detail="path is invalid")
    origin = await _preview_origin(ticket)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        upstream = await client.get(
            f"{origin}/{asset_path}",
            headers={"X-Daytona-Skip-Preview-Warning": "true"},
        )
    media_type = upstream.headers.get("content-type", "application/octet-stream").split(";")[0]
    return Response(content=upstream.content, status_code=upstream.status_code, media_type=media_type)


@app.websocket("/api/v1/computer/view/{ticket}/websockify")
async def computer_view_socket(websocket: WebSocket, ticket: str) -> None:
    try:
        account_id = _ticket_account(ticket)
    except HTTPException:
        await websocket.close(code=4401)
        return
    computer = DaytonaComputer(
        api_key=settings.daytona_api_key or "",
        api_url=settings.daytona_api_url,
        account_id=tenant_digest(account_id),
        target=settings.daytona_target,
    )
    origin = await asyncio.to_thread(computer.preview_origin)
    if not origin:
        await websocket.close(code=4404)
        return
    import websockets

    upstream = origin.replace("https://", "wss://").replace("http://", "ws://") + "/websockify"
    await websocket.accept(subprotocol="binary")
    try:
        async with websockets.connect(
            upstream,
            subprotocols=["binary"],
            max_size=None,
            additional_headers={"X-Daytona-Skip-Preview-Warning": "true"},
        ) as remote:
            async def from_browser() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        break
                    if message.get("bytes") is not None:
                        await remote.send(message["bytes"])
                    elif message.get("text") is not None:
                        await remote.send(message["text"])

            async def from_desktop() -> None:
                async for data in remote:
                    if isinstance(data, bytes):
                        await websocket.send_bytes(data)
                    else:
                        await websocket.send_text(data)

            done, pending = await asyncio.wait(
                {asyncio.create_task(from_browser()), asyncio.create_task(from_desktop())},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                task.result()
    except Exception:
        await websocket.close()


def _ticket_account(ticket: str) -> str:
    found = _SCREEN_TICKETS.get(ticket)
    if found is None or found[1] < time.time():
        raise HTTPException(status_code=401, detail="Layar kedaluwarsa. Buka lagi.")
    return found[0]


async def _preview_origin(ticket: str) -> str:
    if not settings.daytona_api_key:
        raise HTTPException(status_code=503, detail="Komputer Daytona belum dikonfigurasi.")
    computer = DaytonaComputer(
        api_key=settings.daytona_api_key,
        api_url=settings.daytona_api_url,
        account_id=tenant_digest(_ticket_account(ticket)),
        target=settings.daytona_target,
    )
    origin = await asyncio.to_thread(computer.preview_origin)
    if not origin:
        raise HTTPException(status_code=404, detail="Nyalakan komputer dulu.")
    return origin


def _account_computer() -> DaytonaComputer | None:
    runtime = current_runtime.get()
    computer = getattr(runtime, "computer", None)
    return computer if isinstance(computer, DaytonaComputer) else None


@app.get("/")
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment_name}


@app.post("/api/v1/auth/chatgpt/device/start")
async def chatgpt_device_start() -> dict[str, object]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            CODEX_DEVICE_CODE_URL,
            json={"client_id": CODEX_CLIENT_ID},
        )
    if response.is_error:
        raise HTTPException(
            status_code=502,
            detail="Device Code Authentication belum aktif pada akun/workspace ChatGPT.",
        )
    device = response.json()
    device_code = device.get("device_code") or device.get("device_auth_id")
    user_code = device.get("user_code")
    if not device_code or not user_code:
        raise HTTPException(status_code=502, detail="Response device OAuth tidak lengkap")
    flow = {
        "device_code": device_code,
        "user_code": user_code,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
    }
    flow_id = credential_store.seal(flow)
    await credential_store.put("flows", flow_id, flow)
    return {
        "flow_id": flow_id,
        "user_code": user_code,
        "verification_url": device.get("verification_uri")
        or device.get("verification_uri_complete")
        or "https://auth.openai.com/codex/device",
        "interval": max(int(device.get("interval") or 5), 3),
    }


@app.post("/api/v1/auth/chatgpt/device/poll")
async def chatgpt_device_poll(payload: DevicePollInput) -> dict[str, object]:
    flow = await credential_store.get("flows", payload.flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="Device login tidak ditemukan")
    if datetime.fromisoformat(str(flow["expires_at"])) < datetime.now(UTC):
        await credential_store.delete("flows", payload.flow_id)
        raise HTTPException(status_code=410, detail="Kode device sudah kedaluwarsa")
    async with httpx.AsyncClient(timeout=30) as client:
        poll_response = await client.post(
            CODEX_DEVICE_TOKEN_URL,
            json={
                "device_auth_id": flow["device_code"],
                "user_code": flow["user_code"],
            },
        )
    try:
        poll = poll_response.json() if poll_response.content else {}
    except ValueError:
        poll = {}
    error = poll.get("error") if isinstance(poll, dict) else None
    error_code = error if isinstance(error, str) else (
        str(error.get("code") or error.get("type") or error.get("message"))
        if isinstance(error, dict)
        else None
    )
    if not poll_response.is_success:
        if poll_response.status_code in {403, 404, 408, 429} or poll_response.status_code >= 500 or error_code in {
            "authorization_pending",
            "slow_down",
        }:
            return {"status": "pending", "slow_down": error_code == "slow_down"}
        await credential_store.delete("flows", payload.flow_id)
        raise HTTPException(status_code=400, detail=f"Device OAuth gagal: {error_code or 'unknown'}")
    authorization_code = poll.get("authorization_code")
    verifier = poll.get("code_verifier")
    if not authorization_code or not verifier:
        return {"status": "pending"}
    async with httpx.AsyncClient(timeout=30) as client:
        token_response = await client.post(
            CODEX_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": CODEX_DEVICE_REDIRECT_URI,
                "client_id": CODEX_CLIENT_ID,
                "code_verifier": verifier,
            },
        )
    if token_response.is_error:
        detail = token_response.text[:160].replace("\n", " ")
        raise HTTPException(status_code=502, detail=f"Token device ChatGPT tidak dapat ditukar ({detail})")
    tokens = token_response.json()
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    id_token = tokens.get("id_token")
    if not access_token or not refresh_token:
        raise HTTPException(status_code=502, detail="Token ChatGPT tidak lengkap")
    access_claims = _jwt_payload(access_token)
    id_claims = _jwt_payload(id_token or "")
    auth_claims = access_claims.get("https://api.openai.com/auth") or {}
    account_id = auth_claims.get("chatgpt_account_id")
    if not account_id:
        raise HTTPException(status_code=502, detail="Account ID ChatGPT tidak ada pada token")
    expires = tokens.get("expires_in")
    connection: dict[str, object] = {
        "auth_mode": "codex",
        "client_id": CODEX_CLIENT_ID,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "id_token": id_token,
        "account_id": account_id,
        "email": id_claims.get("email"),
        "subject": id_claims.get("sub"),
        "scope": tokens.get("scope", "openid profile email offline_access"),
        "expires_at": (
            datetime.now(UTC) + timedelta(seconds=int(expires))
        ).isoformat()
        if expires
        else None,
    }
    models = await _list_codex_models(connection)
    connection["preferred_model"] = models[0]["id"] if models else LATEST_CODEX_MODEL["id"]
    # Keep the browser session header small. The model list is fetched again and
    # must not be sealed into X-Bandros-Session (Vercel rejects oversized headers).
    connection.pop("id_token", None)
    session_token = credential_store.seal(connection)
    await credential_store.put("sessions", session_token, connection)
    await credential_store.delete("flows", payload.flow_id)
    return {
        "status": "connected",
        "session_token": session_token,
        "email": connection.get("email"),
        "preferred_model": connection["preferred_model"],
    }


@app.get("/api/v1/auth/chatgpt/start")
async def chatgpt_auth_start() -> dict[str, str]:
    configured_client_id = settings.openai_client_id
    if running_on_vercel() and not configured_client_id:
        raise HTTPException(
            status_code=503,
            detail=(
                "Sign in with ChatGPT untuk website memerlukan OPENAI_CLIENT_ID "
                "oaiapp_... dan callback Vercel yang didaftarkan ke OpenAI."
            ),
        )
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    host_id = f"urn:uuid:{secrets.token_hex(16)}"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    client_id = str(configured_client_id or "dynamic_agent_client")
    await credential_store.put(
        "oauth_states",
        state,
        {
            "code_verifier": verifier,
            "nonce": nonce,
            "client_id": client_id,
            "host_id": host_id,
            "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        },
    )
    query = urlencode({
        "client_id": client_id,
        "redirect_uri": settings.openai_redirect_uri,
        "response_type": "code",
        "scope": settings.openai_scopes,
        "resource": "https://api.openai.com/v1",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    if client_id == "dynamic_agent_client":
        query += f"&{urlencode({'agent_name_hint': 'Bandros AI', 'ext_agent_host_id': host_id})}"
    return {"authorization_url": f"https://auth.openai.com/api/accounts/authorize?{query}"}


@app.get("/api/v1/auth/chatgpt/callback")
async def chatgpt_auth_callback(code: str | None = None, state: str | None = None, error: str | None = None, client_id: str | None = None) -> dict[str, str]:
    if not state:
        raise HTTPException(status_code=400, detail="OAuth state tidak valid atau sudah kedaluwarsa")
    transaction = await credential_store.get("oauth_states", state)
    await credential_store.delete("oauth_states", state)
    expires_at = transaction.get("expires_at") if transaction else None
    if not transaction or not isinstance(expires_at, str) or datetime.fromisoformat(expires_at) < datetime.now(UTC):
        raise HTTPException(status_code=400, detail="OAuth state sudah kedaluwarsa")
    if error:
        raise HTTPException(status_code=400, detail=f"ChatGPT OAuth gagal: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Callback ChatGPT tidak membawa authorization code")
    verifier = str(transaction["code_verifier"])
    nonce = str(transaction["nonce"])
    requested_client_id = str(transaction["client_id"])
    host_id = str(transaction["host_id"])
    if client_id and requested_client_id != "dynamic_agent_client" and client_id != requested_client_id:
        raise HTTPException(status_code=400, detail="Client ID ChatGPT tidak cocok")
    issued_client_id = client_id or requested_client_id
    if issued_client_id == "dynamic_agent_client":
        raise HTTPException(status_code=502, detail="Registrasi client ChatGPT belum selesai")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://auth.openai.com/api/accounts/oauth/token",
            headers=_openai_token_headers(issued_client_id),
            data={
                "grant_type": "authorization_code",
                "client_id": issued_client_id,
                "code": code,
                "redirect_uri": settings.openai_redirect_uri,
                "code_verifier": verifier,
                "resource": "https://api.openai.com/v1",
            },
        )
    if response.is_error:
        raise HTTPException(status_code=502, detail="Token ChatGPT tidak dapat ditukar")
    tokens = response.json()
    access_token = tokens.get("access_token")
    if not access_token:
        raise HTTPException(status_code=502, detail="Token ChatGPT tidak ada pada response")
    granted_scope = tokens.get("scope", settings.openai_scopes)
    if "chatgpt.tokens.use.direct" not in granted_scope.split():
        raise HTTPException(status_code=403, detail="Akun ChatGPT tidak memberikan izin penggunaan subscription")
    id_token = tokens.get("id_token")
    if not id_token:
        raise HTTPException(status_code=502, detail="ID token ChatGPT tidak ada pada response")
    try:
        signing_key = openai_jwks.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=issued_client_id,
            issuer="https://auth.openai.com",
            options={"require": ["sub", "exp", "iat"]},
        )
    except jwt.PyJWTError as token_error:
        raise HTTPException(status_code=400, detail="ID token ChatGPT tidak valid") from token_error
    if claims.get("nonce") != nonce:
        raise HTTPException(status_code=400, detail="ID token nonce tidak valid")
    expires = tokens.get("expires_in")
    preferred_model = None
    async with httpx.AsyncClient(timeout=30) as client:
        models_response = await client.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if models_response.is_success:
            catalog = models_response.json().get("models", [])
            preferred_model = next(
                (
                    item.get("slug")
                    for item in catalog
                    if item.get("visibility") == "list" and item.get("slug")
                ),
                None,
            )
    access_claims = _jwt_payload(access_token)
    auth_claims = access_claims.get("https://api.openai.com/auth") or {}
    account_id = auth_claims.get("chatgpt_account_id") or claims.get("sub")
    if not isinstance(account_id, str) or not account_id:
        raise HTTPException(status_code=502, detail="Account ID ChatGPT tidak ada pada token")
    connection = {
        "auth_mode": "siwc",
        "client_id": issued_client_id,
        "host_id": host_id,
        "access_token": access_token,
        "refresh_token": tokens.get("refresh_token"),
        "id_token": id_token,
        "account_id": account_id,
        "email": claims.get("email"),
        "subject": claims.get("sub"),
        "scope": granted_scope,
        "preferred_model": preferred_model,
        "expires_at": (datetime.now(UTC) + timedelta(seconds=int(expires))).isoformat() if expires else None,
    }
    session_token = credential_store.seal(connection)
    await credential_store.put("sessions", session_token, connection)
    return RedirectResponse(
        url=f"{settings.web_origin}?chatgpt=connected&session={quote(session_token)}",
        status_code=303,
    )


@app.get("/api/v1/auth/chatgpt/status")
def chatgpt_auth_status() -> dict[str, object]:
    connection = _chatgpt_connection()
    if not connection:
        return {
            "connected": False,
            "available": True,
            "reason": None,
        }
    codex = connection.get("auth_mode") == "codex"
    return {
        "connected": True,
        "available": True,
        "email": connection.get("email"),
        "subject": connection.get("subject"),
        "scope": connection.get("scope"),
        "expires_at": connection.get("expires_at"),
        "subscription_enabled": codex
        or "chatgpt.tokens.use.direct" in str(connection.get("scope") or "").split(),
        "preferred_model": connection.get("preferred_model"),
        "models": connection.get("models") or [],
        "auth_mode": connection.get("auth_mode", "siwc"),
    }


@app.post("/api/v1/auth/chatgpt/disconnect")
async def chatgpt_auth_disconnect() -> dict[str, bool]:
    connection = _chatgpt_connection()
    if connection and connection.get("_session_token"):
        await credential_store.delete("sessions", str(connection["_session_token"]))
        current_chatgpt_connection.set(None)
    else:
        repository.clear_chatgpt_connection()
    return {"disconnected": True}


@app.get("/api/v1/auth/chatgpt/models")
async def chatgpt_models() -> dict[str, object]:
    connection = _chatgpt_connection()
    if not connection:
        raise HTTPException(status_code=404, detail="ChatGPT belum terhubung")
    if connection.get("refresh_token") and ChatGPTGateway._expiring(connection.get("expires_at")):
        await _refresh_chatgpt_token(connection)
        connection = _chatgpt_connection() or connection
    if connection.get("auth_mode") == "codex":
        models = await _list_codex_models(connection)
        session_token = connection.get("_session_token")
        if isinstance(session_token, str) and session_token:
            stored = {key: value for key, value in connection.items() if key != "_session_token"}
            stored["models"] = models
            if models:
                stored["preferred_model"] = models[0]["id"]
            await credential_store.put("sessions", session_token, stored)
        return {"models": models}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {connection['access_token']}"},
        )
    response.raise_for_status()
    catalog = response.json()
    models = [
        {
            "id": item.get("slug") or item.get("id"),
            "display_name": item.get("display_name") or item.get("slug") or item.get("id"),
        }
        for item in catalog.get("models", catalog.get("data", []))
        if (item.get("slug") or item.get("id")) and item.get("visibility", "list") == "list"
    ]
    return {"models": ensure_latest_codex_models(models)}


@app.get("/api/v1/bots", response_model=list[Bot])
def list_bots() -> list[Bot]:
    _seed_workspace(repository)
    return repository.list_bots()


@app.post("/api/v1/bots", response_model=Bot, status_code=status.HTTP_201_CREATED)
def create_bot(payload: CreateBot) -> Bot:
    bot = repository.create_bot(payload.name, payload.description, payload.instructions, payload.model)
    if "manager" in bot.name.lower():
        skills = {skill.name: skill.id for skill in repository.list_skills()}
        for skill_name in ("workspace_admin", "job_manager", "coordination"):
            if skill_id := skills.get(skill_name):
                repository.assign_skill(bot.id, skill_id)
    return bot


@app.get("/api/v1/bots/{bot_id}", response_model=Bot)
def get_bot(bot_id: UUID) -> Bot:
    try:
        return repository.get_bot(bot_id)
    except KeyError as error:
        raise not_found(error) from error


@app.patch("/api/v1/bots/{bot_id}", response_model=Bot)
def update_bot(bot_id: UUID, payload: UpdateBot) -> Bot:
    try:
        return repository.update_bot(bot_id, payload.model_dump(exclude_unset=True))
    except KeyError as error:
        raise not_found(error) from error


@app.get("/api/v1/skills", response_model=list[Skill])
def list_skills() -> list[Skill]:
    return repository.list_skills()


@app.post("/api/v1/skills", response_model=Skill, status_code=status.HTTP_201_CREATED)
def create_skill(payload: SkillInput) -> Skill:
    try:
        return repository.create_skill(payload.name, payload.description, payload.content)
    except Exception as error:
        raise HTTPException(status_code=409, detail="skill name is already in use") from error


@app.get("/api/v1/clawhub/search", response_model=list[ClawHubListing])
async def search_clawhub(q: str) -> list[ClawHubListing]:
    from .clawhub import ClawHubError, search_skills

    try:
        results = await search_skills(q.strip())
    except ClawHubError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return [ClawHubListing.model_validate(item) for item in results]


@app.post("/api/v1/clawhub/install", response_model=Skill)
async def install_clawhub_skill(payload: ClawHubInstall) -> Skill:
    from .clawhub import ClawHubError, fetch_skill_markdown, parse_skill_markdown

    try:
        markdown = await fetch_skill_markdown(payload.slug, payload.owner_handle)
    except ClawHubError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    name, description, content = parse_skill_markdown(markdown)
    name = name or payload.slug[:100]
    if not content.strip():
        content = markdown[:20_000]
    source = f"https://clawhub.ai/{payload.owner_handle}/skills/{payload.slug}"
    if source not in content:
        content = f"{content}\n\nSource: {source}"[:20_000]
    skill = repository.upsert_skill(name, description or payload.slug, content)
    if payload.bot_id is not None:
        try:
            repository.assign_skill(payload.bot_id, skill.id)
        except KeyError as error:
            raise not_found(error) from error
    else:
        for bot in repository.list_bots():
            if bot.status is BotStatus.ACTIVE:
                repository.assign_skill(bot.id, skill.id)
    return skill


@app.get("/api/v1/plugins", response_model=list[Plugin])
def list_plugins() -> list[Plugin]:
    return repository.list_plugins()


@app.post("/api/v1/plugins", response_model=Plugin, status_code=status.HTTP_201_CREATED)
async def create_plugin(payload: PluginInput) -> Plugin:
    from .mcp_client import McpError, discover_tools

    try:
        tools = await discover_tools(payload.url.strip(), payload.token)
    except McpError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    try:
        return repository.create_plugin(payload.name.strip(), payload.url.strip(), payload.token, tools)
    except Exception as error:
        raise HTTPException(status_code=409, detail="plugin name is already in use") from error


@app.delete("/api/v1/plugins/{plugin_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_plugin(plugin_id: UUID) -> None:
    try:
        repository.delete_plugin(plugin_id)
    except KeyError as error:
        raise not_found(error) from error


@app.get("/api/v1/bots/{bot_id}/skills", response_model=list[Skill])
def list_bot_skills(bot_id: UUID) -> list[Skill]:
    try:
        return repository.list_bot_skills(bot_id)
    except KeyError as error:
        raise not_found(error) from error


@app.post("/api/v1/bots/{bot_id}/skills", status_code=status.HTTP_204_NO_CONTENT)
def assign_skill(bot_id: UUID, payload: AssignSkill) -> None:
    try:
        repository.assign_skill(bot_id, payload.skill_id, payload.config)
    except KeyError as error:
        raise not_found(error) from error


@app.delete("/api/v1/bots/{bot_id}/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def unassign_skill(bot_id: UUID, skill_id: UUID) -> None:
    repository.unassign_skill(bot_id, skill_id)


@app.get("/api/v1/jobs", response_model=list[Job])
def list_jobs(bot_id: UUID | None = Query(default=None)) -> list[Job]:
    return repository.list_jobs(bot_id)


@app.post("/api/v1/jobs", response_model=Job, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobInput) -> Job:
    try:
        return repository.create_job(payload.title, payload.description, payload.priority, payload.assignee_bot_id)
    except KeyError as error:
        raise not_found(error) from error


@app.patch("/api/v1/jobs/{job_id}", response_model=Job)
def update_job(job_id: UUID, payload: JobUpdate) -> Job:
    try:
        return repository.update_job(job_id, payload.model_dump())
    except KeyError as error:
        raise not_found(error) from error


@app.get("/api/v1/groups", response_model=list[WorkGroup])
def list_groups() -> list[WorkGroup]:
    return repository.list_groups()


@app.post("/api/v1/groups", response_model=WorkGroup, status_code=status.HTTP_201_CREATED)
def create_group(payload: GroupInput) -> WorkGroup:
    try:
        return repository.create_group(payload.name, payload.description, payload.member_bot_ids)
    except KeyError as error:
        raise not_found(error) from error


@app.post("/api/v1/groups/{group_id}/members", response_model=WorkGroup)
def add_group_member(group_id: UUID, payload: GroupMemberInput) -> WorkGroup:
    try:
        repository.add_group_member(group_id, payload.bot_id)
        return repository.get_group(group_id)
    except KeyError as error:
        raise not_found(error) from error


@app.get("/api/v1/groups/{group_id}/activity", response_model=list[GroupActivity])
def group_activity(group_id: UUID) -> list[GroupActivity]:
    try:
        repository.get_group(group_id)
    except KeyError as error:
        raise not_found(error) from error
    activity: list[GroupActivity] = []
    seen: set[str] = set()
    for run in repository.runs_for_group(group_id):
        if not _run_is_live(run.id):
            continue
        bot = repository.get_bot(run.bot_id)
        seen.add(bot.name)
        activity.append(GroupActivity(bot_id=bot.id, name=bot.name, status=str(run.status)))
    group = repository.get_group(group_id)
    for bot_id in repository.queued_bot_ids(group_id):
        member = next((item for item in group.members if item.id == bot_id), None)
        if member is None or member.name in seen:
            continue
        seen.add(member.name)
        activity.append(GroupActivity(bot_id=member.id, name=member.name, status="running"))
    for name in runtime.typing_names(group_id):
        if name in seen:
            continue
        member = next((item for item in group.members if item.name == name), None)
        if member is not None:
            activity.append(GroupActivity(bot_id=member.id, name=member.name, status="running"))
    return activity


def _run_is_live(run_id: UUID) -> bool:
    task = runtime._tasks.get(run_id)
    return task is not None and not task.done()


@app.get("/api/v1/bots/{bot_id}/activity", response_model=BotActivity)
def bot_activity(bot_id: UUID) -> BotActivity:
    try:
        repository.get_bot(bot_id)
        conversation_id = repository.conversation_for_bot(bot_id)
    except KeyError as error:
        raise not_found(error) from error
    active = repository.active_runs_for_bot(bot_id)
    latest = repository.latest_run_for_bot(bot_id)
    pending = [
        approval
        for approval in repository.list_approvals(ApprovalStatus.PENDING)
        if latest is not None and approval.run_id == latest.id and latest.status is RunStatus.WAITING_APPROVAL
    ]
    if any(_run_is_live(run.id) for run in active):
        return BotActivity(working=True, approvals=pending)
    if active:
        return BotActivity(working=False, error="Balasan terputus. Kirim ulang pesan.", approvals=pending)
    if pending:
        return BotActivity(working=False, approvals=pending)
    messages = repository.list_messages(conversation_id)
    latest = repository.latest_run_for_bot(bot_id)
    if (
        messages
        and messages[-1].role == "user"
        and latest is not None
        and latest.status in {RunStatus.FAILED, RunStatus.FAILED_RETRYABLE, RunStatus.CANCELLED}
        and latest.error
    ):
        return BotActivity(working=False, error=latest.error)
    return BotActivity(working=False)


@app.get("/api/v1/groups/{group_id}/messages", response_model=list[GroupMessage])
def list_group_messages(group_id: UUID) -> list[GroupMessage]:
    try:
        return repository.list_group_messages(group_id)
    except KeyError as error:
        raise not_found(error) from error


class GroupStep(BaseModel):
    speaker: str | None = None
    pending: int = 0


@app.post("/api/v1/groups/{group_id}/messages", response_model=GroupMessage, status_code=status.HTTP_201_CREATED)
async def post_group_message(group_id: UUID, payload: GroupMessageInput) -> GroupMessage:
    try:
        group = repository.get_group(group_id)
        message = repository.append_group_message(group_id, "user", payload.content)
    except KeyError as error:
        raise not_found(error) from error
    # The previous turn stops. The bot reads this message and decides whether it is a stop, a continuation, or a new task.
    runtime.interrupt_group(group_id)
    runtime.schedule_group_reply(group_id, payload.content, None, 0)
    return message


@app.post("/api/v1/groups/{group_id}/advance", response_model=GroupStep)
async def advance_group_message(group_id: UUID) -> GroupStep:
    try:
        repository.get_group(group_id)
    except KeyError as error:
        raise not_found(error) from error
    speaker = await runtime.advance_group(group_id)
    return GroupStep(speaker=speaker, pending=repository.group_queue_size(group_id))


@app.post("/api/v1/groups/{group_id}/cancel", response_model=list[Run])
def cancel_group_runs(group_id: UUID) -> list[Run]:
    try:
        repository.get_group(group_id)
        runs = repository.runs_for_group(group_id)
        runtime.interrupt_group(group_id)
        return [repository.get_run(run.id) for run in runs]
    except KeyError as error:
        raise not_found(error) from error


@app.post("/api/v1/bots/{bot_id}/handoffs", response_model=Handoff, status_code=status.HTTP_202_ACCEPTED)
async def create_handoff(bot_id: UUID, payload: HandoffInput) -> Handoff:
    try:
        source = repository.get_bot(bot_id)
        if source.status is not BotStatus.ACTIVE:
            raise HTTPException(status_code=409, detail="source bot is archived")
        target = repository.get_bot(payload.target_bot_id)
        if target.status is not BotStatus.ACTIVE:
            raise HTTPException(status_code=409, detail="target bot is archived")
        handoff = repository.create_handoff(bot_id, payload.target_bot_id, payload.task, None)
        conversation_id = repository.conversation_for_bot(target.id)
    except KeyError as error:
        raise not_found(error) from error
    repository.append_message(conversation_id, f"bot:{source.id}", f"Delegasi dari {source.name}: {payload.task}")
    run = repository.create_run(target.id, conversation_id, payload.task, _model_for_bot(target))
    handoff = repository.set_handoff_child(handoff.id, run.id)
    await _start_run(run.id)
    return handoff


@app.get("/api/v1/bots/{bot_id}/handoffs", response_model=list[Handoff])
def list_handoffs(bot_id: UUID) -> list[Handoff]:
    try:
        repository.get_bot(bot_id)
        return repository.list_handoffs(bot_id)
    except KeyError as error:
        raise not_found(error) from error


@app.delete("/api/v1/bots/{bot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bot(bot_id: UUID) -> None:
    try:
        repository.delete_bot(bot_id)
    except KeyError as error:
        raise not_found(error) from error


@app.post("/api/v1/bots/{bot_id}/archive", response_model=Bot)
def archive_bot(bot_id: UUID) -> Bot:
    try:
        return repository.set_bot_status(bot_id, BotStatus.ARCHIVED)
    except KeyError as error:
        raise not_found(error) from error


@app.post("/api/v1/bots/{bot_id}/restore", response_model=Bot)
def restore_bot(bot_id: UUID) -> Bot:
    try:
        return repository.set_bot_status(bot_id, BotStatus.ACTIVE)
    except KeyError as error:
        raise not_found(error) from error


@app.get("/api/v1/bots/{bot_id}/messages", response_model=list[Message])
def list_messages(bot_id: UUID) -> list[Message]:
    try:
        return repository.list_messages(repository.conversation_for_bot(bot_id))
    except KeyError as error:
        raise not_found(error) from error


@app.patch("/api/v1/bots/{bot_id}/messages/{message_id}", response_model=Message)
def edit_message(bot_id: UUID, message_id: UUID, payload: MessageEditInput) -> Message:
    try:
        conversation_id = repository.conversation_for_bot(bot_id)
        existing = repository.get_message(message_id)
        if existing.conversation_id != conversation_id:
            raise KeyError("message does not belong to bot conversation")
        message = repository.edit_message(message_id, payload.content)
        if message.conversation_id != conversation_id:
            raise KeyError("message does not belong to bot conversation")
        return message
    except KeyError as error:
        raise not_found(error) from error


@app.post("/api/v1/bots/{bot_id}/regenerate", response_model=Run, status_code=status.HTTP_202_ACCEPTED)
async def regenerate(bot_id: UUID, payload: RegenerateInput | None = None) -> Run:
    try:
        bot = repository.get_bot(bot_id)
        if bot.status is not BotStatus.ACTIVE:
            raise HTTPException(status_code=409, detail="bot is archived")
        conversation_id = repository.conversation_for_bot(bot_id)
        latest_user = next(
            (message for message in reversed(repository.list_messages(conversation_id)) if message.role == "user"),
            None,
        )
        if latest_user is None:
            raise HTTPException(status_code=409, detail="no user message to regenerate")
        run = repository.create_run(
            bot_id,
            conversation_id,
            latest_user.content,
            _model_for_bot(bot, (payload.model if payload else None) or latest_user.model),
        )
        await _start_run(run.id)
        return repository.get_run(run.id)
    except KeyError as error:
        raise not_found(error) from error


@app.post("/api/v1/bots/{bot_id}/messages", response_model=Run, status_code=status.HTTP_202_ACCEPTED)
async def send_message(bot_id: UUID, payload: MessageInput) -> Run:
    try:
        bot = repository.get_bot(bot_id)
        if bot.status is not BotStatus.ACTIVE:
            raise HTTPException(status_code=409, detail="bot is archived")
        conversation_id = repository.conversation_for_bot(bot_id)
    except KeyError as error:
        raise not_found(error) from error
    repository.append_message(
        conversation_id,
        "user",
        payload.content,
        model=payload.model,
        attachments=payload.attachments,
    )
    runtime.interrupt_bot(bot_id)
    if pending := runtime.pending_dm_run(bot_id):
        return repository.get_run(runtime.queue_dm(bot_id, pending, payload.content))
    run = repository.create_run(
        bot_id,
        conversation_id,
        (
            f"Lanjutkan tahap yang sama dari pekerjaan yang terhenti. "
            f"Catatan tahap sebelumnya: {interrupted.continuation}\n\n"
            f"Pesan pengguna: {payload.content}"
            if is_resume_request(payload.content)
            and (interrupted := repository.latest_interrupted_run(bot_id)) is not None
            else payload.content
        ),
        _model_for_bot(bot, payload.model),
    )
    run_id = runtime.queue_dm(bot_id, run.id, payload.content)
    if settings.await_runs:
        task = runtime._burst_tasks.get(("dm", bot_id))
        if task is not None:
            await task
    return repository.get_run(run_id)


@app.get("/api/v1/runs/{run_id}", response_model=Run)
def get_run(run_id: UUID) -> Run:
    try:
        return repository.get_run(run_id)
    except KeyError as error:
        raise not_found(error) from error


@app.get("/api/v1/runs/{run_id}/events/stream")
async def stream_run_events(run_id: UUID) -> StreamingResponse:
    try:
        repository.get_run(run_id)
    except KeyError as error:
        raise not_found(error) from error

    async def events() -> AsyncIterator[str]:
        cursor = 0
        terminal = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.FAILED_RETRYABLE, RunStatus.CANCELLED}
        while True:
            for event in repository.list_events(run_id):
                if event.id <= cursor:
                    continue
                cursor = event.id
                yield f"event: {event.type}\ndata: {json.dumps(event.model_dump(mode='json'))}\n\n"
            run = repository.get_run(run_id)
            if run.status in terminal:
                yield f"event: run.completed\ndata: {json.dumps(run.model_dump(mode='json'))}\n\n"
                break
            yield ": keep-alive\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/api/v1/runs/{run_id}/cancel", response_model=Run)
def cancel_run(run_id: UUID) -> Run:
    try:
        runtime.stop(run_id)
        return repository.get_run(run_id)
    except KeyError as error:
        raise not_found(error) from error


@app.get("/api/v1/runs/{run_id}/events", response_model=list[RunEvent])
def list_run_events(run_id: UUID) -> list[RunEvent]:
    try:
        repository.get_run(run_id)
    except KeyError as error:
        raise not_found(error) from error
    return repository.list_events(run_id)


@app.post("/api/v1/runs/{run_id}/approvals", response_model=Approval, status_code=status.HTTP_201_CREATED)
def propose_approval(run_id: UUID, payload: ApprovalProposal) -> Approval:
    try:
        run = repository.get_run(run_id)
    except KeyError as error:
        raise not_found(error) from error
    decision = policy.decide(payload.risk_class)
    if not decision.requires_approval:
        raise HTTPException(status_code=409, detail="this action class does not require approval")
    if run.status is not RunStatus.WAITING_APPROVAL:
        raise HTTPException(status_code=409, detail="run is not waiting for approval")
    matching = next(
        (
            approval
            for approval in repository.list_approvals(ApprovalStatus.PENDING)
            if approval.run_id == run_id
            and approval.tool_name == payload.tool_name
            and approval.risk_class == payload.risk_class
            and approval.payload == payload.payload
        ),
        None,
    )
    if matching is None:
        raise HTTPException(status_code=409, detail="approval is not bound to a pending tool call")
    return matching


@app.get("/api/v1/approvals", response_model=list[Approval])
def list_approvals(status_filter: ApprovalStatus | None = Query(default=None, alias="status")) -> list[Approval]:
    return repository.list_approvals(status_filter)


@app.post("/api/v1/approvals/{approval_id}/approve", response_model=Approval)
async def approve(approval_id: UUID) -> Approval:
    try:
        approval = repository.decide_approval(approval_id, ApprovalStatus.APPROVED)
        runtime.resume_after_approval(approval_id)
        if settings.await_runs:
            task = runtime._tasks.get(approval.run_id)
            if task is not None:
                await task
        return repository.get_approval(approval_id)
    except KeyError as error:
        raise not_found(error) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/v1/approvals/{approval_id}/reject", response_model=Approval)
async def reject(approval_id: UUID) -> Approval:
    try:
        approval = repository.decide_approval(approval_id, ApprovalStatus.REJECTED)
        runtime.resume_after_approval(approval_id)
        if settings.await_runs:
            task = runtime._tasks.get(approval.run_id)
            if task is not None:
                await task
        return repository.get_approval(approval_id)
    except KeyError as error:
        raise not_found(error) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/v1/bots/{bot_id}/memories", response_model=list[Memory])
def list_memories(bot_id: UUID) -> list[Memory]:
    try:
        repository.get_bot(bot_id)
    except KeyError as error:
        raise not_found(error) from error
    return repository.list_memories(bot_id)
