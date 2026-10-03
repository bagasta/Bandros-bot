"""One small Daytona container per account. It stays archived until a tool needs it."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx


SNAPSHOT = "daytona-small"
AUTO_STOP_MINUTES = 5
AUTO_ARCHIVE_MINUTES = 60
PREVIEW_HOLD_SECONDS = 8
PREVIEW_GRACE_SECONDS = 4
WORKSPACE = "/home/daytona/workspace"
_SKIP_STATES = {"destroyed", "destroying", "deleted", "error", "unknown"}
_LIVE_STATES = {"started", "starting", "creating"}


def _account_lock(account_id: str) -> threading.RLock:
    with _ACCOUNT_LOCKS_GUARD:
        return _ACCOUNT_LOCKS.setdefault(account_id, threading.RLock())


_ACCOUNT_LOCKS: dict[str, threading.RLock] = {}
_ACCOUNT_LOCKS_GUARD = threading.Lock()


class ComputerError(RuntimeError):
    pass


class DaytonaComputer:
    """Filesystem and shell on a 1 vCPU container with lazy desktop activation."""

    def __init__(
        self,
        api_key: str,
        api_url: str,
        account_id: str,
        target: str = "us",
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] | None = None,
        preview_hold_seconds: float = PREVIEW_HOLD_SECONDS,
        preview_grace_seconds: float = PREVIEW_GRACE_SECONDS,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.account_id = account_id
        self.target = target
        self.preview_hold_seconds = preview_hold_seconds
        self.preview_grace_seconds = preview_grace_seconds
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._client = client or httpx.Client(timeout=30)
        self._sleep = sleep
        self._now = now or time.monotonic
        self._sandbox_id: str | None = None
        self._account_lock = _account_lock(account_id)
        self._preview_seen = threading.Event()

    def list_dir(self, relative: str) -> list[dict[str, str]]:
        remote = self._remote(relative)
        response = self._toolbox("GET", "/files", params={"path": remote})
        if response.status_code == 404:
            raise ComputerError("directory not found")
        self._raise_for_status(response)
        entries = []
        for item in response.json():
            name = str(item.get("name") or Path(str(item.get("path", ""))).name)
            if not name or name in {".", ".."}:
                continue
            kind = "directory" if item.get("isDir") else "file"
            parent = "." if relative in {"", "."} else relative.strip("/")
            child = name if parent == "." else f"{parent}/{name}"
            entries.append({"path": child, "type": kind})
        return entries[:200]

    def read_text(self, relative: str) -> str:
        response = self._toolbox("GET", "/files/download", params={"path": self._remote(relative)})
        if response.status_code == 404:
            raise ComputerError("file not found")
        self._raise_for_status(response)
        return response.content.decode("utf-8", errors="replace")[:100_000]

    def write_text(self, relative: str, content: str) -> None:
        remote = self._remote(relative)
        parent = str(Path(remote).parent)
        self._toolbox("POST", "/files/folder", params={"path": parent, "mode": "755"})
        response = self._toolbox(
            "POST",
            "/files/upload",
            params={"path": remote},
            files={"file": ("file.txt", content.encode("utf-8"), "text/plain")},
        )
        self._raise_for_status(response)

    def screenshot(self) -> str:
        response = self._toolbox("GET", "/computeruse/screenshot")
        self._raise_for_status(response)
        body = response.json()
        image = body.get("screenshot") or body.get("image") or body.get("data") or ""
        return str(image)[:180_000]

    def click(self, x: int, y: int) -> None:
        response = self._toolbox("POST", "/computeruse/mouse/click", json={"x": int(x), "y": int(y), "button": "left"})
        self._raise_for_status(response)

    def type_text(self, text: str) -> None:
        response = self._toolbox("POST", "/computeruse/keyboard/type", json={"text": text})
        self._raise_for_status(response)

    def run(self, command: str) -> dict[str, Any]:
        response = self._toolbox(
            "POST",
            "/process/execute",
            json={"command": command, "cwd": WORKSPACE, "timeout": 15},
        )
        self._raise_for_status(response)
        body = response.json()
        return {"exit_code": int(body.get("exitCode") or 0), "output": str(body.get("result") or "")[:8_000]}

    def status(self) -> dict[str, str | None]:
        sandbox = self._find()
        if sandbox is None or sandbox.get("state") != "started":
            return {"state": "off", "screen_url": None}
        screen_url = self._preview_origin(str(sandbox["id"]))
        if screen_url:
            self._preview_seen.set()
        return {"state": "on", "screen_url": screen_url}

    def wake(self) -> dict[str, str | None]:
        """Start the small sandbox and its desktop. Caller must park it again."""
        self._preview_seen.clear()
        with self._account_lock:
            sandbox = self._ensure()
            started = self._toolbox_raw(sandbox, "POST", "/computeruse/start")
            self._raise_for_status(started)
            return {
                "state": "on",
                "screen_url": self._wait_for_preview(str(sandbox["id"])),
            }

    def preview_origin(self) -> str | None:
        sandbox = self._find()
        if sandbox is None or sandbox.get("state") != "started":
            return None
        return self._preview_origin(str(sandbox["id"]))

    def _preview_origin(self, sandbox_id: str) -> str | None:
        try:
            response = self._send(
                "GET",
                f"/sandbox/{sandbox_id}/ports/6080/signed-preview-url",
                params={"expiresInSeconds": 3600},
            )
        except ComputerError:
            return None
        url = response.json().get("url")
        if not url:
            return None
        parts = urlsplit(str(url))
        return urlunsplit((parts.scheme, parts.netloc, "", "", ""))

    def park_after_preview(self) -> None:
        """Leave the desktop URL readable until the panel can show it, then park."""
        sandbox = self._find()
        if sandbox is not None and sandbox.get("state") == "started":
            self._wait_for_preview_view()
        self.park()

    def _wait_for_preview_view(self) -> None:
        deadline = self._now() + self.preview_hold_seconds
        visible_until: float | None = None
        while True:
            now = self._now()
            if self._preview_seen.is_set() and visible_until is None:
                visible_until = now + self.preview_grace_seconds
            if visible_until is not None and now >= visible_until:
                return
            if now >= deadline:
                return
            remaining = deadline - now
            if visible_until is not None:
                remaining = min(remaining, visible_until - now)
            self._sleep(min(0.2, max(remaining, 0)))

    def park(self) -> None:
        """Stop compute billing, then archive so disk is not billed either."""
        with self._account_lock:
            sandbox = self._find()
            if sandbox is None:
                return
            self._park_sandbox(sandbox)

    def _park_sandbox(self, sandbox: dict[str, Any]) -> None:
        sandbox_id = str(sandbox["id"])
        state = str(sandbox.get("state") or "")
        if state in _LIVE_STATES or state == "stopping":
            self._send("POST", f"/sandbox/{sandbox_id}/stop")
            sandbox = self._wait(sandbox_id, {"stopped", "archived"})
            state = str(sandbox.get("state") or "")
        if state == "stopped":
            self._send("POST", f"/sandbox/{sandbox_id}/archive")
            self._wait(sandbox_id, {"archived", "stopped"})

    def _ensure(self) -> dict[str, Any]:
        with self._account_lock:
            return self._ensure_unlocked()

    def _ensure_unlocked(self) -> dict[str, Any]:
        sandbox = self._find()
        if sandbox is None:
            created = self._send(
                "POST",
                "/sandbox",
                json={
                    "snapshot": SNAPSHOT,
                    "name": f"bandros-{self.account_id[:12]}",
                    "labels": {"app": "bandros", "account": self.account_id},
                    "autoStopInterval": AUTO_STOP_MINUTES,
                    "autoArchiveInterval": AUTO_ARCHIVE_MINUTES,
                    "public": False,
                    "target": self.target,
                    "env": {"VNC_RESOLUTION": "1280x800"},
                },
            )
            sandbox = created.json()
            discovered = self._find()
            if discovered is not None:
                sandbox = discovered
        sandbox_id = str(sandbox["id"])
        self._sandbox_id = sandbox_id
        state = str(sandbox.get("state") or "")
        if state not in {"started", "starting", "creating"}:
            self._send("POST", f"/sandbox/{sandbox_id}/start")
            state = "starting"
        if state != "started":
            sandbox = self._wait(sandbox_id, {"started"})
        self._toolbox_raw(sandbox, "POST", "/files/folder", params={"path": WORKSPACE, "mode": "755"})
        return sandbox

    def _find(self) -> dict[str, Any] | None:
        cursor = None
        candidates: list[dict[str, Any]] = []
        while True:
            params: dict[str, str] = {"labels": json.dumps({"app": "bandros", "account": self.account_id})}
            if cursor:
                params["cursor"] = cursor
            body = self._send("GET", "/sandbox", params=params).json()
            for item in body.get("items") or []:
                labels = item.get("labels") or {}
                if labels.get("app") != "bandros" or labels.get("account") != self.account_id:
                    continue
                if str(item.get("state") or "") in _SKIP_STATES:
                    continue
                candidates.append(item)
            cursor = body.get("nextCursor")
            if not cursor:
                break

        if not candidates:
            return None

        priority = {"started": 0, "starting": 1, "creating": 2, "stopping": 3, "stopped": 4, "archived": 5}
        chosen = min(candidates, key=lambda item: priority.get(str(item.get("state") or ""), 6))
        for duplicate in candidates:
            if duplicate is not chosen:
                self._park_sandbox(duplicate)
        self._sandbox_id = str(chosen["id"])
        return chosen

    def _wait(self, sandbox_id: str, targets: set[str]) -> dict[str, Any]:
        deadline = time.monotonic() + 90
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self._send("GET", f"/sandbox/{sandbox_id}").json()
            state = str(last.get("state") or "")
            if state in targets:
                return last
            if state in _SKIP_STATES:
                raise ComputerError(state)
            self._sleep(0.4)
        raise ComputerError(f"sandbox stayed {last.get('state')}")

    def _wait_for_preview(self, sandbox_id: str) -> str:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            origin = self._preview_origin(sandbox_id)
            if origin:
                return origin
            self._sleep(0.4)
        raise ComputerError("desktop preview did not become ready")

    def _remote(self, relative: str) -> str:
        raw = (relative or ".").strip() or "."
        path = Path(raw)
        if path.is_absolute() or any(part == ".." for part in path.parts):
            raise ComputerError("path must stay inside the Bot workspace")
        if path == Path("."):
            return WORKSPACE
        return f"{WORKSPACE}/{path.as_posix()}"

    def _toolbox(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return self._toolbox_raw(self._ensure(), method, path, **kwargs)

    def _toolbox_raw(self, sandbox: dict[str, Any], method: str, path: str, **kwargs: Any) -> httpx.Response:
        proxy = str(sandbox.get("toolboxProxyUrl") or "").rstrip("/")
        if not proxy:
            sandbox = self._send("GET", f"/sandbox/{sandbox['id']}").json()
            proxy = str(sandbox.get("toolboxProxyUrl") or "").rstrip("/")
        if not proxy:
            raise ComputerError("sandbox has no toolbox")
        response = self._client.request(method, f"{proxy}/{sandbox['id']}{path}", headers=self._headers, **kwargs)
        if response.status_code >= 400 and response.status_code != 404:
            self._raise_for_status(response)
        return response

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, f"{self.api_url}{path}", headers=self._headers, **kwargs)
        self._raise_for_status(response)
        return response

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        raise ComputerError(f"daytona {response.status_code}: {response.text[:300]}")
