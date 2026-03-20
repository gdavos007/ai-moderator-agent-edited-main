"""
Tests for Phase 3: Multi-Participant Audio Routing refactor.

Validates that:
1. _get_audio_input() safely accesses private SDK internals
2. _set_stt_participant() updates cache and calls SDK
3. _route_audio_to_participant() orchestrates routing correctly
4. _current_stt_participant cache is used by event handlers
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime


# ---------------------------------------------------------------------------
# Minimal stub of CommunityModeratorAgent for unit testing
# (Tests mirror logic from moderator_agent.py rather than importing directly
#  due to heavy LiveKit dependencies — see CLAUDE.md)
# ---------------------------------------------------------------------------

class FakeAudioInput:
    """Mimics the RoomIO._audio_input handle."""
    def __init__(self):
        self._participant_identity = None

    def set_participant(self, identity):
        self._participant_identity = identity


class FakeRoomIO:
    def __init__(self, audio_input=None):
        self._audio_input = audio_input


class FakeAgentSession:
    def __init__(self, room_io=None):
        self._room_io = room_io


class FakeParticipantManager:
    def __init__(self, participants=None, unavailable=None, observer=None):
        self.participants = participants or []
        self.unavailable_participants = set(unavailable or [])
        self._observer = observer

    def get_observer_identity(self):
        return self._observer


class StubAgent:
    """Reproduces the relevant methods from CommunityModeratorAgent."""

    def __init__(self):
        self.agent_session = None
        self.participant_manager = None
        self.expected_respondent = None
        self.actual_respondent = None
        self.turn_transition_time = None
        self._current_stt_participant = None
        self.current_question_num = 1
        self.observer_mode_enabled = False
        self.pending_stt_transcript = None
        # Delivery state tracking
        self.question_delivery_state = {}

    # --- Methods under test (mirrored from moderator_agent.py) ---

    def _get_audio_input(self):
        """Return the RoomIO audio_input handle, or None if unavailable."""
        if not self.agent_session:
            return None
        room_io = getattr(self.agent_session, '_room_io', None)
        if not room_io:
            return None
        return getattr(room_io, '_audio_input', None)

    def _set_stt_participant(self, identity, *, context=""):
        """Direct STT to listen to a single participant. Returns True on success."""
        audio_input = self._get_audio_input()
        if not audio_input:
            return False
        try:
            audio_input.set_participant(identity)
            self._current_stt_participant = identity
            return True
        except Exception:
            return False

    def _active_respondent_count(self):
        if not self.participant_manager:
            return 0
        available = [
            p for p in self.participant_manager.participants
            if p not in self.participant_manager.unavailable_participants
        ]
        return len(available)

    def _set_delivery_state(self, question_num, participant, state, *, context=""):
        self.question_delivery_state[(question_num, participant)] = state

    def _is_avatar_identity(self, identity):
        return identity and 'avatar' in identity.lower()

    async def manage_participant_muting(self, active_participant):
        pass  # No-op for testing

    async def _route_audio_to_participant(self, participant, *, context="", skip_muting=False):
        """Centralized audio routing."""
        self.expected_respondent = participant
        self.actual_respondent = None
        self.turn_transition_time = datetime.now()
        self._set_delivery_state(self.current_question_num, participant, "delivering", context=context)

        respondent_count = self._active_respondent_count()
        is_multi = respondent_count > 1

        if is_multi:
            self._set_stt_participant(participant, context=f"{context}_stt_lock")
            if not skip_muting:
                asyncio.create_task(self.manage_participant_muting(participant))

        if self.observer_mode_enabled:
            self.pending_stt_transcript = None
            observer_identity = self.participant_manager.get_observer_identity()
            if observer_identity:
                self._set_stt_participant(observer_identity, context=f"{context}_observer")

        if is_multi:
            await asyncio.sleep(0.01)  # Shortened for tests (real code uses 0.5s)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetAudioInput:
    """_get_audio_input() safely navigates private SDK internals."""

    def test_returns_none_when_no_session(self):
        agent = StubAgent()
        assert agent._get_audio_input() is None

    def test_returns_none_when_no_room_io(self):
        agent = StubAgent()
        agent.agent_session = MagicMock(spec=[])  # No _room_io attr
        assert agent._get_audio_input() is None

    def test_returns_none_when_room_io_has_no_audio_input(self):
        agent = StubAgent()
        agent.agent_session = FakeAgentSession(FakeRoomIO(audio_input=None))
        assert agent._get_audio_input() is None

    def test_returns_audio_input_when_available(self):
        audio_input = FakeAudioInput()
        agent = StubAgent()
        agent.agent_session = FakeAgentSession(FakeRoomIO(audio_input))
        assert agent._get_audio_input() is audio_input


class TestSetSttParticipant:
    """_set_stt_participant() updates cache and calls SDK."""

    def test_returns_false_when_no_audio_input(self):
        agent = StubAgent()
        assert agent._set_stt_participant("alice") is False
        assert agent._current_stt_participant is None

    def test_sets_participant_and_updates_cache(self):
        audio_input = FakeAudioInput()
        agent = StubAgent()
        agent.agent_session = FakeAgentSession(FakeRoomIO(audio_input))

        result = agent._set_stt_participant("alice", context="test")
        assert result is True
        assert agent._current_stt_participant == "alice"
        assert audio_input._participant_identity == "alice"

    def test_returns_false_on_sdk_exception(self):
        audio_input = MagicMock()
        audio_input.set_participant.side_effect = RuntimeError("SDK error")
        agent = StubAgent()
        agent.agent_session = FakeAgentSession(FakeRoomIO(audio_input))

        result = agent._set_stt_participant("alice", context="test")
        assert result is False
        # Cache should NOT be updated on failure
        assert agent._current_stt_participant is None

    def test_successive_calls_update_cache(self):
        audio_input = FakeAudioInput()
        agent = StubAgent()
        agent.agent_session = FakeAgentSession(FakeRoomIO(audio_input))

        agent._set_stt_participant("alice", context="first")
        assert agent._current_stt_participant == "alice"

        agent._set_stt_participant("bob", context="second")
        assert agent._current_stt_participant == "bob"
        assert audio_input._participant_identity == "bob"


class TestRouteAudioToParticipant:
    """_route_audio_to_participant() orchestrates routing correctly."""

    @pytest.mark.asyncio
    async def test_solo_mode_no_stt_lock(self):
        """Single respondent: no STT lock, no muting, no spillover drain."""
        audio_input = FakeAudioInput()
        agent = StubAgent()
        agent.agent_session = FakeAgentSession(FakeRoomIO(audio_input))
        agent.participant_manager = FakeParticipantManager(participants=["alice"])

        await agent._route_audio_to_participant("alice", context="test_solo")

        assert agent.expected_respondent == "alice"
        assert agent.actual_respondent is None
        assert agent.turn_transition_time is not None
        # Solo mode: STT should NOT be locked to participant
        assert agent._current_stt_participant is None

    @pytest.mark.asyncio
    async def test_multi_mode_stt_lock(self):
        """Multiple respondents: STT locked to target participant."""
        audio_input = FakeAudioInput()
        agent = StubAgent()
        agent.agent_session = FakeAgentSession(FakeRoomIO(audio_input))
        agent.participant_manager = FakeParticipantManager(
            participants=["alice", "bob"]
        )

        await agent._route_audio_to_participant("bob", context="test_multi")

        assert agent.expected_respondent == "bob"
        # STT should be locked to bob (no observer mode)
        assert agent._current_stt_participant == "bob"
        assert audio_input._participant_identity == "bob"

    @pytest.mark.asyncio
    async def test_observer_mode_overrides_stt_to_observer(self):
        """In observer mode, STT should end up on observer (not respondent)."""
        audio_input = FakeAudioInput()
        agent = StubAgent()
        agent.agent_session = FakeAgentSession(FakeRoomIO(audio_input))
        agent.participant_manager = FakeParticipantManager(
            participants=["alice", "bob"],
            observer="observer1"
        )
        agent.observer_mode_enabled = True

        await agent._route_audio_to_participant("alice", context="test_observer")

        assert agent.expected_respondent == "alice"
        # STT should be on observer (overrides respondent lock)
        assert agent._current_stt_participant == "observer1"
        assert audio_input._participant_identity == "observer1"
        # pending_stt_transcript should be cleared
        assert agent.pending_stt_transcript is None

    @pytest.mark.asyncio
    async def test_delivery_state_set(self):
        """Delivery state should be set to 'delivering'."""
        agent = StubAgent()
        audio_input = FakeAudioInput()
        agent.agent_session = FakeAgentSession(FakeRoomIO(audio_input))
        agent.participant_manager = FakeParticipantManager(participants=["alice"])
        agent.current_question_num = 3

        await agent._route_audio_to_participant("alice", context="test_state")

        assert agent.question_delivery_state[(3, "alice")] == "delivering"

    @pytest.mark.asyncio
    async def test_skip_muting_flag(self):
        """skip_muting=True should not call manage_participant_muting."""
        audio_input = FakeAudioInput()
        agent = StubAgent()
        agent.agent_session = FakeAgentSession(FakeRoomIO(audio_input))
        agent.participant_manager = FakeParticipantManager(
            participants=["alice", "bob"]
        )
        agent.manage_participant_muting = AsyncMock()

        await agent._route_audio_to_participant("alice", context="test", skip_muting=True)
        agent.manage_participant_muting.assert_not_called()

    @pytest.mark.asyncio
    async def test_muting_called_by_default(self):
        """skip_muting=False (default) should kick off muting in multi mode."""
        audio_input = FakeAudioInput()
        agent = StubAgent()
        agent.agent_session = FakeAgentSession(FakeRoomIO(audio_input))
        agent.participant_manager = FakeParticipantManager(
            participants=["alice", "bob"]
        )
        muting_called = False
        original_manage = agent.manage_participant_muting

        async def track_muting(p):
            nonlocal muting_called
            muting_called = True
            await original_manage(p)

        agent.manage_participant_muting = track_muting

        await agent._route_audio_to_participant("alice", context="test")
        # Give the create_task a chance to run
        await asyncio.sleep(0.05)
        assert muting_called


class TestCurrentSttParticipantCache:
    """Event handlers should use _current_stt_participant instead of SDK internals."""

    def test_cache_used_for_identity_check(self):
        """Simulates the on_user_input_transcribed identity check logic."""
        agent = StubAgent()
        agent.expected_respondent = "alice"
        agent._current_stt_participant = "bob"

        # Mirrored logic from on_user_input_transcribed
        current_stt_participant = agent._current_stt_participant or 'UNKNOWN'
        assert current_stt_participant == "bob"

        # Should detect mismatch
        if current_stt_participant != agent.expected_respondent:
            agent.actual_respondent = current_stt_participant
        assert agent.actual_respondent == "bob"

    def test_cache_none_falls_back_to_unknown(self):
        agent = StubAgent()
        agent._current_stt_participant = None

        current_stt_participant = agent._current_stt_participant or 'UNKNOWN'
        assert current_stt_participant == 'UNKNOWN'

    def test_avatar_identity_filtered(self):
        """Avatar identities should be filtered out."""
        agent = StubAgent()
        agent._current_stt_participant = "avatar_agent_123"

        current_stt_participant = agent._current_stt_participant or 'UNKNOWN'
        assert agent._is_avatar_identity(current_stt_participant) is True

    def test_observer_command_check_uses_cache(self):
        """Simulates the observer command STT check logic."""
        agent = StubAgent()
        agent._current_stt_participant = "observer1"

        # Mirrored from on_user_input_transcribed observer command check
        current_stt_participant = agent._current_stt_participant
        observer_identity = "observer1"

        assert current_stt_participant == observer_identity


class TestPrivateSdkAccessConfinement:
    """Verify that _get_audio_input is the single point of SDK access."""

    def test_get_audio_input_is_only_sdk_accessor(self):
        """Read the source file and verify _room_io/_audio_input only in _get_audio_input."""
        import re
        import os

        src_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'moderator_agent.py')
        with open(src_path, 'r') as f:
            lines = f.readlines()

        violations = []
        in_get_audio_input = False

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # Track if we're inside _get_audio_input method
            if 'def _get_audio_input(self)' in line:
                in_get_audio_input = True
                continue
            if in_get_audio_input:
                # Method ends at next def or class or blank line after return
                if stripped.startswith('def ') or stripped.startswith('class '):
                    in_get_audio_input = False
                elif '_room_io' in line or '_audio_input' in line:
                    continue  # Inside _get_audio_input, this is fine

            if not in_get_audio_input:
                # Check for direct SDK access outside _get_audio_input
                # Exclude: comments, the _get_audio_input call itself, and _set_stt_participant
                if re.search(r'\._room_io\b', line) or re.search(r'\._audio_input\b', line):
                    # Allow references in _get_audio_input's own docstring or the method calling it
                    if '_get_audio_input' not in line and 'audio_input = self._get_audio_input' not in line:
                        # Allow comments
                        if not stripped.startswith('#') and not stripped.startswith('"""'):
                            violations.append((i, stripped))

        assert violations == [], (
            f"Found {len(violations)} private SDK access(es) outside _get_audio_input:\n"
            + "\n".join(f"  L{n}: {l}" for n, l in violations)
        )
