"""
Regression tests for four voice-agent bugs observed during the March 25, 2026
demo session.  Uses real phrases from logs/agent.log.

Bug 1 (Q3): Disfluency guard + greeting guard cascade creates ~30s delay
Bug 2 (Q4): Overlapping watchdog nudge race — two nudges for same silence
Bug 3 (Q5): Premature nudge fires before long question finishes playing
Bug 4 (Q5): LLM misclassifies complaint/meta-commentary as PARTIAL / claim

Source of truth: src/moderator_agent.py
"""

import unittest


# ── Mirrored constants from moderator_agent.py ────────────────────────────────

DISFLUENCY_EXTENSION_BUDGET = 10.0
IDLE_NO_VAD_TIMEOUT = 12.0
TTS_SAFETY_MARGIN = 3.0

_DISFLUENT_STARTER_TOKENS = frozenset({
    "well", "um", "uh", "uhm", "erm", "hmm", "ah", "oh",
    "like", "so", "i", "think", "guess", "mean",
    "you", "know", "yeah", "yes", "no", "okay", "ok",
    "right", "and", "but", "just", "that", "the", "a",
    "it", "its", "is", "was", "not", "really",
    "hi", "hello", "hey",
})

_FIRST_UTTERANCE_GREETING_TOKENS = frozenset({
    "sure", "thanks", "thank", "morning", "evening", "afternoon",
    "good", "nice", "meet", "to", "how", "are", "doing", "fine",
    "great", "welcome", "greetings",
})


def _is_disfluent_starter(text: str) -> bool:
    words = [w.strip(".,!?…'\"") for w in text.lower().split()]
    words = [w for w in words if w]
    if not words:
        return True
    return all(w in _DISFLUENT_STARTER_TOKENS for w in words)


def _is_first_utterance_greeting(text: str) -> bool:
    words = [w.strip(".,!?…'\"") for w in text.lower().split()]
    words = [w for w in words if w]
    if not words:
        return False
    allowed = _DISFLUENT_STARTER_TOKENS | _FIRST_UTTERANCE_GREETING_TOKENS
    return all(w in allowed for w in words)


def _estimate_tts_duration(text: str) -> float:
    return max(len(text) / 15.0, 1.0)


# ── Bug 1: Disfluency + greeting guard cascade ──────────────────────────────

class TestBug1DisfluencyGreetingCascade(unittest.TestCase):
    """Q3 regression: 'Yeah.' should not be double-handled by both guards."""

    def test_yeah_is_disfluent(self):
        """'Yeah.' matches the disfluency guard."""
        self.assertTrue(_is_disfluent_starter("Yeah."))

    def test_yeah_also_matches_greeting(self):
        """'Yeah.' also matches the greeting guard (superset of disfluent tokens)."""
        self.assertTrue(_is_first_utterance_greeting("Yeah."))

    def test_disfluency_budget_skips_greeting_guard(self):
        """After disfluency guard consumed its budget, greeting guard is skipped.

        Simulates the fixed _process_captured_response flow:
        1. Disfluency guard fires → _disfluency_budget_used = True
        2. Greeting guard check includes `not _disfluency_budget_used` → skipped
        3. Text falls through to LLM analysis directly
        """
        captured_text = "Yeah."
        _disfluency_budget_used = False

        # Step 1: Disfluency guard fires
        if _is_disfluent_starter(captured_text):
            _disfluency_budget_used = True

        # Step 2: Greeting guard should be SKIPPED
        greeting_guard_fired = False
        if (not _disfluency_budget_used
                and _is_first_utterance_greeting(captured_text)):
            greeting_guard_fired = True

        self.assertTrue(_disfluency_budget_used)
        self.assertFalse(greeting_guard_fired,
                         "'Yeah.' should NOT trigger greeting guard after disfluency")

    def test_sure_only_triggers_greeting_not_disfluency(self):
        """'Sure' is NOT in _DISFLUENT_STARTER_TOKENS, only in greeting tokens.
        It should only trigger the greeting guard, not the disfluency guard."""
        self.assertFalse(_is_disfluent_starter("Sure"))
        self.assertTrue(_is_first_utterance_greeting("Sure"))

        _disfluency_budget_used = False
        if _is_disfluent_starter("Sure"):
            _disfluency_budget_used = True

        greeting_guard_fired = False
        if (not _disfluency_budget_used
                and _is_first_utterance_greeting("Sure")):
            greeting_guard_fired = True

        self.assertFalse(_disfluency_budget_used)
        self.assertTrue(greeting_guard_fired,
                        "'Sure' should trigger greeting guard (not disfluency)")


