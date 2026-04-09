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

from src.domain.turn_state import TurnInfo, reset_for_repeat_flags
from src.domain.constants import SurveyState


# ── Mock moderator with the fields that _reset_for_repeat touches ────────────

class MockModerator:
    """Replicates CommunityModeratorAgent fields used by _reset_for_repeat."""

    def __init__(self, participant: str):
        self.expected_respondent = participant
        self.actual_respondent = participant  # stale from "repeat" utterance
        self.captured_response = "Can you repeat the question?"
        self.latest_user_response = "Can you repeat the question?"
        self.response_captured = True
        self.last_stt_fragment = "Can you repeat the question?"
        self.pending_stt_transcript = "Can you repeat the question?"
        self.response_fragments = ["Can you repeat the question?"]
        self.last_fragment_time = datetime.now()
        self._turn_accumulated_text = "Can you repeat the question?"
        self.encouragement_given = True
        self._encouragement_followup_given = False
        self._last_transcript_progress_time = None
        self._short_offtopic_count = 0
        self._last_short_offtopic_norm = None
        self._had_stt_transcript_this_turn = True
        self.waiting_for_response = False
        self.last_speech_time = datetime.now()
        self._transition_filler_said = True
        self._ack_already_spoken = False
        self._prewarmed_ack_text = None
        self._first_utterance_greeting_guard_used = False
        self._first_vad_speaking_time = None
        self.user_currently_speaking = True
        self.turn_time_exceeded = True
        self.turn_transition_time = None

        self.max_turn_duration = 20
        self.first_interrupt_grace = 10
        self.second_interrupt_grace = 15
        self.current_question_num = 3
        self.current_turn = TurnInfo(participant_identity=participant, start_time=datetime.now())

        from src.domain.deadline_manager import DeadlineManager
        self._deadline_mgr = DeadlineManager()
        self._deadline_mgr.set_deadline(10)  # only 10s left
        self.response_timeout_task = MagicMock()
        self.response_timeout_task.cancel = MagicMock()
        self.turn_monitor_task = MagicMock()
        self.turn_monitor_task.cancel = MagicMock()
        self.agent_session = MagicMock()

    @property
    def _response_epoch(self):
        return self._deadline_mgr.epoch

    @property
    def _polling_deadline(self):
        return self._deadline_mgr.deadline

    def monitor_response_timeout(self, participant, *, epoch=0):
        f = asyncio.Future()
        f.set_result(None)
        return f

    def monitor_turn_duration(self, session):
        f = asyncio.Future()
        f.set_result(None)
        return f


def _reset_for_repeat_standalone(mod: MockModerator, participant: str, *, context: str = ""):
    """Wrapper around domain reset_for_repeat_flags plus task/timing management."""
    saved_expected = mod.expected_respondent

    # Use domain function for core flag reset (no longer bumps epoch)
    reset_for_repeat_flags(mod)

    # Deadline/watchdog reset via DeadlineManager (bumps epoch, sets deadline)
    polling_timeout = (
        mod.max_turn_duration
        + mod.first_interrupt_grace
        + mod.second_interrupt_grace
        + 10
    )
    mod._deadline_mgr.reset_for_retry(polling_timeout)

    # Task management (not in domain function)
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


# ── Epoch-based stale-timeout regression tests ─────────────────────────────
# Regression for Q2 Ganesh case (2026-03-26): timeout nudge → repeat request →
# stale timeout task from the pre-repeat turn sets waiting_for_response=False
# and the agent records [NO RESPONSE - TIMEOUT] even though the participant
# was about to answer the repeated question.


class _FakeTimeout:
    """Simulates monitor_response_timeout logic with epoch guard."""

    @staticmethod
    def would_fire(mod: MockModerator, captured_epoch: int) -> bool:
        """Return True if a timeout task holding *captured_epoch* would
        set ``waiting_for_response = False`` — i.e. the stale-task race.

        Mirrors the three epoch checks in the production
        ``monitor_response_timeout``.
        """
        if mod._response_epoch != captured_epoch:
            return False  # Stale — silently exit
        if mod.last_speech_time is not None:
            return False  # User spoke
        if not mod.waiting_for_response:
            return False  # Already handled
        return True


