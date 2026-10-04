from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any
from uuid import UUID, uuid4

from .database import Database
from .domain import Approval, ApprovalStatus, Bot, BotStatus, GroupMessage, Handoff, Job, Memory, Message, Plugin, Run, RunEvent, RunStatus, Skill, WorkGroup
from .orchestrator import ORCHESTRATOR_NAME


def now() -> datetime:
    return datetime.now(UTC)


def dump_time(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def load_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Repository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_bot(self, name: str, description: str, instructions: str, model: str | None) -> Bot:
        bot_id, conversation_id, timestamp = uuid4(), uuid4(), now()
        with self.database.connection() as db:
            db.execute(
                "INSERT INTO bots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(bot_id), name, description, instructions, model, BotStatus.ACTIVE, dump_time(timestamp), dump_time(timestamp)),
            )
            db.execute("INSERT INTO conversations VALUES (?, ?, ?)", (str(conversation_id), str(bot_id), dump_time(timestamp)))
        return self.get_bot(bot_id)

    def list_bots(self) -> list[Bot]:
        with self.database.connection() as db:
            rows = db.execute("SELECT * FROM bots ORDER BY created_at DESC").fetchall()
        bots = [self._bot(row) for row in rows]
        return sorted(bots, key=lambda bot: bot.name.lower() != ORCHESTRATOR_NAME.lower())

    def get_bot(self, bot_id: UUID) -> Bot:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM bots WHERE id = ?", (str(bot_id),)).fetchone()
        if row is None:
            raise KeyError("bot not found")
        return self._bot(row)

    def update_bot(self, bot_id: UUID, fields: dict[str, Any]) -> Bot:
        accepted = {
            key: value
            for key, value in fields.items()
            if value is not None or key == "model"
        }
        if not accepted:
            return self.get_bot(bot_id)
        accepted["updated_at"] = dump_time(now())
        assignments = ", ".join(f"{column} = ?" for column in accepted)
        with self.database.connection() as db:
            result = db.execute(
                f"UPDATE bots SET {assignments} WHERE id = ?", (*accepted.values(), str(bot_id))
            )
        if result.rowcount != 1:
            raise KeyError("bot not found")
        return self.get_bot(bot_id)

    def delete_bot(self, bot_id: UUID) -> None:
        self.get_bot(bot_id)
        bot = str(bot_id)
        with self.database.connection() as db:
            run_ids = [row["id"] for row in db.execute("SELECT id FROM runs WHERE bot_id = ?", (bot,)).fetchall()]
            conversation_ids = [row["id"] for row in db.execute("SELECT id FROM conversations WHERE bot_id = ?", (bot,)).fetchall()]
            if run_ids:
                marks = ",".join("?" * len(run_ids))
                db.execute(f"DELETE FROM tool_calls WHERE run_id IN ({marks})", run_ids)
                db.execute(f"DELETE FROM run_events WHERE run_id IN ({marks})", run_ids)
                db.execute(f"DELETE FROM approvals WHERE run_id IN ({marks})", run_ids)
                db.execute(f"DELETE FROM group_run_links WHERE run_id IN ({marks})", run_ids)
                db.execute(
                    f"DELETE FROM handoffs WHERE parent_run_id IN ({marks}) OR child_run_id IN ({marks})",
                    [*run_ids, *run_ids],
                )
            db.execute("DELETE FROM handoffs WHERE source_bot_id = ? OR target_bot_id = ?", (bot, bot))
            db.execute("UPDATE jobs SET assignee_bot_id = NULL WHERE assignee_bot_id = ?", (bot,))
            db.execute("UPDATE jobs SET created_by_bot_id = NULL WHERE created_by_bot_id = ?", (bot,))
            db.execute("UPDATE group_messages SET sender_bot_id = NULL WHERE sender_bot_id = ?", (bot,))
            db.execute("DELETE FROM memories WHERE bot_id = ?", (bot,))
            db.execute("DELETE FROM runs WHERE bot_id = ?", (bot,))
            if conversation_ids:
                marks = ",".join("?" * len(conversation_ids))
                db.execute(f"DELETE FROM messages WHERE conversation_id IN ({marks})", conversation_ids)
            db.execute("DELETE FROM conversations WHERE bot_id = ?", (bot,))
            db.execute("DELETE FROM bots WHERE id = ?", (bot,))

    def set_bot_status(self, bot_id: UUID, status: BotStatus) -> Bot:
        return self.update_bot(bot_id, {"status": status})

    def conversation_for_bot(self, bot_id: UUID) -> UUID:
        with self.database.connection() as db:
            row = db.execute("SELECT id FROM conversations WHERE bot_id = ?", (str(bot_id),)).fetchone()
        if row is None:
            raise KeyError("conversation not found")
        return UUID(row["id"])

    def append_message(
        self,
        conversation_id: UUID,
        role: str,
        content: str,
        *,
        model: str | None = None,
        usage: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        citations: list[dict[str, Any]] | None = None,
    ) -> Message:
        message_id, timestamp = uuid4(), now()
        with self.database.connection() as db:
            db.execute(
                "INSERT INTO messages (id, conversation_id, role, content, model, usage, attachments, citations, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(message_id),
                    str(conversation_id),
                    role,
                    content,
                    model,
                    json.dumps(usage or {}),
                    json.dumps(attachments or []),
                    json.dumps(citations or []),
                    dump_time(timestamp),
                ),
            )
        return Message(
            id=message_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
            model=model,
            usage=usage or {},
            attachments=attachments or [],
            citations=citations or [],
            created_at=timestamp,
        )

    def list_messages(self, conversation_id: UUID, limit: int = 50) -> list[Message]:
        with self.database.connection() as db:
            rows = db.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ?", (str(conversation_id), limit)).fetchall()
        return [self._message(row) for row in reversed(rows)]

    def get_message(self, message_id: UUID) -> Message:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM messages WHERE id = ?", (str(message_id),)).fetchone()
        if row is None:
            raise KeyError("message not found")
        return self._message(row)

    def edit_message(self, message_id: UUID, content: str) -> Message:
        with self.database.connection() as db:
            result = db.execute(
                "UPDATE messages SET content = ? WHERE id = ? AND role = 'user'",
                (content, str(message_id)),
            )
        if result.rowcount != 1:
            raise KeyError("editable user message not found")
        return self.get_message(message_id)

    def create_run(self, bot_id: UUID, conversation_id: UUID, prompt: str, model: str) -> Run:
        run_id, timestamp = uuid4(), now()
        with self.database.connection() as db:
            db.execute(
                "INSERT INTO runs (id, bot_id, conversation_id, status, prompt, model, error, continuation, usage, stop_requested, created_at, started_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(run_id), str(bot_id), str(conversation_id), RunStatus.QUEUED, prompt, model, None, None, "{}", 0, dump_time(timestamp), None, None),
            )
        self.record_event(run_id, "run.queued", {"model": model})
        return self.get_run(run_id)

    def set_run_prompt(self, run_id: UUID, prompt: str) -> None:
        with self.database.connection() as db:
            db.execute("UPDATE runs SET prompt = ? WHERE id = ?", (prompt, str(run_id)))

    def set_run_continuation(self, run_id: UUID, continuation: str) -> None:
        with self.database.connection() as db:
            db.execute("UPDATE runs SET continuation = ? WHERE id = ?", (continuation[:2_000], str(run_id)))

    def latest_interrupted_run(self, bot_id: UUID) -> Run | None:
        with self.database.connection() as db:
            row = db.execute(
                """
                SELECT * FROM runs
                WHERE bot_id = ? AND status = ? AND continuation IS NOT NULL AND continuation != ''
                ORDER BY completed_at DESC, created_at DESC LIMIT 1
                """,
                (str(bot_id), RunStatus.CANCELLED),
            ).fetchone()
        return self._run(row) if row else None

    def get_run(self, run_id: UUID) -> Run:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM runs WHERE id = ?", (str(run_id),)).fetchone()
        if row is None:
            raise KeyError("run not found")
        return self._run(row)

    def update_run(
        self,
        run_id: UUID,
        status: RunStatus,
        error: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> Run:
        timestamp = now()
        started_at = dump_time(timestamp) if status is RunStatus.RUNNING else None
        completed_at = dump_time(timestamp) if status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED} else None
        with self.database.connection() as db:
            db.execute(
                "UPDATE runs SET status = ?, error = COALESCE(?, error), usage = COALESCE(?, usage), started_at = COALESCE(?, started_at), completed_at = COALESCE(?, completed_at) WHERE id = ?",
                (status, error, json.dumps(usage) if usage is not None else None, started_at, completed_at, str(run_id)),
            )
        self.record_event(run_id, f"run.{status}", {"error": error} if error else {})
        return self.get_run(run_id)

    def commit_assistant_turn(self, run_id: UUID, content: str, model: str | None) -> bool:
        """Save the reply only while this run is still the active one."""
        message_id, timestamp = uuid4(), now()
        with self.database.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT conversation_id, stop_requested, status FROM runs WHERE id = ?", (str(run_id),)).fetchone()
            if row is None or row["stop_requested"] or row["status"] != RunStatus.RUNNING:
                return False
            db.execute(
                "INSERT INTO messages (id, conversation_id, role, content, model, usage, attachments, citations, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(message_id),
                    row["conversation_id"],
                    "assistant",
                    content,
                    model,
                    "{}",
                    "[]",
                    "[]",
                    dump_time(timestamp),
                ),
            )
            db.execute(
                "UPDATE runs SET status = ?, completed_at = ? WHERE id = ? AND stop_requested = 0 AND status = ?",
                (RunStatus.COMPLETED, dump_time(timestamp), str(run_id), RunStatus.RUNNING),
            )
        self.record_event(run_id, "run.completed", {})
        return True

    def request_stop(self, run_id: UUID) -> Run:
        with self.database.connection() as db:
            result = db.execute("UPDATE runs SET stop_requested = 1 WHERE id = ?", (str(run_id),))
        if result.rowcount != 1:
            raise KeyError("run not found")
        self.record_event(run_id, "run.stop_requested", {})
        return self.get_run(run_id)

    def recover_incomplete_runs(self) -> list[UUID]:
        """Return work safe to retry; preserve approval waits as durable user decisions."""
        with self.database.connection() as db:
            db.execute(
                "UPDATE runs SET status = ?, error = ? WHERE status = ?",
                (RunStatus.FAILED_RETRYABLE, "API process restarted during execution", RunStatus.RUNNING),
            )
            rows = db.execute(
                "SELECT id FROM runs WHERE status IN (?, ?)",
                (RunStatus.QUEUED, RunStatus.FAILED_RETRYABLE),
            ).fetchall()
        return [UUID(row["id"]) for row in rows]

    def fail_orphaned_runs(self) -> int:
        """Close runs whose server process is gone so the chat can show a reply instead of typing forever."""
        notice = "Balasan terputus. Kirim ulang pesan."
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT id, conversation_id FROM runs WHERE status IN (?, ?)",
                (RunStatus.QUEUED, RunStatus.RUNNING),
            ).fetchall()
        for row in rows:
            self.append_message(UUID(row["conversation_id"]), "assistant", notice)
            self.update_run(UUID(row["id"]), RunStatus.FAILED, notice)
        return len(rows)

    def latest_run_for_bot(self, bot_id: UUID) -> Run | None:
        with self.database.connection() as db:
            row = db.execute(
                "SELECT * FROM runs WHERE bot_id = ? ORDER BY created_at DESC LIMIT 1",
                (str(bot_id),),
            ).fetchone()
        return self._run(row) if row else None

    def record_event(self, run_id: UUID, type_: str, payload: dict[str, Any]) -> RunEvent:
        timestamp = now()
        with self.database.connection() as db:
            cursor = db.execute("INSERT INTO run_events (run_id, type, payload, created_at) VALUES (?, ?, ?, ?)", (str(run_id), type_, json.dumps(payload), dump_time(timestamp)))
        return RunEvent(id=cursor.lastrowid, run_id=run_id, type=type_, payload=payload, created_at=timestamp)

    def list_events(self, run_id: UUID) -> list[RunEvent]:
        with self.database.connection() as db:
            rows = db.execute("SELECT * FROM run_events WHERE run_id = ? ORDER BY id", (str(run_id),)).fetchall()
        return [RunEvent(id=row["id"], run_id=UUID(row["run_id"]), type=row["type"], payload=json.loads(row["payload"]), created_at=load_time(row["created_at"])) for row in rows]

    def create_approval(
        self, run_id: UUID, tool_name: str, risk_class: str, reason: str, payload: dict[str, Any]
    ) -> Approval:
        approval = Approval(
            id=uuid4(),
            run_id=run_id,
            tool_name=tool_name,
            risk_class=risk_class,
            reason=reason,
            payload=payload,
            status=ApprovalStatus.PENDING,
            requested_at=now(),
            decided_at=None,
        )
        with self.database.connection() as db:
            db.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(approval.id), str(run_id), tool_name, risk_class, reason,
                    json.dumps(payload), approval.status, dump_time(approval.requested_at), None,
                ),
            )
        self.record_event(run_id, "approval.requested", {"approval_id": str(approval.id), "tool_name": tool_name})
        return approval

    def get_approval(self, approval_id: UUID) -> Approval:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM approvals WHERE id = ?", (str(approval_id),)).fetchone()
        if row is None:
            raise KeyError("approval not found")
        return self._approval(row)

    def list_approvals(self, status: ApprovalStatus | None = None) -> list[Approval]:
        with self.database.connection() as db:
            if status is None:
                rows = db.execute("SELECT * FROM approvals ORDER BY requested_at DESC").fetchall()
            else:
                rows = db.execute("SELECT * FROM approvals WHERE status = ? ORDER BY requested_at DESC", (status,)).fetchall()
        return [self._approval(row) for row in rows]

    def decide_approval(self, approval_id: UUID, decision: ApprovalStatus) -> Approval:
        if decision not in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
            raise ValueError("approval must be approved or rejected")
        decided_at = now()
        with self.database.connection() as db:
            result = db.execute(
                "UPDATE approvals SET status = ?, decided_at = ? WHERE id = ? AND status = ?",
                (decision, dump_time(decided_at), str(approval_id), ApprovalStatus.PENDING),
            )
        if result.rowcount != 1:
            raise ValueError("approval is already decided or unavailable")
        approval = self.get_approval(approval_id)
        self.record_event(approval.run_id, "approval.resolved", {"approval_id": str(approval.id), "decision": decision})
        return approval

    def add_memory(self, bot_id: UUID, kind: str, content: str) -> Memory:
        memory = Memory(id=uuid4(), bot_id=bot_id, kind=kind, content=content, created_at=now())
        with self.database.connection() as db:
            db.execute("INSERT INTO memories VALUES (?, ?, ?, ?, ?)", (str(memory.id), str(bot_id), kind, content, dump_time(memory.created_at)))
        return memory

    def list_memories(self, bot_id: UUID) -> list[Memory]:
        with self.database.connection() as db:
            rows = db.execute("SELECT * FROM memories WHERE bot_id = ? ORDER BY created_at DESC", (str(bot_id),)).fetchall()
        return [Memory(id=UUID(row["id"]), bot_id=UUID(row["bot_id"]), kind=row["kind"], content=row["content"], created_at=load_time(row["created_at"])) for row in rows]

    def save_chatgpt_connection(
        self,
        client_id: str,
        host_id: str,
        preferred_model: str | None,
        access_token: str,
        refresh_token: str | None,
        id_token: str | None,
        subject: str | None,
        email: str | None,
        expires_at: datetime | None,
        scope: str,
    ) -> None:
        timestamp = dump_time(now())
        with self.database.connection() as db:
            db.execute(
                """
                INSERT INTO chatgpt_oauth
                    (id, client_id, host_id, preferred_model, subject, email, access_token, refresh_token, id_token, expires_at, scope, created_at, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    client_id = excluded.client_id,
                    host_id = excluded.host_id,
                    preferred_model = excluded.preferred_model,
                    subject = excluded.subject,
                    email = excluded.email,
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    id_token = excluded.id_token,
                    expires_at = excluded.expires_at,
                    scope = excluded.scope,
                    updated_at = excluded.updated_at
                """,
                (client_id, host_id, preferred_model, subject, email, access_token, refresh_token, id_token, dump_time(expires_at), scope, timestamp, timestamp),
            )

    def create_oauth_transaction(
        self,
        state: str,
        code_verifier: str,
        nonce: str,
        client_id: str,
        host_id: str,
        expires_at: datetime,
    ) -> None:
        with self.database.connection() as db:
            db.execute("DELETE FROM oauth_transactions WHERE expires_at < ?", (dump_time(now()),))
            db.execute(
                "INSERT INTO oauth_transactions VALUES (?, ?, ?, ?, ?, ?)",
                (state, code_verifier, nonce, client_id, host_id, dump_time(expires_at)),
            )

    def consume_oauth_transaction(self, state: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute(
                "SELECT * FROM oauth_transactions WHERE state = ?", (state,)
            ).fetchone()
            db.execute("DELETE FROM oauth_transactions WHERE state = ?", (state,))
        if not row or load_time(row["expires_at"]) < now():
            return None
        return dict(row)

    def update_chatgpt_tokens(
        self,
        access_token: str,
        refresh_token: str | None,
        expires_at: datetime | None,
        scope: str,
    ) -> None:
        timestamp = dump_time(now())
        with self.database.connection() as db:
            db.execute(
                "UPDATE chatgpt_oauth SET access_token = ?, refresh_token = COALESCE(?, refresh_token), expires_at = ?, scope = ?, updated_at = ? WHERE id = 1",
                (access_token, refresh_token, dump_time(expires_at), scope, timestamp),
            )

    def chatgpt_connection(self) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM chatgpt_oauth WHERE id = 1").fetchone()
        return dict(row) if row else None

    def clear_chatgpt_connection(self) -> None:
        with self.database.connection() as db:
            db.execute("DELETE FROM chatgpt_oauth WHERE id = 1")

    def create_skill(self, name: str, description: str, content: str) -> Skill:
        skill_id, timestamp = uuid4(), now()
        with self.database.connection() as db:
            db.execute(
                "INSERT INTO skills VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(skill_id), name, description, content, 1, dump_time(timestamp), dump_time(timestamp)),
            )
        return self.get_skill(skill_id)

    def list_skills(self) -> list[Skill]:
        with self.database.connection() as db:
            rows = db.execute("SELECT * FROM skills WHERE enabled = 1 ORDER BY name").fetchall()
        return [self._skill(row) for row in rows]

    def upsert_skill(self, name: str, description: str, content: str) -> Skill:
        timestamp = now()
        with self.database.connection() as db:
            row = db.execute("SELECT id FROM skills WHERE name = ?", (name,)).fetchone()
            if row is None:
                skill_id = uuid4()
                db.execute(
                    "INSERT INTO skills VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(skill_id), name, description, content, 1, dump_time(timestamp), dump_time(timestamp)),
                )
            else:
                skill_id = UUID(row["id"])
                db.execute(
                    "UPDATE skills SET description = ?, content = ?, enabled = 1, updated_at = ? WHERE id = ?",
                    (description, content, dump_time(timestamp), str(skill_id)),
                )
        return self.get_skill(skill_id)

    def get_skill(self, skill_id: UUID) -> Skill:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM skills WHERE id = ?", (str(skill_id),)).fetchone()
        if row is None:
            raise KeyError("skill not found")
        return self._skill(row)

    def assign_skill(self, bot_id: UUID, skill_id: UUID, config: dict[str, Any] | None = None) -> None:
        self.get_bot(bot_id)
        self.get_skill(skill_id)
        with self.database.connection() as db:
            db.execute(
                "INSERT INTO bot_skills (bot_id, skill_id, config) VALUES (?, ?, ?) ON CONFLICT(bot_id, skill_id) DO UPDATE SET config = excluded.config",
                (str(bot_id), str(skill_id), json.dumps(config or {})),
            )

    def unassign_skill(self, bot_id: UUID, skill_id: UUID) -> None:
        with self.database.connection() as db:
            db.execute("DELETE FROM bot_skills WHERE bot_id = ? AND skill_id = ?", (str(bot_id), str(skill_id)))

    def list_bot_skills(self, bot_id: UUID) -> list[Skill]:
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT skills.* FROM skills JOIN bot_skills ON bot_skills.skill_id = skills.id WHERE bot_skills.bot_id = ? AND skills.enabled = 1 ORDER BY skills.name",
                (str(bot_id),),
            ).fetchall()
        return [self._skill(row) for row in rows]

    def create_job(self, title: str, description: str, priority: str, assignee_bot_id: UUID | None, created_by_bot_id: UUID | None = None) -> Job:
        if assignee_bot_id is not None:
            self.get_bot(assignee_bot_id)
        job_id, timestamp = uuid4(), now()
        with self.database.connection() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(job_id), title, description, "open", priority, str(assignee_bot_id) if assignee_bot_id else None, str(created_by_bot_id) if created_by_bot_id else None, dump_time(timestamp), dump_time(timestamp)),
            )
        return self.get_job(job_id)

    def list_jobs(self, bot_id: UUID | None = None) -> list[Job]:
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT * FROM jobs WHERE assignee_bot_id = ? ORDER BY updated_at DESC" if bot_id else "SELECT * FROM jobs ORDER BY updated_at DESC",
                (str(bot_id),) if bot_id else (),
            ).fetchall()
        return [self._job(row) for row in rows]

    def get_job(self, job_id: UUID) -> Job:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
        if row is None:
            raise KeyError("job not found")
        return self._job(row)

    def update_job(self, job_id: UUID, fields: dict[str, Any]) -> Job:
        accepted = {key: value for key, value in fields.items() if value is not None}
        if "assignee_bot_id" in accepted and accepted["assignee_bot_id"] is not None:
            self.get_bot(accepted["assignee_bot_id"])
            accepted["assignee_bot_id"] = str(accepted["assignee_bot_id"])
        if not accepted:
            return self.get_job(job_id)
        accepted["updated_at"] = dump_time(now())
        assignments = ", ".join(f"{column} = ?" for column in accepted)
        with self.database.connection() as db:
            cursor = db.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", (*accepted.values(), str(job_id)))
        if cursor.rowcount != 1:
            raise KeyError("job not found")
        return self.get_job(job_id)

    def create_group(self, name: str, description: str, member_bot_ids: list[UUID]) -> WorkGroup:
        group_id, timestamp = uuid4(), now()
        for bot_id in member_bot_ids:
            self.get_bot(bot_id)
        with self.database.connection() as db:
            db.execute("INSERT INTO work_groups VALUES (?, ?, ?, ?)", (str(group_id), name, description, dump_time(timestamp)))
            db.executemany("INSERT INTO group_members VALUES (?, ?)", [(str(group_id), str(bot_id)) for bot_id in dict.fromkeys(member_bot_ids)])
        return self.get_group(group_id)

    def list_groups(self) -> list[WorkGroup]:
        with self.database.connection() as db:
            rows = db.execute("SELECT * FROM work_groups ORDER BY created_at DESC").fetchall()
        return [self._group(row) for row in rows]

    def get_group(self, group_id: UUID) -> WorkGroup:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM work_groups WHERE id = ?", (str(group_id),)).fetchone()
        if row is None:
            raise KeyError("group not found")
        return self._group(row)

    def add_group_member(self, group_id: UUID, bot_id: UUID) -> None:
        self.get_group(group_id)
        self.get_bot(bot_id)
        with self.database.connection() as db:
            db.execute("INSERT OR IGNORE INTO group_members VALUES (?, ?)", (str(group_id), str(bot_id)))

    def remove_group_member(self, group_id: UUID, bot_id: UUID) -> None:
        self.get_group(group_id)
        with self.database.connection() as db:
            db.execute("DELETE FROM group_members WHERE group_id = ? AND bot_id = ?", (str(group_id), str(bot_id)))

    def append_group_message(self, group_id: UUID, sender_type: str, content: str, sender_bot_id: UUID | None = None) -> GroupMessage:
        self.get_group(group_id)
        message_id, timestamp = uuid4(), now()
        with self.database.connection() as db:
            db.execute("INSERT INTO group_messages VALUES (?, ?, ?, ?, ?, ?)", (str(message_id), str(group_id), sender_type, str(sender_bot_id) if sender_bot_id else None, content, dump_time(timestamp)))
        return GroupMessage(id=message_id, group_id=group_id, sender_type=sender_type, sender_bot_id=sender_bot_id, content=content, created_at=timestamp)

    def drop_copied_group_context(self) -> None:
        """Remove private copies of group lines. They made every later request carry a huge snapshot."""
        with self.database.connection() as db:
            found = db.execute(
                "SELECT 1 FROM messages WHERE role = 'group' AND content LIKE '[Grup %' LIMIT 1"
            ).fetchone()
        if found is None:
            return
        with self.database.connection() as db:
            db.execute("DELETE FROM messages WHERE role = 'group' AND content LIKE '[Grup %'")

    def enqueue_group_speaker(
        self,
        group_id: UUID,
        bot_id: UUID,
        content: str,
        sender_bot_id: UUID | None,
        depth: int,
    ) -> bool:
        """Queue one bot to speak. The same bot is not queued twice for this group."""
        with self.database.connection() as db:
            existing = db.execute(
                "SELECT 1 FROM group_queue WHERE group_id = ? AND bot_id = ?",
                (str(group_id), str(bot_id)),
            ).fetchone()
            if existing is not None:
                return False
            count = db.execute("SELECT COUNT(*) AS n FROM group_queue WHERE group_id = ?", (str(group_id),)).fetchone()
            if count is not None and int(count["n"]) >= 12:
                return False
            db.execute(
                "INSERT INTO group_queue (group_id, bot_id, content, sender_bot_id, depth, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(group_id), str(bot_id), content, str(sender_bot_id) if sender_bot_id else None, depth, dump_time(now())),
            )
        return True

    def peek_group_speaker(self, group_id: UUID) -> tuple[int, UUID, str, UUID | None, int] | None:
        with self.database.connection() as db:
            row = db.execute(
                "SELECT * FROM group_queue WHERE group_id = ? ORDER BY id LIMIT 1",
                (str(group_id),),
            ).fetchone()
        if row is None:
            return None
        return (
            int(row["id"]),
            UUID(row["bot_id"]),
            row["content"],
            UUID(row["sender_bot_id"]) if row["sender_bot_id"] else None,
            int(row["depth"]),
        )

    def drop_group_speaker(self, turn_id: int) -> None:
        with self.database.connection() as db:
            db.execute("DELETE FROM group_queue WHERE id = ?", (turn_id,))

    def clear_group_queue(self, group_id: UUID) -> None:
        with self.database.connection() as db:
            db.execute("DELETE FROM group_queue WHERE group_id = ?", (str(group_id),))

    def group_queue_size(self, group_id: UUID) -> int:
        with self.database.connection() as db:
            row = db.execute("SELECT COUNT(*) AS n FROM group_queue WHERE group_id = ?", (str(group_id),)).fetchone()
        return int(row["n"]) if row else 0

    def queued_bot_ids(self, group_id: UUID) -> list[UUID]:
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT bot_id FROM group_queue WHERE group_id = ? ORDER BY id",
                (str(group_id),),
            ).fetchall()
        return [UUID(row["bot_id"]) for row in rows]

    def pending_group_ids(self) -> list[UUID]:
        with self.database.connection() as db:
            rows = db.execute("SELECT DISTINCT group_id FROM group_queue ORDER BY id").fetchall()
        return [UUID(row["group_id"]) for row in rows]

    def list_group_messages(self, group_id: UUID) -> list[GroupMessage]:
        self.get_group(group_id)
        with self.database.connection() as db:
            rows = db.execute("SELECT * FROM group_messages WHERE group_id = ? ORDER BY created_at", (str(group_id),)).fetchall()
        return [GroupMessage(id=UUID(row["id"]), group_id=UUID(row["group_id"]), sender_type=row["sender_type"], sender_bot_id=UUID(row["sender_bot_id"]) if row["sender_bot_id"] else None, content=row["content"], created_at=load_time(row["created_at"])) for row in rows]

    def link_run_to_group(self, run_id: UUID, group_id: UUID) -> None:
        with self.database.connection() as db:
            db.execute("INSERT INTO group_run_links VALUES (?, ?)", (str(run_id), str(group_id)))

    def group_for_run(self, run_id: UUID) -> UUID | None:
        with self.database.connection() as db:
            row = db.execute("SELECT group_id FROM group_run_links WHERE run_id = ?", (str(run_id),)).fetchone()
        return UUID(row["group_id"]) if row else None

    def active_runs_for_bot(self, bot_id: UUID) -> list[Run]:
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT * FROM runs WHERE bot_id = ? AND status IN (?, ?)",
                (str(bot_id), RunStatus.QUEUED, RunStatus.RUNNING),
            ).fetchall()
        return [self._run(row) for row in rows]

    def runs_for_group(self, group_id: UUID) -> list[Run]:
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT runs.* FROM runs JOIN group_run_links ON group_run_links.run_id = runs.id WHERE group_run_links.group_id = ? AND runs.status IN (?, ?)",
                (str(group_id), RunStatus.QUEUED, RunStatus.RUNNING),
            ).fetchall()
        return [self._run(row) for row in rows]

    def latest_interrupted_run_for_group(self, group_id: UUID, bot_id: UUID) -> Run | None:
        with self.database.connection() as db:
            row = db.execute(
                """
                SELECT runs.* FROM runs
                JOIN group_run_links ON group_run_links.run_id = runs.id
                WHERE group_run_links.group_id = ? AND runs.bot_id = ?
                  AND runs.status = ? AND runs.continuation IS NOT NULL
                  AND runs.continuation != ''
                ORDER BY runs.completed_at DESC, runs.created_at DESC LIMIT 1
                """,
                (str(group_id), str(bot_id), RunStatus.CANCELLED),
            ).fetchone()
        return self._run(row) if row else None

    def create_handoff(self, source_bot_id: UUID, target_bot_id: UUID, task: str, parent_run_id: UUID | None) -> Handoff:
        if source_bot_id == target_bot_id:
            raise ValueError("a bot cannot hand off work to itself")
        self.get_bot(source_bot_id)
        self.get_bot(target_bot_id)
        handoff_id, timestamp = uuid4(), now()
        with self.database.connection() as db:
            db.execute("INSERT INTO handoffs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (str(handoff_id), str(source_bot_id), str(target_bot_id), str(parent_run_id) if parent_run_id else None, None, task, "queued", None, dump_time(timestamp), None))
        return self.get_handoff(handoff_id)

    def set_handoff_child(self, handoff_id: UUID, child_run_id: UUID) -> Handoff:
        with self.database.connection() as db:
            db.execute("UPDATE handoffs SET child_run_id = ?, status = 'running' WHERE id = ?", (str(child_run_id), str(handoff_id)))
        return self.get_handoff(handoff_id)

    def complete_handoffs_for_run(self, child_run_id: UUID, result: str, success: bool) -> list[Handoff]:
        completed_at = dump_time(now())
        with self.database.connection() as db:
            db.execute("UPDATE handoffs SET status = ?, result = ?, completed_at = ? WHERE child_run_id = ? AND status = 'running'", ("completed" if success else "failed", result, completed_at, str(child_run_id)))
            rows = db.execute("SELECT * FROM handoffs WHERE child_run_id = ?", (str(child_run_id),)).fetchall()
        return [self._handoff(row) for row in rows]

    def get_handoff(self, handoff_id: UUID) -> Handoff:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM handoffs WHERE id = ?", (str(handoff_id),)).fetchone()
        if row is None:
            raise KeyError("handoff not found")
        return self._handoff(row)

    def list_handoffs(self, bot_id: UUID | None = None) -> list[Handoff]:
        with self.database.connection() as db:
            rows = db.execute("SELECT * FROM handoffs WHERE source_bot_id = ? OR target_bot_id = ? ORDER BY created_at DESC", (str(bot_id), str(bot_id))).fetchall() if bot_id else db.execute("SELECT * FROM handoffs ORDER BY created_at DESC").fetchall()
        return [self._handoff(row) for row in rows]

    def create_tool_call(self, run_id: UUID, tool_name: str, arguments: dict[str, Any]) -> UUID:
        tool_call_id, timestamp = uuid4(), now()
        with self.database.connection() as db:
            db.execute("INSERT INTO tool_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (str(tool_call_id), str(run_id), tool_name, json.dumps(arguments), "running", None, dump_time(timestamp), None))
        return tool_call_id

    def complete_tool_call(self, tool_call_id: UUID, result: dict[str, Any], success: bool) -> None:
        with self.database.connection() as db:
            db.execute("UPDATE tool_calls SET status = ?, result = ?, completed_at = ? WHERE id = ?", ("completed" if success else "failed", json.dumps(result), dump_time(now()), str(tool_call_id)))

    def _bot(self, row: Any) -> Bot:
        return Bot(id=UUID(row["id"]), name=row["name"], description=row["description"], instructions=row["instructions"], model=row["model"], status=BotStatus(row["status"]), created_at=load_time(row["created_at"]), updated_at=load_time(row["updated_at"]))

    def _skill(self, row: Any) -> Skill:
        return Skill(id=UUID(row["id"]), name=row["name"], description=row["description"], content=row["content"], enabled=bool(row["enabled"]), created_at=load_time(row["created_at"]), updated_at=load_time(row["updated_at"]))

    def _job(self, row: Any) -> Job:
        return Job(id=UUID(row["id"]), title=row["title"], description=row["description"], status=row["status"], priority=row["priority"], assignee_bot_id=UUID(row["assignee_bot_id"]) if row["assignee_bot_id"] else None, created_by_bot_id=UUID(row["created_by_bot_id"]) if row["created_by_bot_id"] else None, created_at=load_time(row["created_at"]), updated_at=load_time(row["updated_at"]))

    def _group(self, row: Any) -> WorkGroup:
        group_id = UUID(row["id"])
        with self.database.connection() as db:
            member_rows = db.execute("SELECT bots.* FROM bots JOIN group_members ON group_members.bot_id = bots.id WHERE group_members.group_id = ? ORDER BY group_members.rowid", (str(group_id),)).fetchall()
        return WorkGroup(id=group_id, name=row["name"], description=row["description"], members=[self._bot(member) for member in member_rows], created_at=load_time(row["created_at"]))

    def list_plugins(self) -> list[Plugin]:
        with self.database.connection() as db:
            rows = db.execute("SELECT * FROM plugins ORDER BY created_at").fetchall()
        return [self._plugin(row) for row in rows]

    def get_plugin(self, plugin_id: UUID) -> Plugin:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM plugins WHERE id = ?", (str(plugin_id),)).fetchone()
        if row is None:
            raise KeyError("plugin not found")
        return self._plugin(row)

    def get_plugin_by_name(self, name: str) -> tuple[Plugin, str | None]:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM plugins WHERE lower(name) = lower(?)", (name.strip(),)).fetchone()
        if row is None:
            raise KeyError("plugin not found")
        return self._plugin(row), row["token"]

    def create_plugin(self, name: str, url: str, token: str | None, tools: list[dict[str, str]]) -> Plugin:
        plugin_id, timestamp = uuid4(), now()
        description = ", ".join(tool["name"] for tool in tools[:8])
        with self.database.connection() as db:
            db.execute(
                "INSERT INTO plugins (id, name, url, description, token, tools_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(plugin_id), name.strip(), url.strip(), description, token or None, json.dumps(tools), dump_time(timestamp)),
            )
        return self.get_plugin(plugin_id)

    def delete_plugin(self, plugin_id: UUID) -> None:
        self.get_plugin(plugin_id)
        with self.database.connection() as db:
            db.execute("DELETE FROM plugins WHERE id = ?", (str(plugin_id),))

    def _plugin(self, row: Any) -> Plugin:
        tools = json.loads(row["tools_json"] or "[]")
        names = [str(tool.get("name")) for tool in tools if isinstance(tool, dict) and tool.get("name")]
        return Plugin(id=UUID(row["id"]), name=row["name"], url=row["url"], description=row["description"], tools=names, created_at=load_time(row["created_at"]))

    def _handoff(self, row: Any) -> Handoff:
        return Handoff(id=UUID(row["id"]), source_bot_id=UUID(row["source_bot_id"]), target_bot_id=UUID(row["target_bot_id"]), parent_run_id=UUID(row["parent_run_id"]) if row["parent_run_id"] else None, child_run_id=UUID(row["child_run_id"]) if row["child_run_id"] else None, task=row["task"], status=row["status"], result=row["result"], created_at=load_time(row["created_at"]), completed_at=load_time(row["completed_at"]))

    def _message(self, row: Any) -> Message:
        return Message(
            id=UUID(row["id"]),
            conversation_id=UUID(row["conversation_id"]),
            role=row["role"],
            content=row["content"],
            model=row["model"],
            usage=json.loads(row["usage"] or "{}"),
            attachments=json.loads(row["attachments"] or "[]"),
            citations=json.loads(row["citations"] or "[]"),
            created_at=load_time(row["created_at"]),
        )

    def _run(self, row: Any) -> Run:
        return Run(
            id=UUID(row["id"]),
            bot_id=UUID(row["bot_id"]),
            conversation_id=UUID(row["conversation_id"]),
            status=RunStatus(row["status"]),
            prompt=row["prompt"],
            model=row["model"],
            error=row["error"],
            usage=json.loads(row["usage"] or "{}"),
            stop_requested=bool(row["stop_requested"]),
            continuation=row["continuation"] if "continuation" in row.keys() else None,
            created_at=load_time(row["created_at"]),
            started_at=load_time(row["started_at"]),
            completed_at=load_time(row["completed_at"]),
        )

    def _approval(self, row: Any) -> Approval:
        return Approval(id=UUID(row["id"]), run_id=UUID(row["run_id"]), tool_name=row["tool_name"], risk_class=row["risk_class"], reason=row["reason"], payload=json.loads(row["payload"]), status=ApprovalStatus(row["status"]), requested_at=load_time(row["requested_at"]), decided_at=load_time(row["decided_at"]))
