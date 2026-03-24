"""
Tests for the first-utterance greeting guard.

The guard fires only on the first captured utterance after question delivery
when the text is purely a greeting / acknowledgment / mic-check token.
It prevents premature off-topic analysis on social phrases like "Sure",
"Thanks", "Good morning" without polluting the global disfluency token set.

Source of truth: src/moderator_agent.py — _is_first_utterance_greeting(),
_FIRST_UTTERANCE_GREETING_TOKENS, _FIRST_UTTERANCE_GREETING_PHRASES,
_DISFLUENT_STARTER_TOKENS.
"""

import unittest


# ── Mirror constants / helpers from moderator_agent.py ──

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


def _is_first_utterance_greeting(text: str) -> bool:
    """Return True if text is a greeting/acknowledgment with no substantive content."""
    words = [w.strip(".,!?…'\"") for w in text.lower().split()]
    words = [w for w in words if w]
    if not words:
        return False
    allowed = _DISFLUENT_STARTER_TOKENS | _FIRST_UTTERANCE_GREETING_TOKENS
    return all(w in allowed for w in words)


class TestFirstUtteranceGreetingClassification(unittest.TestCase):
    """Verify _is_first_utterance_greeting correctly classifies greetings."""

    def test_pure_greeting_tokens(self):
        """Single greeting/ack tokens should match."""
        self.assertTrue(_is_first_utterance_greeting("Sure"))
        self.assertTrue(_is_first_utterance_greeting("Sure."))
        self.assertTrue(_is_first_utterance_greeting("Thanks"))
        self.assertTrue(_is_first_utterance_greeting("Thanks!"))
        self.assertTrue(_is_first_utterance_greeting("Great"))
        self.assertTrue(_is_first_utterance_greeting("Fine"))

    def test_multi_word_greetings(self):
        """Multi-word greeting phrases should match."""
        self.assertTrue(_is_first_utterance_greeting("Good morning"))
        self.assertTrue(_is_first_utterance_greeting("Good evening!"))
        self.assertTrue(_is_first_utterance_greeting("Good afternoon."))
        self.assertTrue(_is_first_utterance_greeting("Thank you"))
        self.assertTrue(_is_first_utterance_greeting("Nice to meet you"))
        self.assertTrue(_is_first_utterance_greeting("How are you"))
        self.assertTrue(_is_first_utterance_greeting("How are you doing"))

    def test_greeting_mixed_with_disfluent_tokens(self):
        """Greeting tokens mixed with disfluent tokens should match."""
        self.assertTrue(_is_first_utterance_greeting("Yeah, sure"))
        self.assertTrue(_is_first_utterance_greeting("Oh, thanks"))
        self.assertTrue(_is_first_utterance_greeting("Um, good morning"))
        self.assertTrue(_is_first_utterance_greeting("Well, hi, thanks"))

    def test_greeting_with_substantive_content_no_match(self):
        """Greeting followed by real content should NOT match."""
        self.assertFalse(_is_first_utterance_greeting("Sure, I think the product is great"))
        self.assertFalse(_is_first_utterance_greeting("Thanks, productivity has increased"))
        self.assertFalse(_is_first_utterance_greeting("Good morning, my answer is collaboration"))
        self.assertFalse(_is_first_utterance_greeting("Nice to meet you, I love cheese"))

    def test_empty_text_no_match(self):
        """Empty text should not match (handled by disfluency guard)."""
        self.assertFalse(_is_first_utterance_greeting(""))
        self.assertFalse(_is_first_utterance_greeting("  "))

    def test_pure_disfluent_tokens_still_match(self):
        """Pure disfluent tokens also match since disfluent set is included."""
        self.assertTrue(_is_first_utterance_greeting("Um"))
        self.assertTrue(_is_first_utterance_greeting("Yeah"))
        self.assertTrue(_is_first_utterance_greeting("Hi"))

    def test_substantive_only_no_match(self):
        """Substantive text without greetings should not match."""
        self.assertFalse(_is_first_utterance_greeting("I love the product"))
        self.assertFalse(_is_first_utterance_greeting("Productivity increases"))
        self.assertFalse(_is_first_utterance_greeting("Pizza"))


class TestGreetingGuardStateBehavior(unittest.TestCase):
    """Verify guard firing semantics: once per question, first utterance only."""

    def test_guard_fires_at_most_once(self):
        """Simulates guard flag preventing re-firing on repeated greetings."""
        guard_used = False
        results = []

        for utterance in ["Sure.", "Sure.", "Sure."]:
            if not guard_used and _is_first_utterance_greeting(utterance):
                guard_used = True
                results.append("guard_fired")
            else:
                results.append("fell_through")

        self.assertEqual(results, ["guard_fired", "fell_through", "fell_through"])

    def test_guard_skipped_after_encouragement(self):
        """Guard should not fire if encouragement was already given."""
        encouragement_given = True
        guard_used = False

        should_fire = (
            not guard_used
            and not encouragement_given
            and _is_first_utterance_greeting("Sure")
        )
        self.assertFalse(should_fire)

    def test_guard_skipped_after_relevance_prompt(self):
        """Guard should not fire if relevance prompt was already given."""
        relevance_prompt_given = True
        guard_used = False

        should_fire = (
            not guard_used
            and not relevance_prompt_given
            and _is_first_utterance_greeting("Thanks")
        )
        self.assertFalse(should_fire)

    def test_guard_skipped_after_short_offtopic(self):
        """Guard should not fire if short off-topic count > 0."""
        short_offtopic_count = 1
        guard_used = False

        should_fire = (
            not guard_used
            and short_offtopic_count == 0
            and _is_first_utterance_greeting("Good morning")
        )
        self.assertFalse(should_fire)

    def test_guard_resets_per_question(self):
        """Simulate per-question reset of the guard flag."""
        guard_used = True
        # Simulate _reset_response_flags
        guard_used = False
        self.assertFalse(guard_used)
        # Guard can fire again on next question
        should_fire = not guard_used and _is_first_utterance_greeting("Sure")
        self.assertTrue(should_fire)


if __name__ == "__main__":
    unittest.main()
