from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import json
import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import AsyncIterator
from uuid import UUID
from urllib.parse import quote, urlencode

import httpx
import jwt
from jwt import PyJWKClient

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from .database import Database
from .domain import AssignSkill, Approval, ApprovalProposal, ApprovalStatus, Bot, BotStatus, CreateBot, GroupInput, GroupMessage, GroupMessageInput, Handoff, HandoffInput, Job, JobInput, JobUpdate, Memory, Message, MessageEditInput, MessageInput, RegenerateInput, Run, RunEvent, RunStatus, Skill, SkillInput, UpdateBot, WorkGroup
from .model_gateway import ChatGPTGateway, CompositeGateway, MockGateway, OpenRouterGateway
from .policy import PolicyEngine
from .repository import Repository
from .runtime import RunRuntime
from .settings import Settings, running_on_vercel


settings = Settings.from_environment()
repository = Repository(Database(settings.database_path))
runtime = RunRuntime(
    repository=repository,
    model_gateway=CompositeGateway(
        MockGateway()
        if settings.model_gateway == "mock"
        else OpenRouterGateway(settings.openrouter_api_key, settings.openrouter_base_url),
        ChatGPTGateway(
            repository.chatgpt_connection,
            lambda connection: _refresh_chatgpt_token(connection),
        ),
    ),
    default_model=settings.default_model,
    max_model_calls=settings.max_model_calls_per_run,
    workspace_root=settings.workspace_root,
)
policy = PolicyEngine()
openai_jwks = PyJWKClient("https://auth.openai.com/.well-known/jwks.json")


def _jwt_payload(token: str) -> dict[str, str]:
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
            "https://auth.openai.com/api/accounts/oauth/token",
            headers=_openai_token_headers(str(client_id)),
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
                "resource": "https://api.openai.com/v1",
            },
        )
    if response.is_error:
        return None
    tokens = response.json()
    access_token = tokens.get("access_token")
    if not access_token:
        return None
    expires = tokens.get("expires_in")
    repository.update_chatgpt_tokens(
        access_token=access_token,
        refresh_token=tokens.get("refresh_token"),
        expires_at=datetime.now(UTC) + timedelta(seconds=int(expires)) if expires else None,
        scope=tokens.get("scope", str(connection.get("scope") or "")),
    )
    return access_token

DEFAULT_SKILLS = (
    ("workspace_admin", "Membuat, mengubah, dan mengarsipkan Bot sesuai struktur tim.", "Kelola identitas Bot dan tanggung jawabnya secara jelas."),
    ("job_manager", "Membuat dan memperbarui job yang terukur untuk anggota tim.", "Setiap job punya pemilik, prioritas, dan status."),
    ("coordination", "Mendelegasikan pekerjaan dan mengirim pembaruan ke grup kerja.", "Delegasi harus spesifik dan diberikan ke Bot yang tepat."),
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    repository.database.initialize()
    known_names = {skill.name for skill in repository.list_skills()}
    for name, description, content in DEFAULT_SKILLS:
        if name not in known_names:
            repository.create_skill(name, description, content)
    default_skill_ids = {skill.name: skill.id for skill in repository.list_skills()}
    for bot in repository.list_bots():
        if "manager" in bot.name.lower():
            for skill_name in ("workspace_admin", "job_manager", "coordination"):
                skill_id = default_skill_ids.get(skill_name)
                if skill_id:
                    repository.assign_skill(bot.id, skill_id)
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    runtime.recover()
    yield


app = FastAPI(title="Persistent Agent Workspace", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.cors_allow_all else ["http://localhost:3000", "http://127.0.0.1:3000", settings.web_origin],
    allow_origin_regex=None if settings.cors_allow_all else r"https://.*\.vercel\.app",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.middleware("http")
async def optional_auth(request: Request, call_next):
    public_oauth = request.url.path in {"/api/v1/auth/chatgpt/start", "/api/v1/auth/chatgpt/callback"}
    public_health = request.url.path in {"/health", "/"}
    if settings.api_auth_token and not public_health and not public_oauth and request.method != "OPTIONS":
        expected = f"Bearer {settings.api_auth_token}"
        provided = request.headers.get("Authorization")
        if provided != expected:
            return JSONResponse({"detail": "authentication required"}, status_code=401)
    return await call_next(request)


def not_found(error: KeyError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))


def _model_for_bot(bot: Bot, requested: str | None = None) -> str:
    if requested:
        return requested
    if bot.model:
        return bot.model
    connection = repository.chatgpt_connection()
    if (
        connection
        and "chatgpt.tokens.use.direct" in str(connection.get("scope") or "").split()
        and connection.get("preferred_model")
    ):
        return f"chatgpt/{connection['preferred_model']}"
    return settings.default_model


async def _start_run(run_id: UUID) -> None:
    if settings.await_runs:
        await runtime.start_and_wait(run_id)
    else:
        runtime.start(run_id)


@app.get("/")
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment_name}


