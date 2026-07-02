"""
Tests for substance gate and pause cooldown constants (Commits 2 & 3).

Covers the invariant: neither on_user_speech_committed nor the legacy
fallback in _await_response may commit text shorter than MIN_COMMITTED_CHARS.
Also tests the pause cooldown constants.
"""

import pytest

from src.domain.text_analysis import _is_committable
from src.domain.constants import (
    MIN_COMMITTED_CHARS,
    PAUSE_COOLDOWN_QUANTITATIVE,
    PAUSE_COOLDOWN_QUALITATIVE,
    STABILIZATION_QUANTITATIVE,
    STABILIZATION_QUALITATIVE,
    POLL_WAIT_CAP_QUANTITATIVE,
    POLL_WAIT_CAP_DEFAULT,
)


# ── Tests for _is_committable ─────────────────────────────────────────────────

class TestIsCommittable:
    """Verify the substance gate threshold."""

    @pytest.mark.parametrize("text", [
        "", "  ", "I", "A", "i", "a",
    ])
    def test_rejects_micro_fragments(self, text):
        assert not _is_committable(text), f"Expected NOT committable: '{text}'"

    @pytest.mark.parametrize("text", [
        "No", "OK", "ok", "Yes", "five", "um", "Hi",
        "I think it's great",
        "No opinion",
    ])
    def test_accepts_valid_responses(self, text):
        assert _is_committable(text), f"Expected committable: '{text}'"


# ── Tests for primary commitment path (on_user_speech_committed) ──────────────

class CommittedPathState:
    """Simulates the state modified by on_user_speech_committed."""

    def __init__(self):
        self.captured_response = None
        self._response_ready_set = False
        self._turn_accumulated_text = ""
        self._last_transcript_progress_time = None
        self._had_stt_transcript_this_turn = False
        self._first_fragment_time = None

    def simulate_committed(self, transcript: str) -> None:
        """Mirror of the substance-gate logic in on_user_speech_committed."""
        transcript = transcript.strip()
        if not transcript:
            return

        if not _is_committable(transcript):
            # Accumulate micro-fragment
            if self._turn_accumulated_text:
                self._turn_accumulated_text += " " + transcript
            else:
                self._turn_accumulated_text = transcript
            # Still update tracking
            self._last_transcript_progress_time = "updated"
            self._had_stt_transcript_this_turn = True
            return

        # Include accumulated text
        corrected = transcript
        if self._turn_accumulated_text:
            corrected = f"{self._turn_accumulated_text} {corrected}"
            self._turn_accumulated_text = ""

        self.captured_response = corrected
        self._response_ready_set = True
        self._last_transcript_progress_time = "updated"
        self._had_stt_transcript_this_turn = True


class TestPrimaryCommitPath:
    """Verify substance gate in on_user_speech_committed."""

    def test_micro_fragment_accumulated_not_committed(self):
        state = CommittedPathState()
        state.simulate_committed("I")
        assert state.captured_response is None
        assert state._response_ready_set is False
        assert state._turn_accumulated_text == "I"

    def test_valid_response_committed(self):
        state = CommittedPathState()
        state.simulate_committed("No")
        assert state.captured_response == "No"
        assert state._response_ready_set is True

    def test_micro_then_full_response(self):
        """Micro-fragment accumulated, then full response incorporates it."""
        state = CommittedPathState()
        state.simulate_committed("I")
        assert state.captured_response is None
        assert state._turn_accumulated_text == "I"

        state.simulate_committed("I think it's great")
        assert state.captured_response == "I I think it's great"
        assert state._turn_accumulated_text == ""

    def test_multiple_micro_fragments_accumulated(self):
        state = CommittedPathState()
        state.simulate_committed("I")
        state.simulate_committed("A")
        assert state._turn_accumulated_text == "I A"
        assert state.captured_response is None

    def test_tracking_updated_for_micro_fragment(self):
        state = CommittedPathState()
        state.simulate_committed("I")
        assert state._had_stt_transcript_this_turn is True
        assert state._last_transcript_progress_time == "updated"

    def test_empty_string_ignored(self):
        state = CommittedPathState()
        state.simulate_committed("")
        assert state.captured_response is None
        assert state._turn_accumulated_text == ""

    def test_whitespace_only_ignored(self):
        state = CommittedPathState()
        state.simulate_committed("   ")
        assert state.captured_response is None
        assert state._turn_accumulated_text == ""


# ── Tests for legacy fallback path (_await_response) ──────────────────────────

class TestLegacyFallbackPath:
    """Verify substance gate in the legacy fragment-promotion path."""

    def test_micro_fragment_not_promoted(self):
        """latest_user_response of 'I' should NOT be promoted to captured_response."""
        latest = "I"
        assert not _is_committable(latest)

    def test_valid_fragment_promoted(self):
        """latest_user_response of 'No' should be promotable."""
        latest = "No"
        assert _is_committable(latest)

    def test_short_numeric_word_promoted(self):
        """STT emits numbers as words: 'five' is 4 chars, committable."""
        latest = "five"
        assert _is_committable(latest)


# ── Tests for pause cooldown constants (Commit 3) ────────────────────────────

class TestPauseCooldownConstants:
    """Verify pause cooldown values used in _await_response fragment promotion."""

    def test_quantitative_cooldown(self):
        # Priority 2 latency work (2026-07-01): lowered 1.5→0.6 so short,
        # high-confidence quantitative answers are acknowledged quickly.
        assert PAUSE_COOLDOWN_QUANTITATIVE == 0.6

    def test_qualitative_cooldown(self):
        # 2026-07-02 latency work: lowered 2.5→1.2 after Priority 1 moved
        # analysis off the critical path (qual cooldown was the dominant latency).
        assert PAUSE_COOLDOWN_QUALITATIVE == 1.2

    def test_qualitative_cooldown_still_absorbs_brief_pause(self):
        """Qual cooldown must stay meaningfully above the quant cooldown so a
        brief mid-thought pause doesn't clip an open-ended answer."""
        assert PAUSE_COOLDOWN_QUALITATIVE >= 1.0

    def test_quantitative_cooldown_is_sub_second(self):
        """Quant answers are short/high-confidence — cooldown deliberately < 1s."""
        assert 0.3 <= PAUSE_COOLDOWN_QUANTITATIVE < 1.0

    def test_qualitative_greater_than_quantitative(self):
        """Qualitative questions get a longer pause window."""
        assert PAUSE_COOLDOWN_QUALITATIVE > PAUSE_COOLDOWN_QUANTITATIVE


class TestPriority2LatencyGates:
    """Verify the Priority 2 fast-gate constants for quantitative questions."""

    def test_stabilization_quant_is_short(self):
        assert 0.3 <= STABILIZATION_QUANTITATIVE <= 1.0

    def test_stabilization_qual_longer_than_quant(self):
        assert STABILIZATION_QUALITATIVE > STABILIZATION_QUANTITATIVE

    def test_poll_cap_quant_is_sub_half_second(self):
        assert 0.3 <= POLL_WAIT_CAP_QUANTITATIVE <= 0.5

    def test_poll_cap_quant_faster_than_default(self):
        assert POLL_WAIT_CAP_QUANTITATIVE < POLL_WAIT_CAP_DEFAULT
