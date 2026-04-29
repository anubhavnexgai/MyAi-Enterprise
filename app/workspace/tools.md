# Tools — usage guidelines

The full tool catalogue and exact JSON schemas are injected automatically by
`prompts.build_tool_prompt()`. This file is for the *judgement* parts that the
schema can't express.

## When to use a tool vs. answer directly

- **Greetings, general knowledge, code questions, explanations:** answer
  directly. Do not call `rag_query` or `web_search` for things you already
  know.
- **Anything touching the user's machine** (files, apps, clipboard,
  screenshots, system info): use the tool. Don't guess.
- **Anything time-sensitive or recent** (news, prices, live data): use
  `web_search` or `url_summarizer`.
- **Anything destructive** (delete, overwrite, send to a third party): the
  guardrails layer will route it through the approval queue. Just call the
  tool — don't try to bypass.

## Tool selection hints

- `open_file` over `read_file` when the user gives a name/keyword instead of a
  full path. `open_file` searches Desktop / Downloads / Documents.
- `type_in_app` for "write X in Notepad" style requests — `app_launcher` only
  opens the app, it doesn't type.
- `browse_web` for "go to / fill / click" tasks. `open_url` only opens a URL,
  it doesn't interact.
- `orchestrate` for genuinely multi-step tasks. A single tool call doesn't
  need orchestration.
- `mcp_call` only when an MCP server is configured for that capability — check
  before calling.

## Forbidden patterns

- Calling `rag_query` "just in case" — only when the user explicitly asks
  about their indexed documents.
- Calling `web_search` for facts you know.
- Chaining `read_file` → `write_file` to "edit" a file when the user asked
  for a one-line change. Use `type_in_app` or open the file in an editor.

## Unknown tool? Don't fake it.

If you want to do something there's no tool for, **say so** instead of
narrating a fake action. The skill-factory will eventually be able to write
new tools on the fly — for now, escalate to the user.
