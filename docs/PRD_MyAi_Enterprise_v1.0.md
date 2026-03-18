# MyAi Enterprise Platform — Product Requirements Document

**Version:** 1.3
---

## 1. North Star Vision

**MyAi becomes the single AI-powered operating layer for the enterprise** — every employee, from intern to CEO, talks to one assistant that understands their role, knows their data, learns from every interaction, and orchestrates specialized AI agents on their behalf.

The end state:
- A new employee joins, logs in, and MyAi already knows their role, department, and what tools they need.
- An HR manager asks "show me attrition trends this quarter" and gets a chart pulled live from the HRIS database, analyzed by the APOLLO agent, and narrated by the LLM — all in one response.
- A department head creates a "Customer Success" agent from **Agent Hub** in 5 minutes, connects it to the CRM, and every employee using MyAi can access it immediately.
- The platform gets smarter every week. Corrections feed back into prompts. Highly-rated answers become knowledge base entries. Low-performing agents get flagged for review.
- All of this runs **on-premise** — no data leaves the organization's infrastructure. The AI workforce is fully owned.

**Integration with NexgAI:** MyAi is the employee-facing front door to the NexgAI AI Workforce platform. NexgAI provides the specialized agent framework; **Agent Hub** is the control centre for creating, configuring, and managing all agents; MyAi provides the unified chat interface, analytics, and learning loop that makes those agents accessible to every employee.

```
  Agent Hub (Control Centre)          MyAi (Employee Interface)
  ┌──────────────────────┐           ┌──────────────────────┐
  │ Create agents        │           │ Chat UI              │
  │ Configure prompts    │◄────────►│ All agents available  │
  │ Manage data sources  │  API/WS   │ Usage analytics      │
  │ Set agent personas   │           │ Self-learning loop   │
  │ Monitor performance  │           │ Employee auth        │
  └──────────────────────┘           └──────────────────────┘
```

---

### MVP Scope (v1.0 — Phases 1 + 2)

| Capability | What Ships | What Doesn't |
|-----------|-----------|-------------|
| **Auth & Access** | Email/password login, 4-tier role hierarchy, session management | SSO/SAML, 2FA, LDAP/AD sync |
| **Admin Dashboard** | Real-time analytics (messages, users, skills, errors, system health), user management (role change, activate/deactivate), conversation volume charts | Custom date pickers, exportable reports, audit log downloads |
| **Enterprise Skills** | 8 built-in agents (VULCAN, VESTA, MIDAS, MINERVA, EKLAVYA, FALCON, APOLLO, JANUS) — all accessible to every user | Agent Hub integration, dynamic agent loading |
| **Web UI** | Login/setup flow, WebSocket chat, sidebar with status/skills, admin dashboard page | Mobile-responsive design, dark/light theme toggle |
| **Microsoft 365** | OAuth2 calendar/email/files via Graph API | SharePoint document indexing, Teams channel integration |
| **Data Layer** | SQLite with usage_events logging, ChromaDB for RAG | Company database connectors, encrypted data source configs |
| **Infrastructure** | Single-server deployment, `--web-only` mode, Ollama local LLM | Docker compose, systemd service, load balancing |

---

## 2. Problem Statement

Organizations need an AI assistant that:
- Serves **all employees** with a single unified interface
- Provides **management visibility** into how AI is being used across the organization
- **Improves over time** by learning from user interactions and feedback
- Connects to **specialized agents** managed centrally via Agent Hub
- Connects to **company data sources** (databases, SharePoint, file servers) securely
- Runs **locally** — no data leaves the organization's infrastructure

---

## 3. Target Users

| Role | Description | Access Level |
|------|-------------|-------------|
| **Super Admin** | IT/Platform owner who deploys and manages MyAi | Full access: dashboard, user management, data sources, system config |
| **Admin** | Department heads, IT managers | Dashboard view, user management |
| **Manager** | Team leads | All skills, file access, team activity view |
| **Employee** | General workforce | All skills, read-only file access |

**Note:** All users have access to all skills/agents. Skill access control is not role-gated — every employee can use every available agent. Agent configuration, creation, and lifecycle management happens in **Agent Hub**, not in MyAi.

---

## 4. Phased Delivery Roadmap

### Phase 0: Foundation — DONE

| Version | Scope | Status |
|---------|-------|--------|
| 0.1 | Slack migration, core agent framework | DONE |
| 0.2 | Enterprise skills framework (8 agents), confidence-based routing | DONE |
| 0.3 | Microsoft Graph integration, Web UI (WebSocket chat) | DONE |

