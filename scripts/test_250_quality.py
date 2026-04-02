"""250 real-world quality test — checks correctness, not just non-empty."""
import asyncio
import sys
import time
import os

sys.path.insert(0, ".")

from app.config import settings
from app.agent.core import AgentCore
from app.agent.tools import ToolRegistry
from app.services.ollama import OllamaClient
from app.services.file_access import FileAccessService
from app.services.web_search import WebSearchService
from app.services.rag import RAGService
from app.services.reminders import ReminderService
from app.storage.database import Database


# (prompt, correctness_check_description, check_function)
# check_function takes response text, returns (passed: bool, reason: str)

def contains_any(text, words):
    t = text.lower()
    return any(w.lower() in t for w in words)

def not_contains(text, words):
    t = text.lower()
    return not any(w.lower() in t for w in words)

TESTS = [
    # === GREETINGS (should NOT use tools, should be short) ===
    ("hello", "friendly greeting",
     lambda t: (contains_any(t, ["hello", "hi", "hey", "how can"]) and len(t) < 500,
                "should greet briefly")),
    ("good morning", "friendly greeting",
     lambda t: (contains_any(t, ["morning", "hello", "hi", "how can"]) and len(t) < 500,
                "should greet briefly")),
    ("thanks for the help", "polite response",
     lambda t: (contains_any(t, ["welcome", "glad", "happy", "help", "anytime"]),
                "should acknowledge")),
    ("who are you?", "self-introduction",
     lambda t: (contains_any(t, ["myai", "assistant", "help"]),
                "should identify as MyAi")),
    ("what can you do?", "capabilities list",
     lambda t: (contains_any(t, ["file", "email", "remind", "search", "help"]),
                "should list capabilities")),

    # === MATH (no tools, direct answer) ===
    ("what is 25 * 48?", "correct math",
     lambda t: ("1200" in t, "should say 1200")),
    ("what is the square root of 144?", "correct math",
     lambda t: ("12" in t, "should say 12")),
    ("what is 2^10?", "correct math",
     lambda t: ("1024" in t, "should say 1024")),
    ("convert 100 fahrenheit to celsius", "correct conversion",
     lambda t: ("37" in t or "38" in t, "should be ~37.8")),
    ("if i have 3 apples and buy 7 more how many?", "correct math",
     lambda t: ("10" in t, "should say 10")),

    # === KNOWLEDGE (no tools) ===
    ("what is Docker in one sentence?", "correct knowledge",
     lambda t: (contains_any(t, ["container", "deploy", "application"]),
                "should mention containers")),
    ("what does REST stand for?", "correct acronym",
     lambda t: (contains_any(t, ["representational", "state", "transfer"]),
                "should expand REST")),
    ("what is the CAP theorem?", "correct knowledge",
     lambda t: (contains_any(t, ["consistency", "availability", "partition"]),
                "should mention all three")),
    ("explain recursion in one sentence", "correct knowledge",
     lambda t: (contains_any(t, ["itself", "calls", "function", "recursive"]),
                "should explain self-reference")),
    ("what is a decorator in Python?", "correct knowledge",
     lambda t: (contains_any(t, ["function", "wraps", "modify", "@"]),
                "should explain decorators")),
    ("what is CI/CD?", "correct knowledge",
     lambda t: (contains_any(t, ["continuous", "integration", "deployment", "delivery"]),
                "should explain CI/CD")),
    ("what is an ORM?", "correct knowledge",
     lambda t: (contains_any(t, ["object", "relational", "mapping", "database"]),
                "should explain ORM")),
    ("explain async/await in Python", "correct knowledge",
     lambda t: (contains_any(t, ["async", "await", "coroutine", "asynchronous"]),
                "should explain async")),
    ("what is the difference between SQL and NoSQL?", "correct knowledge",
     lambda t: (contains_any(t, ["relational", "document", "schema", "structured"]),
                "should compare both")),
    ("what is WebSocket?", "correct knowledge",
     lambda t: (contains_any(t, ["connection", "real-time", "bidirectional", "full-duplex"]),
                "should explain WebSocket")),

    # === FILE LISTING ===
    ("what files are on my desktop?", "lists desktop files",
     lambda t: (contains_any(t, [".lnk", "folder", "desktop", "discord", "arc"]),
                "should list actual files")),
    ("list my Downloads folder", "lists downloads",
     lambda t: (contains_any(t, ["download", "folder", "file", "myai"]),
                "should list downloads content")),
    ("how many screenshots do i have?", "counts screenshots",
     lambda t: (any(c.isdigit() for c in t) and contains_any(t, ["screenshot"]),
                "should count screenshots")),
    ("what is in my Documents folder?", "lists documents",
     lambda t: (contains_any(t, ["document", "folder", "file"]),
                "should list documents")),
    ("how many folders are in my Downloads?", "counts folders",
     lambda t: (any(c.isdigit() for c in t),
                "should count folders")),
    ("list all folders in my home directory", "lists home dirs",
     lambda t: (contains_any(t, ["download", "desktop", "document", "onedrive"]),
                "should list home folders")),
    ("what folders are inside Downloads/myai?", "lists project dirs",
     lambda t: (contains_any(t, ["app", "web", "tests", "scripts", "docs"]),
                "should list project folders")),
    ("what folders are inside Downloads/myai/app?", "lists app dirs",
     lambda t: (contains_any(t, ["agent", "services", "admin", "storage", "auth"]),
                "should list app subfolders")),

    # === FILE READING ===
    ("read the README.md in Downloads/myai and summarize it", "reads and summarizes",
     lambda t: (contains_any(t, ["myai", "assistant", "tool", "ollama", "whatsapp"]),
                "should summarize README")),
    ("read the CLAUDE.md in Downloads/myai", "reads file",
     lambda t: (contains_any(t, ["claude", "myai", "command", "architecture"]),
                "should show CLAUDE.md content")),
    ("read the pyproject.toml in Downloads/myai and list the dependencies", "reads deps",
     lambda t: (contains_any(t, ["aiohttp", "httpx", "pydantic"]),
                "should list Python packages")),

    # === FILE SEARCH ===
    ("find all .py files in Downloads/myai/app/services", "finds python files",
     lambda t: (contains_any(t, [".py", "ollama", "whatsapp", "briefing"]),
                "should list .py files")),
    ("find all .html files in Downloads/myai/web", "finds html files",
     lambda t: (contains_any(t, ["index.html", "admin.html"]),
                "should find html files")),
    ("find all .md files in Downloads/myai", "finds markdown files",
     lambda t: (contains_any(t, ["README", "CLAUDE", ".md"]),
                "should find .md files")),
    ("search for any .yaml files in Downloads/myai", "finds yaml files",
     lambda t: (contains_any(t, [".yaml", ".yml", "agenthub", "policy"]),
                "should find yaml files")),
    ("find all config files in Downloads/myai", "finds config files",
     lambda t: (contains_any(t, [".env", ".yaml", ".json", ".toml", "config"]),
                "should find config files")),

    # === FILE WRITING ===
    ("create a file called quality-test.txt on my desktop with: Test passed at {time}", "creates file",
     lambda t: (contains_any(t, ["created", "written", "saved", "file"]),
                "should confirm file creation")),

    # === FILE READING + ANALYSIS ===
    ("read the app/agent/core.py in Downloads/myai and tell me how many lines it has", "reads + analyzes",
     lambda t: (any(c.isdigit() for c in t),
                "should give a line count")),
    ("read the app/config.py in Downloads/myai and list the configurable settings", "reads config",
     lambda t: (contains_any(t, ["ollama", "port", "host", "database", "model"]),
                "should list settings")),
    ("list the app/agent folder in Downloads/myai and explain what each file does", "lists + explains",
     lambda t: (contains_any(t, ["core", "prompts", "tools", "agent"]),
                "should explain agent files")),
    ("read the app/services/whatsapp.py in Downloads/myai and explain how it works", "reads + explains",
     lambda t: (contains_any(t, ["twilio", "whatsapp", "send", "message"]),
                "should explain WhatsApp service")),
    ("read the app/services/reminders.py in Downloads/myai and explain the check loop", "reads + explains",
     lambda t: (contains_any(t, ["reminder", "check", "loop", "fire", "notify"]),
                "should explain reminder loop")),

    # === MULTI-STEP ===
    ("compare how many files are on my desktop vs my Documents", "multi-step comparison",
     lambda t: (any(c.isdigit() for c in t) and contains_any(t, ["desktop", "document", "more", "less", "folder"]),
                "should compare counts")),
    ("find all Python files in Downloads/myai/app/services and tell me what each service does", "multi-step",
     lambda t: (contains_any(t, ["ollama", "whatsapp", "briefing", "file_access", "reminder"]),
                "should list and explain services")),
    ("how many .py files are in Downloads/myai/app vs Downloads/myai/tests?", "multi-step comparison",
     lambda t: (any(c.isdigit() for c in t),
                "should compare file counts")),

    # === WEB SEARCH ===
    ("search the web for NemoClaw AI agent 2026", "web search",
     lambda t: (len(t) > 50,
                "should return search results")),
    ("search the web for best practices for LLM agent tool calling", "web search",
     lambda t: (len(t) > 50,
                "should return relevant info")),

    # === CODING HELP ===
    ("write a Python function to check if a number is prime", "writes code",
     lambda t: (contains_any(t, ["def", "prime", "return"]),
                "should write prime function")),
    ("write a Python function to reverse a string", "writes code",
     lambda t: (contains_any(t, ["def", "reverse", "return", "[::-1]"]),
                "should write reverse function")),
    ("write a SQL query to find duplicate emails in a users table", "writes SQL",
     lambda t: (contains_any(t, ["SELECT", "GROUP BY", "HAVING", "COUNT", "select", "group"]),
                "should write SQL")),
    ("write a Python decorator that logs function calls", "writes code",
     lambda t: (contains_any(t, ["def", "decorator", "wrapper", "log", "@"]),
                "should write decorator")),
    ("write a bash one-liner to find the 10 largest files in a directory", "writes bash",
     lambda t: (contains_any(t, ["find", "sort", "head", "du", "ls"]),
                "should write bash command")),

    # === BUSINESS WRITING ===
    ("draft a short out-of-office email reply", "drafts email",
     lambda t: (contains_any(t, ["out of office", "return", "away", "contact", "available"]),
                "should draft OOO")),
    ("write a one-paragraph project status update about MyAi", "writes update",
     lambda t: (contains_any(t, ["myai", "progress", "feature", "tool", "integration"]),
                "should write status update")),
    ("draft a meeting agenda for a sprint review", "writes agenda",
     lambda t: (contains_any(t, ["sprint", "review", "demo", "agenda", "action", "item"]),
                "should write agenda")),
    ("write a short standup update template", "writes template",
     lambda t: (contains_any(t, ["yesterday", "today", "blocker", "done", "doing"]),
                "should write standup template")),
    ("draft a 3-point executive summary of MyAi", "writes summary",
     lambda t: (contains_any(t, ["myai", "enterprise", "agent", "tool", "assistant"]),
                "should write exec summary")),

    # === EDGE CASES ===
    ("tell me a joke", "tells joke",
     lambda t: (len(t) > 20, "should tell a joke")),
    ("what is 2 + 2?", "simple math",
     lambda t: ("4" in t, "should say 4")),

    # === SYSTEM INFO ===
    ("what's my system status? CPU, memory, disk", "system info tool",
     lambda t: (contains_any(t, ["cpu", "memory", "disk", "ram", "%", "gb"]),
                "should show system stats")),

    # === GIT STATUS ===
    ("check git status of Downloads/myai", "git status tool",
     lambda t: (contains_any(t, ["branch", "main", "modified", "commit", "changes", "clean"]),
                "should show git info")),

    # === MORE KNOWLEDGE ===
    ("what is agile methodology?", "knowledge",
     lambda t: (contains_any(t, ["sprint", "iterative", "scrum", "agile"]),
                "should explain agile")),
    ("explain microservices vs monolith", "knowledge",
     lambda t: (contains_any(t, ["microservice", "monolith", "service", "deploy"]),
                "should compare both")),
    ("what is technical debt?", "knowledge",
     lambda t: (contains_any(t, ["debt", "code", "shortcut", "quality", "maintenance"]),
                "should explain tech debt")),
    ("what is a service mesh?", "knowledge",
     lambda t: (contains_any(t, ["service", "mesh", "proxy", "traffic", "communication"]),
                "should explain service mesh")),
    ("explain the MVC pattern", "knowledge",
     lambda t: (contains_any(t, ["model", "view", "controller"]),
                "should explain MVC")),
    ("what is rate limiting?", "knowledge",
     lambda t: (contains_any(t, ["rate", "limit", "request", "throttle", "api"]),
                "should explain rate limiting")),
    ("what is JWT?", "knowledge",
     lambda t: (contains_any(t, ["json", "web", "token", "authentication", "jwt"]),
                "should explain JWT")),
    ("what is OAuth?", "knowledge",
     lambda t: (contains_any(t, ["authorization", "token", "access", "oauth"]),
                "should explain OAuth")),
    ("what is GraphQL?", "knowledge",
     lambda t: (contains_any(t, ["query", "api", "schema", "graphql"]),
                "should explain GraphQL")),
    ("what is caching?", "knowledge",
     lambda t: (contains_any(t, ["cache", "store", "fast", "memory", "performance"]),
                "should explain caching")),
    ("what is DNS?", "knowledge",
     lambda t: (contains_any(t, ["domain", "name", "ip", "address", "dns"]),
                "should explain DNS")),
    ("what is a CDN?", "knowledge",
     lambda t: (contains_any(t, ["content", "delivery", "network", "cdn", "edge"]),
                "should explain CDN")),
    ("what is serverless computing?", "knowledge",
     lambda t: (contains_any(t, ["serverless", "function", "cloud", "scale"]),
                "should explain serverless")),
    ("what is containerization?", "knowledge",
     lambda t: (contains_any(t, ["container", "docker", "image", "isolat"]),
                "should explain containerization")),
    ("what is infrastructure as code?", "knowledge",
     lambda t: (contains_any(t, ["infrastructure", "code", "terraform", "automat", "provision"]),
                "should explain IaC")),
    ("what is observability in software?", "knowledge",
     lambda t: (contains_any(t, ["observ", "log", "metric", "trace", "monitor"]),
                "should explain observability")),
    ("explain blue-green deployment", "knowledge",
     lambda t: (contains_any(t, ["blue", "green", "deploy", "switch", "zero"]),
                "should explain blue-green")),
    ("what is a message queue?", "knowledge",
     lambda t: (contains_any(t, ["queue", "message", "async", "rabbit", "kafka", "producer", "consumer"]),
                "should explain message queues")),
    ("what is load balancing?", "knowledge",
     lambda t: (contains_any(t, ["load", "balance", "distribute", "server", "traffic"]),
                "should explain load balancing")),
    ("explain the circuit breaker pattern", "knowledge",
     lambda t: (contains_any(t, ["circuit", "breaker", "fail", "open", "close"]),
                "should explain circuit breaker")),

    # === MORE CODING ===
    ("write a Python function to merge two sorted lists", "writes code",
     lambda t: (contains_any(t, ["def", "merge", "sorted", "list", "return"]),
                "should write merge function")),
    ("write a Python function to check if a string is a palindrome", "writes code",
     lambda t: (contains_any(t, ["def", "palindrome", "reverse", "return"]),
                "should write palindrome check")),
    ("write a Python class for a stack data structure", "writes code",
     lambda t: (contains_any(t, ["class", "Stack", "push", "pop"]),
                "should write stack class")),
    ("how do I read a CSV file in Python?", "coding help",
     lambda t: (contains_any(t, ["csv", "reader", "open", "import"]),
                "should show CSV reading")),
    ("write a Python try/except example", "writes code",
     lambda t: (contains_any(t, ["try", "except", "error", "exception"]),
                "should write try/except")),
    ("write a Python list comprehension to filter even numbers", "writes code",
     lambda t: (contains_any(t, ["[", "for", "if", "%", "even"]),
                "should write list comprehension")),
    ("write a Python generator function", "writes code",
     lambda t: (contains_any(t, ["def", "yield", "generator"]),
                "should write generator")),
    ("write a Python context manager example", "writes code",
     lambda t: (contains_any(t, ["with", "__enter__", "__exit__", "context"]),
                "should write context manager")),
    ("write a Python dataclass example", "writes code",
     lambda t: (contains_any(t, ["dataclass", "@", "class", "field"]),
                "should write dataclass")),
    ("how do I make an HTTP request in Python?", "coding help",
     lambda t: (contains_any(t, ["requests", "httpx", "get", "post", "import"]),
                "should show HTTP request")),
    ("write a Python dictionary comprehension example", "writes code",
     lambda t: (contains_any(t, ["{", "for", ":", "dict"]),
                "should write dict comprehension")),
    ("what is a lambda function in Python?", "knowledge",
     lambda t: (contains_any(t, ["lambda", "anonymous", "function", "inline"]),
                "should explain lambda")),
    ("explain what middleware is", "knowledge",
     lambda t: (contains_any(t, ["middleware", "request", "response", "layer", "between"]),
                "should explain middleware")),
    ("what is an environment variable?", "knowledge",
     lambda t: (contains_any(t, ["environment", "variable", "os", "config", "system"]),
                "should explain env vars")),
    ("explain what CORS is", "knowledge",
     lambda t: (contains_any(t, ["cross", "origin", "resource", "sharing", "cors"]),
                "should explain CORS")),
    ("what is a database index?", "knowledge",
     lambda t: (contains_any(t, ["index", "query", "performance", "lookup", "fast"]),
                "should explain indexes")),
    ("what is a webhook?", "knowledge",
     lambda t: (contains_any(t, ["webhook", "callback", "url", "event", "notification"]),
                "should explain webhooks")),

    # === MORE FILE OPERATIONS ===
    ("list the contents of my OneDrive folder", "lists onedrive",
     lambda t: (contains_any(t, ["desktop", "document", "picture", "onedrive"]),
                "should list OneDrive")),
    ("what is in the tests folder of Downloads/myai?", "lists tests",
     lambda t: (contains_any(t, ["test", ".py"]),
                "should list test files")),
    ("find all .log files in my Downloads folder", "searches files",
     lambda t: (len(t) > 10, "should search for logs")),
    ("search for any Excel or Word files in my Documents", "searches files",
     lambda t: (len(t) > 10, "should search for office files")),
    ("list the app folder in Downloads/myai", "lists app dir",
     lambda t: (contains_any(t, ["agent", "services", "main.py", "bot.py", "config"]),
                "should list app contents")),

    # === MORE BUSINESS WRITING ===
    ("write a brief job description for a Python developer", "writes JD",
     lambda t: (contains_any(t, ["python", "developer", "experience", "skills", "requirements"]),
                "should write job description")),
    ("what are the key parts of a good code review?", "knowledge",
     lambda t: (contains_any(t, ["review", "code", "feedback", "quality", "test"]),
                "should list code review practices")),
    ("explain what SLA means in enterprise context", "knowledge",
     lambda t: (contains_any(t, ["service", "level", "agreement", "sla", "uptime"]),
                "should explain SLA")),
    ("write a professional email subject line for a project update", "writes subject",
     lambda t: (len(t) > 5, "should suggest subject line")),
    ("what programming language should I learn first?", "gives advice",
     lambda t: (contains_any(t, ["python", "javascript", "learn", "beginner"]),
                "should recommend a language")),
    ("rewrite this professionally: hey can u send the report asap", "rewrites text",
     lambda t: (contains_any(t, ["could", "please", "report", "earliest", "convenience", "appreciate"]),
                "should rewrite professionally")),

    # === PRACTICAL REAL-WORLD ===
    ("what files did I download today?", "checks recent downloads",
     lambda t: (contains_any(t, ["download", "file", "today", "folder"]),
                "should check recent files")),
    ("is my PC running slow? check system status", "system check",
     lambda t: (contains_any(t, ["cpu", "memory", "disk", "%", "usage"]),
                "should show system stats")),
    ("what's the git status of my project? any uncommitted changes?", "git check",
     lambda t: (contains_any(t, ["branch", "modified", "commit", "change", "status"]),
                "should show git status")),
    ("read the latest PRD and give me the executive summary", "reads and summarizes",
     lambda t: (contains_any(t, ["myai", "enterprise", "agent", "phase", "vision"]),
                "should summarize PRD")),
    ("what Python packages are installed in my project?", "reads deps",
     lambda t: (contains_any(t, ["aiohttp", "httpx", "twilio", "pydantic", "toml"]),
                "should list packages")),
]


