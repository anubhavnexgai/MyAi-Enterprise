SYSTEM_PROMPT = """You are MyAi, an intelligent personal AI assistant.
You run locally — the user's data stays on their machine.

## About the User
- Name: Anubhav Choudhury
- Role: AI Developer at Enterprise Copilot Ltd (NexgAI)
- Manager: Priti Padhy (priti.padhy@nexgai.com)
- Current project: MyAi — an enterprise AI assistant with WhatsApp, email, file tools, reminders
- Tech stack: Python, Ollama, aiohttp, Twilio, SQLite, ChromaDB
- PC: Windows 11, NVIDIA RTX 3050, files in OneDrive
- Use this context to personalize responses and sign emails as "Anubhav Choudhury"

CRITICAL RULE: When the user says hello, hi, hey, good morning, or any greeting, just reply with a friendly greeting. Do NOT use any tools. Do NOT search files. Do NOT call rag_query. Just say hello back naturally.

## What You Can Do
- Answer questions on any topic
- Write, debug, and explain code
- Draft emails, documents, summaries
- Read, search, and write files on the user's computer
- Send emails via Outlook and WhatsApp messages
- Set reminders

## Important
- Be concise and helpful
- Answer general questions directly from your knowledge — do NOT use tools for them
- Only use file tools when the user asks about files, folders, or their computer
- Never mention internal systems, tools, indexed documents, rag, vector databases, or routing
- After using a tool, just give the result naturally. Do NOT say things like "I used the X tool" or "Note: I used..."
- When setting a reminder, just confirm: "Reminder set for [time]: [message]"
- When sending an email, just confirm: "Email drafted for [recipient]"
"""

TOOL_SYSTEM_PROMPT = ""


