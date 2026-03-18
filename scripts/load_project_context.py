"""Load comprehensive project context into the database for a user.

Usage:
    python scripts/load_project_context.py [--user-id USER_ID]

If --user-id is not provided, loads for all existing user profiles.
"""

import asyncio
import argparse
import sys
sys.path.insert(0, ".")

from app.storage.database import Database
from app.config import settings


CONTEXTS = {
    "MyAi-Project-Overview": """MyAi (codenamed OpenClaw) is a secure, locally-running AI agent that integrates into Slack as a personal assistant. All LLM inference happens locally via Ollama — no data leaves the user's machine unless they explicitly enable web search.

Key capabilities:
- Chat with a local LLM (llama3.1:8b default, switchable at runtime)
- Read/write/search files on the user's machine (sandboxed, permission-gated)
- Web search via DuckDuckGo or Tavily
- RAG: index directories and do semantic search over documents using ChromaDB
- Receive meeting transcripts and suggest real-time responses
- Proactive messaging: sends meeting suggestions directly to the user's Slack channel
- User profiles and meeting history for personalized suggestions""",

    "MyAi-Tech-Stack": """Tech stack:
- Python 3.11+ with async throughout (aiohttp, aiosqlite, httpx)
- Ollama for local LLM inference (chat + embeddings)
- ChromaDB for vector storage (RAG)
- Slack Bolt SDK with Socket Mode (no ngrok needed)
- SQLite (aiosqlite) for conversations, user profiles, meeting history
- pydantic-settings for configuration management
- Docker + docker-compose for deployment
- pytest + pytest-asyncio for testing""",

    "MyAi-Architecture": """Architecture overview:
- app/main.py: Slack Bolt AsyncApp with Socket Mode + debug HTTP server for testing
- app/bot.py: SlackBot class, routes commands and messages to the agent
- app/agent/core.py: AgentCore with IntentRouter — pattern-matches common requests (file ops, search) before falling back to LLM
- app/agent/tools.py: ToolRegistry with 6 tools (read_file, list_directory, search_files, write_file, web_search, rag_query)
- app/services/ollama.py: OllamaClient for chat, generate, embeddings
- app/services/meeting_transcript.py: MeetingTranscriptService — session management, transcript ingestion, debounced suggestion generation
- app/services/rag.py: RAGService — directory indexing, chunking, ChromaDB semantic search
- app/services/file_access.py: Sandboxed file operations with permission checks
- app/storage/database.py: SQLite tables for conversations, messages, user_profiles, meeting_history, user_contexts
- app/security/permissions.py: Per-user auth and permission grants""",

    "MyAi-Meeting-System": """Meeting transcript & suggestion system:
1. User sends /transcript start [subject] to begin a session
2. User pastes transcript text with /transcript paste <text>
3. Transcript is parsed (VTT or plain text) and appended to session
4. After debounce period (15s default), Ollama generates a suggestion using:
   - User's profile (name, role, bio)
   - User's stored contexts (project knowledge, domain info)
   - Recent meeting history (last 3 meetings with summaries)
   - The rolling transcript (last 12000 chars)
5. Suggestion is delivered via Slack message to the user's channel
6. User ends session with /transcript end
7. On session end: transcript is summarized by Ollama and saved to meeting_history table

Key technical details:
- Content-hash dedup prevents duplicate suggestions
- Debounce timer resets when new transcript arrives
- Debug HTTP server on port 8001 for simulate script testing""",

    "MyAi-My-Role": """Anubhav Choudhury's role in the MyAi project:
- Primary developer building the MyAi Slack bot
- Responsible for the full stack: backend (Python/asyncio), Slack integration, meeting transcript system
- Built the real-time meeting suggestion pipeline (transcript → Ollama → Slack message)
- Handles database schema design, agent tools, and end-to-end testing
- Uses Windows 11, VS Code, Python 3.12, with local Ollama for inference
- Current focus: making the meeting suggestion system production-ready""",

    "MyAi-Commands": """Bot commands (use in DM or @mention, or via /myai slash command):
- /help — List all commands
- /status — Show bot health, model, features
- /model <name> — Switch Ollama model (e.g., /model mistral:7b)
- /profile name:<> role:<> bio:<> — Set user profile for personalized suggestions
- /context add <name> <content> — Store project/topic knowledge for meeting context
- /context list — View stored contexts
- /context remove <name> — Delete a context
- /allow <path> — Grant file access to a directory
- /revoke — Revoke all file permissions
- /search on|off — Toggle web search
- /index <path> — Index directory for RAG semantic search
- /transcript start [subject] — Start a transcript session
- /transcript paste <text> — Feed transcript text
- /transcript status — Check session status
- /transcript end — End session and save summary
- /clear — Clear conversation history""",

    "MyAi-Deployment": """Deployment and setup:
- Requires: Python 3.11+, Ollama running locally
- Slack: Create app at api.slack.com/apps, enable Socket Mode, add bot scopes
- Required bot scopes: chat:write, im:history, im:read, im:write, app_mentions:read, commands, users:read
- Event subscriptions: message.im, app_mention
- Socket Mode: no ngrok needed — bot connects outbound to Slack
- 2 terminals needed: Ollama (ollama serve), MyAi (python -m app.main)
- Docker deployment available (docker-compose.yml) with host.docker.internal for Ollama
- Data persisted in data/ directory (SQLite + ChromaDB)
- Configuration via .env file and config/permissions.yaml""",
}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=str, default="", help="Slack user ID to load context for")
    args = parser.parse_args()

    db = Database(settings.database_path)
    await db.init()

    if args.user_id:
        user_ids = [args.user_id]
    else:
        # Load for all existing user profiles
        import aiosqlite
        async with aiosqlite.connect(settings.database_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("SELECT user_id FROM user_profiles")
            rows = await cursor.fetchall()
            user_ids = [r["user_id"] for r in rows]

        if not user_ids:
            async with aiosqlite.connect(settings.database_path) as conn:
                cursor = await conn.execute("SELECT DISTINCT user_id FROM conversations LIMIT 5")
                rows = await cursor.fetchall()
                user_ids = [r[0] for r in rows]

    if not user_ids:
        print("No users found in database. Please provide --user-id or set your profile first with /profile in Slack.")
        return

    for uid in user_ids:
        print(f"\nLoading contexts for user: {uid[:20]}...")
        for name, content in CONTEXTS.items():
            await db.add_context(uid, name, content)
            print(f"  + {name} ({len(content)} chars)")

    print(f"\nDone! Loaded {len(CONTEXTS)} contexts for {len(user_ids)} user(s).")
    print("These will be used in meeting suggestions automatically.")


if __name__ == "__main__":
    asyncio.run(main())
