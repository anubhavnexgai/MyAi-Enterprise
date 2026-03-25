# MyAi — AgentHub Integration Implementation Plan

**Author:** Anubhav Choudhury
**Date:** March 25, 2026
**Status:** Draft
**Ref:** My AI_AgentHub Integration Plan.docx (Section 8 — External AI Assistant)

---

## 1. Current State (What's Already Built)

MyAi already has a working NexgAI integration layer that will serve as the foundation:

| Component | Status | Location |
|-----------|--------|----------|
| Service auth (local mode + SSO) | Working | `app/services/nexgai_client.py` |
| Health check | Working | `GET /health` → checks NexgAI |
| Session creation | Working | Generates UUID locally |
| Message routing (v3 chat API) | Working | `POST /api/v3/chat` |
| Circuit breaker (3 failures → 60s cooldown) | Working | `CircuitBreaker` class |
| Ollama fallback | Working | `AgentCore._chat()` |
| Stub response filtering | Working | Filters generic "I'm here to help" |
| Streaming (SSE → WebSocket) | Built | `process_message_streaming()` |

**What needs to change:** Migrate from v3 chat API to the new external gateway (`/external/agenthub/*`).

---

## 2. Deliverables (Section 8 of Integration Plan)

### 2.1 Service-to-Service Authentication

**Current:** MyAi uses local mode (no auth) or SSO service-login.
**Target:** Tenant-scoped API key from AgentHub.

**Implementation:**

```
Phase 1: API Key Auth
├── Store API key in .env: AGENTHUB_API_KEY=...
├── Send on every request: Authorization: Bearer <api_key>
├── Include X-Tenant-ID header
└── Handle 401 → re-authenticate or alert admin

Phase 2: External Bearer Token (future)
├── Request short-lived JWT from AgentHub token endpoint
├── Cache token, refresh before expiry
└── Fall back to API key if token endpoint unavailable
```

**Files to modify:**
- `app/services/nexgai_client.py` → add `_api_key_auth()` method
- `app/config.py` → add `agenthub_api_key` setting
- `.env` → add `AGENTHUB_API_KEY`

**Estimated effort:** 2-3 hours

---

### 2.2 End-User Context Passing

Every request to AgentHub must include the end user's identity and role.

**Request payload:**
```json
{
  "user_id": "823784aa-9ffa-...",
  "tenant_id": "enterprise_copilot",
  "roles": ["admin"],
  "session_id": "myai-abc123",
  "message": "check compliance status",
  "context": {
    "source": "web_ui",
    "user_name": "Anubhav Choudhury",
    "user_email": "anubhav.choudhury@nexgai.com"
  }
}
```

**Implementation:**
- Extract user info from `auth_user` object in WebSocket handler
- Map MyAi roles (super_admin, admin, manager, employee) to AgentHub roles
- Pass `session_id` from MyAi's conversation system
- For WhatsApp users, pass phone number as user_id with "whatsapp" source

**Files to modify:**
- `app/services/nexgai_client.py` → update `send_message()` payload
- `app/agent/core.py` → pass `user` object to `_try_nexgai()`

**Estimated effort:** 2-3 hours

---

### 2.3 Agent Discovery Integration

**Flow:**
```
MyAi starts → GET /external/agenthub/agents
                → Cache allowed agents per user
                → Display in sidebar under "Enterprise Skills"
                → Refresh every 5 minutes or on role change
```

**Implementation:**

```python
# New method in nexgai_client.py
async def discover_agents(self, user_id: str, tenant_id: str, roles: list[str]) -> list[dict]:
    """Fetch agents the user is allowed to access."""
    resp = await self._request("GET", "/external/agenthub/agents", headers={
        "Authorization": f"Bearer {self._api_key}",
        "X-Tenant-ID": tenant_id,
        "X-User-ID": user_id,
        "X-User-Roles": ",".join(roles),
    })
    return resp.get("agents", [])
```

**Agent metadata to cache:**
- agent_id, name, description
- capabilities/tools (for smart routing)
- authority_level