@app.get("/api/v1/auth/chatgpt/start")
def chatgpt_auth_start() -> dict[str, str]:
    existing = repository.chatgpt_connection() or {}
    configured_client_id = settings.openai_client_id or existing.get("client_id")
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
    host_id = str(existing.get("host_id") or f"urn:uuid:{secrets.token_hex(16)}")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    client_id = str(configured_client_id or "dynamic_agent_client")
    repository.create_oauth_transaction(
        state,
        verifier,
        nonce,
        client_id,
        host_id,
        datetime.now(UTC) + timedelta(minutes=10),
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
    if existing.get("id_token"):
        query += f"&{urlencode({'id_token_hint': existing['id_token'], 'login_hint': existing.get('email', '')})}"
    return {"authorization_url": f"https://auth.openai.com/api/accounts/authorize?{query}"}


@app.get("/api/v1/auth/chatgpt/callback")
async def chatgpt_auth_callback(code: str | None = None, state: str | None = None, error: str | None = None, client_id: str | None = None) -> dict[str, str]:
    if not state:
        raise HTTPException(status_code=400, detail="OAuth state tidak valid atau sudah kedaluwarsa")
    transaction = repository.consume_oauth_transaction(state)
    if not transaction:
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
    existing_connection = repository.chatgpt_connection()
    if existing_connection and existing_connection.get("client_id") and issued_client_id != existing_connection["client_id"]:
        raise HTTPException(status_code=400, detail="Client ID ChatGPT tidak cocok dengan koneksi tersimpan")
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
    repository.save_chatgpt_connection(
        client_id=issued_client_id,
        host_id=host_id,
        preferred_model=preferred_model,
        access_token=access_token,
        refresh_token=tokens.get("refresh_token"),
        id_token=id_token,
        subject=claims.get("sub"),
        email=claims.get("email"),
        expires_at=datetime.now(UTC) + timedelta(seconds=int(expires)) if expires else None,
        scope=granted_scope,
    )
    return RedirectResponse(url=f"{settings.web_origin}?chatgpt=connected", status_code=303)


@app.get("/api/v1/auth/chatgpt/status")
def chatgpt_auth_status() -> dict[str, object]:
    connection = repository.chatgpt_connection()
    if not connection:
        return {
            "connected": False,
            "available": not running_on_vercel() or bool(settings.openai_client_id),
            "reason": None
            if not running_on_vercel() or settings.openai_client_id
            else "Website OAuth membutuhkan OPENAI_CLIENT_ID dari OpenAI.",
        }
    return {
        "connected": True,
        "available": True,
        "email": connection.get("email"),
        "subject": connection.get("subject"),
        "scope": connection.get("scope"),
        "expires_at": connection.get("expires_at"),
        "subscription_enabled": "chatgpt.tokens.use.direct" in connection.get("scope", "").split(),
        "preferred_model": connection.get("preferred_model"),
    }


@app.post("/api/v1/auth/chatgpt/disconnect")
def chatgpt_auth_disconnect() -> dict[str, bool]:
    repository.clear_chatgpt_connection()
    return {"disconnected": True}


@app.get("/api/v1/auth/chatgpt/models")
async def chatgpt_models() -> dict[str, object]:
    connection = repository.chatgpt_connection()
    if not connection:
        raise HTTPException(status_code=404, detail="ChatGPT belum terhubung")
    if connection.get("refresh_token") and ChatGPTGateway._expiring(connection.get("expires_at")):
        await _refresh_chatgpt_token(connection)
        connection = repository.chatgpt_connection() or connection
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
    return {"models": models}


@app.get("/api/v1/bots", response_model=list[Bot])
def list_bots() -> list[Bot]:
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
        return repository.update_bot(bot_id, payload.model_dump())
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


@app.get("/api/v1/groups/{group_id}/messages", response_model=list[GroupMessage])
def list_group_messages(group_id: UUID) -> list[GroupMessage]:
    try:
        return repository.list_group_messages(group_id)
    except KeyError as error:
        raise not_found(error) from error


@app.post("/api/v1/groups/{group_id}/messages", response_model=GroupMessage, status_code=status.HTTP_201_CREATED)
async def post_group_message(group_id: UUID, payload: GroupMessageInput) -> GroupMessage:
    try:
        group = repository.get_group(group_id)
        message = repository.append_group_message(group_id, "user", payload.content)
    except KeyError as error:
        raise not_found(error) from error
    for member in group.members:
        if member.status is BotStatus.ACTIVE:
            conversation_id = repository.conversation_for_bot(member.id)
            prompt = f"Pesan grup {group.name} dari Bos: {payload.content}\nBalas dengan update singkat untuk grup."
            repository.append_message(conversation_id, "group", prompt)
            run = repository.create_run(member.id, conversation_id, prompt, _model_for_bot(member))
            repository.link_run_to_group(run.id, group_id)
            await _start_run(run.id)
    return message


@app.post("/api/v1/groups/{group_id}/cancel", response_model=list[Run])
def cancel_group_runs(group_id: UUID) -> list[Run]:
    try:
        repository.get_group(group_id)
        runs = repository.runs_for_group(group_id)
        for run in runs:
            runtime.stop(run.id)
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
    run = repository.create_run(
        bot_id,
        conversation_id,
        payload.content,
        _model_for_bot(bot, payload.model),
    )
    await _start_run(run.id)
    return repository.get_run(run.id)


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
