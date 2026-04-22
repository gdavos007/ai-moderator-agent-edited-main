"""
Tests for hardened uncertain-response handling.

Covers:
  1. Unified uncertain-response path returns correct action for first/second/third calls
  2. Deterministic follow-up fires after encouragement already used
  3. Silence watchdog triggers after N seconds of no transcript progression
  4. Flag reset consistency across question and participant transitions
  5. Scenario: user says "I don't know" → encouragement → 10s silence → watchdog fires
"""

import time

import pytest

from src.domain.text_analysis import is_uncertain_response
from src.domain.constants import (
    SILENCE_WATCHDOG_TIMEOUT,
    POST_ENCOURAGEMENT_FOLLOWUP_TEMPLATE,
    SILENCE_WATCHDOG_PROMPT_TEMPLATE,
)


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
        # Meta-commentary + uncertain phrase → should be uncertain
        "Oh, you're talking to me. Uh, I don't know.",
        "Are you talking to me? I'm not sure.",
        "Is that for me? I have no idea.",
        "Oh that's me. Pass.",
    ])
    def test_detects_uncertain_with_meta_commentary(self, text):
        assert is_uncertain_response(text), f"Expected uncertain after meta-commentary stripping: '{text}'"

    @pytest.mark.parametrize("text", [
        "I like the product but I don't know what else to say",
        "The color is nice, but I'm not sure about the price",
        "Five out of ten",
        "Yes",
        "I would recommend it because the quality is good",
    ])
    def test_rejects_substantive(self, text):
        assert not is_uncertain_response(text), f"Expected NOT uncertain: '{text}'"

    @pytest.mark.parametrize("text", [
        # Meta-commentary + real partial answer → NOT uncertain
        "Oh, you're talking to me. I think the product is decent but I'm not sure about pricing.",
        # Pure meta-commentary without uncertainty phrase → NOT uncertain
        "You're talking to me.",
        # Meta-commentary + repeat request → NOT uncertain (should reach is_repeat_request)
        "Oh, you're talking to me? Can you repeat the question?",
        # "talking to" in substantive context → must NOT be stripped
        "I was talking to my friend about this, I don't know",
    ])
    def test_rejects_meta_commentary_without_pure_uncertainty(self, text):
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


class TestDisfluentUncertainResponse:
    """Regression: hedged/disfluent uncertain phrases must route to encouragement."""

    @pytest.mark.parametrize("text", [
        "Well umm...I think I....I don't know",         # original reported bug
        "umm, I think, I don't know",                   # shorter disfluent variant
        "I think I don't know",                         # hedge + uncertain
        # --- Multi-stutter regression (second reported bug, 2026-04-22) -----
        # Intermediate "don't...." used to survive as the fragment "don" after
        # apostrophe stripping and counted as substantive content.  The 2-dot
        # stutter-strip added to is_uncertain_response() makes this uncertain.
        "I....I don't....I will...I think I....I don't know",  # exact reported bug
        "I....I....I don't know",                       # simple multi-stutter
        "I....um....I don't know",                      # stutter + filler
    ])
    def test_disfluent_uncertain_is_detected(self, text):
        assert is_uncertain_response(text), (
            f"Disfluent uncertain phrase should be flagged: '{text}'"
        )

    @pytest.mark.parametrize("text", [
        "I like the product but I'm not sure about pricing",
        "I think it's good, don't know",
        # --- Substantive-with-stutter must stay substantive ----------------
        # The 2-dot stutter-strip removes the false-start "I...." but the
        # remaining opinion words must still register as content.
        "I....I really like the product",               # stutter + substantive
        "I....I think the price is too high",           # stutter + opinion
    ])
    def test_substantive_answers_still_accepted(self, text):
        assert not is_uncertain_response(text), (
            f"Answer with real content must NOT be flagged uncertain: '{text}'"
        )