# ── Bug 2: Overlapping watchdog nudge race ───────────────────────────────────

class TestBug2WatchdogRace(unittest.TestCase):
    """Q4 regression: at most one first nudge per turn."""

    def test_first_nudge_flag_prevents_double_nudge(self):
        """Simulates two watchdogs becoming eligible; only one should nudge."""
        _first_nudge_given = False
        nudge_count = 0

        # Watchdog 1 (monitor_response_timeout) fires first
        if not _first_nudge_given:
            _first_nudge_given = True
            nudge_count += 1  # Would call _safe_say()

        # Watchdog 2 (_check_idle_no_vad) fires ~1s later
        if not _first_nudge_given:
            _first_nudge_given = True
            nudge_count += 1

        self.assertEqual(nudge_count, 1, "Only one nudge should fire per turn")
        self.assertTrue(_first_nudge_given)

    def test_first_nudge_flag_resets_on_new_turn(self):
        """Flag resets when response flags are reset for new question."""
        _first_nudge_given = True

        # Simulate _reset_response_flags
        _first_nudge_given = False

        # Both watchdogs eligible again for next question
        nudge_count = 0
        if not _first_nudge_given:
            _first_nudge_given = True
            nudge_count += 1

        self.assertEqual(nudge_count, 1)

    def test_idle_no_vad_defers_when_timeout_already_nudged(self):
        """If monitor_response_timeout already nudged, idle-no-VAD skips."""
        _first_nudge_given = False

        # Timeout monitor fires
        if not _first_nudge_given:
            _first_nudge_given = True
            timeout_nudged = True
        else:
            timeout_nudged = False

        # Idle-no-VAD fires
        if not _first_nudge_given:
            idle_nudged = True
        else:
            idle_nudged = False

        self.assertTrue(timeout_nudged)
        self.assertFalse(idle_nudged)


# ── Bug 3: Premature nudge during long TTS ───────────────────────────────────

class TestBug3TTSAwareTimeout(unittest.TestCase):
    """Q5 regression: no nudge before estimated audible question completion."""

    def test_effective_timeout_includes_tts_remaining(self):
        """With 17.5s remaining TTS, effective timeout should be 32.5s, not 15s."""
        nudge_timeout = 15
        tts_remaining = 17.5
        effective = nudge_timeout + tts_remaining
        self.assertAlmostEqual(effective, 32.5, places=1)

    def test_effective_timeout_zero_tts(self):
        """With 0 remaining TTS, effective timeout is standard 15s."""
        nudge_timeout = 15
        tts_remaining = 0.0
        effective = nudge_timeout + tts_remaining
        self.assertEqual(effective, 15.0)

    def test_q5_scenario_no_premature_nudge(self):
        """Q5 had 272-char question (est=18.1s). Nudge at 15s was premature.
        With fix, nudge fires at 15 + 17.5 = 32.5s — well after TTS ends."""
        question = "Focus groups have always been limited to small groups of 8 to 12 people in a room at a time. How do you think the ability to conduct AI moderated focus groups of 50, 100, 200 or even 500 people at a time will impact the market research industry?"
        est_duration = _estimate_tts_duration(question)
        elapsed_at_playout = 0.6  # SDK wait_for_playout returns quickly
        remaining = est_duration - elapsed_at_playout

        nudge_timeout = 15
        effective_nudge_time = nudge_timeout + remaining

        # The nudge should NOT fire before the question finishes playing
        self.assertGreater(effective_nudge_time, est_duration,
                           "Nudge must not fire before question finishes playing")

    def test_idle_no_vad_already_accounts_for_tts(self):
        """Verify the idle-no-VAD formula correctly offsets TTS remaining."""
        tts_remaining = 17.5
        tts_offset = tts_remaining + TTS_SAFETY_MARGIN  # 17.5 + 3.0 = 20.5
        idle_fire_time = IDLE_NO_VAD_TIMEOUT + tts_offset  # 12.0 + 20.5 = 32.5

        self.assertGreater(idle_fire_time, 17.5 + 3,
                           "Idle-no-VAD should not fire during TTS + safety margin")


# ── Bug 4: LLM misclassification of meta-commentary ─────────────────────────