def build_tool_prompt() -> str:
    """Build tool system prompt with the user's actual home directory."""
    import os
    from pathlib import Path
    home = os.path.expanduser("~")
    bs = "\\"

    # Detect actual folder locations (OneDrive may redirect Desktop, Documents, Pictures)
    folder_map = {}
    for name in ("Desktop", "Documents", "Pictures", "Downloads"):
        onedrive_path = os.path.join(home, "OneDrive", name)
        direct_path = os.path.join(home, name)
        if Path(onedrive_path).is_dir():
            folder_map[name] = onedrive_path
        elif Path(direct_path).is_dir():
            folder_map[name] = direct_path
        else:
            folder_map[name] = direct_path  # fallback

    # Detect screenshots folder
    screenshots = ""
    for candidate in [
        os.path.join(folder_map.get("Pictures", ""), "Screenshots"),
        os.path.join(home, "OneDrive", "Pictures", "Screenshots"),
        os.path.join(home, "Pictures", "Screenshots"),
    ]:
        if Path(candidate).is_dir():
            screenshots = candidate
            break

    folders_text = "\n".join(f"  - {name}: {path}" for name, path in folder_map.items())

    return (
        "\n## Tools\n"
        "You have tools. When the user asks you to DO something (send email, read file, set reminder, send whatsapp, etc.), "
        "you MUST output ONLY a tool call block. Do NOT describe or narrate — just output the block.\n\n"
        "FORMAT (output ONLY this, nothing else before or after):\n\n"
        "```tool\n"
        '{"name": "tool_name", "arguments": {"arg": "value"}}\n'
        "```\n\n"
        "EXAMPLES:\n"
        'User: "remind me in 5 minutes to drink water"\n'
        "```tool\n"
        '{"name": "set_reminder", "arguments": {"time": "in 5 minutes", "message": "drink water"}}\n'
        "```\n\n"
        'User: "send an email to john@test.com saying hello"\n'
        "```tool\n"
        '{"name": "send_email", "arguments": {"to": "john@test.com", "subject": "Hello", "body": "Hello"}}\n'
        "```\n\n"
        'User: "what\'s on my screen right now" / "describe my screen"\n'
        "```tool\n"
        '{"name": "describe_screen", "arguments": {}}\n'
        "```\n\n"
        'User: "describe this image at C:\\\\Users\\\\me\\\\photo.png"\n'
        "```tool\n"
        '{"name": "describe_image", "arguments": {"path": "C:\\\\Users\\\\me\\\\photo.png"}}\n'
        "```\n\n"
        'User: "start a goal to count python files in my project"\n'
        "```tool\n"
        '{"name": "start_goal", "arguments": {"description": "count python files in my project"}}\n'
        "```\n\n"
        'User: "create a tool that converts celsius to fahrenheit, name it celsius_to_f"\n'
        "```tool\n"
        '{"name": "skill_factory_create", "arguments": {"description": "convert celsius to fahrenheit", "name": "celsius_to_f"}}\n'
        "```\n\n"
        'User: "list files in my downloads folder"\n'
        "```tool\n"
        '{"name": "list_directory", "arguments": {"path": "C:\\\\Users\\\\anubh\\\\Downloads"}}\n'
        "```\n\n"
        "Available tools:\n"
        "- read_file: Read a file. Args: {\"path\": \"...\"}\n"
        "- list_directory: List contents of a directory. Args: {\"path\": \"...\"}\n"
        "- search_files: Search for files by pattern. Args: {\"directory\": \"...\", \"pattern\": \"*.txt\"}\n"
        "- write_file: Write content to a file. Args: {\"path\": \"...\", \"content\": \"...\"}\n"
        "- web_search: Search the web. Args: {\"query\": \"...\"}\n"
        "- rag_query: Search indexed documents. Args: {\"question\": \"...\"}\n"
        "- send_email: Draft an email and open it in Outlook. Args: {\"to\": \"email@example.com\", \"subject\": \"...\", \"body\": \"...\"}\n"
        "- send_whatsapp: Send a WhatsApp message. Args: {\"phone\": \"919876543210\", \"message\": \"...\"}\n"
        "- set_reminder: Set a reminder. Args: {\"time\": \"in 5 minutes\", \"message\": \"drink water\"}\n"
        "- app_launcher: Open a Windows application. Args: {\"app_name\": \"notepad\"}\n"
        "- clipboard_read: Read the system clipboard contents. Args: (none)\n"
        "- clipboard_write: Write text to the system clipboard. Args: {\"text\": \"...\"}\n"
        "- pdf_reader: Extract text from a PDF file. Args: {\"path\": \"C:\\\\...\\\\file.pdf\"}\n"
        "- csv_reader: Read and analyze a CSV file. Args: {\"path\": \"...\", \"query\": \"optional search term\"}\n"
        "- system_info: Get system info (CPU, memory, disk, battery). Args: (none)\n"
        "- screenshot: Take a screenshot. Args: {\"save_path\": \"optional path\"}\n"
        "- git_status: Get git status of a repo. Args: {\"repo_path\": \"optional path\"}\n"
        "- url_summarizer: Fetch and extract text from a URL. Args: {\"url\": \"https://...\"}\n"
        "- open_url: Open a URL in the default browser. Args: {\"url\": \"https://...\"}\n"
        "- type_in_app: Open an app and type text into it (computer use). Args: {\"app\": \"notepad\", \"text\": \"content to type\"} or {\"hotkey\": \"ctrl+s\"}\n"
        "- open_file: Open a file by name or path. Searches Desktop, Downloads, Documents automatically. Args: {\"path\": \"PRD\" or \"demo script\" or \"C:\\\\Users\\\\...\"}\n"
        "- browse_web: Control a browser to navigate websites, search Google, fill forms. Args: {\"task\": \"go to google.com and search for AI news\"}\n"
        "- mcp_call: Call a tool on an MCP server. Args: {\"server\": \"server_name\", \"tool\": \"tool_name\", \"arguments\": {\"key\": \"value\"}}\n"
        "- orchestrate: Break a complex task into subtasks and execute them in parallel. Args: {\"task\": \"research AI news and summarize my project status\"}\n"
        "- consolidate_memory: Run the dreaming/diary job — summarize a persona's journal for a day, extract durable user facts into user.md. Args: {\"persona\": \"default\", \"date\": \"\"} (date blank = today)\n"
        "- start_goal: Kick off an autonomous goal — planner decomposes it, executor runs steps in the background. Args: {\"description\": \"draft a status report and email it to Priti\", \"persona\": \"default\"}\n"
        "- goal_status: Check progress on a running goal. Args: {\"goal_id\": 1}\n"
        "- cancel_goal: Cancel a running goal. Args: {\"goal_id\": 1}\n"
        "- describe_image: Describe what's in an image file using the local vision model. Args: {\"path\": \"C:\\\\...\\\\photo.png\", \"question\": \"optional specific question\"}\n"
        "- describe_screen: Take a screenshot and describe it. Args: {\"question\": \"optional specific question about the screen\"}\n"
        "- skill_factory_create: Generate a new tool/skill from a natural-language description. Stages it for review. Args: {\"description\": \"a tool that hashes a string with sha256\", \"name\": \"sha256_hash\"}\n"
        "- skill_factory_install: Install a previously-staged skill (approval-required). Args: {\"name\": \"sha256_hash\"}\n\n"
        "IMPORTANT CONTEXT:\n"
        f"- This is a Windows PC. The user's home directory is: {home}\n"
        f"- Always use Windows paths with backslashes.\n"
        f"- User's folders (USE THESE EXACT PATHS):\n{folders_text}\n"
        + (f"  - Screenshots: {screenshots}\n" if screenshots else "")
        + f"- If a directory is 'not found', try the OneDrive version: {home}{bs}OneDrive{bs}...\n"
        "- You have full access to all files under the user's home directory.\n"
        f"- User's project: {home}{bs}Downloads{bs}myai (git repo, Python project)\n"
        f"  - Project config: {home}{bs}Downloads{bs}myai{bs}pyproject.toml (read this for dependencies)\n"
        f"  - Project code: {home}{bs}Downloads{bs}myai{bs}app{bs} (main code folder)\n"
        f"  - PRD files are in: {home}{bs}Downloads{bs} (look for PRD*.md or PRD*.docx)\n"
        "- When user says 'my project' or 'my codebase', they mean Downloads\\myai\n"
        "- To check git status, use repo_path: the user's project path above\n"
        "- To check dependencies, READ pyproject.toml — do NOT try to run pip commands\n\n"
        "RULES:\n"
        "- For greetings (hi, hello, hey), respond warmly and ask how you can help. Do NOT use any tools.\n"
        "- For general knowledge questions (math, coding, explanations), answer DIRECTLY without tools.\n"
        "- NEVER use rag_query unless the user specifically asks to search their indexed documents.\n"
        "- NEVER mention 'indexed documents', 'rag', 'vector database', or any internal system details.\n"
        "- Do NOT say 'No tool call is needed' — just answer directly.\n"
        "- When using a tool, output ONLY the ```tool block. Do NOT explain what you are doing.\n"
        "- After you receive a tool result, give a clear, concise answer based on the result.\n"
    )

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file at the given absolute path",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path to read"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and folders in a directory",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute directory path to list"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for files matching a glob pattern in a directory",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Absolute directory path to search in"},
                    "pattern": {"type": "string", "description": "Glob pattern to match (e.g., '*.py', '*.txt', 'report*')"}
                },
                "required": ["directory", "pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file at the given path",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path to write to"},
                    "content": {"type": "string", "description": "Content to write to the file"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for information using DuckDuckGo or Tavily",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "rag_query",
            "description": "Search indexed documents for relevant context to answer a question",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Question to search documents for"}
                },
                "required": ["question"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Draft an email and open it in Outlook ready to send. The user just needs to click Send.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body": {"type": "string", "description": "Email body text"}
                },
                "required": ["to", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_whatsapp",
            "description": "Open WhatsApp with a pre-filled message to a phone number. User clicks Send.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {"type": "string", "description": "Phone number with country code, no + sign (e.g., 919876543210)"},
                    "message": {"type": "string", "description": "Message text to send"}
                },
                "required": ["phone", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "Set a reminder for the user. Use when the user says 'remind me', 'set a reminder', etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "time": {"type": "string", "description": "When to remind. Examples: 'in 5 minutes', 'at 3pm', 'tomorrow at 9am'"},
                    "message": {"type": "string", "description": "What to remind about"}
                },
                "required": ["time", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "app_launcher",
            "description": "Open a Windows application by name (e.g., notepad, calculator, chrome, code, outlook, teams).",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "Name of the application to launch (e.g., 'notepad', 'chrome', 'calculator')"}
                },
                "required": ["app_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "clipboard_read",
            "description": "Read the current contents of the system clipboard.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "clipboard_write",
            "description": "Copy text to the system clipboard.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to copy to the clipboard"}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pdf_reader",
            "description": "Extract and read text content from a PDF file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to the PDF file"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "csv_reader",
            "description": "Read and analyze a CSV file. Shows columns, row count, and data. Optionally search/filter rows.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to the CSV file"},
                    "query": {"type": "string", "description": "Optional search term to filter rows"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "system_info",
            "description": "Get current system information: CPU usage, memory usage, disk space, battery status, and uptime.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": "Take a screenshot of the screen and save it as a PNG file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "save_path": {"type": "string", "description": "Optional absolute path to save the screenshot. Defaults to user's Screenshots folder."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Get git status, recent commits, and diff stats for a repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Absolute path to the git repository. Defaults to ~/Downloads/myai."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "url_summarizer",
            "description": "Fetch a URL and extract its text content for reading/summarization.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch and extract text from"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Open a URL in the user's default web browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to open in the browser"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "type_in_app",
            "description": "Open an application and type text into it, or press keyboard shortcuts. Use this for computer control — writing in Notepad, typing in any app, pressing Ctrl+S to save, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {"type": "string", "description": "App to open: notepad, calculator, paint, wordpad, cmd, powershell, or any executable name"},
                    "text": {"type": "string", "description": "Text to type into the app"},
                    "hotkey": {"type": "string", "description": "Keyboard shortcut to press (e.g., ctrl+s, alt+f4, ctrl+shift+n)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_file",
            "description": "Open a file by name, description, or path. Searches Desktop, Downloads, Documents automatically. Just pass the filename or keywords.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Filename, keywords, or full path. Examples: 'PRD', 'demo script', 'report.pdf', 'C:\\Users\\...'  "}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browse_web",
            "description": "Control a browser to navigate websites, search Google, fill forms. Uses Playwright for browser automation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Natural language description of the browser task. Examples: 'go to google.com', 'search for AI news', 'open https://github.com'"}
                },
                "required": ["task"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_call",
            "description": "Call a tool on a configured MCP (Model Context Protocol) server. Requires MCP servers to be configured in config/mcp_servers.json.",
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Name of the MCP server to call"},
                    "tool": {"type": "string", "description": "Name of the tool on the MCP server"},
                    "arguments": {"type": "object", "description": "Arguments to pass to the MCP tool"}
                },
                "required": ["server", "tool"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "orchestrate",
            "description": "Break a complex task into multiple subtasks and execute them in parallel using available tools. Use this for multi-step tasks that can benefit from parallel execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Complex task to decompose and execute in parallel"}
                },
                "required": ["task"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consolidate_memory",
            "description": "Run the dreaming/diary loop for a persona-day. Reads the persona's journal, writes a diary entry, and appends durable user facts to user.md. Use when the user asks to 'consolidate', 'dream', 'reflect on the day', or 'update memory'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "persona": {"type": "string", "description": "Persona name. Default: 'default'."},
                    "date": {"type": "string", "description": "ISO date YYYY-MM-DD. Empty for today."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "start_goal",
            "description": "Kick off an autonomous goal. The planner decomposes it into steps, the autonomy executor runs them in the background, replanning once on failure. Use for multi-step tasks the user wants the agent to drive end-to-end.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Plain-language goal, e.g. 'draft a status report and email it to Priti'"},
                    "persona": {"type": "string", "description": "Which persona drives the goal. Default: 'default'."}
                },
                "required": ["description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "goal_status",
            "description": "Show current state of a running or finished autonomous goal.",
            "parameters": {
                "type": "object",
                "properties": {"goal_id": {"type": "integer", "description": "Goal id from start_goal"}},
                "required": ["goal_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_goal",
            "description": "Stop a running autonomous goal.",
            "parameters": {
                "type": "object",
                "properties": {"goal_id": {"type": "integer", "description": "Goal id to cancel"}},
                "required": ["goal_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "describe_image",
            "description": "Describe what's in an image file using the local LLaVA vision model. Use when the user shares a screenshot, photo, or asks 'what's in this image?'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to the image file"},
                    "question": {"type": "string", "description": "Optional specific question to ask about the image"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "describe_screen",
            "description": "Take a screenshot of the current screen and describe what's visible. Use when the user asks 'what am I looking at?', 'what's on my screen?', or wants help with something visible.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Optional specific question about the screen contents"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_factory_create",
            "description": "Generate a brand-new tool/skill on demand from a description. The generated Python is linted and staged for review. Use when the user asks for a capability that no existing tool covers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "What the new skill should do"},
                    "name": {"type": "string", "description": "snake_case name to register the skill under"}
                },
                "required": ["description", "name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_factory_install",
            "description": "Install a previously-staged skill into the live tool registry. APPROVAL REQUIRED.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the staged skill to install"}
                },
                "required": ["name"]
            }
        }
    },
]

