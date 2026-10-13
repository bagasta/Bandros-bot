"""One Firecracker microVM on this machine: 1.5 GiB RAM and a local ~2 GiB disk."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from typing import Callable

import httpx

from .computer import ComputerError


MEM_MIB = 1536
AGENT_PORT = 7000
# Host ports stay off the other project's Firecracker desktop (5902 / 6080).
VNC_PORT = 5912
VIEWER_PORT = 6090
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_KERNEL = Path("/home/bagas/firecracker-workspace/assets/vmlinux-6.1.102")
_DEFAULT_NOVNC = Path("/home/bagas/firecracker-workspace/novnc")
_LOCK = threading.Lock()


class FirecrackerComputer:
    """A single local desktop, persistently bound to one authenticated account."""

    def __init__(self, account_id: str, root: Path | None = None) -> None:
        self.account_id = account_id
        self.root = root or (_PROJECT_ROOT / "data" / "firecracker")
        self.run_dir = self.root / "run"
        self.kernel = Path(os.environ.get("FIRECRACKER_KERNEL") or _DEFAULT_KERNEL)
        self.rootfs = Path(os.environ.get("FIRECRACKER_ROOTFS") or (self.root / "rootfs.ext4"))
        self.novnc = Path(os.environ.get("FIRECRACKER_NOVNC") or _DEFAULT_NOVNC)
        self.vnc_port = int(os.environ.get("BANDROS_VNC_PORT") or VNC_PORT)
        self.viewer_port = int(os.environ.get("BANDROS_VIEWER_PORT") or VIEWER_PORT)
        self._sleep: Callable[[float], None] = time.sleep
        self._preview_seen = threading.Event()

    @property
    def screen_url(self) -> str:
        return f"http://127.0.0.1:{self.viewer_port}/vnc.html?autoconnect=1&resize=scale"

    def status(self) -> dict[str, str | None]:
        if not self._account_allowed():
            return {"state": "off", "screen_url": None}
        if not self._api_ready() or not _port_open(self.vnc_port):
            return {"state": "off", "screen_url": None}
        return {"state": "on", "screen_url": self.screen_url}

    def wake(self) -> dict[str, str | None]:
        self._ensure_running()
        return {"state": "on", "screen_url": self.screen_url}

    def keep_screen(self) -> None:
        self.note_preview_shown()
        self._ensure_running()

    def note_preview_shown(self) -> None:
        self._preview_seen.set()

    def park(self, *, respect_screen: bool = False) -> None:
        """Leave the local microVM running. Stopping it would drop the desktop."""

    def park_after_preview(self) -> None:
        self.park(respect_screen=True)

    def preview_origin(self) -> str | None:
        if not self._account_allowed():
            return None
        if not _port_open(self.viewer_port):
            return None
        return f"http://127.0.0.1:{self.viewer_port}"

    def run(self, command: str) -> dict[str, object]:
        code, output = self._rpc("run", command.encode())
        return {"exit_code": code, "output": output.decode("utf-8", errors="replace")[:8_000]}

    def read_text(self, relative: str) -> str:
        code, output = self._rpc("read", relative.encode())
        if code != 0:
            raise ComputerError(output.decode("utf-8", errors="replace") or "file not found")
        return output.decode("utf-8", errors="replace")[:100_000]

    def write_text(self, relative: str, content: str) -> None:
        code, output = self._rpc("write", f"{relative}\n{content}".encode())
        if code != 0:
            raise ComputerError(output.decode("utf-8", errors="replace") or "write failed")

    def list_dir(self, relative: str) -> list[dict[str, str]]:
        code, output = self._rpc("list", relative.encode())
        if code != 0:
            raise ComputerError(output.decode("utf-8", errors="replace") or "directory not found")
        entries = []
        for line in output.decode("utf-8", errors="replace").splitlines():
            kind, _, path = line.partition(" ")
            if kind in {"file", "directory"} and path:
                entries.append({"path": path, "type": kind})
        return entries[:200]

    def screenshot(self) -> str:
        self._ensure_running()
        with tempfile.NamedTemporaryFile(prefix="bandros-screen-", suffix=".png") as capture:
            self._vnc("capture", capture.name)
            return base64.b64encode(Path(capture.name).read_bytes()).decode("ascii")

    def click(self, x: int, y: int) -> None:
        self._ensure_running()
        self._vnc("move", str(int(x)), str(int(y)), "click", "1")

    def type_text(self, text: str) -> None:
        self._ensure_running()
        self._vnc("type", text)

    def press_key(self, key: str) -> None:
        self._ensure_running()
        normalized = key.strip().lower().replace("+", "-")
        allowed = {
            "enter", "tab", "escape", "backspace", "delete", "space",
            "up", "down", "left", "right",
            "ctrl-l", "ctrl-a", "ctrl-c", "ctrl-v", "ctrl-t", "ctrl-w",
        }
        if normalized not in allowed:
            raise ComputerError("Tombol tidak didukung.")
        self._vnc("key", normalized)

    def _ensure_running(self) -> None:
        self._claim_account()
        with _LOCK:
            if not self._api_ready():
                self._boot()
            self._start_viewers()
        self._wait_for_agent()

    def _account_allowed(self) -> bool:
        if self.account_id == "local":
            return True  # Trusted startup warmer, never a tenant session.
        try:
            owner = (self.root / "account-owner").read_text().strip()
        except FileNotFoundError:
            return False
        return owner == hashlib.sha256(self.account_id.encode()).hexdigest()

    def _claim_account(self) -> None:
        if self.account_id == "local":
            return
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.root / "account-owner", os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "r+") as owner_file:
            fcntl.flock(owner_file, fcntl.LOCK_EX)
            owner = owner_file.read().strip()
            identity = hashlib.sha256(self.account_id.encode()).hexdigest()
            if owner and owner != identity:
                raise ComputerError("Komputer lokal sudah terikat ke akun lain. Gunakan VM terpisah untuk akun ini.")
            if not owner:
                owner_file.write(identity)
                owner_file.flush()
                os.fsync(owner_file.fileno())

    def _boot(self) -> None:
        if not self.kernel.is_file():
            raise ComputerError(f"Kernel Firecracker tidak ada: {self.kernel}")
        if not self.rootfs.is_file():
            raise ComputerError(f"Disk Firecracker tidak ada: {self.rootfs}")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        log_dir = self.root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        socket_path = self.run_dir / "workspace.sock"
        self._stop_owned_firecracker()
        socket_path.unlink(missing_ok=True)
        (self.run_dir / "vsock.sock").unlink(missing_ok=True)
        for marker in ("network-namespace.ready", "slirp.ready", "network-namespace.pid", "slirp4netns.pid"):
            (self.run_dir / marker).unlink(missing_ok=True)
        log = open(log_dir / "firecracker.log", "ab")
        subprocess.Popen(
            ["bash", str(_PROJECT_ROOT / "scripts" / "firecracker" / "networked-firecracker.sh")],
            env={
                **os.environ,
                "FC_RUN_DIR": str(self.run_dir),
                "FC_FIRECRACKER_BIN": shutil.which("firecracker") or "firecracker",
            },
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        if not _wait(lambda: socket_path.is_socket(), 10, self._sleep):
            raise ComputerError("Socket API Firecracker tidak muncul.")
        self._configure()

    def _owned_firecracker_pid(self) -> int | None:
        socket_path = str(self.run_dir / "workspace.sock")
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                arguments = (entry / "cmdline").read_bytes().decode(errors="replace").split("\0")
            except OSError:
                continue
            if "--api-sock" in arguments:
                index = arguments.index("--api-sock")
                if index + 1 < len(arguments) and arguments[index + 1] == socket_path:
                    return int(entry.name)
        return None

    def _stop_owned_firecracker(self) -> None:
        pid = self._owned_firecracker_pid()
        if pid is None:
            return
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self._owned_firecracker_pid() != pid:
                return
            self._sleep(0.1)
        if self._owned_firecracker_pid() == pid:
            os.kill(pid, signal.SIGKILL)
            if not _wait(lambda: self._owned_firecracker_pid() != pid, 3, self._sleep):
                raise ComputerError("VM Firecracker lama tidak berhenti; disk guest tidak disentuh.")

    def _configure(self) -> None:
        socket_path = self.run_dir / "workspace.sock"
        with httpx.Client(transport=httpx.HTTPTransport(uds=str(socket_path)), base_url="http://firecracker", timeout=30) as client:
            calls = (
                ("/machine-config", {"vcpu_count": 1, "mem_size_mib": MEM_MIB, "smt": False}),
                ("/boot-source", {"kernel_image_path": str(self.kernel), "boot_args": "console=ttyS0 reboot=k panic=1 pci=off"}),
                ("/drives/rootfs", {"drive_id": "rootfs", "path_on_host": str(self.rootfs), "is_root_device": True, "is_read_only": False}),
                ("/network-interfaces/eth0", {"iface_id": "eth0", "guest_mac": "AA:FC:00:00:00:01", "host_dev_name": "tapfc"}),
                ("/vsock", {"guest_cid": 3, "uds_path": str(self.run_dir / "vsock.sock")}),
                ("/actions", {"action_type": "InstanceStart"}),
            )
            for path, payload in calls:
                response = client.put(path, json=payload)
                if response.status_code >= 300:
                    raise ComputerError(f"Firecracker menolak {path}: {response.text[:200]}")

    def _start_viewers(self) -> None:
        log_dir = self.root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        if not _port_open(self.vnc_port):
            if not (self.run_dir / "vsock.sock").is_socket():
                if not _wait(lambda: (self.run_dir / "vsock.sock").is_socket(), 20, self._sleep):
                    raise ComputerError("vsock Firecracker tidak siap.")
            log = open(log_dir / "vnc.log", "ab")
            subprocess.Popen(
                [os.environ.get("PYTHON", "python3"), str(_PROJECT_ROOT / "scripts" / "firecracker" / "vnc_proxy.py")],
                env={**os.environ, "FC_VSOCK": str(self.run_dir / "vsock.sock"), "FC_VNC_PORT": str(self.vnc_port)},
                stdout=log,
                stderr=log,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            if not _wait(lambda: _port_open(self.vnc_port), 30, self._sleep):
                raise ComputerError("Proxy VNC tidak siap.")
        if not _port_open(self.viewer_port):
            websockify = _websockify_bin()
            log = open(log_dir / "novnc.log", "ab")
            subprocess.Popen(
                [websockify, "--web", str(self.novnc), f"127.0.0.1:{self.viewer_port}", f"127.0.0.1:{self.vnc_port}"],
                stdout=log,
                stderr=log,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            if not _wait(lambda: _port_open(self.viewer_port), 15, self._sleep):
                raise ComputerError("noVNC tidak siap.")

    def _wait_for_agent(self) -> None:
        if _wait(self._agent_ready, 45, self._sleep):
            return
        raise ComputerError("Agen di dalam microVM belum menjawab.")

    def _agent_ready(self) -> bool:
        try:
            code, _output = self._rpc_once("run", b"true")
        except (OSError, ComputerError, ValueError):
            return False
        return code == 0

    def _rpc(self, op: str, body: bytes) -> tuple[int, bytes]:
        self._ensure_running()
        return self._rpc_once(op, body)

    def _rpc_once(self, op: str, body: bytes) -> tuple[int, bytes]:
        self._claim_account()
        vsock = self.run_dir / "vsock.sock"
        if not vsock.is_socket():
            raise ComputerError("microVM belum jalan.")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(20)
            client.connect(str(vsock))
            client.sendall(f"CONNECT {AGENT_PORT}\n".encode())
            ack = _readline(client)
            if not ack.startswith(b"OK"):
                raise ComputerError("vsock menolak koneksi agen.")
            client.sendall(op.encode() + b"\n" + str(len(body)).encode() + b"\n" + body)
            code = int(_readline(client) or b"1")
            length = int(_readline(client) or b"0")
            if length > 200_000:
                raise ComputerError("Jawaban agen terlalu besar.")
            return code, _read_exact(client, length)

    def _vnc(self, *args: str) -> None:
        self._claim_account()
        driver = _vncdotool_bin()
        result = subprocess.run(
            [driver, "-s", f"127.0.0.1::{self.vnc_port}", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:200]
            raise ComputerError(detail or "Aksi layar gagal.")

    def _api_ready(self) -> bool:
        socket_path = self.run_dir / "workspace.sock"
        if not socket_path.is_socket():
            return False
        try:
            with httpx.Client(transport=httpx.HTTPTransport(uds=str(socket_path)), base_url="http://firecracker", timeout=2) as client:
                machine_ready = client.get("/machine-config").status_code < 300
                namespace_pid_file = self.run_dir / "network-namespace.pid"
                slirp_pid_file = self.run_dir / "slirp4netns.pid"
                markers = all((self.run_dir / marker).is_file() for marker in ("network-namespace.ready", "slirp.ready"))
                namespace_pid = int(namespace_pid_file.read_text().strip())
                slirp_pid = int(slirp_pid_file.read_text().strip())
                firecracker_pid = self._owned_firecracker_pid()
                slirp_command = self._process_command(slirp_pid)
                slirp_owned = (
                    "slirp4netns" in slirp_command
                    and f"/proc/{namespace_pid}/ns/net" in slirp_command
                    and "tapslirp" in slirp_command
                )
                return machine_ready and markers and firecracker_pid == namespace_pid and slirp_owned
        except (httpx.HTTPError, OSError, ValueError):
            return False

    @staticmethod
    def _process_command(pid: int) -> str:
        try:
            return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            return ""


def _wait(ready: Callable[[], bool], seconds: float, sleep: Callable[[float], None]) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if ready():
            return True
        sleep(0.2)
    return ready()


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _readline(client: socket.socket) -> bytes:
    chunks = []
    while True:
        char = client.recv(1)
        if not char or char == b"\n":
            return b"".join(chunks)
        chunks.append(char)


def _read_exact(client: socket.socket, length: int) -> bytes:
    chunks = []
    remaining = length
    while remaining:
        part = client.recv(remaining)
        if not part:
            break
        chunks.append(part)
        remaining -= len(part)
    return b"".join(chunks)


def _vncdotool_bin() -> str:
    local = _PROJECT_ROOT / ".venv" / "bin" / "vncdotool"
    return str(local) if local.is_file() else "vncdotool"


def _websockify_bin() -> str:
    local = _PROJECT_ROOT / ".venv" / "bin" / "websockify"
    return str(local) if local.is_file() else "websockify"
