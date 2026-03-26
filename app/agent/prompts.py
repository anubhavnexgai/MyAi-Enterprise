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
        "- open_url: Open a URL in the default browser. Args: {\"url\": \"https://...\"}\n\n"
        "IMPORTANT CONTEXT:\n"
        f"- This is a Windows PC. The user's home directory is: {home}\n"
        f"- Always use Windows paths with backslashes.\n"
        f"- User's folders (USE THESE EXACT PATHS):\n{folders_text}\n"
        + (f"  - Screenshots: {screenshots}\n" if screenshots else "")
        + f"- If a directory is 'not found', try the OneDrive version: {home}{bs}OneDrive{bs}...\n"
        "- You have full access to all files under the user's home directory.\n\n"
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
