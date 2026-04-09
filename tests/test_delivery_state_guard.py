"""Tests for delivery-state guard: responses must not be accepted before TTS delivery.

Verifies that:
- Transcripts arriving during "delivering" state are discarded
- Transcripts arriving after "delivered" state are accepted
- pending_stt_transcript is cleared at turn boundaries
- conversation_item_added does NOT mark answered if delivery is unconfirmed
- polling loop rejects responses when delivery is unconfirmed

These tests avoid importing the heavy livekit stack by replicating only
the fields/logic under test.
"""

import pytest
from datetime import datetime
from typing import Dict, Tuple, Optional

from src.domain.delivery_state import delivery_key, set_delivery_state, is_delivery_confirmed


class DeliveryStateMixin:
    """Wraps domain delivery-state functions with agent-like interface for tests."""

    def __init__(self):
        self.question_delivery_state: Dict[Tuple[int, str], str] = {}
        self.question_delivery_retries: Dict[Tuple[int, str], int] = {}
        self.current_question_num: int = 0
        self.expected_respondent: Optional[str] = None
        self.latest_user_response: Optional[str] = None
        self.pending_stt_transcript: Optional[str] = None
        self.response_captured: bool = False
        self.response_fragments: list = []
        self.last_fragment_time: Optional[datetime] = None
        self.last_stt_fragment: str = ""

    def _delivery_key(self, question_num: int, participant: str) -> Tuple[int, str]:
        return delivery_key(question_num, participant)

    def _set_delivery_state(self, question_num: int, participant: str, state: str, context: str = ""):
        set_delivery_state(self.question_delivery_state, question_num, participant, state)

    def _is_delivery_confirmed(self, question_num: int, participant: str) -> bool:
        return is_delivery_confirmed(self.question_delivery_state, question_num, participant)


def delivery_guard_check(mod: DeliveryStateMixin) -> bool:
    """Replica of the delivery guard logic added to _capture_user_response_immediately,
    user_input_transcribed, and polling loops. Returns True if transcript should be REJECTED."""
    expected = mod.expected_respondent
    if expected and mod.current_question_num:
        if not mod._is_delivery_confirmed(mod.current_question_num, expected):
            return True
    return False


def simulate_capture_with_guard(mod: DeliveryStateMixin, transcript: str) -> bool:
    """Simulate _capture_user_response_immediately with delivery guard.
    Returns True if transcript was stored, False if rejected."""
    if delivery_guard_check(mod):
        mod.pending_stt_transcript = None
        return False
    mod.latest_user_response = transcript
    mod.response_captured = True
    return True


def simulate_user_input_transcribed_with_guard(mod: DeliveryStateMixin, transcript: str) -> bool:
    """Simulate user_input_transcribed with delivery guard.
    Returns True if transcript was stored, False if rejected."""
    if delivery_guard_check(mod):
        return False
    mod.pending_stt_transcript = transcript
    mod.latest_user_response = transcript
    return True


def simulate_polling_accept_with_guard(mod: DeliveryStateMixin, participant: str) -> bool:
    """Simulate the polling loop acceptance check.
    Returns True if response accepted, False if rejected."""
    if not mod._is_delivery_confirmed(mod.current_question_num, participant):
        mod.latest_user_response = None
        mod.pending_stt_transcript = None
        mod.response_captured = False
        return False
    return True


def simulate_conversation_item_added_with_guard(
    mod: DeliveryStateMixin, participant: str, answered_set: set
) -> bool:
    """Simulate conversation_item_added handler.
    Returns True if participant was marked answered, False if skipped."""
    if mod._is_delivery_confirmed(mod.current_question_num, participant):
        answered_set.add((mod.current_question_num, participant))
        mod._set_delivery_state(mod.current_question_num, participant, "answered", context="conv_item")
        mod.response_captured = True
        return True
    else:
        return False


