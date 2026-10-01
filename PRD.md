# Product Requirements Document (PRD)

## Persistent Agent Workspace — Grok Bot-like Local-First Agent Platform

**Status:** Draft v1.0  
**Date:** 2026-09-29  
**Primary language:** Python 3.12+  
**Development mode:** Local-first, free/open-source where possible  
**Target:** Build a persistent AI teammate platform inspired by the operating model of Grok Bot, without depending on Grok as the model.

---

## 1. Executive Summary

This project is a local-first platform for creating **persistent AI Bots** that behave more like durable teammates than disposable chat sessions.

Each Bot has its own identity, role, conversation history, learned memory, skills, permissions, and routines. Bots can execute long-running work, interact with tools, use a persistent Docker-based computer, wait for human approval, resume after process failure, and collaborate with other Bots.

The platform deliberately separates:

- **Bot state** — identity, configuration, memory, conversations, skills, permissions.
- **Run state** — an individual execution of a Bot task.
- **Workflow state** — durable execution, retries, schedules, waiting, and resumption.
- **Computer state** — filesystem, browser profile, terminal environment, repositories, and artifacts.
- **Model provider state** — OpenRouter credentials, model selection, usage, and limits.

The initial version will run locally with minimal infrastructure cost using:

- **PydanticAI** for the agent loop and tool calling.
- **DBOS** for durable workflows, queues, schedules, retries, and human-in-the-loop waiting.
- **PostgreSQL** for product/application persistence.
- **Docker** for per-user persistent sandbox computers.
- **Playwright + Chromium** for browser automation inside the sandbox.
- **FastAPI** for the backend API and WebSocket/SSE streaming.
- **Next.js** for the web client.
- **OpenRouter** as the model gateway.
- **OpenRouter OAuth PKCE / API keys** for model-provider authorization.
- **Sign in with ChatGPT** as the desired product identity provider when OpenAI makes the integration available to this app; authentication must remain provider-pluggable so development is not blocked by partner access.

The MVP should be able to run on a developer laptop with Docker Compose.

---

# 2. Product Vision

Build a personal AI workspace in which users create specialized Bots that:

1. persist over time;
2. remember role-specific context;
3. continue working independently of a single chat request;
4. use a real computer environment;
5. produce durable files and artifacts;
6. run scheduled routines;
7. request approval before risky actions;
8. collaborate asynchronously with other Bots;
9. recover from application crashes or restarts;
10. can switch between models without changing the product architecture.

The core product primitive is **Bot**, not chat completion and not framework `Agent` objects.

PydanticAI is an implementation detail of how a Bot reasons during a Run.

---

# 3. Product Principles

## 3.1 Durable over ephemeral

A Bot survives application restarts. Its identity and memory are database records, not Python objects held in RAM.

## 3.2 Computer state is different from Bot state

Bots belonging to the same user may share a persistent computer/workspace while keeping separate identity, memory, conversations, and instructions.

## 3.3 Explicit side effects

Model reasoning may propose an action, but actions with external or destructive effects pass through a policy and approval layer before execution.

## 3.4 Model-provider agnostic

OpenRouter is the initial model gateway. No domain model or business logic may be tightly coupled to a specific OpenRouter model slug.

## 3.5 Local-first and cost-aware

Core development must work locally using open-source components. Paid hosted infrastructure may be added later without changing the primary domain model.

## 3.6 Durable workflows are infrastructure, not prompts

Scheduling, retries, pause/resume, waiting for approval, and background execution must not be implemented by telling the LLM to "remember" them.

## 3.7 Bots are not a security boundary

In the Grok Bot-inspired model, Bots under one user may share a computer. Isolation between users is stronger than isolation between Bots owned by the same user.

---

# 4. Problem Statement

Most agent implementations are still structured as:

```text
user message
    -> LLM
    -> tool loop
    -> final answer
    -> process ends
```

This is insufficient for an AI teammate product because there is no first-class concept of:

- persistent identity;
- long-term role-specific memory;
- durable tasks;
- recoverable background work;
- persistent computer state;
- recurring routines;
- asynchronous Bot-to-Bot handoff;
- approval workflows;
- durable artifacts.

The project will introduce these primitives explicitly rather than attempting to simulate them only through prompts.

---

# 5. Goals

## 5.1 MVP goals

The MVP must support:

1. Create, update, archive, and delete persistent Bots.
2. Give each Bot a name, description, instructions, model policy, skills, and permissions.
3. Chat with a Bot through a persistent conversation.
4. Run PydanticAI using models accessed through OpenRouter.
5. Stream model output and tool activity to the client.
6. Persist messages, runs, tool calls, results, and errors.
7. Give each user a persistent Docker computer with `/workspace` backed by a Docker volume.
8. Allow Bots to execute controlled shell commands inside their user's sandbox.
9. Allow Bots to read/write files inside `/workspace`.
10. Add browser automation using Playwright + Chromium.
11. Run Bot tasks durably through DBOS.
12. Recover incomplete workflow executions after application restart.
13. Pause a run for human approval and resume it later.
14. Create scheduled Bot routines.
15. Store long-term Bot memory separately from conversation history.
16. Select OpenRouter models per Bot or per Run.
17. Support OpenRouter free routing for zero-cost development where available.
18. Keep authentication/provider layers modular.

## 5.2 Post-MVP goals

- Bot-to-Bot direct messages.
- Group conversations containing multiple Bots.
- Bot handoffs and dependencies.
- MCP integration registry.
- Skill library and teach-a-task flows.
- Browser profiles and session persistence.
- User-configurable policy engine.
- Mobile client.
- Multi-user hosted deployment.
- Stronger sandbox isolation using gVisor, Firecracker, or a managed sandbox provider.
- Semantic/vector retrieval for long-term memory.
- Shared organizational memory.
- Event-triggered routines from GitHub, Slack, webhooks, etc.

---

# 6. Non-Goals for MVP

The MVP will not attempt to provide:

- perfect autonomous operation with no supervision;
- a direct clone of proprietary Grok Bot implementation details;
- access to a user's ChatGPT subscription as generic OpenAI API credits;
- billing or marketplace functionality;
- enterprise RBAC;
- multi-tenant hardened sandboxing for untrusted internet users;
- autonomous payment or financial transaction execution;
- CAPTCHA or 2FA bypass;
- unrestricted host shell access;
- direct Docker socket access from the agent sandbox;
- arbitrary privileged containers;
- production-grade browser computer-use vision in v0;
- guaranteed compatibility with every OpenRouter model.

---

# 7. Important Authentication and Billing Constraint

## 7.1 ChatGPT identity versus model usage

The product must treat these as separate concerns:

### Application identity

The desired UX is **Sign in with ChatGPT** when this project is eligible for that OpenAI identity-provider integration.

Sign in with ChatGPT is an identity flow. It can provide identity fields such as name, email, and profile image for supported external applications. It does not automatically provide model API access, user conversations, ChatGPT memory, API tokens, or ChatGPT subscription billing to this project.

Therefore authentication must be implemented through a provider interface rather than embedding ChatGPT-specific assumptions into the domain model.

Recommended interface:

```text
AuthProvider
├── ChatGPTIdentityProvider     # desired production option when available
├── LocalDevProvider            # required for local development
└── Future providers            # GitHub/Google/email/etc. if needed
```

## 7.2 ChatGPT subscription is not API credit

ChatGPT subscriptions and OpenAI API billing are separate products. The system must not depend on using a ChatGPT Free/Go/Plus/Pro/Business subscription as generic API billing.

## 7.3 Model-provider authorization

OpenRouter will be the model gateway.

Two supported modes:

### Development mode

A server-level `OPENROUTER_API_KEY` may be used for local development.

### Bring-your-own OpenRouter account

Preferred user-facing mode:

```text
User
  -> OpenRouter OAuth PKCE
  -> authorization code
  -> exchange code
  -> user-controlled OpenRouter API key
  -> encrypt and store credential
```

This separates product login from model billing.

The user may choose free OpenRouter routing where available or use their own funded OpenRouter account.

---

# 8. Target Users

## Persona A — Solo Developer

Needs persistent coding, research, and operations Bots while keeping infrastructure cheap and local.

Examples:

- Developer Bot
- Research Bot
- QA Bot
- Release Bot

## Persona B — Technical Power User

Wants reusable automations and multiple specialized AI workers without building every workflow manually.

## Persona C — Small Team — Later Phase

Wants persistent shared Bots, shared skills, connectors, and team workflows.

---

# 9. Core Domain Concepts

## 9.1 User

Owns Bots, credentials, computers, skills, routines, and workspace data.

## 9.2 Bot

A durable AI teammate configuration.

A Bot contains:

- identity;
- description;
- instructions;
- model policy;
- permission policy;
- memory namespace;
- enabled skills;
- conversation references;
- computer binding;
- active/inactive state.

A Bot is **not** a permanently running Python process.

## 9.3 Conversation

A persistent ordered stream of messages belonging to a Bot or, later, a group.

## 9.4 Run

One execution initiated by:

- user message;
- routine;
- Bot-to-Bot message;
- webhook/event;
- retry/resume.

## 9.5 Workflow

The DBOS durable execution wrapping a Run.

## 9.6 Memory

Long-term information selected for future use independently from raw conversation history.

Examples:

- user preferences;
- project conventions;
- Bot-specific role context;
- durable facts;
- summaries.

## 9.7 Computer

Persistent execution environment associated with a user.

Initial implementation:

```text
Docker container + Docker volume
```

## 9.8 Skill

Reusable instructions describing **how** a class of task should be performed.

## 9.9 Routine

A rule describing **when** a Bot should execute a task.

## 9.10 Approval

A durable request for a user decision before a protected action executes.

## 9.11 Handoff — Post-MVP

An asynchronous request from one Bot to another Bot.

---

# 10. High-Level Architecture

```text
                           WEB CLIENT
                            Next.js
                               |
                      HTTP / SSE / WS
                               |
                               v
                    +---------------------+
                    |       FastAPI       |
                    |                     |
                    | Auth                |
                    | Bot API             |
                    | Conversations       |
                    | Runs                |
                    | Approvals           |
                    | Routines            |
                    +----------+----------+
                               |
             +-----------------+------------------+
             |                                    |
             v                                    v
      +--------------+                    +---------------+
      |  PostgreSQL  |                    |     DBOS      |
      |              |                    | durable exec  |
      | product data |                    | queue/sched   |
      +--------------+                    +-------+-------+
                                                  |
                                                  v
                                        +------------------+
                                        |  PydanticAI      |
                                        |  Agent Runtime   |
                                        +--------+---------+
                                                 |
                    +----------------------------+-------------------------+
                    |                            |                         |
                    v                            v                         v
              OpenRouter                    Tool Runtime              Memory
              Model Gateway                     |                    Service
                    |                            |
                    |                  +---------+---------+
                    |                  |                   |
                    v                  v                   v
              external LLMs       Sandbox API         MCP tools
                                      |
                                      v
                            +-------------------+
                            | Docker Computer   |
                            | per user          |
                            |                   |
                            | /workspace        |
                            | shell             |
                            | git               |
                            | Python/Node       |
                            | Chromium          |
                            | Playwright        |
                            +-------------------+
```

---

# 11. Recommended Technology Stack

| Layer | Technology | Decision |
|---|---|---|
| Language | Python 3.12+ | Primary backend/runtime language |
| Agent framework | PydanticAI | Agent loop, typed tools, model abstraction |
| Durable execution | DBOS | Workflow recovery, queues, schedules, waiting |
| Product database | PostgreSQL | Durable product/domain records |
| DB migrations | Alembic | Schema migrations |
| ORM/data layer | SQLAlchemy 2.x or SQLModel | App persistence |
| Backend | FastAPI | HTTP API, streaming endpoints |
| Validation | Pydantic | API/domain schemas |
| Model gateway | OpenRouter | Multi-provider model access |
| Model auth | OpenRouter OAuth PKCE + API key fallback | BYO model account |
| Product auth | ChatGPT identity when eligible, provider abstraction required | User identity |
| Sandbox | Docker Engine | Free local computer runtime |
| Persistent workspace | Docker named volumes | Durable `/workspace` |
| Browser | Playwright + Chromium | Browser automation |
| Frontend | Next.js + TypeScript | Product UI |
| Styling | Tailwind CSS | Fast product UI development |
| Realtime | SSE first, WebSocket where bidirectional events are needed | Run streaming |
| Cache/pubsub | None in v0; Redis optional later | Avoid unnecessary infra |
| Integrations | MCP + native Python tools | External tool layer |
| Observability | structured logs + DBOS metadata initially | Local debugging |
| Tests | pytest + pytest-asyncio | Backend |
| Frontend tests | Vitest + Playwright | UI/integration |
| Local orchestration | Docker Compose | One-command local boot |