### Phase 1: Authentication & Access Control — DONE

**Priority:** P0 (Blocker for all other phases)

#### 1.1 User Authentication
- **FR-1.1:** Web UI login screen (email + password) before granting access
- **FR-1.2:** First-time setup flow creates the initial Super Admin account
- **FR-1.3:** Sessions use opaque UUID tokens with 24-hour expiry
- **FR-1.4:** Passwords hashed with bcrypt
- **FR-1.5:** Logout invalidates the session token server-side
- **FR-1.6:** Slack users auto-provisioned as `employee` role on first message

#### 1.2 Role Hierarchy
- **FR-1.7:** Four role levels: Super Admin > Admin > Manager > Employee
- **FR-1.8:** Higher roles inherit all permissions of lower roles
- **FR-1.9:** Super Admins can promote/demote any user
- **FR-1.10:** Admins can manage users up to Manager level

#### 1.3 Skill Access
- **FR-1.11:** All skills/agents are accessible to every authenticated user regardless of role
- **FR-1.12:** Role hierarchy only controls admin features (dashboard, user management) and file write access — not skill access

#### 1.4 File Access Policies
- **FR-1.14:** File access policies are per-role, not global
- **FR-1.15:** Policies specify directory path + access type (read/write)
- **FR-1.16:** Super Admin configures file policies via admin dashboard

### Phase 2: Super Admin Dashboard — DONE

**Priority:** P0

#### 2.1 Analytics Overview
- **FR-2.1:** Real-time metrics: total messages, active users, skill executions, avg response time, error rate
- **FR-2.2:** Conversation volume chart (hourly/daily) with configurable time period
- **FR-2.3:** Response time distribution (P50, P75, P90, P95, P99)

#### 2.2 Skill Metrics
- **FR-2.4:** Per-skill: execution count, avg confidence, avg response time, success rate, thumbs up/down
- **FR-2.5:** Skill routing distribution (which agents handle the most requests)

#### 2.3 User Management
- **FR-2.6:** User list with name, email, role, message count, last active
- **FR-2.7:** Role change via dropdown (admin+)
- **FR-2.8:** Activate/deactivate user accounts
- **FR-2.9:** Deactivated users cannot log in or send messages

#### 2.4 Error Monitoring
- **FR-2.10:** Error log: timestamp, user, event type, skill, error message
- **FR-2.11:** Error rate tracked over time

#### 2.5 System Health
- **FR-2.12:** Health panel: Ollama status, NexgAI status, database size, total users, conversations, index count, uptime

#### 2.6 Usage Event Logging
- **FR-2.13:** Every interaction logged to `usage_events` table (message, skill_execution, tool_execution, nexgai_execution, nexgai_stream, llm_conversation, error)
- **FR-2.14:** Events include: user_id, skill_name, confidence, response_time_ms, success, error_message, metadata

### Phase 3: Company Data Connection — DONE

**Priority:** P1
**Target:** v1.1

#### 3.1 Data Source Types
- **FR-3.1:** Local directory connector — index files from server filesystem
- **FR-3.2:** SQL database connector — read-only queries against company databases (Postgres, MySQL, SQLite)
- **FR-3.3:** SharePoint connector — access documents via Microsoft Graph
- **FR-3.4:** REST API connector — configurable auth (API key, Bearer token, OAuth)

#### 3.2 Document Ingestion
- **FR-3.5:** Batch indexing of documents into ChromaDB for RAG
- **FR-3.6:** Supported formats: PDF, Word (.docx), plain text, Markdown
- **FR-3.7:** Incremental re-indexing using file hash change detection
- **FR-3.8:** Background indexing that doesn't block the main event loop

#### 3.3 Access Control
- **FR-3.9:** Each data source has a minimum role level for access
- **FR-3.10:** RAG queries filter results by user's accessible data sources
- **FR-3.11:** Data source connection configs encrypted at rest (Fernet)

#### 3.4 Admin UI
- **FR-3.12:** Data source management page in admin dashboard
- **FR-3.13:** Add/edit/delete data sources with connection test
- **FR-3.14:** Indexing status and document count per source

### Phase 3.5: NexgAI Platform Integration — DONE

**Priority:** P1
**Target:** v1.1

MyAi connects to the NexgAI Enterprise Agentization Platform as the employee-facing chat interface. NexgAI provides 24+ specialized agents with Plan→Execute→Critique→Dialogue patterns; MyAi provides auth, analytics, and the unified chat experience.

