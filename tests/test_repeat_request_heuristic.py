"""
Regression test: repeat-request heuristic catches common phrasing variants
even when the word "repeat" is absent.

Before this fix, phrases like "I didn't hear the last part" relied entirely
on the LLM analysis, which could mis-classify them.  The deterministic
heuristic now catches these instantly.

The heuristic has a LENGTH GATE (60 chars max) so that long responses
containing substantive content alongside a trigger phrase are deferred to
LLM analysis.  This prevents false positives like:
  - "I'm an engineer. I live in Dallas. And what's the last part?"
  - "you're asking me to repeat / speak up and I'm like, I am talking"
"""

from src.domain.text_analysis import is_repeat_request

# Length gate constant used in boundary tests — mirrors the value inside
# is_repeat_request() in src/domain/text_analysis.py.
HEURISTIC_MAX_LENGTH = 60


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
    "I liked the last part best.",
    "The first part was the easiest for me.",
    "It happened one more time last week.",
    "I repeat myself when I'm nervous.",
]

# ── Length-gate regression cases (from real sessions) ────────────────────────
# These contain trigger phrases but are long substantive responses that should
# be deferred to LLM analysis, NOT caught by the heuristic.
LENGTH_GATE_NEGATIVE_CASES = [
    # Q1 (W1) bug: partial answer + clarification — "the last part" triggered false positive
    "I'm an engineer. I live in Dallas. And what's the last part of the question?",
    # Q6 (T2) bug: meta-feedback about moderator — "repeat" triggered false positive
    "Sometimes I I feel like you didn't I feel like you didn't hear me, and you're asking me to repeat a speak up and I'm like, I am talking. So I don't know what's going on there. But overall, it's okay.",
    # Q5 (T1) bug: substantive feedback containing "didn't hear"
    "I don't know why you didn't hear me. Because A couple of times. I was just a little confused about that. I didn't feel like I was getting cut off. But, you know, like, I could've sworn I spoke.",
    # Q5 (T1) bug: substantive feedback containing "repeat"
    "You cut me off. More than once. I tried to I try to yeah. Just yeah. I just I felt like yeah. It was, like, a little bit disturbed this time. And repeating the last question, you never repeated the last question. So",
    # Another partial answer variant
    "I'm not retired. I live in Dallas. And what's the first part of the question?",
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


class TestLengthGate:
    """Long responses with trigger phrases must NOT be caught by the heuristic."""

    def test_all_length_gate_negative_cases(self):
        for phrase in LENGTH_GATE_NEGATIVE_CASES:
            result = is_repeat_request(phrase)
            assert result is False, (
                f"FALSE POSITIVE (length gate should have blocked): '{phrase}'"
            )

    def test_short_repeat_still_works(self):
        """Short genuine repeat requests must still be caught."""
        assert is_repeat_request("Can you repeat that?") is True
        assert is_repeat_request("What was the last part?") is True
        assert is_repeat_request("Say that again") is True

    def test_boundary_length(self):
        """Responses right at the boundary."""
        # 60 chars or fewer should still be checked
        short = "Can you say that one more time please?"
        assert len(short) <= HEURISTIC_MAX_LENGTH
        assert is_repeat_request(short) is True

        # Over 60 chars should be skipped even if they contain a request phrase
        long = short + " I was distracted by background noise just now."
        assert len(long) > HEURISTIC_MAX_LENGTH
        assert is_repeat_request(long) is False


class TestRegexPatterns:
    """Regex patterns catch variants not in the phrase list."""

    def test_say_question_again(self):
        assert is_repeat_request("Can you say the question again?") is True

    def test_hear_last_part(self):
        assert is_repeat_request("I couldn't hear the last part") is True

    def test_what_was_second_part(self):
        assert is_repeat_request("What was the second part?") is True

    def test_standalone_repeat_command(self):
        assert is_repeat_request("Repeat") is True
        assert is_repeat_request("Repeat that please") is True

    def test_standalone_one_more_time(self):
        assert is_repeat_request("One more time please") is True