---

# 12. OpenRouter Model Architecture

## 12.1 Requirements

The system must:

- support a configurable default model;
- support per-Bot model preferences;
- support per-Run model overrides;
- expose model capability metadata;
- gracefully handle unsupported tool calling;
- allow model fallback chains;
- log token/usage metadata when returned;
- avoid leaking OpenRouter credentials to sandbox containers;
- support user-owned OpenRouter credentials.

## 12.2 Default local development mode

Environment:

```text
OPENROUTER_API_KEY=...
DEFAULT_MODEL=openrouter/free
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
```

Use `openrouter/free` as the initial zero-cost routing option where practical.

Quality and tool-calling capability may vary because free models can change. Therefore production logic must not assume a specific model implementation behind the free router.

## 12.3 Model policy object

Suggested structure:

```json
{
  "primary_model": "openrouter/free",
  "fallback_models": [],
  "temperature": 0.2,
  "max_output_tokens": 8192,
  "require_tool_calling": true,
  "require_structured_output": false
}
```

## 12.4 Credential precedence

```text
run-specific credential
    > user OpenRouter credential
    > server development key
```

Production should normally disable the server fallback for ordinary users.

---

# 13. Authentication Architecture

## 13.1 Product authentication

Domain interface:

```python
class AuthIdentity:
    provider: str
    provider_subject: str
    email: str | None
    name: str | None
    avatar_url: str | None
```

The internal `users.id` must never equal a provider-specific subject.

Use an `auth_identities` table to map external identities to users.

## 13.2 ChatGPT identity target

Desired UX:

```text
Continue with ChatGPT
        |
        v
OpenAI identity authorization
        |
        v
callback
        |
        v
link/create internal user
```

This feature is conditional on the application being eligible for OpenAI's Sign in with ChatGPT partner/support surface.

The MVP must include an alternate development authentication path so that this dependency does not block engineering.

## 13.3 Local development authentication

Minimum acceptable implementation:

```text
DEV_AUTH=true
DEV_USER_EMAIL=local@example.test
```

When enabled only in development, middleware automatically creates/loads a local development user.

This mode must fail closed in production.

## 13.4 OpenRouter authorization

OpenRouter identity/model credential linking is independent from product login.

UI:

```text
Settings
  -> Model Providers
     -> Connect OpenRouter
```

Flow:

```text
Generate PKCE verifier/challenge
        -> OpenRouter authorization
        -> callback code
        -> backend exchanges code
        -> receive user-controlled key
        -> encrypt
        -> store credential
```

Never send stored provider keys to the frontend after initial connection.

---

# 14. Bot Lifecycle

## 14.1 Create Bot

User provides:

- name;
- description;
- instructions;
- optional model preference;
- enabled tools;
- permissions;
- optional skills.

Backend creates:

- `bot` record;
- default conversation;
- memory namespace;
- association with user's computer.

## 14.2 Execute Bot

```text
User message
   |
   v
persist message
   |
   v
create Run
   |
   v
enqueue DBOS workflow
   |
   v
assemble Bot runtime context
   |
   v
PydanticAI run
   |
   +--> model call
   +--> tool proposal
   +--> policy check
   +--> tool execution / approval wait
   +--> model observation
   +--> repeat
   |
   v
persist final response
   |
   v
optional memory extraction/update
   |
   v
Run = completed
```

## 14.3 Archive Bot

Archived Bot:

- cannot start new routines;
- cannot receive new user tasks by default;
- retains conversations, memory, and artifacts;
- can be restored.

## 14.4 Delete Bot

Delete product records according to retention policy.

Shared user computer files must not be silently deleted when deleting one Bot because the computer may be shared by other Bots.

---

# 15. Run State Machine

```text
CREATED
   |
   v
QUEUED
   |
   v
RUNNING
   |
   +---------> WAITING_APPROVAL
   |                 |
   |          approve|reject
   |                 v
   |              RUNNING
   |
   +---------> WAITING_EXTERNAL
   |
   +---------> FAILED_RETRYABLE
   |                 |
   |                 v
   |              RUNNING
   |
   +---------> FAILED
   |
   +---------> CANCELLED
   |
   v
COMPLETED
```

Each transition must be persisted.

---

# 16. Conversation System

## Requirements

- Conversations belong to one Bot in MVP.
- Messages are append-only records except for administrative deletion/redaction.
- Each message has a normalized role/type.
- Tool events are stored separately from user-visible text messages.
- Conversation context building must use a bounded strategy rather than loading unlimited history.

Message types:

```text
user
assistant
system_event
tool_summary
approval_event
routine_event
handoff_event     # later
```

Context assembly priority:

```text
Bot instructions
+ policy instructions
+ relevant memory
+ active skill instructions
+ recent conversation context
+ current task
+ tool schemas
```

---

# 17. Long-Term Memory

## 17.1 Memory categories

```text
preference
fact
project_context
workflow_preference
summary
relationship/context
```

## 17.2 Memory scoping

Initial namespace:

```text
users/{user_id}/bots/{bot_id}/memory
```

Future shared memory:

```text
users/{user_id}/shared/memory
```

## 17.3 Memory writing

The system should not write every conversation turn into long-term memory.

A memory candidate should be evaluated for:

- stability;
- future usefulness;
- confidence;
- sensitivity;
- duplication;
- expiration need.

MVP may begin with explicit memory operations exposed to the Bot:

```text
memory_list
memory_search
memory_write
memory_update
memory_delete
```

Automatic memory extraction is a later enhancement.

## 17.4 Data representation

MVP: PostgreSQL textual memory records.

Future: `pgvector` embeddings for semantic retrieval.

---

# 18. Persistent Computer

