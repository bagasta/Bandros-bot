from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx


_LINK = re.compile(r"\[([^\]]+)\]\((https://[^)\s]+)\)")
_MAX_RESULTS = 5


def search_model(run_model: str, default_model: str) -> str:
    """OpenRouter search cannot use a ChatGPT model id."""
    for candidate in (run_model, default_model, "openrouter/free"):
        cleaned = candidate.strip()
        if cleaned and not cleaned.startswith("chatgpt/"):
            return cleaned
    return "openrouter/free"


def search_body(query: str, model: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Search the public web for this query with the web search tool. "
                    "Cite every source as a markdown link.\n\n"
                    f"Query: {query}"
                ),
            }
        ],
        "tools": [
            {
                "type": "openrouter:web_search",
                "parameters": {
                    "engine": "auto",
                    "max_results": _MAX_RESULTS,
                    "user_location": {
                        "type": "approximate",
                        "city": "Jakarta",
                        "country": "ID",
                        "timezone": "Asia/Jakarta",
                    },
                },
            }
        ],
        "max_tokens": 600,
    }


def citations_from_payload(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Titles, https URLs, and snippets from an OpenRouter web-search response."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(title: str, url: str, snippet: str) -> None:
        target = url.strip()
        if not target.startswith("https://") or target in seen or len(found) == _MAX_RESULTS:
            return
        seen.add(target)
        label = title.strip() or _host(target)
        found.append(
            {
                "title": label[:180],
                "url": target[:500],
                "snippet": snippet.strip()[:700],
            }
        )

    for title, url, snippet in _annotation_rows(payload):
        add(title, url, snippet)
    for title, url, snippet in _markdown_rows(payload):
        add(title, url, snippet)
    return found


async def openrouter_web_search(
    query: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    response = await client.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=search_body(query, model),
    )
    if response.status_code >= 400:
        return {
            "ok": False,
            "error": (
                f"pencarian web gagal ({response.status_code}). "
                "Lanjut dengan pengetahuan yang ada dan tulis asumsinya."
            ),
        }
    try:
        payload = response.json()
    except ValueError:
        payload = None
    results = citations_from_payload(payload) if isinstance(payload, dict) else []
    if not results:
        return {
            "ok": False,
            "error": (
                "pencarian web tidak mengembalikan hasil. "
                "Lanjut dengan pengetahuan yang ada dan tulis asumsinya."
            ),
        }
    return {"ok": True, "results": results}


def _annotation_rows(payload: dict[str, Any]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for annotation in _annotations(payload):
        if not isinstance(annotation, dict):
            continue
        if annotation.get("type") not in {None, "url_citation"}:
            continue
        nested = annotation.get("url_citation")
        source = nested if isinstance(nested, dict) else annotation
        url = str(source.get("url") or "")
        title = str(source.get("title") or "")
        snippet = str(source.get("content") or source.get("snippet") or "")
        rows.append((title, url, snippet))
    return rows


def _annotations(payload: dict[str, Any]) -> list[Any]:
    found: list[Any] = []
    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict):
                _collect_annotations(message, found)
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if isinstance(item, dict):
                _collect_annotations(item, found)
    return found


def _collect_annotations(node: dict[str, Any], found: list[Any]) -> None:
    annotations = node.get("annotations")
    if isinstance(annotations, list):
        found.extend(annotations)
    content = node.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                _collect_annotations(part, found)


def _markdown_rows(payload: dict[str, Any]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for text in _texts(payload):
        for match in _LINK.finditer(text):
            rows.append((match.group(1), match.group(2), ""))
    return rows


def _texts(payload: dict[str, Any]) -> list[str]:
    texts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            texts.append(node)
            return
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        for key in ("message", "content", "text", "output"):
            if key in node:
                walk(node[key])

    walk(payload.get("choices"))
    walk(payload.get("output"))
    return texts


def _host(url: str) -> str:
    host = urlparse(url).hostname or url
    return host.removeprefix("www.")
