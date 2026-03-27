"""
Regression tests for the PARTIAL repeat meta-question bug.

Bug: When a participant partially answers a multi-part question and asks for
the missing part (e.g. "I live in Dallas. What's the first part of the
question?"), the LLM sometimes puts the participant's meta-question into
unanswered_questions instead of the actual missing sub-question.  The agent
then parrots the participant's phrasing ("what was the first part of the
question?") instead of restating the original prompt.

Fix: A post-LLM sanity check detects meta-question phrasing in
unanswered_questions via _is_meta_question() and downgrades the PARTIAL
to REPEAT_ONLY so the agent repeats the full original question.

Source of truth: src/moderator_agent.py (_is_meta_question, analyze_response
sanity check #2)
"""

import re
import unittest


# ── Mirror _is_meta_question from moderator_agent.py ─────────────────────────

_META_PATTERNS = [
    r"^what\b.*\b(first|last|other|next|second|remaining)\s+(part|half|bit|question)",
    r"^what else did you\b",
    r"^what else (was|were) there\b",
    r"^can you repeat\b",
    r"^could you repeat\b",
    r"^repeat (that|the question|it|please)\b",
    r"^say (that|it|the question) again\b",
    r"^what did you (say|ask)\b",
    r"^what were the other\b",
    r"^what('s| is| was) the rest\b",
]


