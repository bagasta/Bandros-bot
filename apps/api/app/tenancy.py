from __future__ import annotations

import hashlib
from pathlib import Path


def tenant_digest(account_id: str) -> str:
    cleaned = account_id.strip()
    if not cleaned:
        raise ValueError("account id required")
    return hashlib.sha256(cleaned.encode()).hexdigest()


def tenant_locations(account_id: str, data_root: Path, workspace_root: Path) -> tuple[Path, Path, str]:
    """Return the sqlite path, file workspace, and private blob key for one ChatGPT account."""
    digest = tenant_digest(account_id)
    return (
        data_root / "users" / digest / "workspace.db",
        workspace_root / "users" / digest,
        f"state/users/{digest}/workspace.db",
    )
