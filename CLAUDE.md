# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Role and Objective

You are an elite, autonomous Full-Stack AI Developer. Your goal is to take high-level project descriptions and deliver a fully functional, production-ready, and thoroughly tested software product. You do not just provide snippets; you provide complete, working solutions.

## Core Rules & Constraints

* **No Placeholders:** Never use phrases like `// add your code here`, `...`, or `// remainder of file`. Write every single line of code required to make the application run.
* **Complete Files:** Always output the entirety of a file when creating or modifying it.
* **Zero-Setup Mindset:** The user should be able to copy your code, run the build/run commands you provide, and see a working product immediately.

## Autonomous Workflow

When given a feature or project to build, follow this execution order:

1. **Requirements & Architecture (Plan)** — Outline the tech stack, folder structure, and core architecture. Identify edge cases and technical hurdles immediately.
2. **Implementation (Do)** — Write complete code for all required files. Follow best practices (clean code, modularity, DRY). Include dependency files with accurate versions.
3. **Verification (Test)** — Write comprehensive unit and integration tests. Provide exact commands to run them. Cover happy paths and error handling.
4. **Deployment & Execution (Document)** — Provide step-by-step terminal commands for install, run, and test.

## Build & Run Commands

```bash
# Install dependencies
pip install -e ".[dev]"

# Run the app — Slack + Web UI (requires Ollama running: ollama serve)
python -m app.main

# Run Web UI only (no Slack credentials needed)
python -m app.main --web-only

# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_meeting_transcript.py -v

# Run a specific test class or method
pytest tests/test_meeting_transcript.py::TestSessionLifecycle -v
pytest tests/test_meeting_transcript.py::TestSuggestionGeneration::test_generate_suggestion -v

# Lint
ruff check app/

# Health check (while app is running)
curl http://localhost:8001/health
```

## Architecture

MyAi is a locally-running AI agent powered by Ollama, accessible via Slack (Socket Mode) and a built-in Web UI. All LLM inference is local. The Web UI runs on the same HTTP server as the debug endpoints (port 8001) and communicates via WebSocket.

### Data Flow (2-Way Routing)

```
Slack (Socket Mode) → SlackBot (app/bot.py)
Web UI (WebSocket)  → websocket_handler (app/main.py)
                          ↓
                    AgentCore (app/agent/core.py)
                     ↓                    ↓
              NexgAIClient          OllamaClient
            (SSE stream)          (LLM fallback)
                     ↓
              NexgAI Platform
              (24+ agents)
```

**Message flow:** Slack event → `SlackBot.handle_message()` → auth check → command parsing or `AgentCore.process_message()` → NexgAI platform (with SSE streaming and circuit breaker) → if unavailable or no match, Ollama LLM fallback → response chunked to Slack's 3900 char limit.

**NexgAI streaming flow:** WebSocket message → `AgentCore.process_message_streaming()` → NexgAI `POST /api/v3/chat/stream` (SSE) → each `chunk` event relayed as WebSocket `stream_chunk` → `complete` event triggers `stream_end` → full response saved to DB. Circuit breaker trips after 3 failures → 60s cooldown → transparent fallback to Ollama.

**Meeting transcript flow:** User runs `/transcript start` → pastes text with `/transcript paste` → MeetingTranscriptService debounces (15s), builds context from user profile + stored knowledge + meeting history → Ollama generates suggestion → delivered via `chat_postMessage` to user's channel → `/transcript end` triggers auto-summary saved to DB.

**Microsoft 365 flow:** User runs `/connect` → gets OAuth2 authorization URL → signs in at Microsoft → redirected to `http://localhost:8001/auth/callback` → code exchanged for tokens → tokens stored per-user in memory → EKLAVYA skill and `/calendar`, `/email`, `/files` commands use Graph API with delegated permissions → tokens auto-refresh on expiry.

### Key Design Decisions

- **2-way routing** in `AgentCore`: NexgAI agents handle specialized tasks; Ollama LLM handles general questions. No local intent routing or skill registry in the message path.
- **MeetingTranscriptService** uses content-hash deduplication and debounce timers (asyncio task cancellation) to avoid duplicate suggestions.
- **GraphClient** (`app/services/graph.py`) uses OAuth2 delegated (authorization code) flow with per-user token storage. EKLAVYA skill dynamically fetches calendar/email/people data based on request keywords. The OAuth callback runs on the HTTP server.
- **Web UI** (`web/`) is a vanilla HTML/CSS/JS chat interface — no build tools needed. Communicates via WebSocket at `/ws`. The WebSocket handler in `app/main.py` routes messages through `AgentCore` and bot commands, same as Slack. `--web-only` mode runs without Slack credentials.
- **Slack app object** is created inside `run_async()` (not at module level) to avoid import-time token validation errors.
- The HTTP server (port 8001) serves the Web UI, WebSocket, OAuth callback, debug endpoints, and static files alongside Socket Mode.

### Storage

SQLite (aiosqlite) with tables: `conversations`, `messages`, `permissions`, `user_profiles`, `meeting_history`, `user_contexts`, `users`, `api_sessions`, `role_permissions`, `usage_events`, `data_sources`, `indexed_documents`, `nexgai_sessions`, `feedback`, `learning_entries`, `prompt_versions`, `satisfaction_snapshots`. ChromaDB for RAG vector storage. Both persist in `data/`.

## Testing

Tests use `pytest-asyncio` with mocked Ollama and delivery functions. The `service` fixture patches `settings` to set debounce to 0 for fast tests. Test files:
- `tests/test_basic.py` — smoke tests (imports, config, tool parsing)
- `tests/test_meeting_transcript.py` — 33 tests covering session lifecycle, VTT parsing, suggestion generation, delivery, debounce, and edge cases
- `tests/test_skills.py` — 34 tests for enterprise skill routing, execution, confidence scoring, and context loading
- `tests/test_graph.py` — 23 tests for Graph client config, token management, OAuth flow, API operations, and EKLAVYA+Graph integration
- `tests/test_web_ui.py` — 13 tests for Web UI: health/status/skills API, static files, WebSocket auth/messaging/commands/errors
- `tests/test_auth.py` — 36 tests for auth service, RBAC, session management, role hierarchy
- `tests/test_nexgai.py` — 19 tests for NexgAI client: circuit breaker, config, message sending, agent discovery, health checks
- `tests/test_feedback.py` — 15 tests for self-learning loop: feedback service, learning engine, database CRUD, prompt versions, satisfaction snapshots

## Environment Variables

Required for Slack: `SLACK_BOT_TOKEN` (xoxb-...), `SLACK_APP_TOKEN` (xapp-...), `SLACK_SIGNING_SECRET`. Optional for Microsoft 365: `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`, `GRAPH_TENANT_ID`, `GRAPH_REDIRECT_URI`. Optional for NexgAI: `NEXGAI_ENABLED`, `NEXGAI_BASE_URL`, `NEXGAI_TENANT_ID`, `NEXGAI_SERVICE_USER`, `NEXGAI_SERVICE_PASSWORD`. See `.env.example` for all options. Ollama must be running locally on port 11434.

## Scripts

- `scripts/simulate_transcript.py` — sends fake transcript chunks to debug HTTP server (port 8001), requires an active `/transcript start` session
- `scripts/load_project_context.py` — bulk-loads project knowledge into `user_contexts` table for a user
- `scripts/test_transcript_pipeline.py` — diagnostic checking bot health, Slack config, and active sessions
