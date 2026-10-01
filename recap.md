# Work recap

## 2026-09-29 — Initial MVP foundation

- Created a Python 3.12 FastAPI backend with a local SQLite product-state store.
- Implemented persistent Bot profiles, default Bot conversations, append-only messages, run records, and run events.
- Added the Run state model and a local asynchronous runtime with bounded single-model-call execution.
- Added an OpenRouter gateway boundary. It reads its API key only from the process environment and fails clearly when no key is configured.
- Added an independent policy engine and durable approval records for external, destructive, and sensitive action classes.
- Added Bot memory storage and read endpoints.
- Added Docker Compose, a base sandbox image, and an environment template.

## Current scope and next implementation slice

This is a runnable backend foundation. DBOS, PydanticAI tool calling, Docker sandbox orchestration, browser automation, skills, routines, authentication, SSE, and a Next.js interface are not implemented yet. The source boundaries were added so those pieces can be introduced without moving Bot, Run, policy, and model-gateway ownership.

## Validation

- `python3 -m compileall -q apps packages` completed successfully.
- Importing `apps.api.app.main:app` succeeded and reported `Persistent Agent Workspace 0.1.0`.

## 2026-09-29 — Runtime completion work

- Checked the PRD stack against the repository and found the initial backend did not yet include its requested runtime and infrastructure dependencies.
- Added project dependency declarations for DBOS, PydanticAI, SQLAlchemy, Alembic, PostgreSQL driver, and Playwright.
- Added PostgreSQL to Compose for the intended container topology, while preserving the local SQLite product store until its SQLAlchemy migration is completed.
- Added focused lifecycle tests for run completion and durable approval decisions.
- Replaced the raw OpenRouter HTTP completion path with PydanticAI's OpenAI-compatible provider boundary.
- Installed and exercised the backend dependencies in `.venv`; browser binary installation remains a separate, intentionally explicit step.
- Ran `pytest -q`: 2 passed.
- Started Uvicorn on `127.0.0.1:8011`; `GET /health` returned `200` and a Bot creation request returned `201` during the first smoke pass.

## 2026-09-29 — Frontend direction

- Recorded owner-provided design direction in `DESIGN.md`: calm and beginner-friendly workspace.
- Frontend data and actions use `NEXT_PUBLIC_API_BASE_URL`; no Bot records or status values are hardcoded into the UI.

## 2026-09-29 — Installation and run instructions

- Installed the backend package editable in `.venv` and restored the frontend npm dependency tree after clearing a corrupt npm cache entry.
- Expanded `README.md` with separate backend, frontend, Docker Compose, and verification commands.
- At that stage, `next build` exited with `Bus error` in both Turbopack and Webpack modes; the resolution is recorded below.

## 2026-09-29 — Dashboard rendering fix

- Confirmed the blank page came from the installed Next.js 16 SWC native compiler crashing before route rendering.
- Pinned Next.js to `15.5.26`, refreshed `package-lock.json`, and verified the SWC module loads successfully.
- `npm run build` completed successfully; only a CSS `align-items: start` Autoprefixer warning remains.
- Started the production frontend at `http://127.0.0.1:3000`; browser inspection shows the workspace, empty Bot state, and create-Bot form with no browser console errors.
- Confirmed backend `GET http://127.0.0.1:8000/health` returns HTTP 200.
- Added production build/start commands and the Next.js version note to `README.md`.

## 2026-09-29 — Bot conversation flow

- Root cause: dashboard only created/listed Bots; it had no conversation UI. The API message handler also called `asyncio.create_task()` from a synchronous FastAPI endpoint, causing HTTP 500 (`RuntimeError: no running event loop`).
- Added a Bot conversation panel using the existing message and run endpoints, with run status, response history, and visible errors.
- Made message submission and approval decisions async endpoints so run tasks start on FastAPI's event loop.
- Loaded `.env` for Uvicorn via `--env-file .env`; confirmed the API worker has the configured model key without printing it.
- Bot description now reaches the system prompt, and the last ten conversation turns are included for follow-up requests.
- Sent `Jawab persis satu kata: SIAP.` through the dashboard; Arthur returned `SIAP` and the exchange appeared in saved conversation history.
- `npm run build` succeeded; `.venv/bin/python -m pytest -q` reported 2 passed.
- Updated `README.md` with the backend env-file flag and simple Bot usage steps.

## 2026-09-29 — OpenRouter environment loading

- Confirmed `.env` contains a non-empty `OPENROUTER_API_KEY` without printing or exposing its value.
- Added automatic loading of the project-root `.env` in `Settings.from_environment()` so startup does not depend on the shell working directory; existing process environment variables keep precedence.
- Declared `python-dotenv` directly and clarified both automatic loading and Uvicorn's `--env-file` in `README.md`.
- Checked config loading from `/tmp` with the key removed from the inherited environment: project `.env` supplied it successfully. `pytest -q`: 2 passed.
- Left frontend and backend stopped as requested.

## 2026-09-29 — Team workspace, skills, and coordination

- Reframed the workspace from single-Bot chat into an organization model: private Bot conversations, scoped skills, owned jobs, group rooms, and durable Bot-to-Bot handoffs.
- Added persisted `skills`, `bot_skills`, `jobs`, `work_groups`, `group_members`, `group_messages`, `handoffs`, `tool_calls`, and group-run link tables to the local database.
- Added default skills: `workspace_admin`, `job_manager`, and `coordination`. A model receives only the tools enabled through that Bot's assigned skills; calls are saved in `tool_calls` and Run events.
- Added tool implementations for creating/updating/archiving Bots, creating/updating jobs, handing off work to another Bot, and posting group updates. Handoffs create a separate persisted Run for the receiving Bot and return the result to the source Bot's conversation.
- Added APIs and dashboard controls for skill assignment, job creation, group creation, private chat, and group chat. Group messages enqueue individual Bot Runs and persist Bot replies back into the group.
- Permanent deletion is intentionally not automated. The management skill archives a Bot so history remains recoverable.
- Validation: `.venv/bin/python -m pytest -q` reports 4 passed; `npm run build` completed successfully after moving aside a stale generated `.next` cache.