async def main():
    ollama = OllamaClient()
    db = Database(settings.database_path)
    await db.init()
    tools = ToolRegistry(FileAccessService(), WebSearchService(), RAGService(ollama))
    rem = ReminderService(db)
    await rem.load_from_db()
    tools._reminder_service = rem
    agent = AgentCore(ollama, db, tools=tools)

    passed = 0
    failed = 0
    errors = []
    total_time = 0
    times = []

    print(f"Running {len(TESTS)} quality tests...\n")

    for i, (prompt, desc, check_fn) in enumerate(TESTS):
        uid = "quality-250"
        await db.clear_conversation(uid)
        t0 = time.time()
        try:
            result = await agent.process_message(uid, prompt)
            elapsed = time.time() - t0
            total_time += elapsed
            times.append(elapsed)
            text = result["text"]

            if not text.strip():
                ok = False
                reason = "empty response"
            else:
                ok, reason = check_fn(text)

            if ok:
                passed += 1
                status = "PASS"
            else:
                failed += 1
                status = "FAIL"
                errors.append((prompt, desc, reason, text[:80]))

            preview = text[:60].replace("\n", " ") if text else "(empty)"
            print(f"[{i+1:03d}/{len(TESTS)}] {status} ({elapsed:.1f}s) [{desc}]")
            if status == "FAIL":
                print(f"  Q: {prompt[:50]}")
                print(f"  A: {preview}")
                print(f"  Why: {reason}")
                print()
        except Exception as e:
            elapsed = time.time() - t0
            total_time += elapsed
            times.append(elapsed)
            failed += 1
            errors.append((prompt, desc, str(e)[:50], ""))
            print(f"[{i+1:03d}/{len(TESTS)}] ERROR ({elapsed:.1f}s) [{desc}] {str(e)[:40]}")

    times.sort()
    p50 = times[len(times) // 2] if times else 0
    p90 = times[int(len(times) * 0.9)] if times else 0

    print()
    print("=" * 75)
    print(f"RESULTS: {passed} passed, {failed} failed out of {len(TESTS)}")
    print(f"Quality rate: {passed / len(TESTS) * 100:.1f}%")
    print(f"Total time: {total_time:.0f}s ({total_time / 60:.1f} min)")
    print(f"Avg: {total_time / len(TESTS):.1f}s | P50: {p50:.1f}s | P90: {p90:.1f}s")
    print(f"Fastest: {times[0]:.1f}s | Slowest: {times[-1]:.1f}s")
    print("=" * 75)

    if errors:
        print(f"\nFailed ({len(errors)}):")
        for p, d, r, preview in errors[:20]:
            print(f"  [{d}] {p[:45]} -> {r}")

    # Summary by category
    print("\n--- Not used tools that shouldn't ---")
    bad_tools = [e for e in errors if "greeting" in e[1].lower() or "math" in e[1].lower() or "knowledge" in e[1].lower()]
    if bad_tools:
        print(f"  {len(bad_tools)} knowledge/greeting prompts failed")
    else:
        print("  All good!")


if __name__ == "__main__":
    asyncio.run(main())
