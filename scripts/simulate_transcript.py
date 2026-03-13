"""Simulate live meeting transcript being fed to the bot.

Usage:
    python scripts/simulate_transcript.py [--host HOST] [--delay SECONDS] [--context FILE]

Sends fake transcript chunks to the running bot. The bot generates suggestions
via Ollama and returns them in the response (no Teams delivery needed for testing).
"""

import argparse
import asyncio

import httpx

SAMPLE_TRANSCRIPT_CHUNKS = [
    # Chunk 1: Meeting opens
    (
        "Priya: Good morning everyone, let's get started with the weekly sync.\n"
        "Priya: First item on the agenda — the API migration. Raj, where are we on that?"
    ),
    # Chunk 2: Discussion
    (
        "Raj: We've migrated about 70% of the endpoints. The payments module is the tricky one — it has a lot of legacy dependencies.\n"
        "Priya: What's the timeline looking like? The client demo is next Thursday."
    ),
    # Chunk 3: Question directed at user
    (
        "Raj: I think we can finish payments by Monday if we get help from the frontend team on the webhook changes.\n"
        "Priya: Anubhav, you've been working on the frontend side — can your team handle the webhook integration by Monday?"
    ),
    # Chunk 4: More discussion
    (
        "Raj: Also, we found a race condition in the order processing flow. It's not critical but we should fix it before the demo.\n"
        "Priya: Can you file a ticket for that? And what about the database migration — is that done?"
    ),
    # Chunk 5: Another question to user
    (
        "Raj: Database migration is complete. All the staging tests pass. We just need someone to review the rollback procedure.\n"
        "Priya: Anubhav, could you review the rollback procedure since you set up the original schema?"
    ),
]


async def send_chunk(base_url: str, chunk: str) -> dict:
    """Send transcript text to the simulate endpoint and return the response."""
    url = f"{base_url.rstrip('/')}/api/simulate-transcript"
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            url,
            json={"transcript_text": chunk},
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code == 404:
            return {"error": "No active session. Run /join first in Teams."}
        return resp.json()


async def check_sessions(base_url: str) -> dict:
    url = f"{base_url.rstrip('/')}/api/debug/sessions"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        return resp.json()


async def main():
    parser = argparse.ArgumentParser(description="Simulate live meeting transcript")
    parser.add_argument("--host", default="http://localhost:8000", help="Bot server URL")
    parser.add_argument("--delay", type=float, default=5.0, help="Seconds between chunks")
    parser.add_argument("--context", type=str, default="", help="Path to a .txt context file")
    args = parser.parse_args()

    print(f"Target: {args.host}")

    # Check for active sessions
    sessions = await check_sessions(args.host)
    active = sessions.get("active_sessions", [])
    if not active:
        print("\nERROR: No active meeting session!")
        print("1. Start the bot: python -m app.main")
        print("2. In Teams chat with MyAi: /join <meeting-url>")
        print("3. Then re-run this script")
        return

    print(f"Active sessions: {len(active)}")
    for s in active:
        cid = s.get('call_id') or '(none)'
        print(f"  - {cid[:16]}... user={s.get('user_name', '?')} role={s.get('user_role', '?')}")
        print(f"    transcript lines: {s.get('transcript_line_count', 0)}, ref: {bool(s.get('conversation_reference', {}).get('service_url'))}")

    chunks = list(SAMPLE_TRANSCRIPT_CHUNKS)

    # Prepend context file if provided
    if args.context:
        try:
            with open(args.context) as f:
                context_text = f.read().strip()
            print(f"\nLoaded context from {args.context} ({len(context_text)} chars)")
            chunks.insert(0, f"[Meeting Context]\n{context_text}")
        except FileNotFoundError:
            print(f"Warning: context file not found: {args.context}")

    print(f"\nSending {len(chunks)} transcript chunks (delay={args.delay}s)...\n")

    for i, chunk in enumerate(chunks):
        print(f"{'='*60}")
        print(f"CHUNK {i + 1}/{len(chunks)}")
        print(f"{'='*60}")
        for line in chunk.strip().splitlines():
            print(f"  {line}")

        result = await send_chunk(args.host, chunk)

        if "error" in result:
            print(f"\n  ERROR: {result['error']}")
            return

        for r in result.get("results", []):
            suggestion = r.get("suggestion", "")
            print(f"\n  Lines in session: {r.get('total_lines', '?')}")
            print(f"  SUGGESTION: {suggestion}")
            has_ref = bool(r.get("conversation_ref", {}).get("service_url"))
            print(f"  Teams delivery: {'enabled' if has_ref else 'DISABLED (no conversation ref)'}")

        if i < len(chunks) - 1:
            print(f"\n  Waiting {args.delay}s...")
            await asyncio.sleep(args.delay)

    print(f"\n{'='*60}")
    print("Done! If Teams delivery is enabled, suggestions were also sent to your Teams chat.")


if __name__ == "__main__":
    asyncio.run(main())