#### 3.5.1 Service Account Authentication
- **FR-3.5.1:** MyAi authenticates to NexgAI as a service account on startup
- **FR-3.5.2:** JWT access tokens auto-refreshed before expiry
- **FR-3.5.3:** 10 configurable settings via environment variables (NEXGAI_ENABLED, NEXGAI_BASE_URL, NEXGAI_TENANT_ID, NEXGAI_SERVICE_USER, NEXGAI_SERVICE_PASSWORD, NEXGAI_TIMEOUT, NEXGAI_STREAM_TIMEOUT, NEXGAI_CIRCUIT_BREAKER_THRESHOLD, NEXGAI_CIRCUIT_BREAKER_COOLDOWN, NEXGAI_AGENT_CACHE_TTL)

#### 3.5.2 2-Way Routing
- **FR-3.5.4:** Message routing: NexgAI Platform agents → Ollama LLM fallback (general questions)
- **FR-3.5.5:** NexgAI is the primary handler for all specialized tasks; Ollama serves as the general-purpose fallback
- **FR-3.5.6:** Per-user NexgAI session mapping stored in SQLite (`nexgai_sessions` table)

#### 3.5.3 SSE-to-WebSocket Streaming Bridge
- **FR-3.5.7:** NexgAI streams responses via Server-Sent Events (SSE)
- **FR-3.5.8:** MyAi relays SSE chunks to the Web UI as WebSocket `stream_chunk` events in real-time
- **FR-3.5.9:** Stream lifecycle: `stream_start` → `stream_chunk` (n times) → `stream_end` with full assembled response
- **FR-3.5.10:** If streaming fails mid-way, graceful fallback to local Ollama LLM

#### 3.5.4 Agent Discovery
- **FR-3.5.11:** NexgAI agent list fetched via `/agenthub/agents` API and cached in-memory
- **FR-3.5.12:** Cache refreshes every 5 minutes (configurable via NEXGAI_AGENT_CACHE_TTL)
- **FR-3.5.13:** NexgAI agents appear alongside local skills in the Web UI sidebar and `/api/web/skills` endpoint
- **FR-3.5.14:** Each agent entry includes: name, display_name, description, type, status, verticals

#### 3.5.5 Circuit Breaker & Graceful Degradation
- **FR-3.5.15:** Circuit breaker pattern: 3 consecutive failures → 60s cooldown before retry
- **FR-3.5.16:** When circuit is open, NexgAI tier is skipped — falls through to Ollama LLM
- **FR-3.5.17:** Circuit breaker status visible in `/health` and `/api/web/status` endpoints
- **FR-3.5.18:** When NexgAI is not configured (NEXGAI_ENABLED=false), zero impact on existing functionality

#### 3.5.6 Health & Observability
- **FR-3.5.19:** `/health` endpoint includes NexgAI connectivity status and circuit breaker state
- **FR-3.5.20:** Web UI status sidebar shows NexgAI connection status
- **FR-3.5.21:** Startup banner logs NexgAI connection result (Connected / Auth failed / Not configured)
- **FR-3.5.22:** Usage events logged as `nexgai_execution` (non-streaming) and `nexgai_stream` (streaming)

### Phase 4: Self-Learning Loop — DONE

**Priority:** P1
**Target:** v1.2

#### 4.1 Feedback Collection
- **FR-4.1:** Thumbs up/down buttons on every assistant message in Web UI
- **FR-4.2:** Optional text comment with feedback
- **FR-4.3:** Feedback linked to specific message, skill, and conversation

#### 4.2 Learning Engine
- **FR-4.4:** Background task (configurable interval, default 6 hours) analyzes low-rated responses
- **FR-4.5:** Correction detection: flags exchanges where user corrects the assistant
- **FR-4.6:** Generates learning entries (improved responses for similar queries)
- **FR-4.7:** All learning entries require admin approval before activation

#### 4.3 Prompt Optimization
- **FR-4.8:** Analyzes feedback patterns per skill to identify systemic issues
- **FR-4.9:** Suggests system prompt refinements with before/after diff
- **FR-4.10:** Admin reviews and applies via dashboard

#### 4.4 Knowledge Expansion
- **FR-4.11:** Highly-rated unique responses offered as new RAG entries
- **FR-4.12:** Admin approves before adding to knowledge base

#### 4.5 Learning Dashboard
- **FR-4.13:** Pending approvals queue in admin dashboard
- **FR-4.14:** Learning history with approval/rejection tracking
- **FR-4.15:** Satisfaction trend chart (before/after learning applied)

### Phase 5: Agent Hub Integration

**Priority:** P2
**Target:** v1.3