**Display in UI:**
- Replace hardcoded "Enterprise Skills" section in sidebar
- Show agent name + description
- Grey out agents user can't access

**Files to modify:**
- `app/services/nexgai_client.py` → add `discover_agents()`
- `app/main.py` → call on WebSocket auth, cache per user
- `web/app.js` → render agent list dynamically
- `web/index.html` → update sidebar section

**Estimated effort:** 4-5 hours

---

### 2.4 Agent Invocation Integration

**Flow:**
```
User message → MyAi intent classification
  ├── General question → Ollama (local)
  ├── File/email/reminder → Pre-intercept (local)
  └── Specialized task → AgentHub invoke
       ├── POST /external/agenthub/agents/{agent_id}/invoke
       ├── Wait for response (with timeout)
       ├── If 403 → "You don't have access to this capability"
       ├── If timeout → fallback to Ollama
       └── Return response to user
```

**Smart routing (which agent to invoke):**

Option A — Let AgentHub decide (recommended for Phase 1):
```
POST /external/agenthub/chat
{
  "message": "check compliance status",
  "session_id": "...",
  ...
}
→ AgentHub orchestrator picks the right agent
```

Option B — MyAi decides (Phase 2):
```
1. MyAi asks Ollama: "Which agent should handle this? Options: [service_agent, compliance_agent, hr_agent]"
2. Ollama returns: "compliance_agent"
3. MyAi invokes: POST /external/agenthub/agents/compliance_agent/invoke
```

**Implementation:**

```python
# Updated _try_nexgai in core.py
async def _try_nexgai(self, user_id, user_name, text, user=None):
    # 1. Check if AgentHub is available
    if not self.nexgai or not self.nexgai.is_available:
        return None

    # 2. Get user's allowed agents
    agents = await self.nexgai.get_cached_agents(user_id)

    # 3. Route to AgentHub (let it pick the agent)
    result = await self.nexgai.invoke_chat(
        message=text,
        user_id=user_id,
        tenant_id=user.tenant_id if user else "default",
        roles=user.roles if user else ["employee"],
        session_id=session_id,
    )

    # 4. Handle response
    if result.get("status") == "forbidden":
        return "You don't have access to this capability."
    if not result.get("response"):
        return None  # fallback to Ollama

    return f"_Handled by *{result.get('agent_used')}*_\n\n{result['response']}"
```

**Async support for WhatsApp:**
- Add `callback_url` parameter to invoke request
- AgentHub sends result to callback when ready
- Or use the existing background processing pattern

**Files to modify:**
- `app/services/nexgai_client.py` → add `invoke_chat()`, `invoke_agent()`
- `app/agent/core.py` → update `_try_nexgai()`
- `app/main.py` → pass user context through

**Estimated effort:** 6-8 hours

---

### 2.5 Failure Handling

**Matrix:**

| Error | MyAi Response | Action |
|-------|--------------|--------|
| 401 Unauthorized | "AgentHub connection issue" | Re-authenticate, alert admin |
| 403 Forbidden | "You don't have access to this capability" | Log, show to user |
| 404 Agent not found | Fall back to Ollama | Log warning |
| 429 Rate limited | "Too many requests, try again shortly" | Exponential backoff |
| 500 Server error | Fall back to Ollama | Circuit breaker increment |
| Timeout (>30s) | Fall back to Ollama | Circuit breaker increment |
| Network error | Fall back to Ollama | Circuit breaker opens after 3 failures |

**Circuit breaker (already built):**
- 3 consecutive failures → open for 60 seconds
- During open: all requests go directly to Ollama
- After cooldown: half-open, allow 1 request to test
- On success: close circuit, resume normal operation

**User-facing messages:**
- Never show technical errors
- Always have a fallback answer via Ollama
- Log everything for debugging

**Estimated effort:** 2-3 hours (mostly already built)

---

### 2.6 Session Management

**Current state:** MyAi creates UUID sessions (`myai-{uuid}`), stores in database.

**Target:** Consistent session_id passed to AgentHub for multi-turn continuity.

