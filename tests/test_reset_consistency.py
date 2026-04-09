"""
Tests for reset function flag consistency.

Verifies that the domain reset functions (reset_for_repeat_flags,
reset_for_off_topic_flags) clear the correct non-watchdog flags.

Watchdog flags (_stt_nudge_given, _silence_watchdog_fired,
_idle_no_vad_nudge_fired, _first_nudge_given, _post_nudge_extended)
and epoch bumps are now owned by DeadlineManager and tested in
test_deadline_manager.py.

The nudge-for-short-offtopic helper is still mirrored inline as it
has no domain counterpart for non-watchdog flags.
"""

import time

import pytest

from src.domain.turn_state import reset_for_repeat_flags, reset_for_off_topic_flags
from src.domain.deadline_manager import DeadlineManager


# ── Minimal state simulation ─────────────────────────────────────────────────

class ResetState:
    """Has all flags touched by any of the three reset helpers."""

    def __init__(self):
        # ── Response / STT state ──
        self.captured_response = "some response"
        self._response_ready_set = True
        self.latest_user_response = "some response"
        self.response_captured = True
        self.last_stt_fragment = "fragment"
        self.pending_stt_transcript = "pending"
        self.response_fragments = ["frag"]
        self.last_fragment_time = time.time()
        self.actual_respondent = "ganesh"
        self._turn_accumulated_text = "accumulated text"

        # ── Flow control flags (still in domain functions) ──
        self.encouragement_given = True
        self._encouragement_followup_given = True
        self._last_transcript_progress_time = time.time()
        self.question_repeated = True
        self._short_offtopic_count = 3
        self._last_short_offtopic_norm = "some norm"
        self._had_stt_transcript_this_turn = True
        self.waiting_for_response = False
        self.last_speech_time = time.time()
        self._transition_filler_said = True
        self._ack_already_spoken = True
        self._prewarmed_ack_text = "Thank you"
        self._first_utterance_greeting_guard_used = True

        # ── STT health-check state ──
        self._first_vad_speaking_time = time.time()

        # ── Timing ──
        self._first_fragment_time = time.time()
        self._user_stopped_speaking_at = time.time()

        # ── Agent-level fields (not touched by domain functions) ──
        self.response_timeout_task_cancelled = False
        self.turn_monitor_task_cancelled = False
        self.turn_time_exceeded = True

        # ── DeadlineManager (for nudge-for-short-offtopic test) ──
        self._deadline_mgr = DeadlineManager()

    def apply_reset_for_repeat(self):
        """Use domain reset_for_repeat_flags plus wrapper fields."""
        reset_for_repeat_flags(self)
        # Extra fields managed by the class wrapper
        self._first_fragment_time = None
        self._user_stopped_speaking_at = None
        self.turn_time_exceeded = False
        self.response_timeout_task_cancelled = True
        self.turn_monitor_task_cancelled = True
        # DeadlineManager handles watchdog flags + epoch
        self._deadline_mgr.reset_for_retry(50.0)

    def apply_reset_for_off_topic(self):
        """Use domain reset_for_off_topic_flags plus wrapper fields."""
        reset_for_off_topic_flags(self)
        # Extra fields managed by the class wrapper
        self._first_fragment_time = None
        self._user_stopped_speaking_at = None
        self.turn_time_exceeded = False
        self.response_timeout_task_cancelled = True
        self.turn_monitor_task_cancelled = True
        # DeadlineManager handles watchdog flags + epoch
        self._deadline_mgr.reset_for_off_topic(60.0)

    def apply_nudge_for_short_offtopic(self):
        """Mirror of _nudge_for_short_offtopic_retry non-watchdog flag changes
        plus DeadlineManager reset."""
        self.captured_response = None
        self.latest_user_response = None
        self.response_captured = False
        self.last_stt_fragment = ""
        self._turn_accumulated_text = ""
        self.pending_stt_transcript = None
        self.response_fragments = []
        self.last_fragment_time = None
        self.actual_respondent = None

        self.waiting_for_response = True
        self.last_speech_time = None
        self._had_stt_transcript_this_turn = False
        self._first_fragment_time = None
        self._user_stopped_speaking_at = None
        self._last_transcript_progress_time = time.time()
        if not getattr(self, 'user_currently_speaking', False):
            self._first_vad_speaking_time = None

        # DeadlineManager handles watchdog flags + epoch
        self._deadline_mgr.reset_for_short_offtopic(15.0)

        self.response_timeout_task_cancelled = True
        self.turn_monitor_task_cancelled = True


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestResetForOffTopicFixes:
    """Verify _reset_for_off_topic clears previously missing flags."""

    def test_clears_turn_accumulated_text(self):
        state = ResetState()
        assert state._turn_accumulated_text != ""
        state.apply_reset_for_off_topic()
        assert state._turn_accumulated_text == ""

    def test_clears_encouragement_followup_given(self):
        state = ResetState()
        assert state._encouragement_followup_given is True
        state.apply_reset_for_off_topic()
        assert state._encouragement_followup_given is False

    def test_clears_last_transcript_progress_time(self):
        state = ResetState()
        assert state._last_transcript_progress_time is not None
        state.apply_reset_for_off_topic()
        assert state._last_transcript_progress_time is None

    def test_clears_silence_watchdog_fired_via_deadline_mgr(self):
        state = ResetState()
        state._deadline_mgr.mark_silence_watchdog_fired()
        assert state._deadline_mgr.silence_watchdog_fired is True
        state.apply_reset_for_off_topic()
        assert state._deadline_mgr.silence_watchdog_fired is False

    def test_clears_post_nudge_extended_via_deadline_mgr(self):
        state = ResetState()
        state._deadline_mgr._post_nudge_extended = True  # no public setter; test-only
        state.apply_reset_for_off_topic()
        assert state._deadline_mgr.post_nudge_extended is False