## 18.1 Ownership

One persistent sandbox computer per user in MVP.

```text
User
  |
  +-- Bot Researcher ----+
  +-- Bot Developer -----+--> same Computer
  +-- Bot Writer --------+
```

## 18.2 Docker representation

```text
computer_id
container_name
workspace_volume
status
image_version
created_at
last_started_at
```

Example:

```text
container: agent-user-{user_id}
volume:    agent-workspace-{user_id}
mount:     /workspace
```

## 18.3 Computer lifecycle

```text
NOT_CREATED
   -> CREATING
   -> RUNNING
   -> STOPPED
   -> RUNNING
   -> RESETTING
   -> RUNNING
```

Deleting a container should not delete the workspace volume unless the user explicitly requests full computer deletion.

## 18.4 Base image

Initial image should contain:

- Debian/Ubuntu slim base;
- bash;
- git;
- Python;
- Node.js;
- curl;
- jq;
- basic build tooling;
- Chromium;
- Playwright dependencies.

---

# 19. Sandbox Security Requirements

The agent must never receive unrestricted access to the host.

## Required controls

- no `--privileged`;
- no host Docker socket mount;
- non-root container user;
- CPU limit;
- memory limit;
- PID limit;
- explicit volume mounts only;
- no host home-directory mount;
- network policy configurable per tool/run;
- execution timeout;
- command output size limit;
- audit every shell execution;
- environment secret allowlist;
- no provider API key placed inside general sandbox environment.

Recommended local Docker settings where supported:

```text
--cpus
--memory
--pids-limit
--security-opt=no-new-privileges
--cap-drop=ALL
```

Investigate Docker rootless mode for development/hosting hardening.

## Network modes

Policy options:

```text
NONE
RESTRICTED
INTERNET
```

MVP can start with `INTERNET` for trusted single-user local development and move to restrictive defaults before hosted multi-user use.

---

# 20. Tool Runtime

Tools are classified into categories.

## 20.1 Local deterministic tools

Examples:

- calculator;
- parse JSON;
- format data.

## 20.2 Sandbox tools

```text
shell_execute
file_read
file_write
file_list
file_delete
process_start
process_stop
```

## 20.3 Browser tools

```text
browser_open
browser_snapshot
browser_click
browser_type
browser_extract
browser_download
```

## 20.4 External tools

- MCP servers;
- GitHub;
- Slack;
- Google tools;
- custom APIs.

## 20.5 Tool contract

Every tool definition must include:

- stable tool name;
- clear description;
- typed input schema;
- typed result schema where practical;
- side-effect classification;
- approval policy;
- timeout;
- retry policy;
- auditability.

---

# 21. Approval and Policy Engine

Approval must be enforced outside the model prompt.

## 21.1 Action classification

```text
READ_ONLY
LOCAL_WRITE
EXTERNAL_WRITE
DESTRUCTIVE
SENSITIVE
```

Default examples:

| Action | Class | MVP Default |
|---|---|---|
| Read workspace file | READ_ONLY | allow |
| Web GET | READ_ONLY | allow |
| Write file in `/workspace` | LOCAL_WRITE | allow |
| Delete workspace tree | DESTRUCTIVE | ask |
| Git commit | LOCAL_WRITE | allow/ask configurable |
| Git push | EXTERNAL_WRITE | ask |
| Send email | EXTERNAL_WRITE | ask |
| Post message | EXTERNAL_WRITE | ask |
| Install package | LOCAL_WRITE | allow/ask configurable |
| Execute privileged host command | forbidden | deny |

## 21.2 Approval flow

```text
Agent proposes tool call
       |
       v
Policy Engine
       |
  +----+----+
  |         |
ALLOW     ASK
  |         |
execute   create Approval
            |
            v
       DBOS workflow waits
            |
     user approves/rejects
            |
            v
       DBOS workflow resumes
```

## 21.3 Approval record

Contains:

- requesting Run;
- Bot;
- tool name;
- sanitized arguments;
- risk reason;
- requested timestamp;
- decision;
- decision timestamp;
- deciding user.

---

# 22. Durable Execution with DBOS

DBOS is responsible for workflow durability rather than Bot identity.

## Required DBOS capabilities

- durable agent Run workflows;
- checkpointed external I/O;
- background execution;
- queueing;
- retries;
- schedule triggers;
- durable waits for approval;
- run recovery after application restart.

PydanticAI runs should use the official DBOS integration where practical.

External side-effecting tools should be wrapped as durable DBOS steps when idempotency and replay safety require it.

## Principle

```text
PostgreSQL application schema = durable PRODUCT state
DBOS system state            = durable EXECUTION state
```

These concerns may use the same PostgreSQL server but should remain logically separated.

---

# 23. Routines

Routine = trigger + Bot task.

## MVP routine fields

```text
id
user_id
bot_id
name
prompt
cron_expression
timezone
enabled
last_run_at
next_run_at
```

## Routine flow

```text
DBOS schedule fires
      |
      v
create Run
      |
      v
Bot executes routine prompt
      |
      v
result written to conversation
```

## Future triggers

- webhook;
- GitHub event;
- Slack event;
- file arrival;
- incoming email;
- Bot handoff.

---

# 24. Skills

A Skill contains reusable task knowledge.

Proposed format:

```text
skills/
  competitor-research/
    SKILL.md
    metadata.yaml
```

Suggested `SKILL.md` content:

```markdown
# Purpose

# When to use

# Required inputs

# Required tools/access

# Workflow

# Decision rules

# Validation

# Expected output

# Approval boundaries
```

Database stores skill metadata and ownership. Skill content can initially live on disk or in PostgreSQL text fields.

MVP may ship with skill CRUD and explicit per-Bot assignment even if progressive dynamic loading is implemented later.

---

# 25. Browser Automation

## MVP

Playwright controls Chromium inside the user's Docker computer.

Initial interface should prefer semantic/browser APIs rather than exposing raw Playwright directly to the model.

Example:

```text
browser_open(url)
browser_get_page_text()
browser_click(target)
browser_type(target, text)
browser_screenshot()
```

## Browser session persistence

Phase 1:

Persist browser profile under a dedicated path in the user's workspace volume.

```text
/workspace/.browser-profile/
```