Agent creation, configuration, and lifecycle management is handled by **Agent Hub** — a separate application that serves as the control centre for all NexgAI agents. MyAi connects to Agent Hub to discover and use agents.

#### 5.1 Agent Hub Connection
- **FR-5.1:** MyAi connects to Agent Hub via API (REST/WebSocket) to fetch available agents
- **FR-5.2:** Agent registry syncs on startup and periodically (configurable interval)
- **FR-5.3:** New agents published in Agent Hub become available in MyAi without restart
- **FR-5.4:** Agent Hub provides: agent name, persona, description, keywords, system prompt, data source bindings

#### 5.2 Runtime Behaviour
- **FR-5.5:** Agents fetched from Agent Hub compete in routing with same confidence scoring as built-in skills
- **FR-5.6:** Built-in agents (VULCAN, VESTA, etc.) serve as defaults when Agent Hub is unreachable
- **FR-5.7:** MyAi sends usage analytics and feedback data back to Agent Hub for centralized monitoring
- **FR-5.8:** Agent Hub can push config updates (prompt changes, keyword updates) to MyAi in real-time

#### 5.3 Graceful Degradation
- **FR-5.9:** If Agent Hub is unreachable, MyAi continues operating with its 8 built-in agents
- **FR-5.10:** Connection status shown in admin dashboard system health panel

---

## 5. Technical Architecture

### 5.1 System Architecture

```
                      +-------------------+
                      |  NexgAI Platform  |
                      | (Agent Framework) |
                      +--------+----------+
                               |
                       SSE / REST API
                               |
  Agent Hub                    |
  (Control Centre)             |
  +------------------+         |
  | Create agents    |         |
  | Configure prompts|         |
  | Monitor perf     |         |
  +------------------+         |
                               |
                        +------v-----------+
                        |   Web Browser    |
                        |  (Chat + Admin)  |
                        +--------+---------+
                                 |
                    WebSocket / HTTP (port 8001)
                                 |
                        +--------+---------+
                        |   aiohttp Server |
                        |   (app/main.py)  |
                        +--------+---------+
                                 |
              +------------------+------------------+
              |                  |                   |
     +--------v------+  +-------v--------+  +-------v--------+
     | Auth Middleware|  | WebSocket Chat |  | Admin Dashboard|
     | (token check) |  | Handler        |  | API Routes     |
     +--------+------+  +-------+--------+  +-------+--------+
              |                  |                   |
              +------------------+-------------------+
                                 |
                        +--------v---------+
                        |    AgentCore     |
                        | (2-way routing)  |
                        +--------+---------+
                                 |
                    +------------+------------+
                    |                         |
             +------v-------+         +------v--------+
             | NexgAI Client|         | OllamaClient  |
             | (circuit     |         | (LLM fallback)|
             |  breaker)    |         +---------------+
             +------+-------+
                    |
             +------v---------+
             | SSE Streaming  |
             | Bridge → WS    |
             +----------------+
                                 |
                        +--------v---------+
                        |   Data Layer     |
                        +--------+---------+
                        |                  |
               +--------v------+  +--------v------+
               |   SQLite      |  |   ChromaDB    |
               | (users, msgs, |  | (RAG vectors) |
               |  events,      |  |               |
               |  nexgai_sess) |  |               |
               +---------------+  +---------------+
```

### 5.2 Database Schema

```
users ──────────< conversations ──────< messages
  |                                        |
  |──< api_sessions                        |──< feedback (Phase 4)
  |
  |──< nexgai_sessions (MyAi user → NexgAI session mapping)
  |
role_permissions (seeded per role_level)
file_access_policies (per role_level)
usage_events (append-only analytics log)
data_sources ──────< indexed_documents  (Phase 3)
learning_entries                        (Phase 4)
prompt_refinements                      (Phase 4)
```

### 5.3 Technology Stack

| Layer | Technology |
|-------|-----------|
| LLM | Ollama (local, llama3.1:8b default) |
| AI Platform | NexgAI Enterprise Agentization Platform (24+ agents) |
| Backend | Python 3.11+, aiohttp, asyncio |
| Database | SQLite (aiosqlite) with WAL mode |
| Vector Store | ChromaDB |
| Auth | bcrypt + opaque UUID tokens |
| Encryption | Fernet (data source configs) |
| Frontend | Vanilla HTML/CSS/JS (no build tools) |
| Real-time | WebSocket (aiohttp) + SSE bridge (NexgAI) |
| Chat Platform | Slack (Bolt SDK, Socket Mode) |
| Microsoft | Graph API (OAuth2 delegated) |
| Agent Management | Agent Hub (separate app, API integration) |

