"""Tests for the meeting transcript listener feature."""

from __future__ import annotations

import asyncio
import hashlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.meeting_transcript import (
    MeetingSession,
    MeetingTranscriptService,
    _content_hash,
)


# ── Fixtures ──


@pytest.fixture
def mock_ollama():
    """Create a mock OllamaClient."""
    client = MagicMock()
    client.model = "llama3.1:8b"
    client.set_model = MagicMock()
    client.chat = AsyncMock(
        return_value={"message": {"content": "You could mention the quarterly targets."}}
    )
    return client


@pytest.fixture
def mock_deliver():
    """Create a mock delivery function."""
    return AsyncMock()


@pytest.fixture
def service(mock_ollama, mock_deliver):
    """Create a MeetingTranscriptService with mocked dependencies."""
    with patch("app.services.meeting_transcript.settings") as mock_settings:
        mock_settings.meeting_suggestion_debounce_seconds = 0  # no debounce in tests
        mock_settings.meeting_transcript_max_chars = 500
        mock_settings.meeting_suggestion_model = ""
        svc = MeetingTranscriptService(
            ollama=mock_ollama,
            deliver_fn=mock_deliver,
        )
        # Override debounce for fast tests
        svc._debounce_seconds = 0
    return svc


@pytest.fixture
def session(service):
    """Create a test meeting session."""
    return service.start_session(
        call_id="test-call-123",
        user_id="user-456",
        user_name="Alice",
        user_role="Engineer",
        meeting_subject="Sprint Planning",
        conversation_reference={
            "service_url": "https://smba.trafficmanager.net/teams/",
            "conversation_id": "conv-789",
        },
    )


# ── Session Lifecycle Tests ──


class TestSessionLifecycle:
    def test_start_session(self, service):
        session = service.start_session(
            call_id="call-1",
            user_id="user-1",
            user_name="Bob",
        )
        assert session.call_id == "call-1"
        assert session.user_id == "user-1"
        assert session.user_name == "Bob"
        assert session.transcript_lines == []
        assert service.get_session("call-1") is session

    def test_get_session_not_found(self, service):
        assert service.get_session("nonexistent") is None

    def test_end_session(self, service):
        service.start_session(call_id="call-1", user_id="user-1")
        service.end_session("call-1")
        assert service.get_session("call-1") is None

    def test_end_nonexistent_session(self, service):
        # Should not raise
        service.end_session("nonexistent")

    def test_get_session_by_user(self, service):
        service.start_session(call_id="call-1", user_id="user-1", user_name="Alice")
        session = service.get_session_by_user("user-1")
        assert session is not None
        assert session.call_id == "call-1"

    def test_get_session_by_user_not_found(self, service):
        assert service.get_session_by_user("nobody") is None

    def test_active_sessions(self, service):
        service.start_session(call_id="c1", user_id="u1")
        service.start_session(call_id="c2", user_id="u2")
        assert len(service.active_sessions) == 2
        service.end_session("c1")
        assert len(service.active_sessions) == 1


# ── Transcript Parsing Tests ──


class TestTranscriptParsing:
    def test_parse_plain_text(self):
        raw = "Alice: Hello everyone\nBob: Hi Alice\n"
        lines = MeetingTranscriptService._parse_transcript_text(raw)
        assert lines == ["Alice: Hello everyone", "Bob: Hi Alice"]

    def test_parse_vtt_format(self):
        raw = """WEBVTT

1
00:00:01.000 --> 00:00:05.000
Alice: Welcome to the meeting

2
00:00:06.000 --> 00:00:10.000
Bob: Thanks for having me"""
        lines = MeetingTranscriptService._parse_transcript_text(raw)
        assert lines == [
            "Alice: Welcome to the meeting",
            "Bob: Thanks for having me",
        ]

    def test_parse_empty_text(self):
        assert MeetingTranscriptService._parse_transcript_text("") == []
        assert MeetingTranscriptService._parse_transcript_text("   \n\n  ") == []

    def test_parse_vtt_metadata_skipped(self):
        raw = "WEBVTT\nNOTE This is a note\nSTYLE some style\n\nActual content here"
        lines = MeetingTranscriptService._parse_transcript_text(raw)
        assert lines == ["Actual content here"]

    def test_parse_numeric_cue_ids_skipped(self):
        raw = "1\n00:00:01.000 --> 00:00:02.000\nHello\n2\n00:00:03.000 --> 00:00:04.000\nWorld"
        lines = MeetingTranscriptService._parse_transcript_text(raw)
        assert lines == ["Hello", "World"]


# ── Rolling Context Tests ──


