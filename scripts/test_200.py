"""Run 200 prompts through MyAi agent and report results."""
import asyncio
import sys
import time

sys.path.insert(0, ".")

from app.config import settings
from app.agent.core import AgentCore
from app.agent.tools import ToolRegistry
from app.services.ollama import OllamaClient
from app.services.file_access import FileAccessService
from app.services.web_search import WebSearchService
from app.services.rag import RAGService
from app.storage.database import Database

PROMPTS = [
    # === Greetings (8) ===
    "hello", "hi there", "good morning", "hey, how are you?",
    "thanks for the help", "goodbye", "who are you?", "what can you do?",

    # === Simple knowledge (10) ===
    "what is 25 * 48?", "what is the capital of Japan?",
    "explain what an API is in 2 sentences", "write a hello world in Python",
    "what is the difference between HTTP and HTTPS?", "what does REST stand for?",
    "explain recursion simply", "write a SQL query to count rows in a users table",
    "what is Docker?", "what is Git branching?",

    # === Email tool (3) ===
    "send an email to priti.padhy@nexgai.com saying the MyAi demo is ready for review",
    "draft an email to john@company.com with subject Weekly Update saying here are this weeks highlights",
    "email anubhav@nexgai.com saying I will be 10 minutes late to the meeting",

    # === Reminder tool (3) ===
    "remind me in 5 minutes to check the build",
    "set a reminder at 6pm to review code",
    "remind me tomorrow at 9am to prepare for standup",

    # === File listing (7) ===
    "what files are on my desktop?", "list my Downloads folder",
    "how many screenshots do i have?", "what is in my Documents folder?",
    "show me the contents of my Pictures folder", "how many folders are in my Downloads?",
    "list all folders in my home directory",

    # === File reading (3) ===
    "read the README.md in Downloads/openclaw-transfer",
    "read the CLAUDE.md in Downloads/openclaw-transfer",
    "read the .env.example in Downloads/openclaw-transfer",

    # === File search (5) ===
    "find all .py files in Downloads/openclaw-transfer",
    "search for any .txt files on my desktop",
    "find all .html files in Downloads/openclaw-transfer/web",
    "find all .json files in Downloads/openclaw-transfer",
    "search for any .pdf files in my Documents",

    # === File writing (1) ===
    "create a file called test-note.txt on my desktop with the text: MyAi test successful",

    # === Multi-step (4) ===
    "read the requirements.txt in Downloads/openclaw-transfer and tell me how many dependencies there are",
    "list the app/agent folder in Downloads/openclaw-transfer and explain what each file does",
    "find all .css files in Downloads/openclaw-transfer and count them",
    "what is the largest file in my screenshots folder?",

    # === Web search (2) ===
    "search the web for latest Python version",
    "what is the latest news about AI?",

    # === Coding help (5) ===
    "write a Python function to check if a number is prime",
    "write a function to reverse a string in JavaScript",
    "explain async/await in Python",
    "write a regex to validate an email address",
    "what is the time complexity of binary search?",

    # === Business/writing (5) ===
    "draft a short out-of-office email reply",
    "write 3 OKRs for a software engineering team",
    "rewrite this professionally: hey can u send the report asap",
    "write a one-paragraph project status update",
    "give me 3 bullet points for a meeting summary about Q1 results",

    # === Math (5) ===
    "what is 15 * 23?", "what is the square root of 144?",
    "convert 100 Fahrenheit to Celsius", "what is 2^10?",
    "if i have 3 apples and buy 5 more how many do i have?",

    # === Edge cases (4) ===
    "tell me a joke", "what time is it?", "can you help me?", "you are awesome",

    # === Complex multi-tool (12) ===
    "compare how many files are on my desktop vs my Documents folder",
    "find all Python files in Downloads/openclaw-transfer/app/services and tell me what each service does",
    "read the bot.py in Downloads/openclaw-transfer/app and summarize what commands it supports",
    "look at the web folder in Downloads/openclaw-transfer and list all files",
    "how many .py files are in Downloads/openclaw-transfer/tests?",
    "read the main.py in Downloads/openclaw-transfer/app and tell me what port the server runs on",
    "find all config files in Downloads/openclaw-transfer",
    "check my screenshots folder and tell me how many screenshots I have",
    "list my Downloads folder then read the README of any project folder you find",
    "search for any .md files in Downloads/openclaw-transfer",
    "what folders are inside Downloads/openclaw-transfer/app?",
    "read the prompts.py file in Downloads/openclaw-transfer/app/agent and explain the tool system",

    # === WhatsApp tool (1) ===
    "send a whatsapp message to 919876543210 saying hey the demo is ready",

    # === General knowledge advanced (25) ===
    "explain microservices vs monolith in 3 points",
    "what is the CAP theorem?", "explain what Kubernetes does",
    "what is CI/CD?", "what is technical debt?", "what is an ORM?",
    "explain the MVC pattern", "what is a service mesh?",
    "list 5 common software architecture patterns",
    "what are the SOLID principles?",
    "explain what a closure is in programming",
    "what is the difference between SQL and NoSQL?",
    "what is a decorator in Python?",
    "how do I create a virtual environment in Python?",
    "write a Python function to merge two sorted lists",
    "how do I make an HTTP request in Python?",
    "write a function to check if a string is a palindrome",
    "explain the difference between == and === in JavaScript",
    "how do I handle exceptions in Python?",
    "write a bash script to count lines in all .py files",
    "what does the map() function do in Python?",
    "write a Python class for a stack data structure",
    "how do I read a CSV file in Python?",
    "write a function to find the factorial of a number",
    "what is the difference between a list and a tuple in Python?",

    # === More file operations (8) ===
    "list the contents of my OneDrive folder",
    "how many files are in my Documents?",
    "what folders are inside Downloads/openclaw-transfer?",
    "list the app folder in Downloads/openclaw-transfer",
    "what is in the tests folder of Downloads/openclaw-transfer?",
    "read my most recent screenshot filename",
    "find all .log files in my Downloads folder",
    "search for any Excel or Word files in my Documents",

    # === Writing/drafting (5) ===
    "draft a meeting agenda for a sprint planning session",
    "write a brief job description for a Python developer",
    "what are the key parts of a good code review?",
    "explain what SLA means in enterprise context",
    "write a short standup update template",

    # === Complex analysis (8) ===
    "read the app/config.py in Downloads/openclaw-transfer and explain what settings are configurable",
    "look at the test files in Downloads/openclaw-transfer/tests and tell me what test coverage looks like",
    "read the requirements.txt in Downloads/openclaw-transfer and list all technologies used",
    "suggest how I should organize my desktop files",
    "find all .yaml files in Downloads/openclaw-transfer",
    "how many Python files are in the app folder vs tests folder in Downloads/openclaw-transfer?",
    "read the app/services/briefing.py in Downloads/openclaw-transfer and explain what it does",
    "find all files modified today on my desktop",

    # === More knowledge (5) ===
    "what is agile methodology?",
    "explain the difference between Scrum and Kanban",
    "what is a sprint retrospective?",
    "write a professional email subject line for a project update",
    "what programming language should I learn first?",

    # === Service reading (4) ===
    "read the app/services/whatsapp.py in Downloads/openclaw-transfer and explain the Twilio integration",
    "read the app/services/reminders.py in Downloads/openclaw-transfer and explain how reminders work",
    "read the app/services/file_watcher.py in Downloads/openclaw-transfer and explain the file monitoring",
    "find all .png files in my screenshots folder and count them",

    # === File creation (1) ===
    "create a file called ideas.txt on my desktop with: Feature ideas for MyAi v2",

    # === Padding to 200 (lots of knowledge) ===
    "what is machine learning in one sentence?",
    "what does the acronym API stand for?",
    "explain what a REST API is briefly",
    "what is 2 + 2?",
    "explain Git in simple terms",
    "what is the difference between frontend and backend?",
    "write a CSS rule to center a div",
    "what is JSON?",
    "explain what an IDE is",
    "what is npm?",
    "write a Python dictionary comprehension example",
    "what is a lambda function in Python?",
    "explain what middleware is",
    "what is WebSocket?",
    "write a Python try/except example",
    "what is an environment variable?",
    "explain what CORS is",
    "what is a database index?",
    "write a Python list comprehension to filter even numbers",
    "what is rate limiting?",
    "explain what JWT is",
    "what is a webhook?",
    "write a Python generator function",
    "what is caching?",
    "explain what load balancing is",
    "what is a CDN?",
    "write a Python context manager example",
    "what is OAuth?",
    "explain what GraphQL is",
    "what is a message queue?",
    "write a Python dataclass example",
    "what is serverless computing?",
    "explain what DNS is",
    "what is an API gateway?",
    "what is containerization?",
    "explain blue-green deployment",
    "what is infrastructure as code?",
    "what is observability in software?",
    "write a hello world in JavaScript",
    "write a Python function to reverse a string",
]


