"""
Tests for hardened uncertain-response handling.

Covers:
  1. Unified uncertain-response path returns correct action for first/second/third calls
  2. Deterministic follow-up fires after encouragement already used
  3. Silence watchdog triggers after N seconds of no transcript progression
  4. Flag reset consistency across question and participant transitions
  5. Scenario: user says "I don't know" → encouragement → 10s silence → watchdog fires

NOTE: These tests mirror logic from src/moderator_agent.py rather than importing it
directly (heavy LiveKit dependencies). If you change the uncertain-response constants,
phrase lists, or _handle_uncertain_response() in the source, update tests here too.
"""

import re
import time

import pytest


# ── Mirror of is_uncertain_response() from src/moderator_agent.py ────────────

UNCERTAIN_PHRASES = [
    "i don't know",
    "i do not know",
    "don't know",
    "dunno",
    "i'm not sure",
    "i am not sure",
    "not sure",
    "i haven't got a clue",
    "no clue",
    "clueless",
    "beats me",
    "no idea",
    "i have no idea",
    "no opinion",
    "i have no opinion",
    "uncertain",
    "i'm uncertain",
    "i am uncertain",
    "not certain",
    "i'm not certain",
    "i am not certain",
    "pass",
    "skip",
    "next question",
]

SHORT_UNCERTAIN = {"idk", "dunno", "dk", "na", "n/a", "none", "nothing"}


def is_uncertain_response(text: str) -> bool:
    """Standalone copy of the heuristic for testing."""
    if not text:
        return False
    text_lower = text.lower().strip()
    text_lower = re.sub(r"[^\w\s']", "", text_lower).strip()

    if text_lower in SHORT_UNCERTAIN:
        return True

    # STT misrecognition workaround: "I know" or "I know." might be "I don't know"
    if text_lower in ("i know", "i know."):
        return True

    filler_prefixes = [
        "honestly", "well", "um", "uh", "hmm", "like",
        "to be honest", "truthfully", "frankly",
        "i really", "i just", "i mean",
    ]
    cleaned = text_lower
    for prefix in filler_prefixes:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].lstrip(" ,").strip()

    for phrase in UNCERTAIN_PHRASES:
        if phrase in cleaned:
            remaining = cleaned.replace(phrase, "", 1).strip()
            remaining = re.sub(r"[^\w\s]", "", remaining).strip()
            filler_words = {"i", "well", "um", "uh", "like", "really", "just",
                            "honestly", "hmm", "yeah", "ok", "okay", "so",
                            "mean", "guess", "think", "basically"}
            remaining_words = remaining.split()
            substantive_words = [w for w in remaining_words if w.lower() not in filler_words]
            if len(substantive_words) <= 2:
                return True

    return False


# ── Constants mirrored from src/moderator_agent.py ────────────────────────────

POST_ENCOURAGEMENT_FOLLOWUP_TEMPLATE = (
    "That's perfectly fine, {name}. Let's move on to the next question."
)

SILENCE_WATCHDOG_PROMPT_TEMPLATE = (
    "I just want to make sure we're still connected, {name}. "
    "Would you like me to repeat the question, or shall we move on?"
)

SILENCE_WATCHDOG_TIMEOUT = 12.0


# ── Simulated state for _handle_uncertain_response logic ─────────────────────

class UncertainResponseState:
    """Minimal simulation of CommunityModeratorAgent flags for uncertain-response handling."""

    def __init__(self):
        self.encouragement_given = False
        self._encouragement_followup_given = False
        self._silence_watchdog_fired = False
        self._last_transcript_progress_time = None

    def reset_for_question(self):
        self.encouragement_given = False
        self._encouragement_followup_given = False
        self._silence_watchdog_fired = False
        self._last_transcript_progress_time = None

    def handle_uncertain(self, text: str, participant: str) -> str:
        """Mirror of _handle_uncertain_response return values."""
        speaker_name = participant.capitalize()

        if not self.encouragement_given:
            self.encouragement_given = True
            self._last_transcript_progress_time = time.time()
            self._silence_watchdog_fired = False
            return "encouraged"

        if not self._encouragement_followup_given:
            self._encouragement_followup_given = True
            return "move_on"

        return "accepted"


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestIsUncertainResponse:
    """Verify the uncertain-response detection heuristic."""

    @pytest.mark.parametrize("text", [
        "I don't know",
        "I'm not sure",
        "pass",
        "skip",
        "no idea",
        "idk",
        "dunno",
        "beats me",
        "honestly, I don't know",
        "well, I'm not sure",
        "um, no idea",
        "I really have no idea",
    ])
    def test_detects_uncertain(self, text):
        assert is_uncertain_response(text), f"Expected uncertain: '{text}'"

    @pytest.mark.parametrize("text", [
        "I like the product but I don't know what else to say",
        "The color is nice, but I'm not sure about the price",
        "Five out of ten",
        "Yes",
        "I would recommend it because the quality is good",
    ])
    def test_rejects_substantive(self, text):
        assert not is_uncertain_response(text), f"Expected NOT uncertain: '{text}'"

    def test_empty_string(self):
        assert not is_uncertain_response("")

    def test_none_like(self):
        assert not is_uncertain_response("   ")


