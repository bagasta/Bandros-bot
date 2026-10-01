from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from .repository import Repository
from .domain import BotStatus, RiskClass, RunStatus
from .orchestrator import missing_instruction_details
from .policy import PolicyEngine


ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    handler: ToolHandler
    risk_class: RiskClass = RiskClass.LOCAL_WRITE
    timeout_seconds: int = 30
    max_retries: int = 0


class WorkspaceToolset:
    """Only exposes tools granted by the Bot's persisted skills."""

    def __init__(
        self,
        repository: Repository,
        run_id: UUID,
        bot_id: UUID,
        start_child_run: Callable[[UUID], None],
        default_model: str = "openrouter/free",
        approved_tools: dict[str, list[dict[str, Any]]] | None = None,
        workspace_root: Path | None = None,
        on_group_post: Callable[[UUID, str, UUID], None] | None = None,
    ) -> None:
        self.repository = repository
        self.run_id = run_id
        self.bot_id = bot_id
        self.start_child_run = start_child_run
        self.default_model = default_model
        self.policy = PolicyEngine()
        self.approved_tools = approved_tools or {}
        self.workspace_root = (workspace_root or Path("/workspace")).resolve()
        self.on_group_post = on_group_post
        self.skill_names = {skill.name for skill in repository.list_bot_skills(bot_id)}

    def definitions(self) -> list[ToolDefinition]:
        # Every persistent Agent is an orchestrator. Description, instructions,
        # and active jobs shape behavior; capabilities are not manager-only.
        definitions = [
            ToolDefinition("list_bots", "List bots you can create, update, or mention. No payload.", self.list_bots, RiskClass.READ_ONLY),
            ToolDefinition(
                "create_bot",
                "Create a specialist Bot. payload: {name, description, instructions}. instructions must include Tugas, Cara kerja, Output, and Batasan, each concrete enough for the Bot to work alone.",
                self.create_bot,
                RiskClass.LOCAL_WRITE,
            ),
            ToolDefinition("update_bot", "Update a Bot. payload: {bot_id, name?, description?, instructions?, model?}", self.update_bot, RiskClass.LOCAL_WRITE),
            ToolDefinition("archive_bot", "Archive a Bot after explaining why. payload: {bot_id}", self.archive_bot, RiskClass.DESTRUCTIVE),
            ToolDefinition("restore_bot", "Restore an archived Bot. payload: {bot_id}", self.restore_bot, RiskClass.LOCAL_WRITE),
            ToolDefinition("list_groups", "List work groups and their members. No payload.", self.list_groups, RiskClass.READ_ONLY),
            ToolDefinition("create_group", "Create a WhatsApp-style group and join it. payload: {name, description?, member_names?: [bot name], member_bot_ids?}", self.create_group, RiskClass.LOCAL_WRITE),
            ToolDefinition("add_group_member", "Add a bot to a group. payload: {group_id, bot_id? , member_name?}", self.add_group_member, RiskClass.LOCAL_WRITE),
            ToolDefinition("remove_group_member", "Remove a bot from a group. payload: {group_id, bot_id?, member_name?}", self.remove_group_member, RiskClass.LOCAL_WRITE),
            ToolDefinition("list_group_messages", "Read the latest messages in a group. payload: {group_id}", self.list_group_messages, RiskClass.READ_ONLY),
            ToolDefinition("create_job", "Create a job for a Bot. payload: {title, description?, priority?, assignee_bot_id?}", self.create_job, RiskClass.LOCAL_WRITE),
            ToolDefinition("update_job", "Update a job. payload: {job_id, status?, title?, description?, priority?, assignee_bot_id?}", self.update_job, RiskClass.LOCAL_WRITE),
            ToolDefinition("handoff_to_bot", "Delegate a bounded task to another active Bot. payload: {target_bot_id, task}", self.handoff_to_bot, RiskClass.LOCAL_WRITE),
            ToolDefinition("post_to_group", "Post into a group. @Name wakes that bot. payload: {group_id, content}", self.post_to_group, RiskClass.LOCAL_WRITE),
            ToolDefinition("list_workspace_files", "List files under the persistent Bot workspace. payload: {path?}", self.list_workspace_files, RiskClass.READ_ONLY),
            ToolDefinition("read_workspace_file", "Read a text file from the persistent Bot workspace. payload: {path}", self.read_workspace_file, RiskClass.READ_ONLY),
            ToolDefinition("write_workspace_file", "Write a text file into the persistent Bot workspace. payload: {path, content}", self.write_workspace_file, RiskClass.LOCAL_WRITE),
            ToolDefinition("list_memory", "Recall durable facts saved for this Bot. payload: {query?}", self.list_memory, RiskClass.READ_ONLY),
            ToolDefinition("save_memory", "Save a durable preference or fact for future runs. payload: {kind, content}", self.save_memory, RiskClass.LOCAL_WRITE),
        ]
        return [self._audited(definition) for definition in definitions]

    def _audited(self, definition: ToolDefinition) -> ToolDefinition:
        async def handler(payload: dict[str, Any]) -> dict[str, Any]:
            approved_payloads = self.approved_tools.get(definition.name, [])
            approved_index = next(
                (index for index, candidate in enumerate(approved_payloads) if candidate == payload),
                None,
            )
            is_approved = approved_index is not None
            if approved_index is not None:
                approved_payloads.pop(approved_index)
            decision = self.policy.decide(definition.risk_class) if not is_approved else None
            if decision and decision.requires_approval:
                approval = self.repository.create_approval(
                    self.run_id,
                    definition.name,
                    definition.risk_class,
                    decision.reason or "explicit approval required",
                    payload,
                )
                self.repository.update_run(self.run_id, RunStatus.WAITING_APPROVAL)
                self.repository.record_event(
                    self.run_id,
                    "tool.approval_required",
                    {"tool": definition.name, "approval_id": str(approval.id)},
                )
                return {"ok": False, "requires_approval": True, "approval_id": str(approval.id)}
            call_id = self.repository.create_tool_call(self.run_id, definition.name, payload)
            self.repository.record_event(
                self.run_id,
                "tool.started",
                {
                    "tool": definition.name,
                    "tool_call_id": str(call_id),
                    "risk_class": definition.risk_class,
                    "timeout_seconds": definition.timeout_seconds,
                },
            )
            try:
                result = await definition.handler(payload)
            except Exception as error:
                result = {"ok": False, "error": str(error)}
                self.repository.complete_tool_call(call_id, result, False)
                self.repository.record_event(self.run_id, "tool.failed", {"tool": definition.name, "tool_call_id": str(call_id)})
                return result
            self.repository.complete_tool_call(call_id, result, bool(result.get("ok", True)))
            self.repository.record_event(self.run_id, "tool.completed", {"tool": definition.name, "tool_call_id": str(call_id)})
            return result
        return ToolDefinition(
            definition.name,
            definition.description,
            handler,
            definition.risk_class,
            definition.timeout_seconds,
            definition.max_retries,
        )

    async def create_bot(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = self._text(payload, "name")
        description = str(payload.get("description", ""))[:2_000]
        instructions = str(payload.get("instructions", ""))[:10_000]
        missing = missing_instruction_details(description, instructions)
        if missing:
            raise ValueError("Instruksi belum cukup untuk bot bawahan. " + " ".join(missing))
        bot = self.repository.create_bot(name, description, instructions, None)
        if "manager" in bot.name.lower():
            for skill in self.repository.list_skills():
                if skill.name in {"workspace_admin", "job_manager", "coordination"}:
                    self.repository.assign_skill(bot.id, skill.id)
        return {"ok": True, "bot_id": str(bot.id), "name": bot.name}

    async def update_bot(self, payload: dict[str, Any]) -> dict[str, Any]:
        bot_id = UUID(self._text(payload, "bot_id"))
        fields = {key: payload[key] for key in ("name", "description", "instructions", "model") if key in payload}
        bot = self.repository.update_bot(bot_id, fields)
        return {"ok": True, "bot_id": str(bot.id), "name": bot.name}

    async def archive_bot(self, payload: dict[str, Any]) -> dict[str, Any]:
        bot = self.repository.set_bot_status(UUID(self._text(payload, "bot_id")), BotStatus.ARCHIVED)
        return {"ok": True, "bot_id": str(bot.id), "status": bot.status}

    async def restore_bot(self, payload: dict[str, Any]) -> dict[str, Any]:
        bot = self.repository.set_bot_status(UUID(self._text(payload, "bot_id")), BotStatus.ACTIVE)
        return {"ok": True, "bot_id": str(bot.id), "status": bot.status}

    async def list_bots(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "bots": [
                {"id": str(bot.id), "name": bot.name, "description": bot.description, "status": bot.status}
                for bot in self.repository.list_bots()
            ],
        }

    async def list_groups(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "groups": [
                {
                    "id": str(group.id),
                    "name": group.name,
                    "members": [member.name for member in group.members],
                }
                for group in self.repository.list_groups()
            ],
        }

    async def create_group(self, payload: dict[str, Any]) -> dict[str, Any]:
        member_ids = [self.bot_id]
        for raw in payload.get("member_bot_ids") or []:
            member_ids.append(UUID(str(raw)))
        for name in payload.get("member_names") or []:
            match = self._bot_named(str(name))
            if match is None:
                raise ValueError(f"bot named {name} was not found")
            member_ids.append(match.id)
        group = self.repository.create_group(self._text(payload, "name"), str(payload.get("description", ""))[:2_000], member_ids)
        return {"ok": True, "group_id": str(group.id), "name": group.name, "members": [member.name for member in group.members]}

    async def add_group_member(self, payload: dict[str, Any]) -> dict[str, Any]:
        group_id = UUID(self._text(payload, "group_id"))
        bot = self._member_from_payload(payload)
        self.repository.add_group_member(group_id, bot.id)
        return {"ok": True, "group_id": str(group_id), "bot": bot.name}

    async def remove_group_member(self, payload: dict[str, Any]) -> dict[str, Any]:
        group_id = UUID(self._text(payload, "group_id"))
        bot = self._member_from_payload(payload)
        self.repository.remove_group_member(group_id, bot.id)
        return {"ok": True, "group_id": str(group_id), "bot": bot.name}

    async def list_group_messages(self, payload: dict[str, Any]) -> dict[str, Any]:
        group_id = UUID(self._text(payload, "group_id"))
        messages = self.repository.list_group_messages(group_id)[-30:]
        group = self.repository.get_group(group_id)
        names = {member.id: member.name for member in group.members}
        return {
            "ok": True,
            "messages": [
                {
                    "sender": "Kamu" if message.sender_type == "user" else names.get(message.sender_bot_id, "Bot"),
                    "content": message.content,
                }
                for message in messages
            ],
        }

    async def create_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        assignee = UUID(payload["assignee_bot_id"]) if payload.get("assignee_bot_id") else None
        job = self.repository.create_job(
            self._text(payload, "title"),
            str(payload.get("description", ""))[:5_000],
            str(payload.get("priority", "normal")),
            assignee,
            self.bot_id,
        )
        return {"ok": True, "job_id": str(job.id), "status": job.status}

    async def update_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = UUID(self._text(payload, "job_id"))
        fields = {key: payload[key] for key in ("title", "description", "priority", "status") if key in payload}
        if payload.get("assignee_bot_id"):
            fields["assignee_bot_id"] = UUID(str(payload["assignee_bot_id"]))
        job = self.repository.update_job(job_id, fields)
        return {"ok": True, "job_id": str(job.id), "status": job.status}

    async def handoff_to_bot(self, payload: dict[str, Any]) -> dict[str, Any]:
        target_bot_id = UUID(self._text(payload, "target_bot_id"))
        task = self._text(payload, "task")
        target = self.repository.get_bot(target_bot_id)
        if target.status != "active":
            raise ValueError("target bot is archived")
        handoff = self.repository.create_handoff(self.bot_id, target_bot_id, task, self.run_id)
        conversation_id = self.repository.conversation_for_bot(target_bot_id)
        prompt = f"Delegasi dari {self.repository.get_bot(self.bot_id).name}: {task}"
        self.repository.append_message(conversation_id, f"bot:{self.bot_id}", prompt)
        child = self.repository.create_run(target_bot_id, conversation_id, prompt, target.model or self.default_model)
        self.repository.set_handoff_child(handoff.id, child.id)
        self.start_child_run(child.id)
        return {"ok": True, "handoff_id": str(handoff.id), "run_id": str(child.id), "target_bot": target.name}

    async def post_to_group(self, payload: dict[str, Any]) -> dict[str, Any]:
        group_id = UUID(self._text(payload, "group_id"))
        content = self._text(payload, "content")
        message = self.repository.append_group_message(group_id, "bot", content, self.bot_id)
        if self.on_group_post:
            self.on_group_post(group_id, content, self.bot_id)
        return {"ok": True, "message_id": str(message.id)}

    def _bot_named(self, name: str):
        needle = name.strip().lower()
        return next((bot for bot in self.repository.list_bots() if bot.name.lower() == needle), None)

    def _member_from_payload(self, payload: dict[str, Any]):
        if payload.get("bot_id"):
            return self.repository.get_bot(UUID(str(payload["bot_id"])))
        match = self._bot_named(self._text(payload, "member_name"))
        if match is None:
            raise ValueError("bot was not found")
        return match

    def _workspace_path(self, value: str) -> Path:
        candidate = (self.workspace_root / value).resolve()
        if candidate != self.workspace_root and self.workspace_root not in candidate.parents:
            raise ValueError("path must stay inside the Bot workspace")
        return candidate

    async def list_workspace_files(self, payload: dict[str, Any]) -> dict[str, Any]:
        directory = self._workspace_path(str(payload.get("path", ".")))
        if not directory.exists():
            return {"ok": False, "error": "directory not found"}
        if not directory.is_dir():
            return {"ok": False, "error": "path is not a directory"}
        entries = [
            {"path": str(item.relative_to(self.workspace_root)), "type": "directory" if item.is_dir() else "file"}
            for item in sorted(directory.iterdir(), key=lambda item: item.name.lower())[:200]
        ]
        return {"ok": True, "entries": entries}

    async def read_workspace_file(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = self._workspace_path(self._text(payload, "path"))
        if not path.is_file():
            return {"ok": False, "error": "file not found"}
        return {"ok": True, "path": str(path.relative_to(self.workspace_root)), "content": path.read_text(encoding="utf-8")[:100_000]}

    async def write_workspace_file(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = self._workspace_path(self._text(payload, "path"))
        content = str(payload.get("content", ""))
        if len(content) > 100_000:
            raise ValueError("file content exceeds 100000 characters")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(path.relative_to(self.workspace_root)), "characters": len(content)}

    async def list_memory(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query", "")).strip().lower()
        memories = self.repository.list_memories(self.bot_id)
        if query:
            memories = [memory for memory in memories if query in f"{memory.kind} {memory.content}".lower()]
        return {"ok": True, "memories": [{"id": str(memory.id), "kind": memory.kind, "content": memory.content} for memory in memories[:50]]}

    async def save_memory(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind = self._text(payload, "kind")[:100]
        content = self._text(payload, "content")[:10_000]
        memory = self.repository.add_memory(self.bot_id, kind, content)
        return {"ok": True, "memory_id": str(memory.id), "kind": memory.kind}

    @staticmethod
    def _text(payload: dict[str, Any], key: str) -> str:
        value = str(payload.get(key, "")).strip()
        if not value:
            raise ValueError(f"{key} is required")
        return value