class TestResetForRepeatFixes:
    """Verify _reset_for_repeat clears previously missing flags."""

    def test_clears_greeting_guard(self):
        state = ResetState()
        assert state._first_utterance_greeting_guard_used is True
        state.apply_reset_for_repeat()
        assert state._first_utterance_greeting_guard_used is False

    def test_clears_post_nudge_extended_via_deadline_mgr(self):
        state = ResetState()
        state._deadline_mgr._post_nudge_extended = True  # no public setter; test-only
        state.apply_reset_for_repeat()
        assert state._deadline_mgr.post_nudge_extended is False


class TestNudgeShortOfftopicPreservation:
    """Verify _nudge_for_short_offtopic_retry intentionally preserves escalation state."""

    def test_preserves_encouragement_given(self):
        state = ResetState()
        state.apply_nudge_for_short_offtopic()
        assert state.encouragement_given is True

    def test_preserves_short_offtopic_count(self):
        state = ResetState()
        state.apply_nudge_for_short_offtopic()
        assert state._short_offtopic_count == 3

    def test_preserves_question_repeated(self):
        state = ResetState()
        state.apply_nudge_for_short_offtopic()
        assert state.question_repeated is True

    def test_does_bump_epoch_via_deadline_mgr(self):
        state = ResetState()
        original = state._deadline_mgr.epoch
        state.apply_nudge_for_short_offtopic()
        assert state._deadline_mgr.epoch == original + 1

    def test_does_restart_timeout(self):
        state = ResetState()
        state.apply_nudge_for_short_offtopic()
        assert state.response_timeout_task_cancelled is True

    def test_clears_post_nudge_extended_via_deadline_mgr(self):
        state = ResetState()
        state._deadline_mgr._post_nudge_extended = True  # no public setter; test-only
        state.apply_nudge_for_short_offtopic()
        assert state._deadline_mgr.post_nudge_extended is False


class TestResponseStateParity:
    """Verify all three helpers clear the same core response/STT flags."""

    CORE_RESPONSE_FLAGS = [
        "captured_response",
        "latest_user_response",
        "response_captured",
        "last_stt_fragment",
        "_turn_accumulated_text",
        "actual_respondent",
    ]

    def _get_flag_values(self, state):
        return {flag: getattr(state, flag) for flag in self.CORE_RESPONSE_FLAGS}

    def test_all_three_clear_core_response_state(self):
        for reset_name in ["apply_reset_for_repeat", "apply_reset_for_off_topic", "apply_nudge_for_short_offtopic"]:
            state = ResetState()
            getattr(state, reset_name)()
            values = self._get_flag_values(state)
            for flag, value in values.items():
                assert value is None or value is False or value == "" or value == [], \
                    f"{reset_name} did not clear {flag}: got {value!r}"

    def test_all_three_bump_epoch_via_deadline_mgr(self):
        for reset_name in ["apply_reset_for_repeat", "apply_reset_for_off_topic", "apply_nudge_for_short_offtopic"]:
            state = ResetState()
            original = state._deadline_mgr.epoch
            getattr(state, reset_name)()
            assert state._deadline_mgr.epoch == original + 1, \
                f"{reset_name} did not bump epoch via DeadlineManager"

    def test_all_three_clear_post_nudge_extended_via_deadline_mgr(self):
        for reset_name in ["apply_reset_for_repeat", "apply_reset_for_off_topic", "apply_nudge_for_short_offtopic"]:
            state = ResetState()
            state._deadline_mgr._post_nudge_extended = True  # no public setter; test-only
            getattr(state, reset_name)()
            assert state._deadline_mgr.post_nudge_extended is False, \
                f"{reset_name} did not clear post_nudge_extended via DeadlineManager"

    def test_all_three_clear_first_nudge_given_via_deadline_mgr(self):
        for reset_name in ["apply_reset_for_repeat", "apply_reset_for_off_topic", "apply_nudge_for_short_offtopic"]:
            state = ResetState()
            state._deadline_mgr.mark_nudge_given()
            getattr(state, reset_name)()
            assert state._deadline_mgr.first_nudge_given is False, \
                f"{reset_name} did not clear first_nudge_given via DeadlineManager"