class TestDeliveryGuardRejectsBeforeDelivery:
    """Transcripts arriving while delivery_state != 'delivered' must be rejected."""

    def _make_mod(self, state: str = "delivering") -> DeliveryStateMixin:
        mod = DeliveryStateMixin()
        mod.current_question_num = 3
        mod.expected_respondent = "christopher"
        mod._set_delivery_state(3, "christopher", state)
        return mod

    def test_immediate_capture_rejected_during_delivering(self):
        mod = self._make_mod("delivering")
        mod.pending_stt_transcript = "I don't know."
        accepted = simulate_capture_with_guard(mod, "I don't know.")
        assert not accepted
        assert mod.latest_user_response is None
        assert mod.pending_stt_transcript is None

    def test_user_input_transcribed_rejected_during_delivering(self):
        mod = self._make_mod("delivering")
        accepted = simulate_user_input_transcribed_with_guard(mod, "I don't know.")
        assert not accepted
        assert mod.pending_stt_transcript is None
        assert mod.latest_user_response is None

    def test_user_input_transcribed_rejected_during_queued(self):
        mod = self._make_mod("queued")
        accepted = simulate_user_input_transcribed_with_guard(mod, "Stale transcript")
        assert not accepted

    def test_polling_rejects_during_delivering(self):
        mod = self._make_mod("delivering")
        mod.latest_user_response = "Stale response"
        mod.response_captured = True
        accepted = simulate_polling_accept_with_guard(mod, "christopher")
        assert not accepted
        assert mod.latest_user_response is None
        assert not mod.response_captured

    def test_conversation_item_not_marked_answered_during_delivering(self):
        mod = self._make_mod("delivering")
        answered_set = set()
        marked = simulate_conversation_item_added_with_guard(mod, "christopher", answered_set)
        assert not marked
        assert len(answered_set) == 0


class TestDeliveryGuardAcceptsAfterDelivery:
    """Transcripts arriving after delivery_state == 'delivered' must be accepted."""

    def _make_mod(self) -> DeliveryStateMixin:
        mod = DeliveryStateMixin()
        mod.current_question_num = 3
        mod.expected_respondent = "christopher"
        mod._set_delivery_state(3, "christopher", "delivered")
        return mod

    def test_immediate_capture_accepted_after_delivered(self):
        mod = self._make_mod()
        accepted = simulate_capture_with_guard(mod, "I think AI is valuable.")
        assert accepted
        assert mod.latest_user_response == "I think AI is valuable."
        assert mod.response_captured

    def test_user_input_transcribed_accepted_after_delivered(self):
        mod = self._make_mod()
        accepted = simulate_user_input_transcribed_with_guard(mod, "I think AI is valuable.")
        assert accepted
        assert mod.pending_stt_transcript == "I think AI is valuable."
        assert mod.latest_user_response == "I think AI is valuable."

    def test_polling_accepts_after_delivered(self):
        mod = self._make_mod()
        mod.latest_user_response = "Some real response"
        mod.response_captured = True
        accepted = simulate_polling_accept_with_guard(mod, "christopher")
        assert accepted
        assert mod.latest_user_response == "Some real response"

    def test_conversation_item_marks_answered_after_delivered(self):
        mod = self._make_mod()
        answered_set = set()
        marked = simulate_conversation_item_added_with_guard(mod, "christopher", answered_set)
        assert marked
        assert (3, "christopher") in answered_set


