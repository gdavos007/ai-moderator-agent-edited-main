"""
Regression test: repeat-request heuristic catches common phrasing variants
even when the word "repeat" is absent.

Before this fix, phrases like "I didn't hear the last part" relied entirely
on the LLM analysis, which could mis-classify them.  The deterministic
heuristic now catches these instantly.

NOTE: This duplicates the core matching logic from is_repeat_request() in
src/moderator_agent.py rather than importing it (the module has heavy livekit
dependencies).  If you change the phrase list in the source, update it here too.
"""

import re


# ── Mirror of is_repeat_request() logic from src/moderator_agent.py ──────────

REPEAT_PHRASES = [
    "repeat",
    "say that again",
    "say it again",
    "say the question again",
    "say that one more time",
    "come again",
    "what was the question",
    "what's the question",
    "what is the question",
    "what was the last part",
    "what was the first part",
    "what was that last part",
    "what was that first part",
    "didn't hear",
    "did not hear",
    "didn't catch",
    "did not catch",
    "couldn't hear",
    "could not hear",
    "can you say that again",
    "could you say that again",
    "can you say the last part",
    "can you say the first part",
    "could you say the last part",
    "could you say the first part",
    "can you say that one more time",
    "can you repeat",
    "could you repeat",
    "please repeat",
    "one more time",
    "again please",
    "pardon",
    "excuse me",
    "sorry what",
    "what did you say",
    "what did you ask",
    "i missed that",
    "missed the question",
    "didn't understand",
    "did not understand",
    "didn't get that",
    "did not get that",
    "i didn't get the question",
    "can't hear",
    "cannot hear",
    "speak up",
    "louder please",
    "what again",
    "huh",
    "what?",
    "the last part",
    "the first part",
    "last part of the question",
    "first part of the question",
]

REPEAT_PATTERNS = [
    r"\bsay\b.{0,30}\bagain\b",
    r"\bhear\b.{0,20}\b(last|first)\s*part\b",
    r"\bwhat\s+was\b.{0,20}\bpart\b",
]

SHORT_REPEAT = {"what", "what?", "huh", "huh?", "sorry", "sorry?", "come again", "come again?"}


def is_repeat_request(text: str) -> bool:
    """Standalone copy of the heuristic for testing without livekit imports."""
    if not text:
        return False
    text_lower = text.lower().strip()

    for phrase in REPEAT_PHRASES:
        if phrase in text_lower:
            return True

    for pattern in REPEAT_PATTERNS:
        if re.search(pattern, text_lower):
            return True

    if text_lower in SHORT_REPEAT:
        return True

    return False


# ── Positive cases: MUST be detected as repeat requests ──────────────────────

POSITIVE_CASES = [
    # Explicit "repeat"
    "Can you repeat the question?",
    "Repeat that please",
    "Could you repeat it?",
    "Please repeat the question.",
    # "say … again" variants
    "Can you say that again?",
    "Say it again please",
    "Say the question again.",
    "Could you say that one more time?",
    # "didn't hear" variants  (THE BUG: these were missed before)
    "I didn't hear the last part",
    "I didn't hear the first part",
    "I didn't hear you",
    "Sorry, I didn't hear that",
    "I did not hear the question",
    # "last part" / "first part" variants
    "What was the last part?",
    "What was the first part?",
    "Can you say the last part of the question?",
    "Can you say the first part of the question?",
    "Could you say the last part again?",
    # Short confusion
    "What?",
    "Huh?",
    "Pardon?",
    "Sorry what?",
    "Come again?",
    # Other
    "What did you say?",
    "What did you ask?",
    "I missed that",
    "I didn't catch that",
    "One more time please",
    "I didn't understand the question",
    "I didn't get that",
    "Can't hear you",
]

# ── Negative cases: MUST NOT be detected as repeat requests ──────────────────

NEGATIVE_CASES = [
    "I live in Dallas, Texas",
    "Yes, I have participated before",
    "I think opinion research is valuable beyond measure",
    "The infrastructure needs improvement",
    "I don't know",
    "I'm not sure about that",
    "That's a good question, let me think",
    "No, I haven't",
    "Well, in my experience the government needs to invest more",
]


class TestPositiveCases:
    """Every phrase that should trigger a repeat must return True."""

    def test_all_positive_cases(self):
        for phrase in POSITIVE_CASES:
            result = is_repeat_request(phrase)
            assert result is True, f"MISSED repeat request: '{phrase}'"

    def test_case_insensitive(self):
        assert is_repeat_request("CAN YOU REPEAT THAT?") is True
        assert is_repeat_request("i didn't hear the last part") is True
        assert is_repeat_request("WHAT WAS THE LAST PART?") is True


class TestNegativeCases:
    """Actual answers must NOT be flagged as repeat requests."""

    def test_all_negative_cases(self):
        for phrase in NEGATIVE_CASES:
            result = is_repeat_request(phrase)
            assert result is False, f"FALSE POSITIVE repeat request: '{phrase}'"


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_string(self):
        assert is_repeat_request("") is False

    def test_none_input(self):
        assert is_repeat_request(None) is False

    def test_whitespace_only(self):
        assert is_repeat_request("   ") is False

    def test_short_what(self):
        assert is_repeat_request("what") is True

    def test_short_huh(self):
        assert is_repeat_request("huh") is True

    def test_short_sorry(self):
        assert is_repeat_request("sorry?") is True


class TestRegexPatterns:
    """Regex patterns catch variants not in the phrase list."""

    def test_say_question_again(self):
        assert is_repeat_request("Can you say the question again?") is True

    def test_hear_last_part(self):
        assert is_repeat_request("I couldn't hear the last part") is True

    def test_what_was_second_part(self):
        assert is_repeat_request("What was the second part?") is True