class TestRollingContext:
    def test_short_transcript(self, service, session):
        session.transcript_lines = ["Line 1", "Line 2", "Line 3"]
        result = service.get_rolling_transcript(session)
        assert result == "Line 1\nLine 2\nLine 3"

    def test_transcript_trimmed_to_max_chars(self, service, session):
        # Service max is 500 chars
        long_lines = [f"Speaker: This is a fairly long line number {i}" for i in range(50)]
        session.transcript_lines = long_lines
        result = service.get_rolling_transcript(session)
        assert len(result) <= 500
        # Should contain the later lines, not the early ones
        assert "number 49" in result

    def test_empty_transcript(self, service, session):
        session.transcript_lines = []
        result = service.get_rolling_transcript(session)
        assert result == ""


# ── Suggestion Generation Tests ──


class TestSuggestionGeneration:
    @pytest.mark.asyncio
    async def test_generate_suggestion(self, service, session, mock_ollama):
        session.transcript_lines = [
            "Manager: Let's discuss the Q2 targets.",
            "Alice: I think we should focus on retention.",
        ]
        suggestion = await service.generate_and_deliver(session)
        assert suggestion is not None
        assert "quarterly targets" in suggestion

        # Verify Ollama was called with correct prompt structure
        mock_ollama.chat.assert_called_once()
        call_args = mock_ollama.chat.call_args
        messages = call_args[1]["messages"] if "messages" in call_args[1] else call_args[0][0]
        assert len(messages) == 2
        assert "Alice" in messages[0]["content"]  # system prompt contains user name
        assert "Sprint Planning" in messages[0]["content"]  # meeting subject

    @pytest.mark.asyncio
    async def test_skip_on_empty_transcript(self, service, session, mock_ollama):
        session.transcript_lines = []
        suggestion = await service.generate_and_deliver(session)
        assert suggestion is None
        mock_ollama.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_on_unchanged_transcript(self, service, session, mock_ollama):
        session.transcript_lines = ["Alice: Hello"]
        # First call should work
        await service.generate_and_deliver(session)
        mock_ollama.chat.reset_mock()

        # Second call with same transcript should skip
        suggestion = await service.generate_and_deliver(session)
        assert suggestion is None
        mock_ollama.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_suggestion_response(self, service, session, mock_ollama):
        mock_ollama.chat.return_value = {"message": {"content": "NO_SUGGESTION"}}
        session.transcript_lines = ["Alice: Let's wrap up."]
        suggestion = await service.generate_and_deliver(session)
        assert suggestion is None

    @pytest.mark.asyncio
    async def test_ollama_failure_returns_none(self, service, session, mock_ollama):
        mock_ollama.chat.side_effect = Exception("Connection refused")
        session.transcript_lines = ["Alice: Important discussion point"]
        suggestion = await service.generate_and_deliver(session)
        assert suggestion is None

    @pytest.mark.asyncio
    async def test_duplicate_suggestion_not_delivered(self, service, session, mock_ollama, mock_deliver):
        session.transcript_lines = ["Alice: Hello"]
        await service.generate_and_deliver(session)
        mock_deliver.reset_mock()

        # Change transcript but model returns the same suggestion
        session.transcript_lines.append("Bob: Hi")
        session.last_suggestion_hash = ""  # Reset to allow re-generation
        await service.generate_and_deliver(session)
        # The delivery should be skipped since the suggestion text is identical
        mock_deliver.assert_not_called()


# ── Delivery Tests ──


class TestDelivery:
    @pytest.mark.asyncio
    async def test_suggestion_delivered(self, service, session, mock_deliver, mock_ollama):
        session.transcript_lines = ["Manager: What's the status?"]
        await service.generate_and_deliver(session)
        mock_deliver.assert_called_once()
        call_args = mock_deliver.call_args
        assert call_args[0][0] is session
        assert "quarterly targets" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_delivery_failure_logged_not_raised(self, service, session, mock_ollama):
        failing_deliver = AsyncMock(side_effect=Exception("Network error"))
        service.deliver_fn = failing_deliver
        session.transcript_lines = ["Alice: Test"]

        # Should not raise
        suggestion = await service.generate_and_deliver(session)
        assert suggestion is not None

    @pytest.mark.asyncio
    async def test_no_deliver_fn(self, service, session, mock_ollama):
        service.deliver_fn = None
        session.transcript_lines = ["Alice: Test"]
        suggestion = await service.generate_and_deliver(session)
        assert suggestion is not None  # Suggestion generated but not delivered


# ── Debounce / Batching Tests ──


