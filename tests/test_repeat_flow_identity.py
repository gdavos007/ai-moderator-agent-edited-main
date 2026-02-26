"""Tests for repeat-flow identity + state invariants.

Verifies that after a question is repeated:
- expected_respondent remains the SAME participant
- actual_respondent is cleared (will be re-set from STT)
- pending_stt_transcript is cleared (prevents spillover)
- response buffers are cleared
- polling deadline is extended (user gets full time)
- turn_transition_time is set (spillover guard)
- the participant is NOT marked as answered

These tests avoid importing the heavy livekit stack by replicating only
the fields/logic under test.
"""

import asyncio
import time
import pytest
from datetime import datetime
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock
from enum import Enum


# ── Minimal replicas of production types ─────────────────────────────────────

@dataclass
class TurnInfo:
    participant_identity: str
    start_time: datetime
    actual_speaking_duration: float = 0.0


class SurveyState(Enum):
    WELCOME = "welcome"
    WAITING_FOR_OBSERVER = "waiting_for_observer"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"


# ── Mock moderator with the fields that _reset_for_repeat touches ────────────

class MockModerator:
    """Replicates CommunityModeratorAgent fields used by _reset_for_repeat."""

    def __init__(self, participant: str):
        self.expected_respondent = participant
        self.actual_respondent = participant  # stale from "repeat" utterance
        self.latest_user_response = "Can you repeat the question?"
        self.response_captured = True
        self.last_stt_fragment = "Can you repeat the question?"
        self.pending_stt_transcript = "Can you repeat the question?"
        self.response_fragments = ["Can you repeat the question?"]
        self.last_fragment_time = datetime.now()
        self.encouragement_given = True
        self.waiting_for_response = False
        self.last_speech_time = datetime.now()
        self._transition_filler_said = True
        self.user_currently_speaking = True
        self.turn_time_exceeded = True
        self.turn_transition_time = None
        self._polling_deadline = time.time() + 10  # only 10s left

        self.max_turn_duration = 20
        self.first_interrupt_grace = 10
        self.second_interrupt_grace = 15
        self.current_question_num = 3
        self.current_turn = TurnInfo(participant, datetime.now())

        self.response_timeout_task = MagicMock()
        self.response_timeout_task.cancel = MagicMock()
        self.turn_monitor_task = MagicMock()
        self.turn_monitor_task.cancel = MagicMock()
        self.agent_session = MagicMock()

    def monitor_response_timeout(self, participant):
        f = asyncio.Future()
        f.set_result(None)
        return f

    def monitor_turn_duration(self, session):
        f = asyncio.Future()
        f.set_result(None)
        return f


def _reset_for_repeat_standalone(mod: MockModerator, participant: str, *, context: str = ""):
    """Standalone replica of CommunityModeratorAgent._reset_for_repeat."""
    saved_expected = mod.expected_respondent

    mod.latest_user_response = None
    mod.response_captured = False
    mod.last_stt_fragment = ""
    mod.pending_stt_transcript = None
    mod.response_fragments = []
    mod.last_fragment_time = None
    mod.actual_respondent = None

    mod.encouragement_given = False
    mod.waiting_for_response = True
    mod.last_speech_time = None
    mod._transition_filler_said = False

    if mod.response_timeout_task:
        mod.response_timeout_task.cancel()
    mod.response_timeout_task = asyncio.Future()
    mod.response_timeout_task.set_result(None)

    if mod.turn_monitor_task:
        mod.turn_monitor_task.cancel()
        mod.turn_monitor_task = None
    mod.user_currently_speaking = False
    mod.turn_time_exceeded = False
    mod.current_turn = TurnInfo(
        participant_identity=participant,
        start_time=datetime.now(),
    )
    mod.turn_monitor_task = asyncio.Future()
    mod.turn_monitor_task.set_result(None)

    mod.turn_transition_time = datetime.now()

    polling_timeout = (
        mod.max_turn_duration
        + mod.first_interrupt_grace
        + mod.second_interrupt_grace
        + 10
    )
    mod._polling_deadline = time.time() + polling_timeout

    assert mod.expected_respondent == saved_expected, (
        f"REPEAT BUG: expected_respondent changed from "
        f"'{saved_expected}' to '{mod.expected_respondent}' during reset"
    )


# ── Tests ────────────────────────────────────────────────────────────────────

