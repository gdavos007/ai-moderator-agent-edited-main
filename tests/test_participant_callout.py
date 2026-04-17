"""Tests for the participant callout feature.

When the full question has been fully delivered to at least one participant,
subsequent participants hear a short callout phrase instead of the full
question+options text.  These tests replicate the flag logic from
moderator_agent.py (the same replica-stub pattern used by other tests in
this suite).
"""

# ---------------------------------------------------------------------------
# Callout templates — must match moderator_agent.py _callout_templates
# ---------------------------------------------------------------------------
CALLOUT_TEMPLATES = [
    "{name}, what are your thoughts on this?",
    "How about you, {name}?",
    "{name}, what do you think?",
    "And {name}, what's your take?",
]

SAMPLE_QUESTION = "What is your role? The options are: Manager, Individual Contributor, or Other."
W1_QUESTION = "Please tell me where you live and what you do for a living, or are you a student or retired?"


# ---------------------------------------------------------------------------
# Replica stub — mirrors the callout logic in moderator_agent.py
# ---------------------------------------------------------------------------
class CalloutMixin:
    """Minimal replica of the callout flag logic from CommunityModeratorAgent."""

    def __init__(self):
        self._question_spoken_to_group: bool = False
        self._question_callout_counter: int = 0
        self.current_question_num: int = 0
        self.current_question: str = SAMPLE_QUESTION
        self.current_question_id: str = None

    def advance_question(self, q_num: int, q_text: str, question_id: str = None):
        self.current_question_num = q_num
        self.current_question = q_text
        self.current_question_id = question_id
        self._question_spoken_to_group = False

    def build_text_for_participant(self, name: str) -> str:
        """Simulate exact_text_to_say construction."""
        if self._question_spoken_to_group:
            if self.current_question_id == "W1":
                text = f"{name}, what about you?"
            else:
                idx = self._question_callout_counter % len(CALLOUT_TEMPLATES)
                text = CALLOUT_TEMPLATES[idx].format(name=name)
                self._question_callout_counter += 1
        else:
            text = f"{name}, {self.current_question}"
        return text

    def mark_delivered(self, tts_fully_spoken: bool):
        if tts_fully_spoken:
            self._question_spoken_to_group = True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFirstParticipantGetsFullQuestion:
    def test_first_participant_gets_full_question(self):
        mod = CalloutMixin()
        mod.advance_question(1, SAMPLE_QUESTION)
        text = mod.build_text_for_participant("Alice")
        assert text == f"Alice, {SAMPLE_QUESTION}"


class TestSubsequentParticipantGetsCallout:
    def test_subsequent_participant_gets_callout(self):
        mod = CalloutMixin()
        mod.advance_question(1, SAMPLE_QUESTION)
        # First participant — full question
        mod.build_text_for_participant("Alice")
        mod.mark_delivered(tts_fully_spoken=True)
        # Second participant — callout
        text = mod.build_text_for_participant("Bob")
        assert text == CALLOUT_TEMPLATES[0].format(name="Bob")
        assert SAMPLE_QUESTION not in text


class TestPartialDeliveryDoesNotUnlockCallout:
    def test_partial_first_delivery_does_not_unlock_callout(self):
        mod = CalloutMixin()
        mod.advance_question(1, SAMPLE_QUESTION)
        mod.build_text_for_participant("Alice")
        mod.mark_delivered(tts_fully_spoken=False)  # interrupted
        # Second participant should still get full question
        text = mod.build_text_for_participant("Bob")
        assert text == f"Bob, {SAMPLE_QUESTION}"


class TestNewQuestionResetsFlag:
    def test_new_question_resets_flag(self):
        mod = CalloutMixin()
        mod.advance_question(1, SAMPLE_QUESTION)
        mod.build_text_for_participant("Alice")
        mod.mark_delivered(tts_fully_spoken=True)
        assert mod._question_spoken_to_group is True
        # Advance to Q2
        q2_text = "How satisfied are you? Options: Very satisfied, Satisfied, Neutral, Dissatisfied."
        mod.advance_question(2, q2_text)
        assert mod._question_spoken_to_group is False
        # First participant on Q2 gets full question
        text = mod.build_text_for_participant("Alice")
        assert text == f"Alice, {q2_text}"


class TestCalloutPhrasesCycleDeterministically:
    def test_callout_phrases_cycle_deterministically(self):
        mod = CalloutMixin()
        mod.advance_question(1, SAMPLE_QUESTION)
        mod.build_text_for_participant("Alice")
        mod.mark_delivered(tts_fully_spoken=True)
        names = ["Bob", "Charlie", "Diana", "Eve", "Frank"]
        for i, name in enumerate(names):
            text = mod.build_text_for_participant(name)
            expected_template = CALLOUT_TEMPLATES[i % len(CALLOUT_TEMPLATES)]
            assert text == expected_template.format(name=name), (
                f"Participant {i} ({name}): expected template index {i % 4}, got '{text}'"
            )


class TestRepeatRequestStillUsesFullQuestion:
    def test_repeat_request_still_uses_full_question(self):
        mod = CalloutMixin()
        mod.advance_question(1, SAMPLE_QUESTION)
        mod.build_text_for_participant("Alice")
        mod.mark_delivered(tts_fully_spoken=True)
        # After callout mode is active, current_question still holds the full text
        # (used by the repeat-request re-speak path in moderator_agent.py)
        assert mod.current_question == SAMPLE_QUESTION
        # Callout mode doesn't mutate current_question
        mod.build_text_for_participant("Bob")
        assert mod.current_question == SAMPLE_QUESTION


class TestW1UsesFixedCallout:
    def test_w1_subsequent_participants_get_what_about_you(self):
        mod = CalloutMixin()
        mod.advance_question(1, W1_QUESTION, question_id="W1")
        # First participant — full question
        text = mod.build_text_for_participant("Alice")
        assert text == f"Alice, {W1_QUESTION}"
        mod.mark_delivered(tts_fully_spoken=True)
        # Second and third participants — fixed W1 callout
        assert mod.build_text_for_participant("Bob") == "Bob, what about you?"
        assert mod.build_text_for_participant("Charlie") == "Charlie, what about you?"
        # Counter should NOT have incremented (W1 doesn't use rotating templates)
        assert mod._question_callout_counter == 0