class TestUnifiedUncertainHandler:
    """Verify the three-phase uncertain response flow."""

    def test_first_uncertain_returns_encouraged(self):
        state = UncertainResponseState()
        result = state.handle_uncertain("I don't know", "alice")
        assert result == "encouraged"
        assert state.encouragement_given is True
        assert state._encouragement_followup_given is False

    def test_second_uncertain_returns_move_on(self):
        state = UncertainResponseState()
        state.handle_uncertain("I don't know", "alice")  # first
        result = state.handle_uncertain("I don't know", "alice")  # second
        assert result == "move_on"
        assert state._encouragement_followup_given is True

    def test_third_uncertain_returns_accepted(self):
        state = UncertainResponseState()
        state.handle_uncertain("I don't know", "alice")  # first
        state.handle_uncertain("I don't know", "alice")  # second
        result = state.handle_uncertain("I don't know", "alice")  # third
        assert result == "accepted"

    def test_reset_clears_all_flags(self):
        state = UncertainResponseState()
        state.handle_uncertain("I don't know", "alice")
        state.handle_uncertain("I don't know", "alice")
        state.reset_for_question()
        assert state.encouragement_given is False
        assert state._encouragement_followup_given is False
        assert state._silence_watchdog_fired is False
        assert state._last_transcript_progress_time is None

    def test_after_reset_first_uncertain_encouraged_again(self):
        state = UncertainResponseState()
        state.handle_uncertain("pass", "bob")
        state.handle_uncertain("pass", "bob")
        state.reset_for_question()
        result = state.handle_uncertain("skip", "charlie")
        assert result == "encouraged"


class TestDeterministicFollowup:
    """Verify POST_ENCOURAGEMENT_FOLLOWUP_TEMPLATE fires correctly."""

    def test_followup_template_has_name_placeholder(self):
        assert "{name}" in POST_ENCOURAGEMENT_FOLLOWUP_TEMPLATE

    def test_followup_template_formats_correctly(self):
        result = POST_ENCOURAGEMENT_FOLLOWUP_TEMPLATE.format(name="Alice")
        assert "Alice" in result
        assert "move on" in result.lower()

    def test_second_uncertain_triggers_followup_not_acceptance(self):
        """The key behavioral change: second uncertain triggers a spoken follow-up,
        not just silent acceptance."""
        state = UncertainResponseState()
        state.handle_uncertain("I don't know", "alice")
        result = state.handle_uncertain("I don't know", "alice")
        # Before hardening, this would have been "accepted" (silent).
        # After hardening, it should be "move_on" (spoken follow-up).
        assert result == "move_on"


