from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PLACEHOLDERS = {"", "[REDACTED]", "REDACTED"}


def running_on_vercel() -> bool:
    return os.getenv("VERCEL") == "1" or bool(os.getenv("VERCEL_ENV"))


def _env_value(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() in _PLACEHOLDERS:
        return None
    return raw.strip()


def _env_path(name: str, default: Path) -> Path:
    value = _env_value(name)
    return Path(value) if value else default


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    workspace_root: Path
    dev_user_email: str
    openrouter_api_key: str | None
    openrouter_base_url: str
    default_model: str
    model_gateway: str
    max_model_calls_per_run: int
    api_auth_token: str | None
    web_origin: str
    openai_client_id: str | None
    openai_client_secret: str | None
    openai_redirect_uri: str
    openai_scopes: str
    await_runs: bool
    cors_allow_all: bool
    environment_name: str
    daytona_api_key: str | None
    daytona_api_url: str
    daytona_target: str

    @classmethod
    def from_environment(cls) -> "Settings":
        vercel = running_on_vercel()
        if not vercel:
            # Load the project config even when Uvicorn is launched from another directory.
            # Existing process environment values take precedence over the local file.
            load_dotenv(PROJECT_ROOT / ".env", override=False)
        data_root = Path("/tmp/bandros") if vercel else PROJECT_ROOT / "data"
        openrouter_key = _env_value("OPENROUTER_API_KEY")
        gateway = _env_value("MODEL_GATEWAY")
        if gateway is None:
            gateway = "mock" if vercel and not openrouter_key else "openrouter"
        return cls(
            database_path=_env_path("DATABASE_PATH", data_root / "workspace.db"),
            workspace_root=_env_path("WORKSPACE_ROOT", data_root / "workspace"),
            dev_user_email=_env_value("DEV_USER_EMAIL") or "local@example.test",
            openrouter_api_key=openrouter_key,
            openrouter_base_url=(
                _env_value("OPENROUTER_BASE_URL") or ("https://" + "openrouter.ai/api/v1")
            ).rstrip("/"),
            default_model=_env_value("DEFAULT_MODEL") or "openrouter/free",
            model_gateway=gateway,
            max_model_calls_per_run=int(_env_value("MAX_MODEL_CALLS_PER_RUN") or "8"),
            api_auth_token=_env_value("API_AUTH_TOKEN"),
            web_origin=_env_value("WEB_ORIGIN") or "http://127.0.0.1:3000",
            openai_client_id=_env_value("OPENAI_CLIENT_ID"),
            openai_client_secret=_env_value("OPENAI_CLIENT_SECRET"),
            openai_redirect_uri=_env_value("OPENAI_REDIRECT_URI")
            or "http://127.0.0.1:8000/api/v1/auth/chatgpt/callback",
            openai_scopes=_env_value("OPENAI_SCOPES")
            or "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
            await_runs=vercel or (_env_value("AWAIT_RUNS") == "1"),
            cors_allow_all=vercel or (_env_value("CORS_ALLOW_ALL") == "1"),
            environment_name=_env_value("VERCEL_ENV") or ("vercel" if vercel else "local"),
            daytona_api_key=_env_value("DAYTONA_API_KEY"),
            daytona_api_url=(_env_value("DAYTONA_API_URL") or "https://app.daytona.io/api").rstrip("/"),
            daytona_target=_env_value("DAYTONA_TARGET") or "us",
        )
