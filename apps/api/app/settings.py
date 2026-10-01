from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]


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
    openai_client_id: str | None
    openai_client_secret: str | None
    openai_redirect_uri: str
    openai_scopes: str

    @classmethod
    def from_environment(cls) -> "Settings":
        # Load the project config even when Uvicorn is launched from another directory.
        # Existing process environment values take precedence over the local file.
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        return cls(
            database_path=Path(os.getenv("DATABASE_PATH", "./data/agent-workspace.db")),
            workspace_root=Path(os.getenv("WORKSPACE_ROOT", "./data/workspaces")),
            dev_user_email=os.getenv("DEV_USER_EMAIL", "local@example.test"),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
            openrouter_base_url=os.getenv(
                "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
            ).rstrip("/"),
            default_model=os.getenv("DEFAULT_MODEL", "openrouter/free"),
            model_gateway=os.getenv("MODEL_GATEWAY", "openrouter"),
            max_model_calls_per_run=int(os.getenv("MAX_MODEL_CALLS_PER_RUN", "8")),
            api_auth_token=os.getenv("API_AUTH_TOKEN") or None,
            openai_client_id=os.getenv("OPENAI_CLIENT_ID") or None,
            openai_client_secret=os.getenv("OPENAI_CLIENT_SECRET") or None,
            openai_redirect_uri=os.getenv(
                "OPENAI_REDIRECT_URI", "http://127.0.0.1:8000/api/v1/auth/chatgpt/callback"
            ),
            openai_scopes=os.getenv(
                "OPENAI_SCOPES",
                "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
            ),
        )