async def main():
    ollama = OllamaClient()
    db = Database(settings.database_path)
    await db.init()
    file_svc = FileAccessService()
    search_svc = WebSearchService()
    rag_svc = RAGService(ollama)
    tools = ToolRegistry(file_svc, search_svc, rag_svc)
    agent = AgentCore(ollama, db, tools=tools)

    passed = 0
    failed = 0
    errors = []
    total_time = 0
    times = []

    print(f"Running {len(PROMPTS)} prompts...\n")

    for i, prompt in enumerate(PROMPTS):
        uid = "test-200"
        await db.clear_conversation(uid)
        t0 = time.time()
        try:
            result = await agent.process_message(uid, prompt)
            elapsed = time.time() - t0
            total_time += elapsed
            times.append(elapsed)
            text = result["text"]
            ok = bool(text.strip())
            if ok:
                passed += 1
            else:
                failed += 1
                errors.append((prompt, "empty response"))
            status = "PASS" if ok else "FAIL"
            preview = text[:60].replace("\n", " ") if text else "(empty)"
            print(f"[{i+1:03d}/{len(PROMPTS)}] {status} ({elapsed:.1f}s) | {prompt[:35]:<35} | {preview}")
        except Exception as e:
            elapsed = time.time() - t0
            total_time += elapsed
            times.append(elapsed)
            failed += 1
            errors.append((prompt, str(e)[:80]))
            print(f"[{i+1:03d}/{len(PROMPTS)}] ERROR ({elapsed:.1f}s) | {prompt[:35]:<35} | {str(e)[:50]}")

    times.sort()
    p50 = times[len(times) // 2] if times else 0
    p90 = times[int(len(times) * 0.9)] if times else 0

    print()
    print("=" * 75)
    print(f"RESULTS: {passed} passed, {failed} failed out of {len(PROMPTS)}")
    print(f"Pass rate: {passed / len(PROMPTS) * 100:.1f}%")
    print(f"Total time: {total_time:.0f}s ({total_time / 60:.1f} min)")
    print(f"Avg: {total_time / len(PROMPTS):.1f}s | P50: {p50:.1f}s | P90: {p90:.1f}s")
    print(f"Fastest: {times[0]:.1f}s | Slowest: {times[-1]:.1f}s")
    print("=" * 75)
    if errors:
        print(f"\nFailed prompts ({len(errors)}):")
        for p, e in errors[:10]:
            print(f"  - {p[:50]} -> {e}")


if __name__ == "__main__":
    asyncio.run(main())
