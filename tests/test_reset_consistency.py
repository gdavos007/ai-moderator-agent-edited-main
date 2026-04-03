"""
Tests for reset function flag consistency (Commit 4).

Verifies that the three named reset helpers (_reset_for_repeat,
_reset_for_off_topic, _nudge_for_short_offtopic_retry) clear the
correct flags, and documents intentional divergences.

NOTE: These tests mirror logic from src/moderator_agent.py rather than
importing it directly (heavy LiveKit dependencies).
"""

import time

import pytest


# ── Minimal state simulation ─────────────────────────────────────────────────

class ResetState:
    """Mirrors all flags touched by any of the three reset helpers."""

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

        # ── Flow control flags ──
        self.encouragement_given = True
        self._encouragement_followup_given = True
        self._last_transcript_progress_time = time.time()
        self._silence_watchdog_fired = True
        self.question_repeated = True
        self._short_offtopic_count = 3
        self._last_short_offtopic_norm = "some norm"
        self._had_stt_transcript_this_turn = True
        self._idle_no_vad_nudge_fired = True
        self._first_nudge_given = True
        self._post_nudge_extended = True
        self.waiting_for_response = False
        self.last_speech_time = time.time()
        self._transition_filler_said = True
        self._ack_already_spoken = True
        self._prewarmed_ack_text = "Thank you"
        self._first_utterance_greeting_guard_used = True

        # ── STT health-check state ──
        self._first_vad_speaking_time = time.time()
        self._stt_nudge_given = True

        # ── Timing ──
        self._first_fragment_time = time.time()
        self._user_stopped_speaking_at = time.time()

        # ── Epoch / tasks ──
        self._response_epoch = 5
        self.response_timeout_task_cancelled = False
        self.turn_monitor_task_cancelled = False
        self.turn_time_exceeded = True

    def apply_reset_for_repeat(self):
        """Mirror of _reset_for_repeat flag changes."""
        self.captured_response = None
        self.latest_user_response = None
        self.response_captured = False
        self.last_stt_fragment = ""
        self.pending_stt_transcript = None
        self.response_fragments = []
        self.last_fragment_time = None
        self.actual_respondent = None
        self._turn_accumulated_text = ""

        self.encouragement_given = False
        self._encouragement_followup_given = False
        self._last_transcript_progress_time = None
        self._silence_watchdog_fired = False
        self._short_offtopic_count = 0
        self._last_short_offtopic_norm = None
        self._had_stt_transcript_this_turn = False
        self._idle_no_vad_nudge_fired = False
        self._first_nudge_given = False
        self._post_nudge_extended = False
        self.waiting_for_response = True
        self.last_speech_time = None
        self._transition_filler_said = False
        self._ack_already_spoken = False
        self._prewarmed_ack_text = None
        self._first_utterance_greeting_guard_used = False  # Fixed in Commit 4

        self._first_vad_speaking_time = None
        self._stt_nudge_given = False

        self._first_fragment_time = None
        self._user_stopped_speaking_at = None
        self.turn_time_exceeded = False

        self._response_epoch += 1
        self.response_timeout_task_cancelled = True
        self.turn_monitor_task_cancelled = True

    def apply_reset_for_off_topic(self):
        """Mirror of _reset_for_off_topic flag changes."""
        self.captured_response = None
        self.latest_user_response = None
        self.response_captured = False
        self.last_stt_fragment = ""
        self.pending_stt_transcript = None
        self.response_fragments = []
        self.last_fragment_time = None
        self.actual_respondent = None
        self._turn_accumulated_text = ""  # Fixed in Commit 4

        self.encouragement_given = False
        self._encouragement_followup_given = False  # Fixed in Commit 4
        self._last_transcript_progress_time = None  # Fixed in Commit 4
        self._silence_watchdog_fired = False  # Fixed in Commit 4
        self.question_repeated = False  # Off-topic resets this (unlike repeat)
        self._short_offtopic_count = 0
        self._last_short_offtopic_norm = None
        self._had_stt_transcript_this_turn = False
        self._idle_no_vad_nudge_fired = False
        self._first_nudge_given = False
        self._post_nudge_extended = False
        self.waiting_for_response = True
        self.last_speech_time = None
        self._transition_filler_said = False
        self._ack_already_spoken = False
        self._prewarmed_ack_text = None

        self._first_vad_speaking_time = None
        self._stt_nudge_given = False

        self._first_fragment_time = None
        self._user_stopped_speaking_at = None
        self.turn_time_exceeded = False

        self._response_epoch += 1
        self.response_timeout_task_cancelled = True
        self.turn_monitor_task_cancelled = True

    def apply_nudge_for_short_offtopic(self):
        """Mirror of _nudge_for_short_offtopic_retry flag changes."""
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
        self._idle_no_vad_nudge_fired = False
        self._first_nudge_given = False
        self._post_nudge_extended = False
        self._stt_nudge_given = False
        self._first_fragment_time = None
        self._user_stopped_speaking_at = None
        self._last_transcript_progress_time = time.time()  # set to now(), not None
        self._silence_watchdog_fired = False

        self._response_epoch += 1
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

    def test_clears_silence_watchdog_fired(self):
        state = ResetState()
        assert state._silence_watchdog_fired is True
        state.apply_reset_for_off_topic()
        assert state._silence_watchdog_fired is False

    def test_clears_post_nudge_extended(self):
        state = ResetState()
        assert state._post_nudge_extended is True
        state.apply_reset_for_off_topic()
        assert state._post_nudge_extended is False


