"""
Tests for correct acknowledgment name attribution via TurnResult.

Verifies:
  1. When expected == actual, ack_name uses the actual speaker's display name
  2. When expected != actual (permissive mode), ack_name uses ACTUAL speaker
  3. Timeout case: ack_name uses expected participant (since no one else spoke)
  4. _record_turn_result keeps legacy last_respondent in sync
  5. Stale last_respondent is overridden by TurnResult
"""

import pytest
from dataclasses import dataclass
from typing import Optional, Dict


@dataclass(frozen=True)
class TurnResult:
    """Mirror of the production TurnResult (avoids heavy livekit imports)."""
    expected_identity: str
    actual_identity: str
    expected_display: str
    actual_display: str
    was_timeout: bool = False

    @property
    def ack_name(self) -> str:
        return self.actual_display


class FakeParticipantManager:
    def __init__(self, display_names: Dict[str, str]):
        self.participant_display_names = display_names

    def get_display_name(self, identity: str) -> str:
        return self.participant_display_names.get(identity, identity)


class FakeModeratorForAck:
    """Minimal stand-in that mirrors CommunityModeratorAgent's turn result logic."""

    def __init__(self, display_names: Dict[str, str]):
        self.participant_manager = FakeParticipantManager(display_names)
        self.last_respondent = None
        self._last_turn_result: Optional[TurnResult] = None
        self.current_question_num = 1

    def _record_turn_result(self, expected: str, actual: str, *, was_timeout: bool = False) -> TurnResult:
        expected_display = self.participant_manager.get_display_name(expected)
        actual_display = self.participant_manager.get_display_name(actual)
        result = TurnResult(
            expected_identity=expected,
            actual_identity=actual,
            expected_display=expected_display,
            actual_display=actual_display,
            was_timeout=was_timeout,
        )
        self._last_turn_result = result
        self.last_respondent = actual
        return result

    def generate_ack_text(self) -> str:
        _tr = self._last_turn_result
        _ack_name = _tr.ack_name if _tr else (self.last_respondent or "")
        return f"Thank you, {_ack_name}." if _ack_name else "Thank you."


# ── Tests ────────────────────────────────────────────────────────────────

DISPLAY_NAMES = {
    "justin": "Justin",
    "ganesh": "Ganesh",
    "christopher": "Christopher",
}


def test_ack_uses_actual_speaker_when_expected_equals_actual():
    mod = FakeModeratorForAck(DISPLAY_NAMES)
    mod._record_turn_result(expected="ganesh", actual="ganesh")
    assert mod.generate_ack_text() == "Thank you, Ganesh."
    assert mod.last_respondent == "ganesh"


def test_ack_uses_actual_speaker_when_mismatch():
    """Permissive mode: someone else spoke, ack should thank THEM."""
    mod = FakeModeratorForAck(DISPLAY_NAMES)
    mod._record_turn_result(expected="justin", actual="ganesh")
    assert mod.generate_ack_text() == "Thank you, Ganesh."
    assert mod._last_turn_result.expected_identity == "justin"
    assert mod._last_turn_result.actual_identity == "ganesh"


def test_ack_uses_expected_on_timeout():
    """On timeout, expected == actual (no one else spoke)."""
    mod = FakeModeratorForAck(DISPLAY_NAMES)
    mod._record_turn_result(expected="ganesh", actual="ganesh", was_timeout=True)
    assert mod.generate_ack_text() == "Thank you, Ganesh."
    assert mod._last_turn_result.was_timeout is True


def test_stale_last_respondent_overridden():
    """Simulate the exact bug: Justin answered, then Ganesh's turn.

    Before the fix, last_respondent stayed 'justin' across turns.
    With TurnResult, the ack uses the correct name.
    """
    mod = FakeModeratorForAck(DISPLAY_NAMES)

    # Turn 1: Justin answers
    mod._record_turn_result(expected="justin", actual="justin")
    assert mod.generate_ack_text() == "Thank you, Justin."

    # Turn 2: Ganesh's turn — record should update
    mod._record_turn_result(expected="ganesh", actual="ganesh")
    assert mod.generate_ack_text() == "Thank you, Ganesh."
    assert mod.last_respondent == "ganesh"


def test_stale_name_on_timeout_after_different_respondent():
    """The original bug scenario: Justin answers, then Ganesh times out.

    Before fix: last_respondent='justin', ack says 'Thank you, Justin.'
    After fix:  _record_turn_result called in timeout path, ack says 'Thank you, Ganesh.'
    """
    mod = FakeModeratorForAck(DISPLAY_NAMES)

    # Turn 1: Justin answers
    mod._record_turn_result(expected="justin", actual="justin")
    assert mod.generate_ack_text() == "Thank you, Justin."

    # Turn 2: Ganesh times out — timeout path MUST call _record_turn_result
    mod._record_turn_result(expected="ganesh", actual="ganesh", was_timeout=True)
    assert mod.generate_ack_text() == "Thank you, Ganesh."
    assert mod.last_respondent == "ganesh"


def test_turn_result_is_frozen():
    """TurnResult should be immutable (frozen dataclass)."""
    tr = TurnResult(
        expected_identity="justin",
        actual_identity="ganesh",
        expected_display="Justin",
        actual_display="Ganesh",
    )
    with pytest.raises(AttributeError):
        tr.actual_identity = "christopher"  # type: ignore


def test_ack_name_property():
    tr = TurnResult(
        expected_identity="justin",
        actual_identity="ganesh",
        expected_display="Justin",
        actual_display="Ganesh",
    )
    assert tr.ack_name == "Ganesh"


def test_mismatch_does_not_advance_wrong_participant():
    """When actual != expected, last_respondent tracks the ACTUAL speaker,
    not the expected one. This ensures the wrong participant is not
    marked as the respondent for ack purposes."""
    mod = FakeModeratorForAck(DISPLAY_NAMES)
    mod._record_turn_result(expected="christopher", actual="justin")

    assert mod.last_respondent == "justin"
    assert mod._last_turn_result.actual_identity == "justin"
    assert mod._last_turn_result.expected_identity == "christopher"
    assert mod.generate_ack_text() == "Thank you, Justin."


def test_fallback_when_no_turn_result():
    """If _last_turn_result is somehow None, fall back to last_respondent."""
    mod = FakeModeratorForAck(DISPLAY_NAMES)
    mod.last_respondent = "christopher"
    mod._last_turn_result = None
    assert mod.generate_ack_text() == "Thank you, christopher."


def test_empty_fallback():
    """No turn result and no last_respondent → generic 'Thank you.'"""
    mod = FakeModeratorForAck(DISPLAY_NAMES)
    assert mod.generate_ack_text() == "Thank you."
