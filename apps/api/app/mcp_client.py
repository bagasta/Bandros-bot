from __future__ import annotations

import json
from typing import Any

import httpx

from .workspace_tools import public_https_url


class McpError(RuntimeError):
    """A connected plugin could not be reached or rejected the call."""


def _parse_rpc(response: httpx.Response) -> dict[str, Any]:
    text = response.text.strip()
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type or text.startswith("data:") or text.startswith("event:"):
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and ("result" in payload or "error" in payload):
                return payload
        raise McpError("MCP server returned no result.")
    try:
        payload = response.json()
    except json.JSONDecodeError as error:
        raise McpError("MCP server returned an unreadable response.") from error
    if not isinstance(payload, dict):
        raise McpError("MCP server returned an unreadable response.")
    return payload


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    if "error" in payload:
        error = payload["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise McpError((message or "MCP server rejected the call.")[:300])
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


async def rpc(url: str, token: str | None, method: str, params: dict[str, Any], session_id: str | None = None) -> tuple[dict[str, Any], str | None]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-03-26",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        response = await client.post(url, headers=headers, json=body)
    if response.status_code >= 400:
        raise McpError(f"MCP server returned {response.status_code}.")
    session = response.headers.get("mcp-session-id") or session_id
    return _result(_parse_rpc(response)), session


async def _session(url: str, token: str | None) -> str | None:
    try:
        _, session = await rpc(
            url,
            token,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "bandros", "version": "0.1.0"},
            },
        )
    except McpError:
        return None
    return session


async def discover_tools(url: str, token: str | None) -> list[dict[str, str]]:
    if not await public_https_url(url):
        raise McpError("Plugin URL must be a public https address.")
    result, _ = await rpc(url, token, "tools/list", {}, await _session(url, token))
    found: list[dict[str, str]] = []
    for tool in result.get("tools") or []:
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        found.append({"name": str(tool["name"])[:80], "description": str(tool.get("description") or "")[:400]})
        if len(found) == 40:
            break
    return found


async def call_tool(url: str, token: str | None, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not await public_https_url(url):
        raise McpError("Plugin URL must be a public https address.")
    result, _ = await rpc(
        url,
        token,
        "tools/call",
        {"name": name, "arguments": arguments},
        await _session(url, token),
    )
    return result