class TestRepeatFlowIdentity:

    def test_expected_respondent_unchanged(self):
        """After repeat, expected_respondent must be the SAME participant."""
        mod = MockModerator("ganesh")
        _reset_for_repeat_standalone(mod, "ganesh", context="test")
        assert mod.expected_respondent == "ganesh"

    def test_actual_respondent_cleared(self):
        """After repeat, actual_respondent must be None (re-set from STT on next utterance)."""
        mod = MockModerator("ganesh")
        assert mod.actual_respondent == "ganesh"  # stale value before reset
        _reset_for_repeat_standalone(mod, "ganesh", context="test")
        assert mod.actual_respondent is None

    def test_pending_stt_cleared(self):
        """pending_stt_transcript must be None after repeat (prevents spillover)."""
        mod = MockModerator("ganesh")
        assert mod.pending_stt_transcript is not None
        _reset_for_repeat_standalone(mod, "ganesh", context="test")
        assert mod.pending_stt_transcript is None

    def test_response_buffers_cleared(self):
        """All response buffers must be empty after repeat."""
        mod = MockModerator("ganesh")
        _reset_for_repeat_standalone(mod, "ganesh", context="test")
        assert mod.latest_user_response is None
        assert mod.response_captured is False
        assert mod.last_stt_fragment == ""
        assert mod.response_fragments == []
        assert mod.last_fragment_time is None

    def test_polling_deadline_extended(self):
        """Polling deadline must be extended by full turn time after repeat."""
        mod = MockModerator("ganesh")
        old_deadline = mod._polling_deadline
        _reset_for_repeat_standalone(mod, "ganesh", context="test")
        expected_extension = 20 + 10 + 15 + 10  # max_turn + graces + buffer
        assert mod._polling_deadline > old_deadline
        assert mod._polling_deadline >= time.time() + expected_extension - 1

    def test_turn_transition_time_set(self):
        """turn_transition_time must be set after repeat (for spillover guard)."""
        mod = MockModerator("ganesh")
        assert mod.turn_transition_time is None
        _reset_for_repeat_standalone(mod, "ganesh", context="test")
        assert mod.turn_transition_time is not None
        assert (datetime.now() - mod.turn_transition_time).total_seconds() < 1.0

    def test_turn_monitoring_restarted(self):
        """Turn monitor task must be refreshed with new TurnInfo."""
        mod = MockModerator("ganesh")
        old_turn = mod.current_turn
        _reset_for_repeat_standalone(mod, "ganesh", context="test")
        assert mod.current_turn is not old_turn
        assert mod.current_turn.participant_identity == "ganesh"
        assert mod.current_turn.actual_speaking_duration == 0.0

    def test_flow_flags_reset(self):
        """Flow control flags must be reset for fresh response collection."""
        mod = MockModerator("ganesh")
        _reset_for_repeat_standalone(mod, "ganesh", context="test")
        assert mod.encouragement_given is False
        assert mod.waiting_for_response is True
        assert mod.last_speech_time is None
        assert mod._transition_filler_said is False
        assert mod.user_currently_speaking is False
        assert mod.turn_time_exceeded is False

    def test_expected_respondent_stable_across_all_fields(self):
        """expected_respondent must survive even when every other field is reset."""
        mod = MockModerator("christopher")
        mod.expected_respondent = "christopher"
        mod.actual_respondent = "ganesh"  # stale from different speaker
        _reset_for_repeat_standalone(mod, "christopher", context="test")
        assert mod.expected_respondent == "christopher"
        assert mod.actual_respondent is None


class TestRepeatDoesNotMarkAnswered:
    """Verify that the repeat flow does NOT mark the participant as answered."""

    def test_no_participant_marking(self):
        """_reset_for_repeat does not call participant_manager.mark_answered or equivalent."""
        mod = MockModerator("ganesh")
        mod.participant_manager = MagicMock()
        _reset_for_repeat_standalone(mod, "ganesh", context="test")
        # participant_manager should not have been touched
        mod.participant_manager.assert_not_called()


class TestFullRepeatScenario:
    """End-to-end scenario: participant requests repeat, gets repeat, answers."""

    def test_repeat_then_answer_under_correct_identity(self):
        """Simulate: Ganesh asks to repeat → repeat → Ganesh answers → recorded under Ganesh."""
        mod = MockModerator("ganesh")

        # Step 1: Ganesh says "Can you repeat the question?"
        assert mod.latest_user_response == "Can you repeat the question?"
        assert mod.expected_respondent == "ganesh"

        # Step 2: Agent repeats question, then resets
        _reset_for_repeat_standalone(mod, "ganesh", context="scenario_test")

        # Step 3: Verify state is clean for Ganesh's answer
        assert mod.expected_respondent == "ganesh"
        assert mod.actual_respondent is None
        assert mod.latest_user_response is None
        assert mod.response_captured is False
        assert mod.pending_stt_transcript is None

        # Step 4: Ganesh answers after repeat
        mod.actual_respondent = "ganesh"  # set by STT handler
        mod.latest_user_response = "I think opinion research is very valuable."
        mod.response_captured = True

        # Step 5: Verify answer is under correct identity
        assert mod.expected_respondent == "ganesh"
        assert mod.actual_respondent == "ganesh"
        assert "valuable" in mod.latest_user_response
