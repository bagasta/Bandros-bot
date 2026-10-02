from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from .domain import ApprovalStatus, BotStatus, RunStatus
from .mentions import (
    addresses_everyone,
    asks_roll_call,
    group_prompt,
    is_silence,
    is_status_report,
    lead_bot,
    mentioned_bots,
    with_roll_call_mentions,
    without_peer_mentions,
)
from .model_gateway import ModelGateway
from .repository import Repository
from .workspace_tools import WorkspaceToolset


@dataclass(slots=True)
class RunRuntime:
    repository: Repository
    model_gateway: ModelGateway
    default_model: str
    max_model_calls: int
    workspace_root: Path = Path("/workspace")
    _tasks: dict[UUID, asyncio.Task[None]] = field(default_factory=dict, init=False)
    _approved_tools: dict[UUID, dict[str, list[dict[str, object]]]] = field(default_factory=dict, init=False)
    _group_depth: dict[UUID, int] = field(default_factory=dict, init=False)
    _pending_wakes: list[tuple[UUID, str, UUID, int]] = field(default_factory=list, init=False)
    _wake_budget: int = field(default=8, init=False)

    def start(self, run_id: UUID) -> None:
        if task := self._tasks.get(run_id):
            if not task.done():
                return
        self._tasks[run_id] = asyncio.create_task(self._execute(run_id), name=f"run-{run_id}")

    async def start_and_wait(self, run_id: UUID) -> None:
        """Run work in-process so serverless platforms can finish before freeze."""
        self.start(run_id)
        task = self._tasks.get(run_id)
        if task is not None:
            await task

    async def _execute(self, run_id: UUID) -> None:
        run = self.repository.get_run(run_id)
        if run.status not in {RunStatus.QUEUED, RunStatus.FAILED_RETRYABLE}:
            return
        bot = self.repository.get_bot(run.bot_id)
        if bot.status is not BotStatus.ACTIVE:
            self.repository.update_run(run_id, RunStatus.FAILED, "cannot run an archived bot")
            return

        self.repository.update_run(run_id, RunStatus.RUNNING)
        self.repository.record_event(run_id, "model.request.started", {"model": run.model, "call": 1})
        toolset = WorkspaceToolset(
            self.repository,
            run_id,
            bot.id,
            self.start,
            self.default_model,
            self._approved_tools.pop(run_id, {}),
            self.workspace_root,
            on_group_post=self.queue_group_wake,
        )
        try:
            history = self.repository.list_messages(run.conversation_id, limit=50)
            previous_turns = history[:-1][-10:]
            context = "\n".join(
                f"{'Pengguna' if message.role == 'user' else 'Grup' if message.role == 'group' else 'Bot'}: {message.content[-3000:]}"
                for message in previous_turns
            )
            prompt = (
                f"Riwayat percakapan:\n{context}\n\nPesan terbaru pengguna:\n{run.prompt}"
                if context else run.prompt
            )
            skills = self.repository.list_bot_skills(bot.id)
            answer = await self.model_gateway.complete(
                system=self._system_prompt(
                    bot.instructions,
                    bot.description,
                    self._environment_context(bot.id),
                    skills,
                    in_group=self.repository.group_for_run(run_id) is not None,
                ),
                prompt=prompt,
                model=run.model,
                tools=toolset.definitions(),
                request_limit=self.max_model_calls,
            )
        except Exception as error:
            self.repository.update_run(run_id, RunStatus.FAILED_RETRYABLE, str(error))
            self._resolve_handoffs(run_id, str(error), success=False)
            return

        current = self.repository.get_run(run_id)
        if current.status is RunStatus.WAITING_APPROVAL:
            return
        if current.stop_requested:
            self.repository.update_run(run_id, RunStatus.CANCELLED, "generation stopped by user")
            return
        group_id = self.repository.group_for_run(run_id)
        if group_id and not is_silence(answer):
            answer = self._mention_teammates_for_roll_call(group_id, bot, answer, self._group_depth.get(run_id, 0))
            answer = self._drop_answered_peer_mentions(group_id, bot, answer)
        self.repository.record_event(run_id, "assistant.delta", {"content": answer, "final": True})
        self.repository.append_message(run.conversation_id, "assistant", answer, model=run.model)
        if group_id and not is_silence(answer):
            recent = self.repository.list_group_messages(group_id)
            already_posted = bool(recent) and recent[-1].sender_bot_id == bot.id and recent[-1].content.strip() == answer.strip()
            if not already_posted:
                self.repository.append_group_message(group_id, "bot", answer, bot.id)
            depth = self._group_depth.get(run_id, 0)
            if depth < 3:
                self.queue_group_wake(group_id, answer, bot.id, depth + 1)
        self.repository.record_event(run_id, "assistant.message", {"characters": len(answer)})
        self.repository.record_event(run_id, "model.request.completed", {"model": run.model, "call": 1})
        self.repository.update_run(run_id, RunStatus.COMPLETED)
        self._resolve_handoffs(run_id, answer, success=True)
        await self.drain_group_wakes()

    def recover(self) -> int:
        """Make interrupted work recoverable after an API restart."""
        run_ids = self.repository.recover_incomplete_runs()
        for run_id in run_ids:
            self.start(run_id)
        return len(run_ids)

    def stop(self, run_id: UUID) -> None:
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        run = self.repository.request_stop(run_id)
        if run.status in {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING_APPROVAL}:
            self.repository.update_run(run_id, RunStatus.CANCELLED, "generation stopped by user")

    def resume_after_approval(self, approval_id: UUID) -> None:
        approval = self.repository.get_approval(approval_id)
        run = self.repository.get_run(approval.run_id)
        if approval.status is ApprovalStatus.REJECTED:
            self.repository.update_run(run.id, RunStatus.CANCELLED, "approval rejected")
        elif (
            approval.status is ApprovalStatus.APPROVED
            and run.status is RunStatus.WAITING_APPROVAL
            and not run.stop_requested
        ):
            self._approved_tools.setdefault(run.id, {}).setdefault(approval.tool_name, []).append(approval.payload)
            self.repository.update_run(run.id, RunStatus.QUEUED)
            self.start(run.id)

    def _resolve_handoffs(self, run_id: UUID, result: str, success: bool) -> None:
        for handoff in self.repository.complete_handoffs_for_run(run_id, result, success):
            source_conversation = self.repository.conversation_for_bot(handoff.source_bot_id)
            target_name = self.repository.get_bot(handoff.target_bot_id).name
            status = "selesai" if success else "gagal"
            self.repository.append_message(source_conversation, "bot", f"Hasil dari {target_name} ({status}):\n{result}")

    def queue_group_wake(self, group_id: UUID, content: str, sender_bot_id: UUID, depth: int = 1) -> None:
        if depth > 3 or self._wake_budget <= 0 or is_silence(content):
            return
        wake = (group_id, content, sender_bot_id, depth)
        if wake not in self._pending_wakes:
            self._pending_wakes.append(wake)

    async def drain_group_wakes(self) -> None:
        while self._pending_wakes and self._wake_budget > 0:
            group_id, content, sender_bot_id, depth = self._pending_wakes.pop(0)
            await self.speak_in_group(group_id, content, sender_bot_id, depth)

    async def speak_in_group(self, group_id: UUID, content: str, sender_bot_id: UUID | None, depth: int) -> None:
        if depth > 3 or self._wake_budget <= 0:
            return
        group = self.repository.get_group(group_id)
        members = [member for member in group.members if member.status is BotStatus.ACTIVE]
        if addresses_everyone(content):
            targets = members
        else:
            targets = mentioned_bots(content, members)
        if sender_bot_id is not None:
            targets = [member for member in targets if member.id != sender_bot_id]
            if is_status_report(content):
                lead = lead_bot(members)
                targets = [member for member in targets if member.id != lead.id]
        elif not targets and members:
            targets = [lead_bot(members)]
        busy = {run.bot_id for run in self.repository.runs_for_group(group_id)}
        for target in targets:
            if target.id in busy or self._wake_budget <= 0:
                continue
            self._wake_budget -= 1
            messages = self.repository.list_group_messages(group_id)
            names = {member.id: member.name for member in group.members}
            trigger_at = next((index for index in range(len(messages) - 1, -1, -1) if messages[index].content == content), len(messages))
            visible = messages[:trigger_at] + messages[trigger_at + 1 :]
            already = []
            for message in messages[trigger_at + 1 :]:
                name = names.get(message.sender_bot_id)
                if name and name not in already and message.sender_bot_id != target.id:
                    already.append(name)
            transcript = "\n".join(
                f"- {'Pengguna' if message.sender_type == 'user' else names.get(message.sender_bot_id, 'Bot')} — {message.content[:160]}"
                for message in visible[-8:]
            )
            conversation_id = self.repository.conversation_for_bot(target.id)
            prompt = group_prompt(group, content, transcript, already)
            run = self.repository.create_run(target.id, conversation_id, prompt, target.model or self.default_model)
            self.repository.link_run_to_group(run.id, group_id)
            self._group_depth[run.id] = depth
            await self.start_and_wait(run.id)

    def _mention_teammates_for_roll_call(self, group_id: UUID, bot, answer: str, depth: int) -> str:
        if depth != 0:
            return answer
        group = self.repository.get_group(group_id)
        members = [member for member in group.members if member.status is BotStatus.ACTIVE]
        if not members or lead_bot(members).id != bot.id:
            return answer
        messages = self.repository.list_group_messages(group_id)
        user_text = next((message.content for message in reversed(messages) if message.sender_type == "user"), "")
        if not asks_roll_call(user_text):
            return answer
        return with_roll_call_mentions(answer, members, bot)

    def _drop_answered_peer_mentions(self, group_id: UUID, bot, answer: str) -> str:
        group = self.repository.get_group(group_id)
        members = [member for member in group.members if member.status is BotStatus.ACTIVE]
        if not members or lead_bot(members).id == bot.id:
            return answer
        lowered = answer.lower()
        asks_again = "mohon balas" in lowered or "balas status" in lowered or "reply singkat" in lowered
        messages = self.repository.list_group_messages(group_id)
        last_user = next((index for index in range(len(messages) - 1, -1, -1) if messages[index].sender_type == "user"), -1)
        replied = {
            message.sender_bot_id
            for message in messages[last_user + 1 :]
            if message.sender_type == "bot" and message.sender_bot_id not in {None, bot.id}
        }
        mentions_someone_who_replied = any(member.id in replied for member in mentioned_bots(answer, members))
        if not asks_again and not mentions_someone_who_replied:
            return answer
        return without_peer_mentions(answer, members, bot)

    def _environment_context(self, bot_id: UUID) -> str:
        bots = self.repository.list_bots()
        jobs = self.repository.list_jobs(bot_id)
        groups = [group.name for group in self.repository.list_groups() if any(member.id == bot_id for member in group.members)]
        colleagues = ", ".join(f"{item.name}: {item.description or 'tanpa peran'}" for item in bots if item.status is BotStatus.ACTIVE)
        job_list = ", ".join(f"{job.title} [{job.status}]" for job in jobs) or "tidak ada"
        group_list = ", ".join(groups) or "tidak ada"
        memories = self.repository.list_memories(bot_id)
        memory_list = "; ".join(f"{memory.kind}: {memory.content}" for memory in memories[:20]) or "belum ada"
        return (
            f"Rekan kerja aktif: {colleagues or 'tidak ada'}. Job Anda: {job_list}. "
            f"Grup Anda: {group_list}. Memori Bot: {memory_list}."
        )

    @staticmethod
    def _system_prompt(instructions: str, description: str = "", environment: str = "", skills: Sequence[object] | None = None, in_group: bool = False) -> str:
        skill_blocks = []
        for skill in skills or ():
            name = getattr(skill, "name", "skill")
            content = getattr(skill, "content", "")
            skill_blocks.append(f"## {name}\n{content}")
        skill_text = "\n\n".join(skill_blocks) or "none"
        return (
            "You are a persistent named teammate on a shared computer, in the style of a Grok Bot. "
            "Finish the task with tools instead of only drafting advice. "
            "Keep durable project files in the shared workspace. "
            "Memory is for stable preferences, role facts, and short work summaries; "
            "it is not the source of truth for data that changes. "
            "Every Bot can list, create, update, archive, and restore Bots, and can list, create, and edit groups. "
            "When asked to make a Bot and a group with it, call create_bot and then create_group. "
            + (
                "You are the primary orchestrator, Bandros. Delegate specialist work to a Bot you create. "
                "A new Bot needs a specific description plus instructions with the sections Tugas, Cara kerja, Output, and Batasan. "
                "Rewrite the instructions until create_bot returns ok. "
                if "orkestrator" in description.lower()
                else ""
            )
            + (
                "This run is the group conversation. "
                if in_group
                else ""
            )
            + "In a group, other bots react only when the message contains their @Name. "
            "To assign work, the reply itself must mention that teammate. "
            "A message with no @Name is answered only by the lead. "
            "The final reply is the group message, so do not call post_to_group or handoff_to_bot for that task. "
            "One to three short sentences. Do not recap that you already delegated or posted. "
            "If you were mentioned, do the task and report the result. Do not quote the previous speaker or start with their name. "
            "When the user asks to check each bot or mention each bot, mention every other member with @Name and ask for a one-line status. Do not invent their status. "
            "When a teammate only reports status, reply (diam). "
            "Do not say a group tool is missing. "
            "Never claim a file, memory, job, bot, group, or handoff exists unless the tool result says ok. "
            "If a tool requires approval, stop and say exactly what needs approval. "
            "Reply in the user's language and keep the user updated on what you actually did. "
            "Write that reply as clean Markdown: short paragraphs, **bold** only for names, and a bullet list when several items were created.\n\n"
            f"Bot's main responsibility:\n{description or 'Help the user with the task they provide.'}\n\n"
            f"Bot instructions:\n{instructions}\n\n"
            f"Environment knowledge:\n{environment}\n\n"
            f"Active skills:\n{skill_text}"
        )
