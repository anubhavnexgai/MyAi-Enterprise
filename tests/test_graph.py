"""Tests for Microsoft Graph integration."""

from __future__ import annotations

import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.graph import GraphClient, UserTokens, SCOPES


# -- Fixtures --

@pytest.fixture
def graph():
    with patch("app.services.graph.settings") as mock_settings:
        mock_settings.graph_client_id = "test-client-id"
        mock_settings.graph_client_secret = "test-secret"
        mock_settings.graph_tenant_id = "test-tenant"
        mock_settings.graph_redirect_uri = "http://localhost:8001/auth/callback"
        client = GraphClient()
    return client


@pytest.fixture
def graph_unconfigured():
    with patch("app.services.graph.settings") as mock_settings:
        mock_settings.graph_client_id = ""
        mock_settings.graph_client_secret = ""
        mock_settings.graph_tenant_id = ""
        mock_settings.graph_redirect_uri = ""
        client = GraphClient()
    return client


@pytest.fixture
def connected_graph(graph):
    """Graph client with a connected user."""
    graph._token_store["U123"] = UserTokens(
        access_token="test-access-token",
        refresh_token="test-refresh-token",
        expires_at=time.time() + 3600,
        scope=" ".join(SCOPES),
        user_email="anubhav@company.com",
    )
    return graph


# -- Configuration Tests --

class TestGraphConfig:
    def test_is_configured_when_credentials_set(self, graph):
        assert graph.is_configured is True

    def test_not_configured_when_empty(self, graph_unconfigured):
        assert graph_unconfigured.is_configured is False

    def test_auth_url_contains_client_id(self, graph):
        url = graph.get_auth_url(state="U123")
        assert "test-client-id" in url
        assert "U123" in url
        assert "offline_access" in url

    def test_auth_url_contains_scopes(self, graph):
        url = graph.get_auth_url()
        assert "Calendars.ReadWrite" in url
        assert "Mail.ReadWrite" in url


# -- Token Management Tests --

class TestTokenManagement:
    def test_user_not_connected_initially(self, graph):
        assert graph.is_user_connected("U999") is False

    def test_user_connected_after_storing(self, connected_graph):
        assert connected_graph.is_user_connected("U123") is True

    def test_disconnect_removes_tokens(self, connected_graph):
        connected_graph.disconnect_user("U123")
        assert connected_graph.is_user_connected("U123") is False

    def test_get_user_email(self, connected_graph):
        assert connected_graph.get_user_email("U123") == "anubhav@company.com"

    def test_get_user_email_unknown_user(self, graph):
        assert graph.get_user_email("U999") == ""

    @pytest.mark.asyncio
    async def test_get_token_returns_access_token(self, connected_graph):
        token = await connected_graph.get_token("U123")
        assert token == "test-access-token"

    @pytest.mark.asyncio
    async def test_get_token_raises_for_unconnected(self, graph):
        with pytest.raises(ValueError, match="Not connected"):
            await graph.get_token("U999")

    @pytest.mark.asyncio
    async def test_get_token_refreshes_when_expired(self, connected_graph):
        connected_graph._token_store["U123"].expires_at = time.time() - 100

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
                "expires_in": 3600,
            }
            mock_resp.raise_for_status = MagicMock()
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            token = await connected_graph.get_token("U123")
            assert token == "new-access-token"


# -- OAuth Flow Tests --

