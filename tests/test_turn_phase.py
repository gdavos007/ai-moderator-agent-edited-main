"""Tests for TurnPhase enum and TurnPhaseMachine transition validation."""
from src.domain.turn_phase import TurnPhase, TurnPhaseMachine, _ALLOWED_TRANSITIONS


class TestTurnPhaseEnum:
    """Verify the enum members exist and have expected values."""

    def test_all_phases_defined(self):
        expected = {
            "idle", "moderator_speaking", "awaiting_response", "speaking",
            "paused", "processing", "followup", "completed", "timed_out",
            "force_ended",
        }
        actual = {p.value for p in TurnPhase}
        assert actual == expected

    def test_all_phases_have_transitions(self):
        """Every phase must appear as a key in the allowed-transitions table."""
        for phase in TurnPhase:
            assert phase in _ALLOWED_TRANSITIONS, f"{phase} missing from transitions table"


class TestTurnPhaseMachineInit:
    """Verify initial state."""

    def test_starts_idle(self):
        m = TurnPhaseMachine()
        assert m.phase == TurnPhase.IDLE

    def test_transition_count_starts_zero(self):
        m = TurnPhaseMachine()
        assert m.transition_count == 0


class TestValidTransitions:
    """Every allowed transition should succeed and return True."""

    def test_idle_to_moderator_speaking(self):
        m = TurnPhaseMachine()
        assert m.transition_to(TurnPhase.MODERATOR_SPEAKING) is True
        assert m.phase == TurnPhase.MODERATOR_SPEAKING

    def test_moderator_speaking_to_awaiting(self):
        m = TurnPhaseMachine()
        m.transition_to(TurnPhase.MODERATOR_SPEAKING)
        assert m.transition_to(TurnPhase.AWAITING_RESPONSE) is True

    def test_moderator_speaking_to_participant_speaking_bargein(self):
        m = TurnPhaseMachine()
        m.transition_to(TurnPhase.MODERATOR_SPEAKING)
        assert m.transition_to(TurnPhase.PARTICIPANT_SPEAKING) is True

    def test_moderator_speaking_to_idle_shutdown(self):
        m = TurnPhaseMachine()
        m.transition_to(TurnPhase.MODERATOR_SPEAKING)
        assert m.transition_to(TurnPhase.IDLE) is True

    def test_awaiting_to_participant_speaking(self):
        m = TurnPhaseMachine()
        m.transition_to(TurnPhase.MODERATOR_SPEAKING)
        m.transition_to(TurnPhase.AWAITING_RESPONSE)
        assert m.transition_to(TurnPhase.PARTICIPANT_SPEAKING) is True

    def test_awaiting_to_timed_out(self):
        m = TurnPhaseMachine()
        m.transition_to(TurnPhase.MODERATOR_SPEAKING)
        m.transition_to(TurnPhase.AWAITING_RESPONSE)
        assert m.transition_to(TurnPhase.TURN_TIMED_OUT) is True

    def test_awaiting_to_moderator_speaking_reprompt(self):
        m = TurnPhaseMachine()
        m.transition_to(TurnPhase.MODERATOR_SPEAKING)
        m.transition_to(TurnPhase.AWAITING_RESPONSE)
        assert m.transition_to(TurnPhase.MODERATOR_SPEAKING) is True

    def test_participant_speaking_to_paused(self):
        m = TurnPhaseMachine()
        m.transition_to(TurnPhase.MODERATOR_SPEAKING)
        m.transition_to(TurnPhase.AWAITING_RESPONSE)
        m.transition_to(TurnPhase.PARTICIPANT_SPEAKING)
        assert m.transition_to(TurnPhase.PARTICIPANT_PAUSED) is True

    def test_participant_speaking_to_force_ended(self):
        m = TurnPhaseMachine()
        m.transition_to(TurnPhase.MODERATOR_SPEAKING)
        m.transition_to(TurnPhase.AWAITING_RESPONSE)
        m.transition_to(TurnPhase.PARTICIPANT_SPEAKING)
        assert m.transition_to(TurnPhase.TURN_FORCE_ENDED) is True

    def test_participant_paused_to_speaking_resume(self):
        m = TurnPhaseMachine()
        m.transition_to(TurnPhase.MODERATOR_SPEAKING)
        m.transition_to(TurnPhase.AWAITING_RESPONSE)
        m.transition_to(TurnPhase.PARTICIPANT_SPEAKING)
        m.transition_to(TurnPhase.PARTICIPANT_PAUSED)
        assert m.transition_to(TurnPhase.PARTICIPANT_SPEAKING) is True

    def test_participant_paused_to_processing(self):
        m = TurnPhaseMachine()
        m.transition_to(TurnPhase.MODERATOR_SPEAKING)
        m.transition_to(TurnPhase.AWAITING_RESPONSE)
        m.transition_to(TurnPhase.PARTICIPANT_SPEAKING)
        m.transition_to(TurnPhase.PARTICIPANT_PAUSED)
        assert m.transition_to(TurnPhase.PROCESSING_RESPONSE) is True

    def test_processing_to_completed(self):
        m = TurnPhaseMachine()
        m.transition_to(TurnPhase.MODERATOR_SPEAKING)
        m.transition_to(TurnPhase.AWAITING_RESPONSE)
        m.transition_to(TurnPhase.PARTICIPANT_SPEAKING)
        m.transition_to(TurnPhase.PARTICIPANT_PAUSED)
        m.transition_to(TurnPhase.PROCESSING_RESPONSE)
        assert m.transition_to(TurnPhase.TURN_COMPLETED) is True

    def test_processing_to_followup(self):
        m = TurnPhaseMachine()
        m.transition_to(TurnPhase.MODERATOR_SPEAKING)
        m.transition_to(TurnPhase.AWAITING_RESPONSE)
        m.transition_to(TurnPhase.PARTICIPANT_SPEAKING)
        m.transition_to(TurnPhase.PARTICIPANT_PAUSED)
        m.transition_to(TurnPhase.PROCESSING_RESPONSE)
        assert m.transition_to(TurnPhase.MODERATOR_FOLLOWUP) is True

    def test_followup_to_awaiting(self):
        m = TurnPhaseMachine()
        m.transition_to(TurnPhase.MODERATOR_SPEAKING)
        m.transition_to(TurnPhase.AWAITING_RESPONSE)
        m.transition_to(TurnPhase.PARTICIPANT_SPEAKING)
        m.transition_to(TurnPhase.PARTICIPANT_PAUSED)
        m.transition_to(TurnPhase.PROCESSING_RESPONSE)
        m.transition_to(TurnPhase.MODERATOR_FOLLOWUP)
        assert m.transition_to(TurnPhase.AWAITING_RESPONSE) is True

    def test_terminal_states_to_idle(self):
        for terminal in [TurnPhase.TURN_COMPLETED, TurnPhase.TURN_TIMED_OUT, TurnPhase.TURN_FORCE_ENDED]:
            m = TurnPhaseMachine()
            m.force_to(terminal)
            assert m.transition_to(TurnPhase.IDLE) is True, f"{terminal} -> IDLE should be valid"

    def test_full_happy_path(self):
        """Complete turn lifecycle: IDLE -> speak -> await -> speak -> pause -> process -> complete -> IDLE"""
        m = TurnPhaseMachine()
        assert m.transition_to(TurnPhase.MODERATOR_SPEAKING)
        assert m.transition_to(TurnPhase.AWAITING_RESPONSE)
        assert m.transition_to(TurnPhase.PARTICIPANT_SPEAKING)
        assert m.transition_to(TurnPhase.PARTICIPANT_PAUSED)
        assert m.transition_to(TurnPhase.PROCESSING_RESPONSE)
        assert m.transition_to(TurnPhase.TURN_COMPLETED)
        assert m.transition_to(TurnPhase.IDLE)
        assert m.transition_count == 7

    def test_retry_path(self):
        """Turn with retry: ... -> process -> followup -> await -> speak -> pause -> process -> complete"""
        m = TurnPhaseMachine()
        m.transition_to(TurnPhase.MODERATOR_SPEAKING)
        m.transition_to(TurnPhase.AWAITING_RESPONSE)
        m.transition_to(TurnPhase.PARTICIPANT_SPEAKING)
        m.transition_to(TurnPhase.PARTICIPANT_PAUSED)
        m.transition_to(TurnPhase.PROCESSING_RESPONSE)
        # Retry: off-topic redirect
        assert m.transition_to(TurnPhase.MODERATOR_FOLLOWUP)
        assert m.transition_to(TurnPhase.AWAITING_RESPONSE)
        # Second attempt
        assert m.transition_to(TurnPhase.PARTICIPANT_SPEAKING)
        assert m.transition_to(TurnPhase.PARTICIPANT_PAUSED)
        assert m.transition_to(TurnPhase.PROCESSING_RESPONSE)
        assert m.transition_to(TurnPhase.TURN_COMPLETED)
        assert m.transition_to(TurnPhase.IDLE)