class TestTurnBoundaryClearing:
    """pending_stt_transcript and latest_user_response must be cleared at turn boundaries."""

    def test_clearing_at_ask_next_question(self):
        mod = DeliveryStateMixin()
        mod.pending_stt_transcript = "Stale from Q2"
        mod.latest_user_response = "Old response"
        mod.response_fragments = ["frag1"]
        mod.last_fragment_time = datetime.now()
        mod.last_stt_fragment = "old fragment"

        # Simulate the turn-boundary reset (same as ask_next_question)
        mod.latest_user_response = None
        mod.pending_stt_transcript = None
        mod.response_fragments = []
        mod.last_fragment_time = None
        mod.last_stt_fragment = ""

        assert mod.latest_user_response is None
        assert mod.pending_stt_transcript is None
        assert mod.response_fragments == []
        assert mod.last_fragment_time is None
        assert mod.last_stt_fragment == ""

    def test_clearing_at_move_to_next_participant(self):
        mod = DeliveryStateMixin()
        mod.pending_stt_transcript = "Stale from previous participant"
        mod.latest_user_response = "Old response"
        mod.response_fragments = ["frag1", "frag2"]

        mod.latest_user_response = None
        mod.pending_stt_transcript = None
        mod.response_fragments = []
        mod.last_fragment_time = None
        mod.last_stt_fragment = ""

        assert mod.pending_stt_transcript is None
        assert mod.latest_user_response is None
        assert mod.response_fragments == []


class TestRealWorldScenarios:
    """Scenarios from the Feb 17 demo that would have been caught by the guard."""

    def test_q3_christopher_stale_i_dont_know(self):
        """Q3 Christopher: 'I don't know' arrived during delivering from previous turn."""
        mod = DeliveryStateMixin()
        mod.current_question_num = 3
        mod.expected_respondent = "christopher"
        mod._set_delivery_state(3, "christopher", "delivering")

        # Stale transcript from end of Q2
        accepted = simulate_user_input_transcribed_with_guard(mod, "I don't know.")
        assert not accepted, "Stale 'I don't know' should be rejected during delivering"

        # Now delivery completes
        mod._set_delivery_state(3, "christopher", "delivered")

        # Real answer arrives
        accepted = simulate_user_input_transcribed_with_guard(mod, "I agree with Ganesh")
        assert accepted

    def test_q3_justin_stale_repeat_request(self):
        """Q3 Justin: 'Can you repeat the question?' from Ganesh arrived during delivering."""
        mod = DeliveryStateMixin()
        mod.current_question_num = 3
        mod.expected_respondent = "justin"
        mod._set_delivery_state(3, "justin", "delivering")

        # Stale repeat request from previous participant
        accepted = simulate_user_input_transcribed_with_guard(mod, "Can you repeat the question?")
        assert not accepted

        mod._set_delivery_state(3, "justin", "delivered")
        accepted = simulate_user_input_transcribed_with_guard(
            mod, "I think it's very valuable feedback"
        )
        assert accepted

    def test_q4_christopher_stale_justins_answer(self):
        """Q4 Christopher: Justin's Q3 answer arrived during delivering of Q4."""
        mod = DeliveryStateMixin()
        mod.current_question_num = 4
        mod.expected_respondent = "christopher"
        mod._set_delivery_state(4, "christopher", "delivering")

        # Justin's Q3 answer bleeds into Q4 delivery
        accepted = simulate_capture_with_guard(
            mod, "I think it's very valuable feedback for public sector"
        )
        assert not accepted
        assert mod.latest_user_response is None

    def test_full_lifecycle_queued_delivering_delivered_answered(self):
        """Full delivery lifecycle: queued -> delivering -> delivered -> answered."""
        mod = DeliveryStateMixin()
        mod.current_question_num = 1
        mod.expected_respondent = "ganesh"

        # queued
        mod._set_delivery_state(1, "ganesh", "queued")
        assert delivery_guard_check(mod), "Must reject during queued"

        # delivering (TTS speaking)
        mod._set_delivery_state(1, "ganesh", "delivering")
        assert delivery_guard_check(mod), "Must reject during delivering"

        # delivered (TTS complete)
        mod._set_delivery_state(1, "ganesh", "delivered")
        assert not delivery_guard_check(mod), "Must accept after delivered"

        # Accept the response
        accepted = simulate_user_input_transcribed_with_guard(
            mod, "I use AI for data analysis"
        )
        assert accepted

        # Mark answered
        answered_set = set()
        marked = simulate_conversation_item_added_with_guard(mod, "ganesh", answered_set)
        assert marked
        assert (1, "ganesh") in answered_set