class TestOAuthFlow:
    @pytest.mark.asyncio
    async def test_exchange_code(self, graph):
        with patch("httpx.AsyncClient") as mock_client_cls:
            # Mock token exchange
            mock_token_resp = MagicMock()
            mock_token_resp.json.return_value = {
                "access_token": "new-token",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
                "scope": "User.Read Calendars.ReadWrite",
            }
            mock_token_resp.raise_for_status = MagicMock()

            # Mock /me call for email
            mock_me_resp = MagicMock()
            mock_me_resp.json.return_value = {
                "mail": "user@company.com",
                "displayName": "Test User",
            }
            mock_me_resp.raise_for_status = MagicMock()

            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_token_resp)
            mock_client.get = AsyncMock(return_value=mock_me_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            tokens = await graph.exchange_code("auth-code-123", "U456")

            assert tokens.access_token == "new-token"
            assert tokens.refresh_token == "new-refresh"
            assert tokens.user_email == "user@company.com"
            assert graph.is_user_connected("U456")


# -- API Operation Tests --

class TestGraphOperations:
    @pytest.mark.asyncio
    async def test_get_calendar_events(self, connected_graph):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "value": [
                    {
                        "subject": "Sprint Review",
                        "start": {"dateTime": "2026-03-16T14:00:00"},
                        "end": {"dateTime": "2026-03-16T15:00:00"},
                        "location": {"displayName": "Room 42"},
                        "organizer": {"emailAddress": {"name": "PM"}},
                        "isOnlineMeeting": True,
                        "onlineMeetingUrl": "https://teams.link/123",
                        "attendees": [
                            {"emailAddress": {"name": "Alice"}},
                            {"emailAddress": {"name": "Bob"}},
                        ],
                    }
                ]
            }
            mock_resp.raise_for_status = MagicMock()
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            events = await connected_graph.get_calendar_events("U123")
            assert len(events) == 1
            assert events[0]["subject"] == "Sprint Review"
            assert events[0]["location"] == "Room 42"
            assert events[0]["is_online"] is True
            assert "Alice" in events[0]["attendees"]

    @pytest.mark.asyncio
    async def test_get_recent_emails(self, connected_graph):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "value": [
                    {
                        "id": "msg-1",
                        "subject": "Q2 Planning",
                        "from": {"emailAddress": {"name": "Boss", "address": "boss@co.com"}},
                        "receivedDateTime": "2026-03-16T10:00:00Z",
                        "isRead": False,
                        "bodyPreview": "Let's discuss the Q2 roadmap...",
                        "importance": "high",
                    }
                ]
            }
            mock_resp.raise_for_status = MagicMock()
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            emails = await connected_graph.get_recent_emails("U123")
            assert len(emails) == 1
            assert emails[0]["subject"] == "Q2 Planning"
            assert emails[0]["is_read"] is False
            assert emails[0]["importance"] == "high"

    @pytest.mark.asyncio
    async def test_send_email(self, connected_graph):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {}
            mock_resp.status_code = 204
            mock_resp.raise_for_status = MagicMock()
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await connected_graph.send_email(
                "U123",
                to=["alice@company.com"],
                subject="Test",
                body="Hello from MyAi",
            )
            # No exception = success
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_event(self, connected_graph):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"id": "event-123", "subject": "Design Review"}
            mock_resp.status_code = 201
            mock_resp.raise_for_status = MagicMock()
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await connected_graph.create_event(
                "U123",
                subject="Design Review",
                start_time="2026-03-17T14:00:00",
                end_time="2026-03-17T15:00:00",
                attendees=["alice@co.com"],
            )
            assert result["subject"] == "Design Review"

    @pytest.mark.asyncio
    async def test_get_recent_files(self, connected_graph):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "value": [
                    {
                        "name": "Report.docx",
                        "size": 51200,
                        "lastModifiedDateTime": "2026-03-15T09:00:00Z",
                        "webUrl": "https://onedrive.com/report",
                        "file": {"mimeType": "application/docx"},
                    }
                ]
            }
            mock_resp.raise_for_status = MagicMock()
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            files = await connected_graph.get_recent_files("U123")
            assert len(files) == 1
            assert files[0]["name"] == "Report.docx"

    @pytest.mark.asyncio
    async def test_get_people(self, connected_graph):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "value": [
                    {
                        "displayName": "Alice Smith",
                        "scoredEmailAddresses": [{"address": "alice@co.com"}],
                        "jobTitle": "Designer",
                        "department": "UX",
                        "companyName": "Company",
                    }
                ]
            }
            mock_resp.raise_for_status = MagicMock()
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            people = await connected_graph.get_people("U123", query="alice")
            assert len(people) == 1
            assert people[0]["name"] == "Alice Smith"
            assert people[0]["email"] == "alice@co.com"

    @pytest.mark.asyncio
    async def test_get_my_presence(self, connected_graph):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "availability": "Available",
                "activity": "Available",
            }
            mock_resp.raise_for_status = MagicMock()
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            presence = await connected_graph.get_my_presence("U123")
            assert presence["availability"] == "Available"


# -- EKLAVYA + Graph Integration Tests --