Security note: browser session files may contain sensitive authentication material and should be treated as secrets.

## Later

Add vision/computer-use actions for workflows that cannot be performed reliably through DOM automation.

---

# 26. Bot-to-Bot Collaboration — Phase 2

Bots remain independent durable entities.

Do **not** implement another Bot as an in-process temporary subagent when persistent identity is required.

## Handoff flow

```text
Bot A
  |
  | message_bot(B, task)
  v
handoff record
  |
  v
queue Run for Bot B
  |
  v
Bot B executes in own context
  |
  v
reply handoff
  |
  v
Bot A/user receives result
```

Handoff statuses:

```text
queued
accepted
running
completed
failed
cancelled
```

---

# 27. Group Conversations — Phase 3

Group conversation contains:

- user;
- 2+ Bots;
- shared group message stream.

Each Bot retains separate long-term memory and private role configuration.

Dispatcher rules may use:

- explicit `@bot` mentions;
- deterministic routing;
- LLM coordinator later.

Avoid one giant prompt pretending to be several persistent Bots.

---

# 28. Proposed PostgreSQL Schema

## users

```text
id UUID PK
email nullable
name nullable
avatar_url nullable
created_at
updated_at
```

## auth_identities

```text
id UUID PK
user_id FK
provider
provider_subject
metadata_json
authenticated_at
UNIQUE(provider, provider_subject)
```

## provider_credentials

```text
id UUID PK
user_id FK
provider          # openrouter
credential_type   # oauth_key/api_key
encrypted_secret
metadata_json
created_at
updated_at
revoked_at nullable
```

## bots

```text
id UUID PK
user_id FK
name
description
instructions
status
model_policy_json
permission_policy_json
computer_id FK
created_at
updated_at
```

## conversations

```text
id UUID PK
user_id FK
bot_id FK nullable
kind              # bot/group/system
title
created_at
updated_at
```

## messages

```text
id UUID PK
conversation_id FK
sender_type
sender_id nullable
message_type
content_json
created_at
```

## runs

```text
id UUID PK
user_id FK
bot_id FK
conversation_id FK
trigger_type
status
dbos_workflow_id nullable
model_slug nullable
started_at nullable
completed_at nullable
error_json nullable
created_at
```

## tool_calls

```text
id UUID PK
run_id FK
tool_name
arguments_json
risk_class
status
result_json nullable
started_at nullable
completed_at nullable
```

## memories

```text
id UUID PK
user_id FK
bot_id FK nullable
scope
kind
content
metadata_json
confidence nullable
expires_at nullable
created_at
updated_at
```

## skills

```text
id UUID PK
user_id FK nullable
name
description
content
version
enabled
created_at
updated_at
```

## bot_skills

```text
bot_id FK
skill_id FK
config_json
PRIMARY KEY(bot_id, skill_id)
```

## routines

```text
id UUID PK
user_id FK
bot_id FK
name
prompt
cron_expression
timezone
enabled
created_at
updated_at
```

## approvals

```text
id UUID PK
user_id FK
run_id FK
tool_call_id FK nullable
status
reason
request_payload_json
decision_payload_json nullable
requested_at
decided_at nullable
```

## computers

```text
id UUID PK
user_id FK UNIQUE
runtime_type       # docker
runtime_id nullable
workspace_volume
status
image_version
created_at
updated_at
last_started_at nullable
```

## handoffs — Phase 2

```text
id UUID PK
user_id FK
source_bot_id FK
target_bot_id FK
source_run_id FK nullable
target_run_id FK nullable
payload_json
status
created_at
updated_at
```

---

# 29. API Surface

Base prefix:

```text
/api/v1
```

## Auth

```text
GET    /auth/me
POST   /auth/logout
GET    /auth/chatgpt/start          # when available
GET    /auth/chatgpt/callback       # when available
```

## Model providers

```text
GET    /providers
GET    /providers/openrouter/status
POST   /providers/openrouter/oauth/start
GET    /providers/openrouter/oauth/callback
DELETE /providers/openrouter
GET    /models
```

## Bots

```text
GET    /bots
POST   /bots
GET    /bots/{bot_id}
PATCH  /bots/{bot_id}
DELETE /bots/{bot_id}
POST   /bots/{bot_id}/archive
POST   /bots/{bot_id}/restore
```

## Conversations

```text
GET    /bots/{bot_id}/conversations
POST   /bots/{bot_id}/conversations
GET    /conversations/{conversation_id}
GET    /conversations/{conversation_id}/messages
POST   /conversations/{conversation_id}/messages
```

## Runs

```text
GET    /runs/{run_id}
POST   /runs/{run_id}/cancel
GET    /runs/{run_id}/events
```

Use SSE for run output/event streaming initially.

## Approvals

```text
GET    /approvals?status=pending
GET    /approvals/{approval_id}
POST   /approvals/{approval_id}/approve
POST   /approvals/{approval_id}/reject
```

## Computer

```text
GET    /computer
POST   /computer/start
POST   /computer/stop
POST   /computer/reset
GET    /computer/files
```

## Skills

```text
GET    /skills
POST   /skills
GET    /skills/{skill_id}
PATCH  /skills/{skill_id}
DELETE /skills/{skill_id}
POST   /bots/{bot_id}/skills/{skill_id}
DELETE /bots/{bot_id}/skills/{skill_id}
```

## Routines

```text
GET    /routines
POST   /routines
PATCH  /routines/{routine_id}
DELETE /routines/{routine_id}
POST   /routines/{routine_id}/run-now
POST   /routines/{routine_id}/pause
POST   /routines/{routine_id}/resume
```

---

# 30. Realtime Event Model

SSE event examples:

```text
run.started
assistant.delta
assistant.message
model.request.started
model.request.completed
tool.proposed
tool.started
tool.output
tool.completed
approval.requested
approval.resolved
run.waiting
run.completed
run.failed
```

Every event should include:

```text
event_id
run_id
timestamp
type
payload
```

Persist important state-changing events; ephemeral token deltas do not necessarily need database storage.

---

# 31. Agent Runtime Design

A Bot Runtime is assembled for every Run.