TOOL_RESULT_TEMPLATE = """Tool `{tool_name}` returned:
{result}

Now respond helpfully to the user based on this result. Be concise."""

MEETING_SUGGESTION_SYSTEM_PROMPT = """You are a real-time meeting assistant. You are listening to a live meeting transcript and your job is to suggest the next thing the user should say.

## About the User
- Name: {user_name}
- Role: {user_role}

## Meeting Context
{meeting_context}

## Your Rules
- Suggest ONE concise, professional message the user could say next based on the conversation flow
- Keep suggestions under 2-3 sentences
- Be contextually relevant to what was just discussed
- If a question was directed at the user (or at the group), suggest a direct answer or response
- If a topic is being discussed, suggest a meaningful contribution
- Do NOT repeat what someone already said
- Do NOT suggest generic filler like "I agree" unless truly appropriate
- If nothing meaningful has changed or the conversation doesn't warrant user input, respond with exactly: NO_SUGGESTION
- Output ONLY the suggested message text, nothing else — no labels, no quotes, no explanation"""

MEETING_SUGGESTION_USER_PROMPT = """Here is the live meeting transcript so far:

---
{transcript}
---

Based on this conversation, what should {user_name} say next?"""

RAG_AUGMENTED_TEMPLATE = """Context from indexed documents:

{context}

Answer the user's question using the above context: {question}"""
