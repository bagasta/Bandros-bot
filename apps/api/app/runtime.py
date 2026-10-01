from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from .domain import ApprovalStatus, BotStatus, RunStatus
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
        )
        try:
            history = self.repository.list_messages(run.conversation_id, limit=50)
            previous_turns = history[:-1][-10:]
            context = "\n".join(
                f"{'Pengguna' if message.role == 'user' else 'Bot'}: {message.content[-3000:]}"
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
        self.repository.record_event(run_id, "assistant.delta", {"content": answer, "final": True})
        self.repository.append_message(run.conversation_id, "assistant", answer, model=run.model)
        if group_id := self.repository.group_for_run(run_id):
            self.repository.append_group_message(group_id, "bot", answer, bot.id)
        self.repository.record_event(run_id, "assistant.message", {"characters": len(answer)})
        self.repository.record_event(run_id, "model.request.completed", {"model": run.model, "call": 1})
        self.repository.update_run(run_id, RunStatus.COMPLETED)
        self._resolve_handoffs(run_id, answer, success=True)

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
    def _system_prompt(instructions: str, description: str = "", environment: str = "", skills: Sequence[object] | None = None) -> str:
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
            "Hand work to the Bot that owns it, and post to a group when the handoff should stay visible. "
            "Never claim a file, memory, job, or handoff exists unless the tool result says ok. "
            "If a tool requires approval, stop and say exactly what needs approval. "
            "Reply in the user's language and keep the user updated on what you actually did.\n\n"
            f"Bot's main responsibility:\n{description or 'Help the user with the task they provide.'}\n\n"
            f"Bot instructions:\n{instructions}\n\n"
            f"Environment knowledge:\n{environment}\n\n"
            f"Active skills:\n{skill_text}"
        )