class TestDebounce:
    @pytest.mark.asyncio
    async def test_debounce_cancels_pending(self, service, session, mock_ollama):
        """Multiple rapid ingestions should only trigger one suggestion."""
        service._debounce_seconds = 0.1

        session.transcript_lines = []
        await service.ingest_transcript("test-call-123", "Alice: Line 1")
        await service.ingest_transcript("test-call-123", "Bob: Line 2")
        await service.ingest_transcript("test-call-123", "Alice: Line 3")

        # Wait for debounce to fire
        await asyncio.sleep(0.3)

        # Should have been called once (the last scheduled one)
        assert mock_ollama.chat.call_count <= 1

    @pytest.mark.asyncio
    async def test_minimum_time_gap_enforced(self, service, session, mock_ollama):
        """Second suggestion too soon after first should be skipped."""
        service._debounce_seconds = 5  # 5 seconds

        session.transcript_lines = ["Alice: First point"]
        await service.generate_and_deliver(session)

        # Immediately try again with new content
        session.transcript_lines.append("Bob: Second point")
        suggestion = await service.generate_and_deliver(session)
        assert suggestion is None  # skipped due to time gap

    @pytest.mark.asyncio
    async def test_ingest_to_nonexistent_session(self, service, mock_ollama):
        """Ingesting to a non-existent session should be a no-op."""
        await service.ingest_transcript("no-such-call", "Some text")
        mock_ollama.chat.assert_not_called()


# ── Edge Cases ──


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_bot_joins_mid_meeting(self, service, mock_ollama, mock_deliver):
        """Bot joins after conversation has been going -- first transcript
        block contains a lot of prior content."""
        session = service.start_session(
            call_id="mid-join",
            user_id="user-1",
            user_name="Charlie",
            conversation_reference={"service_url": "https://x", "conversation_id": "c1"},
        )
        # Simulate a large initial transcript dump
        prior_lines = [f"Speaker{i % 3}: Discussion point {i}" for i in range(100)]
        raw = "\n".join(prior_lines)
        await service.ingest_transcript("mid-join", raw)

        # Wait for debounce
        await asyncio.sleep(0.2)

        # Should have processed and generated a suggestion
        assert len(session.transcript_lines) == 100
        assert mock_ollama.chat.call_count >= 1

    @pytest.mark.asyncio
    async def test_transcript_disabled_mid_session(self, service, session, mock_ollama):
        """If session is ended while transcript is being processed."""
        session.transcript_lines = ["Alice: In progress"]
        service.end_session("test-call-123")

        # Ingesting after session end should be a no-op
        await service.ingest_transcript("test-call-123", "More text")
        mock_ollama.chat.assert_not_called()

    def test_content_hash_consistency(self):
        """Same content should produce same hash."""
        assert _content_hash("hello world") == _content_hash("hello world")
        assert _content_hash("hello  world") == _content_hash("hello world")
        assert _content_hash("a") != _content_hash("b")

    @pytest.mark.asyncio
    async def test_model_override_restored(self, service, session, mock_ollama):
        """If a meeting_suggestion_model is configured, the original model
        should be restored after the call."""
        with patch("app.services.meeting_transcript.settings") as mock_settings:
            mock_settings.meeting_suggestion_model = "mistral:7b"
            mock_settings.meeting_suggestion_debounce_seconds = 0
            mock_settings.meeting_transcript_max_chars = 500

            session.transcript_lines = ["Alice: Test"]
            await service.generate_and_deliver(session)

            # set_model should have been called twice: once to override, once to restore
            assert mock_ollama.set_model.call_count == 2
            # Last call should restore original
            mock_ollama.set_model.assert_called_with("llama3.1:8b")


# ── Prompt Construction Tests ──


class TestPromptConstruction:
    @pytest.mark.asyncio
    async def test_system_prompt_contains_user_info(self, service, session, mock_ollama):
        session.transcript_lines = ["Manager: Status update?"]
        await service.generate_and_deliver(session)

        call_args = mock_ollama.chat.call_args
        messages = call_args[1]["messages"]
        system_msg = messages[0]["content"]

        assert "Alice" in system_msg
        assert "Engineer" in system_msg
        assert "Sprint Planning" in system_msg

    @pytest.mark.asyncio
    async def test_user_prompt_contains_transcript(self, service, session, mock_ollama):
        session.transcript_lines = ["Bob: Let's review the PR"]
        await service.generate_and_deliver(session)

        call_args = mock_ollama.chat.call_args
        messages = call_args[1]["messages"]
        user_msg = messages[1]["content"]

        assert "Let's review the PR" in user_msg
        assert "Alice" in user_msg  # user_name in prompt


# ── Polling Tests ──