```python
BotRuntimeContext(
    user=...,
    bot=...,
    conversation=...,
    memories=...,
    skills=...,
    computer=...,
    permissions=...,
    model_policy=...,
)
```

The runtime creates/configures a PydanticAI `Agent` dynamically.

Important: Bot configuration is the source of truth; do not serialize Python agent instances.

---

# 32. Context Management

Avoid sending all historical data to every model call.

Initial context strategy:

```text
1. Bot core instructions
2. policy/safety instructions
3. relevant Bot memory
4. active skill content
5. recent message window
6. current task
7. selected artifact/file references
```

Later improvements:

- conversation summarization;
- semantic memory retrieval;
- automatic context budgeting;
- tool-result compaction;
- artifact references rather than full content.

---

# 33. File and Artifact Strategy

Persistent working artifacts live in:

```text
/workspace
```

Suggested structure:

```text
/workspace/
  projects/
  research/
  reports/
  downloads/
  artifacts/
  tmp/
```

Application DB stores artifact metadata, not necessarily file bytes.

Future object-storage interface:

```text
ArtifactStore
├── LocalWorkspaceArtifactStore
└── S3CompatibleArtifactStore
```

---

# 34. Repository Layout

Recommended monorepo:

```text
project/
├── apps/
│   ├── api/
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── api/
│   │   │   ├── auth/
│   │   │   └── settings.py
│   │   └── tests/
│   │
│   ├── worker/
│   │   └── worker.py
│   │
│   └── web/
│       └── Next.js application
│
├── packages/
│   ├── agents/
│   │   ├── factory.py
│   │   ├── runtime.py
│   │   ├── models.py
│   │   └── prompts.py
│   │
│   ├── workflows/
│   │   ├── runs.py
│   │   ├── approvals.py
│   │   ├── routines.py
│   │   └── dbos_config.py
│   │
│   ├── sandbox/
│   │   ├── manager.py
│   │   ├── docker_runtime.py
│   │   ├── policies.py
│   │   └── tools.py
│   │
│   ├── browser/
│   │   ├── playwright_runtime.py
│   │   └── tools.py
│   │
│   ├── memory/
│   │   ├── service.py
│   │   └── retrieval.py
│   │
│   ├── skills/
│   │   ├── loader.py
│   │   └── registry.py
│   │
│   ├── models_gateway/
│   │   ├── openrouter.py
│   │   └── credentials.py
│   │
│   ├── policy/
│   │   ├── engine.py
│   │   └── risk.py
│   │
│   └── db/
│       ├── models/
│       ├── repositories/
│       └── migrations/
│
├── sandbox-image/
│   ├── Dockerfile
│   └── entrypoint.sh
│
├── skills/
│   └── builtins/
│
├── docker-compose.yml
├── .env.example
├── pyproject.toml
└── PRD.md
```

---

# 35. Local Development Environment

Primary command:

```bash
docker compose up --build
```

Core Compose services:

```text
postgres
api
worker
web
```

User sandbox containers are created dynamically by the Sandbox Manager and are not necessarily static Compose services.

## Environment variables

```text
APP_ENV=development
DATABASE_URL=postgresql://...
DBOS_SYSTEM_DATABASE_URL=postgresql://...

DEV_AUTH=true
DEV_USER_EMAIL=local@example.test

OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
DEFAULT_MODEL=openrouter/free

SANDBOX_IMAGE=agent-sandbox:dev
SANDBOX_CPU_LIMIT=1
SANDBOX_MEMORY_LIMIT=1g
SANDBOX_PIDS_LIMIT=256
```

---

# 36. MVP User Experience

## First run

```text
1. User opens app.
2. Local dev auth logs user in.
3. User connects OpenRouter or uses development key.
4. User creates Bot.
5. Bot gets shared user computer automatically.
6. User opens conversation.
7. User asks Bot to inspect/create files.
8. Bot invokes tools inside Docker sandbox.
9. UI streams progress.
10. Bot returns final answer and artifact references.
```

## Approval example

User:

> Prepare the repository and push the branch when ready.

Bot:

```text
clone/read repository
modify code
run tests
commit locally
propose git push
```

UI displays:

```text
Approval required
Action: git push origin feature/foo
Reason: external write
[Approve] [Reject]
```

DBOS workflow remains durably waiting.

After approval:

```text
resume workflow
perform push
return result
```

---

# 37. Functional Requirements

## FR-001 Bot CRUD

User can create, list, inspect, edit, archive, restore, and delete Bots.

## FR-002 Persistent conversation

Conversation survives server restart.

## FR-003 Durable Run

A Run in progress before backend restart resumes or reaches a deterministic recoverable state after restart.

## FR-004 OpenRouter model execution

A Bot can call a selected OpenRouter model and receive streaming output.

## FR-005 User-owned OpenRouter credential

User can connect and revoke OpenRouter authorization.

## FR-006 Sandbox execution

Bot can execute approved shell/file operations inside the user sandbox but not on the host.

## FR-007 Persistent workspace

Files created under `/workspace` survive sandbox container recreation.

## FR-008 Browser automation

Bot can navigate pages through controlled Playwright tools.

## FR-009 Human approval

Protected tool calls pause before side effects and can resume after explicit user decision.

## FR-010 Memory

Bot can store and retrieve long-term memory independently from raw conversation messages.

## FR-011 Skills

User can attach reusable Skills to a Bot.

## FR-012 Routines

User can schedule a recurring Bot task using a cron expression and timezone.

## FR-013 Observability

User/developer can inspect Run status and tool history.

## FR-014 Cancellation

User can cancel queued/running/waiting Runs.

---

# 38. Non-Functional Requirements

## Reliability

- No completed durable step should be unnecessarily repeated after workflow recovery.
- Every Run has an inspectable terminal state.
- Workflow errors are persisted.

## Security

- Provider keys encrypted at rest.
- Provider keys never exposed to sandbox by default.
- Sandbox cannot access host Docker socket.
- All protected actions audited.

## Performance

For local MVP:

- API non-model request p95 target < 500 ms on development hardware.
- First streamed model event target primarily depends on model/provider latency.
- UI should display a Run state immediately after submission.

## Maintainability

- Framework-specific code isolated behind runtime interfaces.
- OpenRouter-specific code isolated behind model-gateway interface.
- Docker-specific code isolated behind sandbox interface.