def _is_meta_question(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return False
    for pat in _META_PATTERNS:
        if re.search(pat, t):
            return True
    return False


def _simulate_sanity_checks(partial_status, unanswered, question_text):
    """Mirror both post-LLM sanity checks from analyze_response().

    Returns (final_status, final_unanswered).
    """
    # Check 1: weak PARTIAL (single-word unanswered)
    if partial_status == "PARTIAL" and unanswered:
        unanswered_words = len(unanswered.split())
        if unanswered_words <= 1:
            return "NO_REPEAT", ""

    # Check 2: meta-question PARTIAL
    if partial_status == "PARTIAL" and unanswered and _is_meta_question(unanswered):
        return "REPEAT_ONLY", question_text

    return partial_status, unanswered


# ── Positive cases: meta-questions that MUST be detected ─────────────────────

class TestMetaQuestionDetection(unittest.TestCase):
    """Participant repeat-request phrasing must be detected as meta-questions."""

    def test_real_bug_what_was_the_first_part(self):
        """Real-world regression: 'what was the first part of the question?'"""
        self.assertTrue(_is_meta_question("what was the first part of the question?"))

    def test_what_was_the_other_part(self):
        self.assertTrue(_is_meta_question("what was the other part?"))

    def test_what_was_the_last_part(self):
        self.assertTrue(_is_meta_question("what was the last part of the question"))

    def test_whats_the_first_part(self):
        self.assertTrue(_is_meta_question("what's the first part?"))

    def test_whats_the_rest(self):
        self.assertTrue(_is_meta_question("what's the rest?"))

    def test_what_is_the_rest_of_the_question(self):
        self.assertTrue(_is_meta_question("what is the rest of the question?"))

    def test_what_was_the_remaining_part(self):
        self.assertTrue(_is_meta_question("what was the remaining part?"))

    def test_can_you_repeat_that(self):
        self.assertTrue(_is_meta_question("can you repeat that?"))

    def test_could_you_repeat_the_question(self):
        self.assertTrue(_is_meta_question("could you repeat the question?"))

    def test_repeat_that_please(self):
        self.assertTrue(_is_meta_question("repeat that please"))

    def test_say_that_again(self):
        self.assertTrue(_is_meta_question("say that again"))

    def test_what_did_you_ask(self):
        self.assertTrue(_is_meta_question("what did you ask?"))

    def test_what_did_you_say(self):
        self.assertTrue(_is_meta_question("what did you say?"))

    def test_what_were_the_other_questions(self):
        self.assertTrue(_is_meta_question("what were the other questions?"))

    def test_what_else_did_you_ask(self):
        self.assertTrue(_is_meta_question("what else did you ask?"))

    def test_what_was_the_next_question(self):
        self.assertTrue(_is_meta_question("what was the next question?"))

    def test_what_was_the_second_part(self):
        self.assertTrue(_is_meta_question("what was the second part?"))


# ── Negative cases: real question content must NOT be flagged ─────────────────

class TestNotMetaQuestion(unittest.TestCase):
    """Legitimate survey sub-questions must NOT be detected as meta-questions."""

    def test_what_else_would_you_like_to_add(self):
        """'What else would you like to add?' is a real survey sub-question."""
        self.assertFalse(_is_meta_question("What else would you like to add?"))

    def test_what_else_concerns_you(self):
        self.assertFalse(_is_meta_question("What else concerns you about this topic?"))

    def test_real_unanswered_subquestion(self):
        """A genuine unanswered sub-question from the original prompt."""
        self.assertFalse(_is_meta_question(
            "what do you do for a living, or are you a student or retired?"
        ))

    def test_where_do_you_live(self):
        self.assertFalse(_is_meta_question("Please tell me where you live."))

    def test_what_do_you_think(self):
        self.assertFalse(_is_meta_question("What do you think about the proposal?"))

    def test_how_long_have_you_lived_there(self):
        self.assertFalse(_is_meta_question("How long have you lived there?"))

    def test_empty_string(self):
        self.assertFalse(_is_meta_question(""))

    def test_whitespace_only(self):
        self.assertFalse(_is_meta_question("   "))

    def test_what_else_can_be_improved(self):
        self.assertFalse(_is_meta_question("What else can be improved?"))

    def test_what_is_the_first_thing_you_notice(self):
        """'first' in a real question context, not about the question itself."""
        self.assertFalse(_is_meta_question(
            "What is the first thing you notice when you walk in?"
        ))


# ── Sanity check integration: PARTIAL downgrade ─────────────────────────────

class TestPartialMetaQuestionDowngrade(unittest.TestCase):
    """When unanswered_questions is a meta-question, PARTIAL must downgrade
    to REPEAT_ONLY with the full original question as fallback."""

    ORIGINAL_QUESTION = (
        "Please tell me where you live and what you do for a living, "
        "or are you a student or retired?"
    )

    def test_real_bug_downgrades_to_repeat_only(self):
        """Real regression: LLM returns participant's meta-question as unanswered."""
        status, unanswered = _simulate_sanity_checks(
            "PARTIAL",
            "what was the first part of the question?",
            self.ORIGINAL_QUESTION,
        )
        self.assertEqual(status, "REPEAT_ONLY",
                         "Meta-question PARTIAL must downgrade to REPEAT_ONLY")
        self.assertEqual(unanswered, self.ORIGINAL_QUESTION,
                         "Fallback must be the full original question")

    def test_legitimate_partial_preserved(self):
        """Genuine unanswered sub-question must stay PARTIAL."""
        status, unanswered = _simulate_sanity_checks(
            "PARTIAL",
            "what do you do for a living, or are you a student or retired?",
            self.ORIGINAL_QUESTION,
        )
        self.assertEqual(status, "PARTIAL")
        self.assertEqual(
            unanswered,
            "what do you do for a living, or are you a student or retired?",
        )

    def test_second_half_answered_first_meta_downgrade(self):
        """Participant answers second half first, asks for other part."""
        status, unanswered = _simulate_sanity_checks(
            "PARTIAL",
            "what was the other part?",
            self.ORIGINAL_QUESTION,
        )
        self.assertEqual(status, "REPEAT_ONLY",
                         "Meta-question must downgrade to REPEAT_ONLY")
        self.assertEqual(unanswered, self.ORIGINAL_QUESTION)

    def test_whats_the_rest_downgrades(self):
        status, unanswered = _simulate_sanity_checks(
            "PARTIAL",
            "what's the rest?",
            self.ORIGINAL_QUESTION,
        )
        self.assertEqual(status, "REPEAT_ONLY")
        self.assertEqual(unanswered, self.ORIGINAL_QUESTION)

    def test_no_repeat_unaffected(self):
        """NO_REPEAT status must pass through unchanged."""
        status, unanswered = _simulate_sanity_checks(
            "NO_REPEAT", "", self.ORIGINAL_QUESTION
        )
        self.assertEqual(status, "NO_REPEAT")
        self.assertEqual(unanswered, "")

    def test_weak_partial_still_caught_first(self):
        """Single-word unanswered is caught by check #1 before check #2."""
        status, unanswered = _simulate_sanity_checks(
            "PARTIAL", "how", self.ORIGINAL_QUESTION
        )
        self.assertEqual(status, "NO_REPEAT",
                         "Single-word unanswered caught by weak-PARTIAL check")
        self.assertEqual(unanswered, "")


if __name__ == "__main__":
    unittest.main()
