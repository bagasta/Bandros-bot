from __future__ import annotations

import re
from urllib.parse import quote

import httpx

_REGISTRY = "https://clawhub.ai"
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,118}$")


class ClawHubError(RuntimeError):
    """The public ClawHub catalog could not be read."""


def _check_slug(value: str, label: str) -> str:
    cleaned = value.strip()
    if not _SLUG.match(cleaned):
        raise ClawHubError(f"{label} is not a ClawHub name.")
    return cleaned


def parse_skill_markdown(text: str) -> tuple[str, str, str]:
    """Read the skill name and summary from SKILL.md frontmatter."""
    body = text.strip()
    name = ""
    description = ""
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            front = body[3:end]
            body = body[end + 4 :].strip()
            current = ""
            collected: list[str] = []
            for line in front.splitlines():
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip().strip("\"'")
                    current = ""
                elif line.startswith("description:"):
                    description = line.split(":", 1)[1].strip()
                    current = "description"
                    collected = [description]
                elif current == "description" and (line.startswith(" ") or line.startswith("\t")):
                    collected.append(line.strip())
                else:
                    current = ""
            if collected:
                description = " ".join(part for part in collected if part)
            if description.startswith(("\"", "'")) and description.endswith(("\"", "'")) and len(description) > 1:
                description = description[1:-1]
    return name[:100], description[:2_000], body[:20_000]


async def search_skills(query: str) -> list[dict[str, str]]:
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        response = await client.get(f"{_REGISTRY}/api/v1/search", params={"q": query, "limit": 8, "nonSuspiciousOnly": "true"})
    if response.status_code >= 400:
        raise ClawHubError(f"ClawHub search returned {response.status_code}.")
    payload = response.json()
    found: list[dict[str, str]] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "")
        owner = str(item.get("ownerHandle") or "")
        if not slug or not owner:
            continue
        found.append({
            "slug": slug,
            "owner_handle": owner,
            "name": str(item.get("displayName") or slug)[:100],
            "summary": str(item.get("summary") or "")[:300],
            "url": f"{_REGISTRY}/{owner}/skills/{slug}",
        })
    return found


async def fetch_skill_markdown(slug: str, owner_handle: str) -> str:
    slug = _check_slug(slug, "Skill")
    owner_handle = _check_slug(owner_handle, "Publisher")
    path = f"{_REGISTRY}/api/v1/skills/{quote(slug)}/file"
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        response = await client.get(path, params={"path": "SKILL.md", "ownerHandle": owner_handle})
    if response.status_code == 404:
        raise ClawHubError("That ClawHub skill was not found.")
    if response.status_code >= 400:
        raise ClawHubError(f"ClawHub returned {response.status_code}.")
    text = response.text.strip()
    if not text:
        raise ClawHubError("That skill has no instructions.")
    return text[:20_000]
