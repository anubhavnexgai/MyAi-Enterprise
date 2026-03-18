# MyAi — Enterprise AI Assistant

A locally-running enterprise AI assistant powered by **Ollama** and the **NexgAI AI Workforce platform**. Accessible via **Slack** (Socket Mode) and a built-in **Web UI**. All LLM inference is local — your data stays on your machine.

## Features

- **2-Way Routing** — Specialized tasks routed to NexgAI platform (24+ AI agents); general questions handled by local Ollama LLM
- **SSE-to-WebSocket Streaming** — NexgAI streams responses via SSE, relayed in real-time to the Web UI
- **Circuit Breaker** — 3 failures → 60s cooldown → transparent fallback to Ollama
- **Slack + Web UI** — Dual interface: Slack (Socket Mode) and browser-based chat (WebSocket)
- **Self-Learning Loop** — Thumbs up/down feedback → background learning engine → admin-approved prompt refinements
- **Microsoft 365** — OAuth2 calendar, email, and files via Graph API
- **Meeting Transcripts** — Real-time transcript pasting with debounced AI suggestions
- **RAG** — Index documents for semantic search with ChromaDB
- **Auth & RBAC** — 4-tier role hierarchy (Super Admin > Admin > Manager > Employee), session management
- **Admin Dashboard** — Usage analytics, user management, learning approvals, satisfaction trends

## Quick Start

### Prerequisites

1. **Python 3.11+**
2. **Ollama** — Install from [ollama.com](https://ollama.com)

### Install & Run

```bash
# Clone and install
git clone <repo-url>
cd myai
pip install -e ".[dev]"

# Pull Ollama models
ollama pull llama3.1:8b
ollama pull nomic-embed-text  # for RAG embeddings

# Configure environment
cp .env.example .env
# Edit .env with your credentials

# Start Ollama
ollama serve

# Run MyAi (Web UI only — no Slack credentials needed)
python -m app.main --web-only

# Run MyAi (Slack + Web UI)
python -m app.main
```

Open http://localhost:8001 for the Web UI.

### CLI Chat (no Slack/Web needed)

```bash
python cli_chat.py
```

## Architecture

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

**Routing:** Every message goes to NexgAI first (if configured and circuit breaker is closed). If NexgAI is unavailable or not configured, Ollama handles it as the general-purpose fallback.

**Self-Learning:** Feedback (thumbs up/down) is collected per message → background engine (every 6h) analyzes patterns → generates learning entries → admin approves via dashboard → prompts updated dynamically.

## Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/status` | Current config, model, and health |
| `/model <name>` | Switch Ollama model |
| `/allow <path>` | Grant file access to a directory |
| `/revoke` | Revoke all file permissions |
| `/search on\|off` | Toggle web search |
| `/index <path>` | Index directory for RAG search |
| `/clear` | Clear conversation history |
| `/connect` | Connect Microsoft 365 account |
| `/calendar` | View upcoming calendar events |
| `/email` | View recent emails |
| `/files` | Search OneDrive files |
| `/transcript start\|paste\|end` | Meeting transcript workflow |

## Environment Variables

```bash
# Required for Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=...

# Ollama (defaults shown)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b

# NexgAI Platform (optional — enables 24+ specialized agents)
NEXGAI_ENABLED=true
NEXGAI_BASE_URL=https://your-nexgai-instance.com
NEXGAI_TENANT_ID=your-tenant-id
NEXGAI_SERVICE_USER=myai-service@company.com
NEXGAI_SERVICE_PASSWORD=...

# Microsoft 365 (optional)
GRAPH_CLIENT_ID=...
GRAPH_CLIENT_SECRET=...
GRAPH_TENANT_ID=...
```

See `.env.example` for all options.

## Testing

```bash
# Run all tests (207 tests)
pytest tests/ -v

# Run a specific test file
pytest tests/test_feedback.py -v

# Run a specific test class or method
pytest tests/test_feedback.py::TestFeedbackService -v

# Lint
ruff check app/
```

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_basic.py` | Smoke tests — imports, config, tool parsing |
| `test_meeting_transcript.py` | 33 tests — session lifecycle, VTT parsing, suggestions |
| `test_skills.py` | 34 tests — skill routing, confidence scoring |
| `test_graph.py` | 23 tests — Graph client, OAuth, API operations |
| `test_web_ui.py` | 13 tests — HTTP endpoints, WebSocket, static files |
| `test_auth.py` | 36 tests — auth service, RBAC, sessions |
| `test_nexgai.py` | 19 tests — NexgAI client, circuit breaker |
| `test_feedback.py` | 15 tests — feedback, learning engine, prompt versions |

## Project Structure

```
myai/
├── app/
│   ├── main.py              # aiohttp server, WebSocket handler, startup
│   ├── bot.py               # Slack bot + slash commands
│   ├── config.py            # Settings (pydantic-settings)
│   ├── agent/
│   │   ├── core.py          # AgentCore — 2-way routing (NexgAI → Ollama)
│   │   ├── prompts.py       # System prompts
│   │   └── tools.py         # Tool registry
│   ├── services/
│   │   ├── ollama.py        # Ollama API client
│   │   ├── nexgai.py        # NexgAI client (SSE streaming, circuit breaker)
│   │   ├── graph.py         # Microsoft Graph OAuth2 + API
│   │   ├── file_access.py   # Sandboxed file operations
│   │   ├── web_search.py    # DuckDuckGo / Tavily
│   │   ├── rag.py           # ChromaDB + embeddings
│   │   └── meeting_transcript.py  # Real-time transcript service
│   ├── learning/
│   │   ├── feedback_service.py  # Feedback CRUD
│   │   ├── engine.py        # Background learning engine
│   │   └── routes.py        # Admin learning API + dashboard
│   ├── security/
│   │   ├── permissions.py   # Permission manager
│   │   └── auth.py          # Auth service, RBAC, sessions
│   └── storage/
│       ├── database.py      # SQLite (18 tables)
│       └── models.py        # Pydantic data models
├── web/
│   ├── index.html           # Chat UI
│   ├── styles.css           # Styles
│   ├── app.js               # WebSocket client + feedback UI
│   └── learning.html        # Admin learning dashboard
├── tests/                   # 207 tests
├── scripts/                 # Utility scripts
├── docs/                    # PRD
├── .env.example
├── pyproject.toml
└── CLAUDE.md
```

## Docker

```bash
docker compose up --build
```

Ollama must be running on the host. The container connects via `host.docker.internal`.

## License

MIT