class TestRepeatAfterTimeoutEpochGuard:
    """Regression: nudge → repeat → fresh answer must succeed.

    Scenario from logs/agent.log 2026-03-26 12:17:54 Q#2 for Ganesh:
      1. Timeout nudge fires ("Please go ahead and share your thoughts.")
      2. Ganesh says "Can you repeat the question?"
      3. Agent repeats question, calls _reset_for_repeat
      4. A stale timeout task from the OLD turn wakes up and sees
         last_speech_time=None / waiting_for_response=True (freshly reset)
      5. BUG (pre-fix): stale task sets waiting_for_response=False → timeout
      6. FIX: epoch guard prevents stale task from acting
    """

    def test_stale_timeout_cannot_end_repeated_turn(self):
        """A timeout task from the pre-repeat epoch must NOT set
        waiting_for_response=False on the repeated turn."""
        mod = MockModerator("ganesh")

        # Simulate: first turn starts, timeout monitor captures epoch
        mod._deadline_mgr.bump_epoch()  # epoch -> 1
        mod.waiting_for_response = True
        stale_epoch = mod._response_epoch  # epoch=1

        # Timeout nudge fires, user says "repeat", _reset_for_repeat runs
        _reset_for_repeat_standalone(mod, "ganesh", context="heuristic_repeat")

        # After reset, epoch has advanced
        assert mod._response_epoch == stale_epoch + 1
        assert mod.waiting_for_response is True
        assert mod.last_speech_time is None  # freshly cleared

        # Stale task wakes up and tries to fire — epoch guard blocks it
        assert not _FakeTimeout.would_fire(mod, stale_epoch), (
            "Stale timeout task must NOT fire after epoch has advanced"
        )

        # Verify the current-epoch task CAN still fire (if timeout actually expires)
        assert _FakeTimeout.would_fire(mod, mod._response_epoch), (
            "Current-epoch timeout must still be able to fire"
        )

    def test_nudge_then_repeat_then_fresh_answer_accepted(self):
        """After repeat, participant answers — must NOT be timed out."""
        mod = MockModerator("ganesh")
        mod._deadline_mgr.bump_epoch()  # epoch -> 1
        mod.waiting_for_response = True
        stale_epoch = mod._response_epoch

        # Timeout nudge → repeat → reset
        _reset_for_repeat_standalone(mod, "ganesh", context="heuristic_repeat")
        current_epoch = mod._response_epoch
        assert current_epoch == stale_epoch + 1

        # Participant speaks after the repeated question
        mod.last_speech_time = datetime.now()
        mod.actual_respondent = "ganesh"
        mod.latest_user_response = "I believe opinion research is very valuable."
        mod.response_captured = True

        # Stale task from old epoch cannot fire
        assert not _FakeTimeout.would_fire(mod, stale_epoch)
        # Current-epoch task also cannot fire — user spoke (last_speech_time set)
        assert not _FakeTimeout.would_fire(mod, current_epoch)
        # waiting_for_response remains True until polling loop processes
        assert mod.waiting_for_response is True

    def test_repeated_turn_timeout_only_if_actually_expires(self):
        """Repeated turn records [NO RESPONSE - TIMEOUT] ONLY if the
        repeated turn's own timeout fires (same epoch, no speech)."""
        mod = MockModerator("ganesh")
        mod._deadline_mgr.bump_epoch()  # epoch -> 1
        mod.waiting_for_response = True

        # Repeat happens
        _reset_for_repeat_standalone(mod, "ganesh", context="heuristic_repeat")
        repeated_epoch = mod._response_epoch

        # User does NOT speak after the repeat — genuine timeout
        assert mod.last_speech_time is None
        assert mod.waiting_for_response is True

        # Current-epoch task can fire (this is correct behavior)
        assert _FakeTimeout.would_fire(mod, repeated_epoch)

        # Simulate the timeout firing
        mod.waiting_for_response = False

        # Now the stale pre-repeat task also wakes — still blocked
        assert not _FakeTimeout.would_fire(mod, repeated_epoch - 1)

    def test_epoch_increments_on_every_reset(self):
        """Each _reset_for_repeat call must bump the epoch."""
        mod = MockModerator("ganesh")
        initial = mod._response_epoch

        _reset_for_repeat_standalone(mod, "ganesh", context="repeat_1")
        assert mod._response_epoch == initial + 1

        _reset_for_repeat_standalone(mod, "ganesh", context="repeat_2")
        assert mod._response_epoch == initial + 2

    def test_multiple_stale_tasks_all_blocked(self):
        """If multiple stale tasks exist (from cascading resets), ALL are
        blocked by their respective epoch mismatches."""
        mod = MockModerator("ganesh")
        mod._deadline_mgr.bump_epoch()  # epoch -> 1
        mod.waiting_for_response = True

        epochs_captured = []
        for i in range(3):
            epochs_captured.append(mod._response_epoch)
            _reset_for_repeat_standalone(mod, "ganesh", context=f"reset_{i}")

        # All previously-captured epochs are stale
        for old_epoch in epochs_captured:
            assert not _FakeTimeout.would_fire(mod, old_epoch), (
                f"Epoch {old_epoch} should be stale (current={mod._response_epoch})"
            )
