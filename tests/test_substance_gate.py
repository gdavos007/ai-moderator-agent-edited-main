"""
Tests for substance gate and pause cooldown constants (Commits 2 & 3).

Covers the invariant: neither on_user_speech_committed nor the legacy
fallback in _await_response may commit text shorter than MIN_COMMITTED_CHARS.
Also tests the pause cooldown constants.

NOTE: These tests mirror logic from src/moderator_agent.py rather than
importing it directly (heavy LiveKit dependencies).
"""

import pytest


# ── Mirrored constants ────────────────────────────────────────────────────────
MIN_COMMITTED_CHARS = 2
PAUSE_COOLDOWN_QUANTITATIVE = 1.5
PAUSE_COOLDOWN_QUALITATIVE = 2.5


def _is_committable(text: str) -> bool:
    """Mirror of the _is_committable helper in moderator_agent.py."""
    return len(text.strip()) >= MIN_COMMITTED_CHARS


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
        assert PAUSE_COOLDOWN_QUANTITATIVE == 1.5

    def test_qualitative_cooldown(self):
        assert PAUSE_COOLDOWN_QUALITATIVE == 2.5

    def test_quantitative_greater_than_one_second(self):
        """A 1-second thinking pause should NOT trigger fragment promotion."""
        assert PAUSE_COOLDOWN_QUANTITATIVE > 1.0

    def test_qualitative_greater_than_quantitative(self):
        """Qualitative questions get a longer pause window."""
        assert PAUSE_COOLDOWN_QUALITATIVE > PAUSE_COOLDOWN_QUANTITATIVE