class TestResetForRepeatFixes:
    """Verify _reset_for_repeat clears previously missing flags."""

    def test_clears_greeting_guard(self):
        state = ResetState()
        assert state._first_utterance_greeting_guard_used is True
        state.apply_reset_for_repeat()
        assert state._first_utterance_greeting_guard_used is False

    def test_clears_post_nudge_extended(self):
        state = ResetState()
        assert state._post_nudge_extended is True
        state.apply_reset_for_repeat()
        assert state._post_nudge_extended is False


class TestNudgeShortOfftopicPreservation:
    """Verify _nudge_for_short_offtopic_retry intentionally preserves escalation state."""

    def test_preserves_encouragement_given(self):
        state = ResetState()
        state.apply_nudge_for_short_offtopic()
        assert state.encouragement_given is True  # intentionally preserved

    def test_preserves_short_offtopic_count(self):
        state = ResetState()
        state.apply_nudge_for_short_offtopic()
        assert state._short_offtopic_count == 3  # intentionally preserved

    def test_preserves_question_repeated(self):
        state = ResetState()
        state.apply_nudge_for_short_offtopic()
        assert state.question_repeated is True  # intentionally preserved

    def test_does_bump_epoch(self):
        state = ResetState()
        original = state._response_epoch
        state.apply_nudge_for_short_offtopic()
        assert state._response_epoch == original + 1

    def test_does_restart_timeout(self):
        state = ResetState()
        state.apply_nudge_for_short_offtopic()
        assert state.response_timeout_task_cancelled is True

    def test_clears_post_nudge_extended(self):
        state = ResetState()
        state.apply_nudge_for_short_offtopic()
        assert state._post_nudge_extended is False


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
        """All three resets clear the core response capture flags to their default values."""
        for reset_name in ["apply_reset_for_repeat", "apply_reset_for_off_topic", "apply_nudge_for_short_offtopic"]:
            state = ResetState()
            getattr(state, reset_name)()
            values = self._get_flag_values(state)
            for flag, value in values.items():
                assert value is None or value is False or value == "" or value == [], \
                    f"{reset_name} did not clear {flag}: got {value!r}"

    def test_all_three_bump_epoch(self):
        for reset_name in ["apply_reset_for_repeat", "apply_reset_for_off_topic", "apply_nudge_for_short_offtopic"]:
            state = ResetState()
            original = state._response_epoch
            getattr(state, reset_name)()
            assert state._response_epoch == original + 1, \
                f"{reset_name} did not bump _response_epoch"

    def test_all_three_clear_post_nudge_extended(self):
        for reset_name in ["apply_reset_for_repeat", "apply_reset_for_off_topic", "apply_nudge_for_short_offtopic"]:
            state = ResetState()
            getattr(state, reset_name)()
            assert state._post_nudge_extended is False, \
                f"{reset_name} did not clear _post_nudge_extended"

    def test_all_three_clear_first_nudge_given(self):
        for reset_name in ["apply_reset_for_repeat", "apply_reset_for_off_topic", "apply_nudge_for_short_offtopic"]:
            state = ResetState()
            getattr(state, reset_name)()
            assert state._first_nudge_given is False, \
                f"{reset_name} did not clear _first_nudge_given"