class TestInvalidTransitions:
    """Invalid transitions should return False and not change phase."""

    def test_idle_to_participant_speaking(self):
        m = TurnPhaseMachine()
        assert m.transition_to(TurnPhase.PARTICIPANT_SPEAKING) is False
        assert m.phase == TurnPhase.IDLE

    def test_idle_to_completed(self):
        m = TurnPhaseMachine()
        assert m.transition_to(TurnPhase.TURN_COMPLETED) is False

    def test_awaiting_to_completed_directly(self):
        m = TurnPhaseMachine()
        m.transition_to(TurnPhase.MODERATOR_SPEAKING)
        m.transition_to(TurnPhase.AWAITING_RESPONSE)
        assert m.transition_to(TurnPhase.TURN_COMPLETED) is False

    def test_processing_to_idle_directly(self):
        m = TurnPhaseMachine()
        m.force_to(TurnPhase.PROCESSING_RESPONSE)
        assert m.transition_to(TurnPhase.IDLE) is False

    def test_completed_to_speaking(self):
        m = TurnPhaseMachine()
        m.force_to(TurnPhase.TURN_COMPLETED)
        assert m.transition_to(TurnPhase.PARTICIPANT_SPEAKING) is False

    def test_invalid_does_not_increment_count(self):
        m = TurnPhaseMachine()
        m.transition_to(TurnPhase.PARTICIPANT_SPEAKING)  # invalid
        assert m.transition_count == 0