**Implementation:**
```
User opens MyAi → New conversation created (UUID)
  → Same UUID used as session_id for AgentHub
  → AgentHub maintains state per session
  → User switches conversation → different session_id
  → WhatsApp gets its own session (wa-conversation-id)
```

**Session lifecycle:**
1. Created when user starts a new chat
2. Passed on every AgentHub request
3. AgentHub maintains agent state per session
4. Session expires after 24 hours of inactivity
5. User can clear session via "New Chat" button

**Files to modify:**
- `app/services/nexgai_client.py` → pass conversation_id as session_id
- `app/agent/core.py` → thread conversation_id through

**Estimated effort:** 2-3 hours

---

## 3. Implementation Timeline

| Week | Deliverable | Depends On |
|------|-------------|------------|
| Week 1 | 2.1 Service auth + 2.2 User context | Aditya's gateway deployed |
| Week 1 | 2.5 Failure handling (already 80% done) | — |
| Week 2 | 2.3 Agent discovery + UI integration | Gateway API available |
| Week 2 | 2.6 Session management | — |
| Week 3 | 2.4 Agent invocation + routing | Discovery working |
| Week 3 | End-to-end testing | All above |
| Week 4 | WhatsApp async invoke + polish | Invocation working |

**Total estimated effort: 20-25 hours across 4 weeks**

---

## 4. New Ideas (Beyond Scope Doc)

### 4.1 Agent Capability Discovery
The discovery response should include tool/capability metadata:
```json
{
  "agent_id": "compliance_agent",
  "capabilities": ["check_compliance", "generate_report", "audit_trail"],
  "input_schema": { "required": ["entity_id"], "optional": ["report_type"] }
}
```
This lets MyAi do smarter routing without trial-and-error.

### 4.2 Context Forwarding
Pass MyAi's local context to agents for personalized responses:
```json
{
  "context": {
    "recent_files": ["sprint-review.md", "demo-notes.md"],
    "active_reminders": ["push code at 5pm"],
    "last_briefing": "22 uncommitted changes in Git repo"
  }
}
```

### 4.3 Bidirectional Events
AgentHub should push notifications TO MyAi:
- Agent completed a background task
- Compliance alert triggered
- New document requires review
- Implement via WebSocket connection from MyAi to AgentHub

### 4.4 Multi-Agent Orchestration
One user query triggers multiple agents in parallel:
```
User: "Prepare for the client meeting tomorrow"
  → Calendar agent: fetches meeting details
  → Email agent: finds related email threads
  → Document agent: locates relevant files
  → MyAi merges results into one briefing
```

### 4.5 Hybrid Local+Remote Intelligence
MyAi as the smart router:
- Simple tasks: handled locally (fast, private)
- Specialized tasks: routed to AgentHub (powerful, governed)
- Critical tasks: double-checked by both
- This is already how MyAi works — formalize it as architecture

---

## 5. Questions for Aditya

1. When will the gateway be deployed for dev testing?
2. Will `/external/agenthub/chat` (auto-routing) be available in Phase 1?
3. What's the API key onboarding process? Self-service or manual?
4. Will agent discovery include capability/tool metadata?
5. What are the actual rate limits per tier?
6. Is SSE streaming supported on the invoke endpoint?
7. Can we get a sandbox/staging environment for integration testing?

---

## 6. Files to Create/Modify

### New files:
- `app/services/agenthub_client.py` — new client replacing nexgai_client for gateway API
- `config/agenthub_policy.yaml` — local cache of agent access policies

### Modified files:
- `app/config.py` — add agenthub settings
- `app/agent/core.py` — update routing logic
- `app/main.py` — pass user context, agent discovery on auth
- `web/app.js` — dynamic agent list in sidebar
- `web/index.html` — sidebar agent section
- `.env` / `.env.example` — new env vars

---

*This plan builds on MyAi's existing integration layer (NexgAI client, circuit breaker, fallback) and extends it to comply with the governed gateway model defined in the Integration Plan.*