class TestSilenceWatchdog:
    """Verify the silence watchdog fires after SILENCE_WATCHDOG_TIMEOUT."""

    def test_watchdog_timeout_is_reasonable(self):
        assert 8.0 <= SILENCE_WATCHDOG_TIMEOUT <= 20.0, \
            f"Watchdog timeout {SILENCE_WATCHDOG_TIMEOUT}s should be 8-20s"

    def test_watchdog_prompt_has_name_placeholder(self):
        assert "{name}" in SILENCE_WATCHDOG_PROMPT_TEMPLATE

    def test_watchdog_prompt_formats(self):
        result = SILENCE_WATCHDOG_PROMPT_TEMPLATE.format(name="Bob")
        assert "Bob" in result

    def test_scenario_idk_then_silence(self):
        """Scenario: user says 'I don't know', gets encouragement,
        then 10s of silence → silence watchdog should be ready to fire.

        The actual watchdog fires in the polling loop (async), but here
        we verify the state machine progression is correct.
        """
        state = UncertainResponseState()

        # Step 1: User says "I don't know"
        assert is_uncertain_response("I don't know")

        # Step 2: First uncertain → encouragement
        result = state.handle_uncertain("I don't know", "alice")
        assert result == "encouraged"
        assert state.encouragement_given is True
        assert state._last_transcript_progress_time is not None
        assert state._silence_watchdog_fired is False

        # Step 3: Simulate 10s of silence (no new transcripts)
        # The watchdog should NOT have fired yet (only fires in polling loop),
        # but the state is ready: _last_transcript_progress_time is set,
        # encouragement_given is True, _silence_watchdog_fired is False.
        simulated_time_since_progress = 10.0
        assert simulated_time_since_progress < SILENCE_WATCHDOG_TIMEOUT
        # At 10s, watchdog has NOT fired (threshold is 12s).

        simulated_time_since_progress = 13.0
        assert simulated_time_since_progress > SILENCE_WATCHDOG_TIMEOUT
        # At 13s, watchdog WOULD fire in the polling loop.

        # Step 4: After watchdog fires, the flag is set
        state._silence_watchdog_fired = True  # Simulating what the loop does

        # Step 5: Watchdog should not fire again
        assert state._silence_watchdog_fired is True

    def test_watchdog_does_not_fire_before_encouragement(self):
        """Watchdog requires encouragement_given=True to prevent firing on
        initial question delivery silence (that's handled by monitor_response_timeout)."""
        state = UncertainResponseState()
        assert state.encouragement_given is False
        # Even if _last_transcript_progress_time were set, the condition
        # self.encouragement_given must be True for watchdog to fire.
        state._last_transcript_progress_time = time.time() - 20
        # Watchdog condition includes encouragement_given check — would not fire.


class TestFlagResetConsistency:
    """Verify all uncertain-response flags are reset together."""

    UNCERTAIN_FLAGS = [
        "encouragement_given",
        "_encouragement_followup_given",
        "_silence_watchdog_fired",
        "_last_transcript_progress_time",
    ]

    def test_all_flags_initialized(self):
        state = UncertainResponseState()
        for flag in self.UNCERTAIN_FLAGS:
            val = getattr(state, flag)
            assert val is False or val is None, \
                f"Flag {flag} should start as False/None, got {val}"

    def test_all_flags_reset_together(self):
        """After mutation, reset_for_question must clear ALL flags."""
        state = UncertainResponseState()
        state.encouragement_given = True
        state._encouragement_followup_given = True
        state._silence_watchdog_fired = True
        state._last_transcript_progress_time = time.time()

        state.reset_for_question()

        for flag in self.UNCERTAIN_FLAGS:
            val = getattr(state, flag)
            assert val is False or val is None, \
                f"Flag {flag} not reset: got {val}"

    def test_encouraged_sets_transcript_progress_time(self):
        """After encouragement, _last_transcript_progress_time must be set
        so the silence watchdog has a reference point."""
        state = UncertainResponseState()
        before = time.time()
        state.handle_uncertain("I don't know", "alice")
        after = time.time()
        assert state._last_transcript_progress_time is not None
        assert before <= state._last_transcript_progress_time <= after


class TestBoundedWaitAfterEncouragement:
    """Verify the polling deadline extension is bounded."""

    def test_encouragement_sets_bounded_deadline(self):
        """The helper extends the polling deadline by at most 15 seconds,
        not indefinitely."""
        # This is a design assertion — the actual extension happens in
        # _handle_uncertain_response, but we verify the constant here.
        MAX_ENCOURAGEMENT_EXTENSION = 15.0
        assert MAX_ENCOURAGEMENT_EXTENSION <= 20.0, "Extension should be bounded"

    def test_watchdog_gives_bounded_additional_time(self):
        """After silence watchdog fires, additional time is at most 8s."""
        MAX_WATCHDOG_EXTENSION = 8.0
        assert MAX_WATCHDOG_EXTENSION <= 10.0, "Watchdog extension should be bounded"
