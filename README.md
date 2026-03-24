# MyAi — Enterprise AI Assistant

A locally-running AI assistant powered by **Ollama (qwen2.5:7b)**. Accessible via **Web UI**, **WhatsApp**, and **Slack**. All LLM inference is local — your data stays on your machine.

## Features

- **Hybrid Agent System** — LLM classifies intent, code executes tools reliably
- **10 Tools** — File read/write/search, email (Outlook), WhatsApp, reminders, web search, RAG
- **WhatsApp Bot** — Bidirectional messaging via Twilio, syncs to web UI in real-time
- **Email via Outlook** — LLM drafts professional emails, opens in Outlook ready to send
- **Smart Reminders** — Natural language ("remind me in 5 minutes to..."), notifies via web + WhatsApp
- **Daily Briefing** — Auto-generated morning briefing on login + 10 AM WhatsApp delivery
- **File Watcher** — Monitors Downloads, Desktop, Documents for new files
- **Multi-Conversation Chat** — Claude.ai-style multiple chat threads with sidebar
- **Dark Obsidian UI** — Modern dark theme with Space Grotesk + Inter fonts
- **Self-Learning Loop** — Feedback collection, admin-approved prompt refinements
- **Admin Dashboard** — Analytics, user management, learning loop, system health
- **Auth & RBAC** — 4-tier role hierarchy, session management
- **NexgAI Integration** — Ready to connect to 24+ specialized enterprise agents
- **Remote Access** — Via ngrok for mobile access

## Quick Start

### Prerequisites

1. **Python 3.11+**
2. **Ollama** — Install from [ollama.com](https://ollama.com)

### Install & Run

```bash
# Clone and install
git clone https://github.com/anubhavnexgai/MyAi-Enterprise.git
cd MyAi-Enterprise
pip install -e ".[dev]"

# Pull Ollama models
ollama pull qwen2.5:7b
ollama pull nomic-embed-text

# Configure environment
cp .env.example .env
# Edit .env with your credentials

# Start Ollama
ollama serve

# Run MyAi
python -m app.main --web-only
```

Open http://localhost:8001 for the Web UI.

### WhatsApp Setup

1. Create a [Twilio account](https://www.twilio.com/try-twilio)
2. Set up WhatsApp Sandbox in Twilio Console
3. Add to `.env`:
```bash
TWILIO_ACCOUNT_SID=your_sid
TWILIO_AUTH_TOKEN=your_token
TWILIO_WHATSAPP_NUMBER=+14155238886
```
4. Start ngrok: `ngrok http 8001`
5. Set Twilio sandbox webhook to: `https://<ngrok-url>/whatsapp/webhook`

### Remote Access

```bash
ngrok http 8001
# Access MyAi from phone at the ngrok URL
```

## Architecture

```
Web UI (WebSocket)  → Pre-intercepts (email/reminder/whatsapp)
WhatsApp (Twilio)   → WhatsApp webhook
                          ↓
                    AgentCore (hybrid agent)
                     ↓              ↓
              LLM classifies    Code executes
              intent + params   tools directly
                     ↓
              Ollama (qwen2.5:7b)
```

**Hybrid Tool System:**
1. **Pre-intercepts** — Email, reminder, WhatsApp detected by regex → executed directly (100% reliable)
2. **LLM Tool Calling** — File operations, web search → LLM outputs tool block → code executes → LLM synthesizes result
3. **Fake Action Detection** — If LLM describes action without executing, forces re-classification

## Tools

| Tool | Execution | Description |
|------|-----------|-------------|
| `send_email` | Pre-intercept + LLM | LLM drafts body, opens Outlook via .eml |
| `set_reminder` | Pre-intercept | Fires via WebSocket + WhatsApp |
| `send_whatsapp` | Pre-intercept | Opens wa.me with pre-filled message |
| `read_file` | LLM tool call | Read file contents |
| `list_directory` | LLM tool call | List folder contents |
| `search_files` | LLM tool call | Glob pattern search |
| `write_file` | LLM tool call | Create/write files |
| `web_search` | LLM tool call | DuckDuckGo search |
| `rag_query` | LLM tool call | Search indexed documents |

## Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/status` | Current config and health |
| `/remind <time> <message>` | Set a reminder |
| `/reminders` | List active reminders |
| `/admin` | Open admin dashboard |
| `/connect` | Connect Microsoft 365 |
| `/search on\|off` | Toggle web search |
| `/index <path>` | Index directory for RAG |
| `/clear` | Clear conversation |

## Environment Variables

```bash
# Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b

# Twilio WhatsApp (optional)
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_WHATSAPP_NUMBER=+14155238886

# Slack (optional)
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...

# NexgAI Platform (optional)
NEXGAI_ENABLED=false
NEXGAI_BASE_URL=http://localhost:8002

# Microsoft 365 (optional)
GRAPH_CLIENT_ID=...
GRAPH_CLIENT_SECRET=...
```

See `.env.example` for all options.

## Testing

```bash
pytest tests/ -v
ruff check app/
```

## Project Structure

```
myai/
├── app/
│   ├── main.py              # Server, WebSocket, pre-intercepts, WhatsApp webhook
│   ├── bot.py               # Slack bot + slash commands
│   ├── config.py            # Settings
│   ├── agent/
│   │   ├── core.py          # Hybrid agent (LLM classify → code execute)
│   │   ├── prompts.py       # System prompts + tool definitions
│   │   └── tools.py         # 10 tool implementations
│   ├── services/
│   │   ├── ollama.py        # Ollama API client
│   │   ├── whatsapp.py      # Twilio WhatsApp (send/receive)
│   │   ├── briefing.py      # Daily briefing generator
│   │   ├── reminders.py     # Reminder service + check loop
│   │   ├── file_watcher.py  # Watchdog file monitoring
│   │   ├── nexgai_client.py # NexgAI platform integration
│   │   ├── graph.py         # Microsoft Graph OAuth2
│   │   ├── file_access.py   # Sandboxed file operations
│   │   ├── web_search.py    # DuckDuckGo search
│   │   └── rag.py           # ChromaDB + embeddings
│   ├── admin/               # Dashboard analytics + routes
│   ├── learning/            # Feedback + learning engine
│   ├── auth/                # Auth service, RBAC
│   └── storage/             # SQLite database
├── web/
│   ├── index.html           # Dark obsidian chat UI
│   ├── styles.css           # Obsidian theme styles
│   ├── app.js               # WebSocket client, multi-conversation
│   ├── admin.html/css/js    # Admin dashboard
├── tests/
├── scripts/
├── .env.example
└── pyproject.toml
```

## License

Proprietary — Enterprise Copilot Ltd