class TestPolling:
    @pytest.fixture
    def mock_graph(self):
        """Create a mock GraphClient."""
        client = MagicMock()
        client.fetch_transcript_content = AsyncMock(return_value=[
            {"id": "t-1", "content": "Alice: Hello from poll\nBob: Hi there"},
        ])
        return client

    @pytest.fixture
    def polling_service(self, mock_ollama, mock_deliver, mock_graph):
        """Service with a graph client for polling."""
        with patch("app.services.meeting_transcript.settings") as mock_settings:
            mock_settings.meeting_suggestion_debounce_seconds = 0
            mock_settings.meeting_transcript_max_chars = 500
            mock_settings.meeting_suggestion_model = ""
            svc = MeetingTranscriptService(
                ollama=mock_ollama,
                deliver_fn=mock_deliver,
                graph_client=mock_graph,
                poll_interval_seconds=0.1,
            )
            svc._debounce_seconds = 0
        return svc

    @pytest.mark.asyncio
    async def test_poll_session_ingests_new_transcripts(self, polling_service, mock_graph, mock_ollama):
        session = polling_service.start_session(
            call_id="poll-call",
            user_id="user-1",
            user_name="Alice",
            meeting_id="meeting-abc",
        )
        # Stop the auto-started poll loop to test _poll_session directly
        if polling_service._poll_task:
            polling_service._poll_task.cancel()

        await polling_service._poll_session(session)

        mock_graph.fetch_transcript_content.assert_called_once_with("meeting-abc")
        assert len(session.transcript_lines) == 2
        assert "t-1" in session.seen_transcript_ids

    @pytest.mark.asyncio
    async def test_poll_deduplicates_transcripts(self, polling_service, mock_graph, mock_ollama):
        session = polling_service.start_session(
            call_id="dedup-call",
            user_id="user-1",
            user_name="Alice",
            meeting_id="meeting-xyz",
        )
        if polling_service._poll_task:
            polling_service._poll_task.cancel()

        # Poll twice -- second should not re-ingest
        await polling_service._poll_session(session)
        line_count_after_first = len(session.transcript_lines)

        await polling_service._poll_session(session)
        assert len(session.transcript_lines) == line_count_after_first

    @pytest.mark.asyncio
    async def test_poll_skips_sessions_without_meeting_id(self, polling_service, mock_graph):
        session = polling_service.start_session(
            call_id="no-mid",
            user_id="user-1",
            user_name="Alice",
            meeting_id="",  # no meeting ID
        )
        if polling_service._poll_task:
            polling_service._poll_task.cancel()

        await polling_service._poll_session(session)
        mock_graph.fetch_transcript_content.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_handles_graph_failure(self, polling_service, mock_graph):
        mock_graph.fetch_transcript_content.side_effect = Exception("Graph down")
        session = polling_service.start_session(
            call_id="fail-call",
            user_id="user-1",
            user_name="Alice",
            meeting_id="meeting-fail",
        )
        if polling_service._poll_task:
            polling_service._poll_task.cancel()

        # Should not raise
        await polling_service._poll_session(session)

    @pytest.mark.asyncio
    async def test_poll_loop_stops_on_end_session(self, polling_service):
        polling_service.start_session(
            call_id="loop-call",
            user_id="user-1",
            user_name="Alice",
            meeting_id="meeting-loop",
        )
        # Poll task should have started
        assert polling_service._poll_task is not None
        assert not polling_service._poll_task.done()

        polling_service.end_session("loop-call")
        # With no sessions, poll task should be cancelled
        assert polling_service._poll_task is None or polling_service._poll_task.done()

    def test_no_poll_without_graph_client(self, mock_ollama, mock_deliver):
        with patch("app.services.meeting_transcript.settings") as mock_settings:
            mock_settings.meeting_suggestion_debounce_seconds = 0
            mock_settings.meeting_transcript_max_chars = 500
            mock_settings.meeting_suggestion_model = ""
            svc = MeetingTranscriptService(
                ollama=mock_ollama,
                deliver_fn=mock_deliver,
                graph_client=None,
            )
            svc._debounce_seconds = 0
        svc.start_session(call_id="no-graph", user_id="u1", meeting_id="m1")
        assert svc._poll_task is None


# ── Token Caching Tests ──


class TestTokenCaching:
    @pytest.mark.asyncio
    async def test_graph_token_cached(self):
        from app.services.graph import GraphClient

        with patch("app.services.graph.settings") as mock_settings:
            mock_settings.microsoft_app_id = "test-id"
            mock_settings.microsoft_app_password = "test-secret"
            mock_settings.microsoft_app_tenant_id = "test-tenant"
            mock_settings.transcript_webhook_secret = "secret"

            client = GraphClient()

            mock_resp = MagicMock()
            mock_resp.json.return_value = {"access_token": "tok-123", "expires_in": 3600}
            mock_resp.raise_for_status = MagicMock()

            with patch("httpx.AsyncClient") as mock_http:
                mock_http_instance = AsyncMock()
                mock_http_instance.post = AsyncMock(return_value=mock_resp)
                mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
                mock_http_instance.__aexit__ = AsyncMock(return_value=None)
                mock_http.return_value = mock_http_instance

                token1 = await client.get_access_token()
                token2 = await client.get_access_token()

                assert token1 == "tok-123"
                assert token2 == "tok-123"
                # Should only have made one HTTP call -- second was cached
                assert mock_http_instance.post.call_count == 1