---

# 39. Security Threat Model — MVP

High-priority threats:

## Prompt injection

External web/file content may instruct the Bot to perform unsafe actions.

Mitigation:

- external content never overrides platform policy;
- protected actions require policy evaluation;
- dangerous external writes require approval.

## Secret leakage

Bot could read credentials and transmit them.

Mitigation:

- do not inject provider keys into sandbox;
- secrets exposed only through scoped tools;
- redact secrets in logs/tool responses.

## Container escape

Mitigation:

- non-root container;
- minimal capabilities;
- resource limits;
- no privileged mode;
- no host socket;
- hosted multi-user release requires stronger isolation review.

## Command injection

Shell tool is intentionally powerful.

Mitigation:

- run only inside sandbox;
- audit commands;
- timeouts/resource limits;
- protected command categories can trigger approvals.

## OAuth/token theft

Mitigation:

- PKCE;
- encrypted credential storage;
- secure/same-site cookies;
- no provider secret returned after storage;
- CSRF/state validation.

---

# 40. Observability

MVP structured logs should include:

```text
request_id
user_id
bot_id
conversation_id
run_id
workflow_id
tool_call_id
approval_id
model_slug
latency_ms
status
```

Do not log:

- raw provider credentials;
- session cookies;
- secret environment values;
- full sensitive tool payloads by default.

Future:

- OpenTelemetry;
- Pydantic Logfire;
- Grafana/Prometheus;
- centralized traces.

---

# 41. Testing Strategy

## Unit tests

- policy classification;
- memory CRUD;
- Bot runtime assembly;
- OpenRouter model configuration;
- credential encryption/decryption;
- tool schemas;
- sandbox command validation.

## Integration tests

- FastAPI + PostgreSQL;
- DBOS workflow recovery;
- approval pause/resume;
- Docker workspace persistence;
- Playwright browser invocation;
- OpenRouter mock server.

## End-to-end tests

1. Create Bot.
2. Send task.
3. Execute tool.
4. Persist file.
5. Restart API/worker.
6. Confirm workflow recovery.
7. Trigger approval.
8. Resolve approval.
9. Verify completed result.

## Security tests

- sandbox cannot access Docker socket;
- sandbox cannot mount arbitrary host paths;
- provider credential is unavailable inside shell;
- unauthorized user cannot access another user's Bot/computer.

---

# 42. Development Milestones

## Milestone 0 — Repository/Foundation

Deliverables:

- monorepo;
- FastAPI;
- Next.js;
- PostgreSQL;
- migrations;
- Docker Compose;
- local dev auth;
- health endpoints.

Exit criteria:

```text
docker compose up
```

boots complete local development environment.

## Milestone 1 — Single Persistent Bot

Deliverables:

- Bot CRUD;
- conversations;
- messages;
- PydanticAI;
- OpenRouter;
- SSE streaming;
- Run records.

Exit criterion:

User can create one Bot and maintain conversation across restarts.

## Milestone 2 — Persistent Computer

Deliverables:

- SandboxManager;
- per-user Docker container;
- Docker named volume;
- file tools;
- shell tool;
- resource limits.

Exit criterion:

Bot creates a file, computer container is recreated, file remains available.

## Milestone 3 — Durable Execution

Deliverables:

- DBOS setup;
- DBOSAgent/PydanticAI integration;
- background Run workflow;
- retries;
- restart recovery;
- cancellation.

Exit criterion:

Kill worker during an active workflow and confirm execution resumes correctly after restart.

## Milestone 4 — Approvals

Deliverables:

- policy engine;
- approval records;
- wait/resume workflow;
- approval UI.

Exit criterion:

External-write tool cannot execute until user approves it.

## Milestone 5 — Memory and Skills

Deliverables:

- memory tools;
- memory UI;
- Skills CRUD;
- assign skills to Bot;
- skill injection into runtime.

Exit criterion:

Bot recalls stored preference in a new conversation and follows assigned Skill.

## Milestone 6 — Browser

Deliverables:

- Chromium in sandbox;
- Playwright service/tool wrapper;
- browser profile persistence;
- browser events/screenshots.

Exit criterion:

Bot can navigate a test site, extract data, and save an artifact.

## Milestone 7 — Routines

Deliverables:

- routine CRUD;
- cron scheduling;
- timezone support;
- run-now;
- routine run history.

Exit criterion:

Scheduled Bot task executes without an active browser client.

## Milestone 8 — Collaboration

Deliverables:

- direct Bot-to-Bot handoff;
- async recipient Run;
- handoff status;
- replies.

Exit criterion:

Research Bot can asynchronously assign a task to Writer Bot and receive/store the result.

---

# 43. MVP Definition of Done

MVP is complete when a developer can:

1. clone the project;
2. run `docker compose up`;
3. log in through development auth;
4. configure or connect OpenRouter;
5. create a persistent Bot;
6. chat with it;
7. use an OpenRouter model;
8. execute commands inside a restricted Docker sandbox;
9. create files that persist across sandbox restart;
10. browse the web using Playwright;
11. run an agent task through DBOS;
12. restart the backend during an unfinished workflow and recover it;
13. receive an approval request for a protected action;
14. approve/reject it;
15. store/retrieve Bot memory;
16. assign a Skill;
17. create a recurring Routine;
18. inspect previous Runs and tool activity.

---

# 44. Product Success Metrics

Development-phase metrics:

- successful Run completion rate;
- Run recovery success rate after forced worker restart;
- tool success/failure rate;
- approval completion rate;
- average tool calls per task;
- average model requests per Run;
- average model cost per Run when paid models are used;
- percentage of tasks completed using free models during development;
- sandbox startup latency;
- routine execution reliability;
- memory retrieval usefulness in eval set.

---

# 45. Risks and Mitigations

## Risk: Free OpenRouter models change frequently

Mitigation:

- model-capability checks;
- configurable primary/fallback model;
- never depend on a hard-coded free model.

## Risk: Tool calling differs across OpenRouter models

Mitigation:

- maintain capability metadata;
- restrict agentic runs to models with required features;
- automated compatibility tests.

## Risk: ChatGPT sign-in not generally available to this project

Mitigation:

- `AuthProvider` abstraction;
- local dev auth;
- keep model auth independent;
- add another OIDC provider if production launch needs it.

## Risk: Docker is insufficient isolation for hostile multi-user hosting

Mitigation:

- single-user/local scope for MVP;
- explicit security boundary in PRD;
- evaluate gVisor/Firecracker/managed sandboxes before public hosting.

## Risk: Long agent loops become expensive

Mitigation:

- max model-call count per Run;
- token/cost budgets;
- timeout;
- tool-call budget;
- user-visible cancellation.

## Risk: Agent performs unintended external action

Mitigation:

- external writes default to approval;
- independent policy engine;
- audit trail;
- least-privilege connectors.

## Risk: Workflow replay duplicates side effects

Mitigation:

- use DBOS steps correctly;
- idempotency keys for external mutations;
- persist external operation IDs;
- distinguish read operations from mutating operations.

---

# 46. Future Architecture Migration Paths

## DBOS -> Temporal

If workflow scale or orchestration requirements outgrow DBOS, preserve a `WorkflowRuntime` interface so durable Run semantics can migrate to Temporal without changing Bot domain concepts.

## Docker -> Managed Sandbox

Preserve:

```text
SandboxRuntime
create()
start()
stop()
exec()
read_file()
write_file()
```

Potential future implementations:

- Daytona;
- Modal;
- Firecracker-based service;
- gVisor sandbox;
- Kubernetes sandbox workers.

## PostgreSQL memory -> semantic retrieval

Add pgvector before introducing a separate vector database unless requirements prove otherwise.

## OpenRouter -> additional gateways

Preserve `ModelGateway` abstraction for direct providers or another routing layer.

---

# 47. Architecture Interfaces to Keep Stable

```text
AuthProvider
ModelGateway
CredentialStore
BotRepository
ConversationRepository
MemoryStore
WorkflowRuntime
SandboxRuntime
BrowserRuntime
ToolRegistry
PolicyEngine
SkillRegistry
RoutineScheduler
ArtifactStore
```

The system should depend on these contracts rather than framework-specific APIs in business logic.

---

# 48. Key Engineering Decisions

## Decision 1

**Use PydanticAI rather than Deep Agents for v0.**

Reason: keep agent reasoning/tool layer relatively lightweight while product durability and lifecycle remain owned by application infrastructure.

## Decision 2

**Use DBOS instead of Temporal initially.**

Reason: durable execution with significantly less operational overhead for a Python/PostgreSQL local-first MVP. Temporal remains a future migration option.

## Decision 3

**Use Docker instead of Daytona initially.**

Reason: zero-cost local development and complete control over the sandbox contract. Daytona can later implement the same `SandboxRuntime` interface.

## Decision 4

**Use OpenRouter as the model gateway.**

Reason: one provider interface for multiple model vendors and a usable free-routing path for development.

## Decision 5

**Separate product identity from model authorization.**

Reason: ChatGPT identity, ChatGPT subscription billing, OpenAI API billing, and OpenRouter billing are different concerns.

## Decision 6

**One persistent computer per user initially.**

Reason: matches the intended Grok Bot-like shared workspace mental model while simplifying collaboration and durable artifacts.

---

# 49. Reference Documentation

## Grok Bot product behavior reference

- https://docs.x.ai/grok-bot/overview
- https://docs.x.ai/grok-bot/bots
- https://docs.x.ai/grok-bot/computer-and-apps
- https://docs.x.ai/grok-bot/computers
- https://docs.x.ai/grok-bot/chat-and-collaboration
- https://docs.x.ai/grok-bot/skills-routines-and-automations
- https://docs.x.ai/grok-bot/approvals-security-and-privacy
- https://docs.x.ai/grok-bot/security-faq
- https://docs.x.ai/grok-bot/files-and-results
- https://docs.x.ai/grok-bot/teams-and-enterprises

## PydanticAI

- https://ai.pydantic.dev/
- https://ai.pydantic.dev/durable_execution/
- https://ai.pydantic.dev/durable_execution/dbos/

## DBOS

- https://docs.dbos.dev/
- https://docs.dbos.dev/integrations/pydantic-ai
- https://docs.dbos.dev/python/tutorials/workflow-tutorial
- https://docs.dbos.dev/ai/ai-quickstart

## OpenRouter

- https://openrouter.ai/docs/quickstart
- https://openrouter.ai/docs/guides/overview/auth/oauth
- https://openrouter.ai/docs/api/api-reference/models/get-models
- https://openrouter.ai/developers

## OpenAI / ChatGPT identity and billing constraints

- https://help.openai.com/en/articles/20001410-sign-in-with-chatgpt
- https://help.openai.com/en/articles/9039756-managing-billing-for-chatgpt-and-the-api-platform

## Docker sandbox hardening

- https://docs.docker.com/engine/security/rootless/
- https://docs.docker.com/engine/containers/resource_constraints/

---

# 50. Final MVP Architecture Snapshot

```text
                         USER
                          |
                  Next.js Web App
                          |
             +------------+------------+
             |                         |
     product authentication        OpenRouter OAuth
     ChatGPT when eligible         / user API key
             |                         |
             +------------+------------+
                          |
                       FastAPI
                          |
       +------------------+-------------------+
       |                  |                   |
       v                  v                   v
   PostgreSQL           DBOS             PydanticAI
 product state      durable runtime       Bot brain
                          |                   |
                          +---------+---------+
                                    |
                         Policy / Tool Runtime
                                    |
                    +---------------+----------------+
                    |                                |
                    v                                v
            Docker Sandbox                    MCP / APIs
             per user
                    |
          +---------+----------+
          |         |          |
          v         v          v
       shell    /workspace   Chromium
                              Playwright
```

The intended product behavior is:

```text
Bot = durable teammate identity
Run = temporary execution
DBOS = durable lifecycle of execution
PydanticAI = reasoning/tool loop
PostgreSQL = durable product state
Docker = persistent user computer
OpenRouter = model gateway
ChatGPT Sign-In = identity only when available
OpenRouter OAuth = model account authorization
Skills = how work is performed
Routines = when work runs
Approvals = permission boundary
```

This separation should remain the foundation of the project even as individual infrastructure components are replaced later.