class TestEklavyaGraphIntegration:
    @pytest.mark.asyncio
    async def test_eklavya_fetches_calendar_for_schedule_request(self):
        from app.skills.executive_assistant import ExecutiveAssistantSkill
        from app.skills.base import SkillContext

        mock_ollama = MagicMock()
        mock_ollama.chat = AsyncMock(
            return_value={"message": {"content": "Here's your schedule..."}}
        )
        mock_db = MagicMock()
        mock_db.get_recent_meetings = AsyncMock(return_value=[])
        mock_db.get_all_contexts = AsyncMock(return_value=[])

        mock_graph = MagicMock()
        mock_graph.is_configured = True
        mock_graph.is_user_connected = MagicMock(return_value=True)
        mock_graph.get_calendar_events = AsyncMock(return_value=[
            {
                "subject": "Sprint Review",
                "start": "2026-03-16T14:00:00",
                "end": "2026-03-16T15:00:00",
                "location": "Room 42",
                "attendees": ["Alice", "Bob"],
            }
        ])

        skill = ExecutiveAssistantSkill(
            ollama=mock_ollama, database=mock_db, graph_client=mock_graph,
        )
        ctx = SkillContext(user_id="U123", user_name="Anubhav", user_role="Engineer")

        result = await skill.execute(ctx, "What's on my calendar today?")
        assert result.success is True

        # Verify Graph calendar was fetched
        mock_graph.get_calendar_events.assert_called_once()

        # Verify calendar data was included in system prompt
        call_args = mock_ollama.chat.call_args
        system_msg = call_args[1]["messages"][0]["content"]
        assert "Sprint Review" in system_msg
        assert "Room 42" in system_msg

    @pytest.mark.asyncio
    async def test_eklavya_suggests_connect_when_not_connected(self):
        from app.skills.executive_assistant import ExecutiveAssistantSkill
        from app.skills.base import SkillContext

        mock_ollama = MagicMock()
        mock_ollama.chat = AsyncMock(
            return_value={"message": {"content": "I can help with that..."}}
        )
        mock_db = MagicMock()
        mock_db.get_recent_meetings = AsyncMock(return_value=[])
        mock_db.get_all_contexts = AsyncMock(return_value=[])

        mock_graph = MagicMock()
        mock_graph.is_configured = True
        mock_graph.is_user_connected = MagicMock(return_value=False)

        skill = ExecutiveAssistantSkill(
            ollama=mock_ollama, database=mock_db, graph_client=mock_graph,
        )
        ctx = SkillContext(user_id="U123", user_name="Anubhav")

        result = await skill.execute(ctx, "What meetings do I have?")
        assert result.success is True

        # Verify system prompt mentions /connect
        call_args = mock_ollama.chat.call_args
        system_msg = call_args[1]["messages"][0]["content"]
        assert "/connect" in system_msg

    @pytest.mark.asyncio
    async def test_eklavya_fetches_emails_for_email_request(self):
        from app.skills.executive_assistant import ExecutiveAssistantSkill
        from app.skills.base import SkillContext

        mock_ollama = MagicMock()
        mock_ollama.chat = AsyncMock(
            return_value={"message": {"content": "Here are your emails..."}}
        )
        mock_db = MagicMock()
        mock_db.get_recent_meetings = AsyncMock(return_value=[])
        mock_db.get_all_contexts = AsyncMock(return_value=[])

        mock_graph = MagicMock()
        mock_graph.is_configured = True
        mock_graph.is_user_connected = MagicMock(return_value=True)
        mock_graph.get_recent_emails = AsyncMock(return_value=[
            {
                "subject": "Q2 Planning",
                "from": "Boss",
                "received": "2026-03-16T10:00:00Z",
                "is_read": False,
                "preview": "Let's discuss Q2...",
            }
        ])

        skill = ExecutiveAssistantSkill(
            ollama=mock_ollama, database=mock_db, graph_client=mock_graph,
        )
        ctx = SkillContext(user_id="U123", user_name="Anubhav")

        result = await skill.execute(ctx, "Check my email inbox")
        assert result.success is True

        mock_graph.get_recent_emails.assert_called_once()
        call_args = mock_ollama.chat.call_args
        system_msg = call_args[1]["messages"][0]["content"]
        assert "Q2 Planning" in system_msg