### 5.4 NexgAI Integration Architecture

```
User Message
     │
     ▼
┌──────────────┐            ┌────────────┐
│ NexgAI Client│───(no)────►│ Ollama LLM │
│ (circuit     │            │ (fallback) │
│  breaker)    │            │            │
└──────┬───────┘            └────────────┘
       │ (yes)
┌──────▼────────┐
│ NexgAI Platform│
│ POST /api/v3/ │
│ chat/stream    │
└──────┬────────┘
       │ SSE events
┌──────▼────────┐
│ Streaming Bridge│
│ SSE → WebSocket │
└──────┬────────┘
       │ stream_chunk
┌──────▼────────┐
│ Web UI Browser │
│ (real-time text)│
└───────────────┘
```

**Circuit Breaker States:**
```
CLOSED ──(3 failures)──► OPEN ──(60s cooldown)──► HALF-OPEN ──(success)──► CLOSED
                                                       │
                                                  (failure)
                                                       │
                                                       ▼
                                                     OPEN
```

---

## 6. Non-Functional Requirements

### 6.1 Performance
- **NFR-1:** WebSocket message round-trip < 500ms (excluding Ollama inference)
- **NFR-2:** Dashboard API responses < 200ms
- **NFR-3:** Login/auth validation < 50ms
- **NFR-4:** SQLite WAL mode for concurrent reads/writes
- **NFR-5:** NexgAI streaming delivers first token to browser within 2s of submission

### 6.2 Security
- **NFR-6:** Passwords hashed with bcrypt
- **NFR-7:** Session tokens are opaque UUIDs (not decodable JWTs)
- **NFR-8:** Data source connection configs encrypted at rest (Fernet)
- **NFR-9:** SQL database connector enforces read-only transactions
- **NFR-10:** File access enforced server-side
- **NFR-11:** No data leaves the organization's infrastructure
- **NFR-12:** NexgAI service account credentials stored as environment variables, never in code or DB

### 6.3 Reliability
- **NFR-13:** Graceful degradation when Ollama is unreachable
- **NFR-14:** Graceful degradation when NexgAI is unreachable (circuit breaker → Ollama LLM fallback)
- **NFR-15:** Schema changes via CREATE TABLE IF NOT EXISTS
- **NFR-16:** WebSocket auto-reconnect on connection loss
- **NFR-17:** NexgAI streaming failure mid-response falls back to complete Ollama LLM response

### 6.4 Scalability
- **NFR-18:** Target: 50 concurrent users on a single server
- **NFR-19:** Learning background task rate-limited (Phase 4)
- **NFR-20:** NexgAI agent list cached to avoid per-request API calls

---

## 7. User Flows

### 7.1 First-Time Setup
1. Deploy MyAi on server, run `python -m app.main --web-only`
2. Open http://localhost:8001
3. See "Create Super Admin" form
4. Enter email, name, password -> account created, auto-login
5. Chat UI loads, admin dashboard at /admin

### 7.2 Employee Login
1. Open http://localhost:8001
2. Enter email and password (account created by admin)
3. Chat UI with all skills available
4. Ask "I forgot my password" -> routes to VULCAN

### 7.3 NexgAI Streaming Response
1. Employee sends "What's my account balance?" in chat
2. AgentCore routes to NexgAI (primary handler for specialized tasks)
3. NexgAI Client creates session (if first message), streams to `/api/v3/chat/stream`
4. SSE events flow: `session` → `status` → `chunk` (n times) → `complete`
5. Each `chunk` relayed via WebSocket as `stream_chunk` — text appears progressively in chat
6. On `complete`, full response stored in DB, logged as `nexgai_stream` usage event
7. If NexgAI unreachable (circuit breaker open), falls through to Ollama LLM silently

### 7.4 Agent Hub Publishes New Agent
1. Admin creates a "Customer Success" agent in Agent Hub
2. Sets persona, keywords, system prompt, connects to CRM data source
3. Publishes the agent
4. MyAi picks up the new agent on next cache refresh (5 min)
5. Agent appears in Web UI sidebar under NexgAI agents
6. Every employee can now use the Customer Success agent via chat

### 7.5 Self-Learning Cycle (Phase 4)
1. Employee asks "How do I request a standing desk?"
2. VESTA responds with generic answer
3. Employee gives thumbs down + comment "We use the Facilities portal"
4. Learning engine generates correction entry
5. Admin approves -> next time, VESTA includes Facilities portal info
