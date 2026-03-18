"""
MyAi CLI Chat — Test the agent without Teams.

Usage:
    python cli_chat.py

Supports all slash commands (/help, /status, /allow, /search, etc.)
"""
import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import settings, permissions_config
from app.agent.core import AgentCore
from app.agent.tools import ToolRegistry
from app.services.ollama import OllamaClient
from app.services.file_access import FileAccessService
from app.services.web_search import WebSearchService
from app.services.rag import RAGService
from app.storage.database import Database
from app.security.permissions import permission_manager

from pathlib import Path


USER_ID = "cli-user"


async def handle_command(text: str, agent: AgentCore, search_service: WebSearchService) -> str | None:
    """Handle slash commands locally (mirrors bot.py logic)."""
    parts = text.split(maxsplit=1)
    command = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if command == "/help":
        return (
            "\n🐾 MyAi Commands\n"
            "─────────────────────────────\n"
            "  /model <n>     Switch Ollama model\n"
            "  /status        Show current config & health\n"
            "  /allow <path>  Grant file access to a directory\n"
            "  /revoke        Revoke all file permissions\n"
            "  /search on|off Toggle web search\n"
            "  /index <path>  Index a directory for RAG\n"
            "  /clear         Clear conversation history\n"
            "  /help          Show this message\n"
            "  /quit          Exit the CLI\n"
        )

    elif command == "/status":
        ollama_ok = await agent.ollama.health_check()
        models = []
        if ollama_ok:
            try:
                model_list = await agent.ollama.list_models()
                models = [m.get("name", "?") for m in model_list[:10]]
            except Exception:
                pass

        search_status = "🟢 On" if search_service.enabled else "🔴 Off"
        dirs = permissions_config.allowed_dirs or ["None"]

        return (
            f"\n🐾 MyAi Status\n"
            f"─────────────────────────────\n"
            f"  Ollama:    {'🟢 Connected' if ollama_ok else '🔴 Not reachable'}\n"
            f"  Model:     {agent.ollama.model}\n"
            f"  Models:    {', '.join(models) or 'N/A'}\n"
            f"  Search:    {search_status}\n"
            f"  Dirs:      {chr(10).join('  ' + d for d in dirs)}\n"
        )

    elif command == "/model":
        if not arg:
            return "Usage: /model <model_name> (e.g., /model mistral:7b)"
        agent.ollama.set_model(arg)
        return f"✅ Switched to model: {arg}"

    elif command == "/allow":
        if not arg:
            return "Usage: /allow <directory_path>"
        resolved = str(Path(arg).expanduser().resolve())
        if not Path(resolved).exists():
            return f"⚠️ Directory not found: {arg}"
        if not Path(resolved).is_dir():
            return f"⚠️ Not a directory: {arg}"
        permissions_config.grant_directory(resolved)
        permission_manager.grant(USER_ID, f"dir:{resolved}")
        return f"✅ Granted access to: {resolved}"

    elif command == "/revoke":
        permissions_config.revoke_all()
        permission_manager.revoke_all(USER_ID)
        return "✅ All file permissions revoked."

    elif command == "/search":
        if arg.lower() in ("on", "true", "enable"):
            search_service.toggle(True)
            return "🔍 Web search enabled."
        elif arg.lower() in ("off", "false", "disable"):
            search_service.toggle(False)
            return "🔍 Web search disabled."
        else:
            return "Usage: /search on or /search off"

    elif command == "/index":
        if not arg:
            return "Usage: /index <directory_path>"
        resolved = str(Path(arg).expanduser().resolve())
        if not permissions_config.is_path_allowed(resolved):
            return f"⚠️ Directory not in allowlist. Run /allow {arg} first."
        print("  ⏳ Indexing... this may take a moment.")
        try:
            result = await agent.tools.rag_service.index_directory(resolved)
            return f"✅ {result}"
        except Exception as e:
            return f"❌ Indexing failed: {e}"

    elif command == "/clear":
        await agent.db.clear_conversation(USER_ID)
        return "✅ Conversation history cleared."

    elif command in ("/quit", "/exit", "/q"):
        print("\n👋 Goodbye!")
        sys.exit(0)

    return None  # Not a command


async def main():
    # Initialize services
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)

    ollama_client = OllamaClient()
    file_service = FileAccessService()
    search_service = WebSearchService()
    rag_service = RAGService(ollama_client)
    database = Database(settings.database_path)
    await database.init()

    tool_registry = ToolRegistry(file_service, search_service, rag_service)
    agent = AgentCore(ollama_client, tool_registry, database)

    # Health check
    ollama_ok = await ollama_client.health_check()

    print()
    print("=" * 55)
    print("  🐾  MyAi CLI Chat")
    print("=" * 55)
    print(f"  Model:   {ollama_client.model}")
    print(f"  Ollama:  {'🟢 Connected' if ollama_ok else '🔴 Not reachable — run: ollama serve'}")
    print(f"  Type /help for commands, /quit to exit")
    print("=" * 55)
    print()

    if not ollama_ok:
        print("⚠️  Warning: Ollama is not reachable. Start it with: ollama serve\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n👋 Goodbye!")
            break

        if not user_input:
            continue

        # Check for slash commands
        if user_input.startswith("/"):
            response = await handle_command(user_input, agent, search_service)
            if response:
                print(f"\n{response}\n")
                continue

        # Send to agent
        print("  ⏳ Thinking...")
        try:
            result = await agent.process_message(USER_ID, user_input)
            print(f"\nMyAi: {result['text']}\n")
        except Exception as e:
            print(f"\n❌ Error: {e}\n")


if __name__ == "__main__":
    asyncio.run(main())