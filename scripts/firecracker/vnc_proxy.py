#!/usr/bin/env python3
"""Bridge the guest VNC vsock to loopback so noVNC can attach."""

from __future__ import annotations

import asyncio
import contextlib
import os


UNIX_SOCKET = os.environ.get("FC_VSOCK", "")
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = int(os.environ.get("FC_VNC_PORT") or "5912")
GUEST_PORT = 5900


async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        guest_reader, guest_writer = await asyncio.open_unix_connection(UNIX_SOCKET)
        guest_writer.write(f"CONNECT {GUEST_PORT}\n".encode())
        await guest_writer.drain()
        response = await guest_reader.readline()
        if not response.startswith(b"OK "):
            writer.close()
            await writer.wait_closed()
            return

        async def copy(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
            while data := await source.read(65536):
                target.write(data)
                await target.drain()

        tasks = [asyncio.create_task(copy(reader, guest_writer)), asyncio.create_task(copy(guest_reader, writer))]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        guest_writer.close()
        await guest_writer.wait_closed()
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def main() -> None:
    if not UNIX_SOCKET:
        raise SystemExit("FC_VSOCK is required")
    server = await asyncio.start_server(relay, LISTEN_HOST, LISTEN_PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
