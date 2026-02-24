# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Agent Platform — a development environment where an AI agent executes code in isolated Docker sandbox containers. Users chat with the agent via WebSocket; the agent plans, writes files, runs commands, and controls a browser inside the sandbox, streaming every step back in real time.

The project is written with Russian-language comments and documentation. Commits use English (conventional commits).

## Commands

### Full stack (Docker Compose)

```bash
docker-compose up --build
```

### Local development

Backend (Python 3.12, FastAPI):

```bash
cd backend
pip install -r requirements.txt
python main.py
```

Frontend (Node 20, React + Vite):

```bash
cd frontend
npm install
npm run dev
```

### Build sandbox image

```bash
cd docker/sandbox
docker build -t ai-agent-sandbox:latest .
```

### Tests (integration, require running backend)

```bash
python test_api.py    # API endpoint tests (health, auth, project CRUD, agent tasks)
python test_site.py   # End-to-end: agent creates a website, inspects file tree
```

## Architecture

### Request Flow

```
User → WebSocket /ws/chat/{project_id}
     → main.py (ConnectionManager)
     → AgentEngine.run()
     → classify_task() → select prompt & LLM provider
     → LLM call with tools
     → ToolExecutor → DockerManager → sandbox container
     → results streamed back via on_step callbacks → WebSocket → UI
```

### Backend (`backend/`)

**main.py** — FastAPI entry point. Two communication layers:

- REST: project CRUD, file operations, auth, LLM status
- WebSocket: `/ws/chat/{project_id}` (agent interaction), `/ws/terminal/{project_id}` (shell)

Active agent engines are stored in an `agent_engines` dict keyed by project_id. A background task cleans up idle sandboxes every 5 minutes (1-hour TTL).

**agents/engine.py** — Core agent loop (up to 50 iterations per task). Key behaviors:

- Rule-based task classifier selects prompt config and LLM routing (simple_chat, coding, browser, vision, etc.)
- Context compression: keeps last 14 messages in full, summarizes older ones, hard cap at 48K tokens
- Parallel tool execution for independent operations
- Adaptive escalation: detects struggle (3+ consecutive errors, loops, stalling) and auto-upgrades model (deepseek → openai → claude)
- JSON repair for truncated tool arguments
- Message validation removes orphan tool messages that would break the API
- Reads `.agent_plan.md` from project dir and injects into system prompt

**agents/tools/definitions.py** — 15 tools in OpenAI function-calling format: file ops (write_file, write_files, read_file, list_files), execute_command, and browser automation (navigate, click, type, fill_form, screenshot, get_page_structure, console_logs, network_failures, execute_script).

**agents/tools/executor.py** — Dispatches via `_tool_{name}` method convention. Caches read_file/list_files results (5s TTL). Truncates outputs per tool (e.g., 5000 chars stdout, 2000 stderr for execute_command).

**docker_manager/manager.py** — Manages sandbox containers. One container per project, mounted at /workspace. Containers get 8GB RAM, 8 CPU cores, exposed ports for noVNC (6080) and dev servers (3000, 5173, 8080, 5000, 4000, 8000). All Docker SDK calls are wrapped with `asyncio.to_thread()` to avoid blocking the event loop.

**llm/router.py** — Multi-model routing via OpenAI-compatible API:

- DeepSeek (default, cheapest) → Qwen → OpenAI → Claude (strongest, vision)
- Fallback chain on provider failure
- CostTracker logs per-request token usage and cost
- Auto-strips images for non-vision models

**config.py** — Pydantic Settings from `.env`. Key flags: `AGENT_USE_STREAMING`, `AGENT_ENHANCED_CONTEXT`, `AGENT_REDUCED_MAX_TOKENS` (1536 vs 2048).

**models.py** — SQLAlchemy async models: User (UUID PK, email, username) and Project (UUID PK, name, owner_id FK, container_id, status).

**auth.py** — JWT (HS256) + bcrypt. Tokens expire in 7 days.

### Frontend (`frontend/src/`)

**App.jsx** — Three-screen flow: Auth → Project Creation → Workspace. Workspace is a resizable 3-panel layout (Chat | Files+Activity | Editor+Terminal+Preview). Panel widths persist in localStorage.

**stores/useStore.js** — Single Zustand store with all state: auth (user, token), project (projectId, ports, status), messages, agentSteps, agentStatus (idle|thinking|working|done|error), WebSocket reference. All API calls (auth, project CRUD, sendMessage) are store actions.

**hooks/useWebSocket.js** — Auto-reconnect with exponential backoff. 45-second heartbeat watchdog. Handles message types: connected, agent_step, agent_stream_chunk, agent_response, agent_stopped, ports_update, error, heartbeat.

**Components pattern**: AgentActivity and ProcessDialog both render from the same `agentSteps` array with different filters (tool_call/tool_result vs llm_text/response). Chat supports multi-modal input (text + images via Ctrl+V + file attachments). Terminal is lazy-loaded.

### Sandbox (`docker/sandbox/`)

Ubuntu 22.04 container with Python 3, Node.js 20, Playwright + Chromium. Runs Xvfb + Fluxbox + X11VNC + noVNC via supervisord for browser automation with GUI access.

## Ports

| Port | Service           |
| ---- | ----------------- |
| 5173 | Frontend (Vite)   |
| 8000 | Backend (FastAPI) |
| 6379 | Redis             |
| 5432 | PostgreSQL        |

## Environment Variables

Copy `.env.example` to `.env`. At minimum, set one LLM API key (DEEPSEEK_API_KEY recommended). Other providers: QWEN_API_KEY, CLAUDE_API_KEY, OPENAI_API_KEY.

## Conventions

- Backend: async/await throughout, type hints, Pydantic for validation
- Frontend: functional components, Zustand for state (no prop drilling), Tailwind for styles
- All user code execution happens only inside sandbox containers — never on the host
- LLM tool schemas follow OpenAI function-calling format
- New tools: add schema to `definitions.py`, add `_tool_{name}` method to `executor.py`

## Дополнительная инструкция

- Всегда отвечай на русском языке
