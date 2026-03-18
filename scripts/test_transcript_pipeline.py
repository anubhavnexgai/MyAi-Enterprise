"""Diagnose whether the bot is running and can process transcripts.

Usage:
    python scripts/test_transcript_pipeline.py
"""

import asyncio
import sys
sys.path.insert(0, ".")

import httpx


async def main():
    print("=" * 60)
    print("TRANSCRIPT PIPELINE DIAGNOSTIC (Slack)")
    print("=" * 60)

    base_url = "http://localhost:8001"

    # Step 1: Check health
    print(f"\n[1] Checking bot health at {base_url}...")
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{base_url}/health", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                print(f"  OK: status={data.get('status')}, ollama={data.get('ollama')}, model={data.get('model')}")
            else:
                print(f"  FAIL: {resp.status_code}")
                return
        except Exception as e:
            print(f"  FAIL: Bot not reachable -- {e}")
            print(f"  Start the bot: python -m app.main")
            return

    # Step 2: Check active sessions
    print(f"\n[2] Checking active transcript sessions...")
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{base_url}/api/debug/sessions")
        data = resp.json()
        sessions = data.get("active_sessions", [])
        if not sessions:
            print("  WARN: No active transcript sessions")
            print("  Start one in Slack: /transcript start My Meeting")
        else:
            for s in sessions:
                print(f"  Session: call_id={s.get('call_id', '?')[:16]}...")
                print(f"    user: {s.get('user_name')}")
                print(f"    subject: {s.get('meeting_subject')}")
                print(f"    transcript_lines: {s.get('transcript_line_count', 0)}")
                channel = s.get('conversation_reference', {}).get('channel_id')
                print(f"    slack_channel: {channel or 'NOT SET'}")

    # Step 3: Check Slack env vars
    print(f"\n[3] Checking Slack configuration...")
    from app.config import settings
    has_bot_token = bool(settings.slack_bot_token and settings.slack_bot_token.startswith("xoxb-"))
    has_app_token = bool(settings.slack_app_token and settings.slack_app_token.startswith("xapp-"))
    has_signing = bool(settings.slack_signing_secret)

    print(f"  SLACK_BOT_TOKEN: {'OK' if has_bot_token else 'MISSING or invalid (needs xoxb-...)'}")
    print(f"  SLACK_APP_TOKEN: {'OK' if has_app_token else 'MISSING or invalid (needs xapp-...)'}")
    print(f"  SLACK_SIGNING_SECRET: {'OK' if has_signing else 'MISSING'}")

    if not has_bot_token or not has_app_token:
        print(f"\n  To set up Slack:")
        print(f"  1. Go to api.slack.com/apps and create/select your app")
        print(f"  2. Enable Socket Mode -> get an App-Level Token (xapp-...)")
        print(f"  3. OAuth & Permissions -> install to workspace -> get Bot Token (xoxb-...)")
        print(f"  4. Event Subscriptions -> subscribe to: message.im, app_mention")
        print(f"  5. Add these to your .env file")

    # Step 4: Test simulate endpoint
    print(f"\n[4] Testing simulate endpoint...")
    if sessions:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{base_url}/api/simulate-transcript",
                json={"transcript_text": "Test: Hello, can everyone hear me?"},
            )
            if resp.status_code == 200:
                data = resp.json()
                for r in data.get("results", []):
                    print(f"  OK: Got suggestion ({len(r.get('suggestion', ''))} chars)")
                    print(f"  Preview: {r.get('suggestion', '')[:100]}")
            else:
                print(f"  WARN: {resp.status_code} -- {resp.text[:200]}")
    else:
        print("  SKIP: No active sessions to test with")

    print(f"\n{'=' * 60}")
    print(f"SUMMARY")
    print(f"{'=' * 60}")
    print(f"For transcript suggestions to work:")
    print(f"  1. Bot is running .................. python -m app.main")
    print(f"  2. Slack tokens configured ......... check .env file")
    print(f"  3. Ollama is running ............... ollama serve")
    print(f"  4. Start a session ................. /transcript start My Meeting")
    print(f"  5. Paste transcript text ........... /transcript paste <text>")


if __name__ == "__main__":
    asyncio.run(main())