class TestIsIn:
    """Test the is_in() convenience method."""

    def test_single_match(self):
        m = TurnPhaseMachine()
        assert m.is_in(TurnPhase.IDLE) is True

    def test_single_no_match(self):
        m = TurnPhaseMachine()
        assert m.is_in(TurnPhase.MODERATOR_SPEAKING) is False

    def test_multi_match(self):
        m = TurnPhaseMachine()
        assert m.is_in(TurnPhase.IDLE, TurnPhase.MODERATOR_SPEAKING) is True

    def test_multi_no_match(self):
        m = TurnPhaseMachine()
        assert m.is_in(TurnPhase.PARTICIPANT_SPEAKING, TurnPhase.PROCESSING_RESPONSE) is False


class TestForceTo:
    """Test the force_to() unconditional transition."""

    def test_force_to_any_phase(self):
        m = TurnPhaseMachine()
        m.force_to(TurnPhase.PROCESSING_RESPONSE)
        assert m.phase == TurnPhase.PROCESSING_RESPONSE

    def test_force_increments_count(self):
        m = TurnPhaseMachine()
        m.force_to(TurnPhase.TURN_COMPLETED)
        assert m.transition_count == 1

    def test_force_to_same_phase(self):
        m = TurnPhaseMachine()
        m.force_to(TurnPhase.IDLE)
        assert m.phase == TurnPhase.IDLE
        assert m.transition_count == 1


class TestOnInvalidCallback:
    """Test the on_invalid_transition callback."""

    def test_callback_fires_on_invalid(self):
        violations = []
        m = TurnPhaseMachine(on_invalid_transition=lambda src, tgt: violations.append((src, tgt)))
        m.transition_to(TurnPhase.PARTICIPANT_SPEAKING)  # invalid from IDLE
        assert len(violations) == 1
        assert violations[0] == (TurnPhase.IDLE, TurnPhase.PARTICIPANT_SPEAKING)

    def test_callback_does_not_fire_on_valid(self):
        violations = []
        m = TurnPhaseMachine(on_invalid_transition=lambda src, tgt: violations.append((src, tgt)))
        m.transition_to(TurnPhase.MODERATOR_SPEAKING)
        assert len(violations) == 0