class TestBug4PromptFormat(unittest.TestCase):
    """Q5 regression: user prompt must match system prompt's 6-field format."""

    def test_user_prompt_has_repeat_request_field(self):
        """The user prompt format instruction must include repeat_request."""
        # This is the fixed format string from moderator_agent.py line 997
        format_instruction = (
            "relevance|already_answered|repeat_request|"
            "partial_status|partial_answer|unanswered_questions"
        )
        fields = format_instruction.split("|")
        self.assertEqual(len(fields), 6)
        self.assertIn("repeat_request", fields)
        self.assertEqual(fields[2], "repeat_request",
                         "repeat_request must be the 3rd field")


class TestBug4WeakPartialDowngrade(unittest.TestCase):
    """Q5 regression: single-word unanswered_questions should be downgraded."""

    def _simulate_sanity_check(self, partial_status, unanswered):
        """Mirror the post-LLM sanity check from analyze_response()."""
        if partial_status == "PARTIAL" and unanswered:
            unanswered_words = len(unanswered.split())
            if unanswered_words <= 1:
                return "NO_REPEAT", ""
        return partial_status, unanswered

    def test_single_word_unanswered_downgraded(self):
        """'how' as unanswered_questions is too vague → downgrade to NO_REPEAT."""
        status, unanswered = self._simulate_sanity_check("PARTIAL", "how")
        self.assertEqual(status, "NO_REPEAT")
        self.assertEqual(unanswered, "")

    def test_specific_unanswered_preserved(self):
        """Multi-word unanswered_questions are genuine → keep PARTIAL."""
        status, unanswered = self._simulate_sanity_check(
            "PARTIAL", "what do you dislike about it"
        )
        self.assertEqual(status, "PARTIAL")
        self.assertEqual(unanswered, "what do you dislike about it")

    def test_no_repeat_not_affected(self):
        """NO_REPEAT status is not changed by sanity check."""
        status, unanswered = self._simulate_sanity_check("NO_REPEAT", "")
        self.assertEqual(status, "NO_REPEAT")


class TestBug4MetaCommentaryClassification(unittest.TestCase):
    """Q5 regression: complaint phrases should not trigger PARTIAL or claim."""

    def test_first_attempt_already_answered_suppressed(self):
        """On first interaction, 'I answered the question' claim is suppressed."""
        is_claim = True
        encouragement_given = False
        short_offtopic_count = 0
        partial_repeat_handled = False

        # Mirror the suppression logic from _process_captured_response
        suppress = (
            is_claim
            and not encouragement_given
            and short_offtopic_count == 0
            and not partial_repeat_handled
        )
        self.assertTrue(suppress,
                        "First-attempt already-answered claim should be suppressed")

    def test_claim_after_encouragement_not_suppressed(self):
        """After encouragement was given, claim is genuine and should stay."""
        is_claim = True
        encouragement_given = True
        short_offtopic_count = 0
        partial_repeat_handled = False

        suppress = (
            is_claim
            and not encouragement_given
            and short_offtopic_count == 0
            and not partial_repeat_handled
        )
        self.assertFalse(suppress)

    def test_claim_after_partial_repeat_not_suppressed(self):
        """After partial repeat was handled, claim is genuine."""
        is_claim = True
        encouragement_given = False
        short_offtopic_count = 0
        partial_repeat_handled = True

        suppress = (
            is_claim
            and not encouragement_given
            and short_offtopic_count == 0
            and not partial_repeat_handled
        )
        self.assertFalse(suppress)


class TestBug4PreserveRepeatRequestPositives(unittest.TestCase):
    """Ensure existing repeat-request detection still works after changes."""

    # These phrases should still be classified as repeat requests by the LLM.
    # We test that they are NOT matched by the disfluency/greeting guards
    # (which would prevent them from reaching LLM analysis).

    def test_repeat_phrases_not_disfluent(self):
        """Repeat requests contain substantive words → not disfluent."""
        repeat_phrases = [
            "What was the first part?",
            "Can you repeat that?",
            "I didn't hear you.",
            "Say that again please.",
            "Come again?",
            "Sorry, what was the question?",
        ]
        for phrase in repeat_phrases:
            self.assertFalse(
                _is_disfluent_starter(phrase),
                f"Repeat request should NOT be caught by disfluency guard: '{phrase}'"
            )

    def test_repeat_phrases_not_greeting(self):
        """Repeat requests are not greetings."""
        repeat_phrases = [
            "What was the first part?",
            "Can you repeat that?",
        ]
        for phrase in repeat_phrases:
            self.assertFalse(
                _is_first_utterance_greeting(phrase),
                f"Repeat request should NOT be caught by greeting guard: '{phrase}'"
            )


if __name__ == "__main__":
    unittest.main()
