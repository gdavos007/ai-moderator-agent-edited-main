"""Turn phase state machine for the AI moderator agent.

Defines the explicit phases a turn can be in and validates transitions
between them. Pure domain logic — no I/O, no SDK imports.
"""
import inspect
import logging
from enum import Enum
from typing import Callable, Optional, Set, FrozenSet

logger = logging.getLogger(__name__)


class TurnPhase(Enum):
    """Explicit phases of a single turn in the survey."""
    IDLE = "idle"                             # Between questions
    MODERATOR_SPEAKING = "moderator_speaking" # Agent delivering TTS
    AWAITING_RESPONSE = "awaiting_response"   # Waiting for first VAD/STT
    PARTICIPANT_SPEAKING = "speaking"         # User actively speaking (VAD)
    PARTICIPANT_PAUSED = "paused"             # User stopped, response not committed
    PROCESSING_RESPONSE = "processing"        # LLM analysis running
    MODERATOR_FOLLOWUP = "followup"           # Agent delivering repeat/redirect/encouragement
    TURN_COMPLETED = "completed"              # Response recorded
    TURN_TIMED_OUT = "timed_out"             # No response, moving on
    TURN_FORCE_ENDED = "force_ended"         # Force-ended by turn monitor


# Allowed transitions: from -> set of valid targets
_ALLOWED_TRANSITIONS: dict[TurnPhase, FrozenSet[TurnPhase]] = {
    TurnPhase.IDLE: frozenset({
        TurnPhase.MODERATOR_SPEAKING,
    }),
    TurnPhase.MODERATOR_SPEAKING: frozenset({
        TurnPhase.AWAITING_RESPONSE,
        TurnPhase.PARTICIPANT_SPEAKING,  # barge-in
        TurnPhase.IDLE,                  # shutdown
    }),
    TurnPhase.AWAITING_RESPONSE: frozenset({
        TurnPhase.PARTICIPANT_SPEAKING,
        TurnPhase.TURN_TIMED_OUT,
        TurnPhase.MODERATOR_SPEAKING,    # watchdog re-prompt
        TurnPhase.IDLE,                  # shutdown
    }),
    TurnPhase.PARTICIPANT_SPEAKING: frozenset({
        TurnPhase.PARTICIPANT_PAUSED,
        TurnPhase.PROCESSING_RESPONSE,   # force capture
        TurnPhase.TURN_FORCE_ENDED,
        TurnPhase.AWAITING_RESPONSE,     # echo discard
    }),
    TurnPhase.PARTICIPANT_PAUSED: frozenset({
        TurnPhase.PARTICIPANT_SPEAKING,   # resume
        TurnPhase.PROCESSING_RESPONSE,    # commit
        TurnPhase.AWAITING_RESPONSE,      # delivery guard discard
        TurnPhase.TURN_TIMED_OUT,
    }),
    TurnPhase.PROCESSING_RESPONSE: frozenset({
        TurnPhase.TURN_COMPLETED,
        TurnPhase.MODERATOR_FOLLOWUP,
    }),
    TurnPhase.MODERATOR_FOLLOWUP: frozenset({
        TurnPhase.AWAITING_RESPONSE,
        TurnPhase.IDLE,                  # shutdown
    }),
    TurnPhase.TURN_COMPLETED: frozenset({
        TurnPhase.IDLE,
    }),
    TurnPhase.TURN_TIMED_OUT: frozenset({
        TurnPhase.IDLE,
    }),
    TurnPhase.TURN_FORCE_ENDED: frozenset({
        TurnPhase.IDLE,
    }),
}


class TurnPhaseMachine:
    """Validates turn phase transitions against an allowed-transitions table.

    The machine ONLY validates transitions. It does NOT perform side effects.
    The moderator class reads the return value and decides what to do.
    """

    def __init__(
        self,
        *,
        on_invalid_transition: Optional[Callable[[TurnPhase, TurnPhase], None]] = None,
    ):
        self._phase = TurnPhase.IDLE
        self._transition_count = 0
        self._on_invalid = on_invalid_transition

    @property
    def phase(self) -> TurnPhase:
        """Current turn phase."""
        return self._phase

    @property
    def transition_count(self) -> int:
        """Total number of successful transitions since creation."""
        return self._transition_count

    def is_in(self, *phases: TurnPhase) -> bool:
        """Return True if current phase is any of the given phases."""
        return self._phase in phases

    def transition_to(self, target: TurnPhase) -> bool:
        """Attempt to transition to target phase.

        Returns True if the transition is valid and was performed.
        Returns False if the transition is invalid (phase unchanged).
        """
        allowed = _ALLOWED_TRANSITIONS.get(self._phase, frozenset())
        caller = _caller_name()
        if target in allowed:
            old = self._phase
            self._phase = target
            self._transition_count += 1
            logger.info("PHASE %s → %s | caller=%s", old.value, target.value, caller)
            return True
        else:
            logger.error(
                f"Invalid turn phase transition: {self._phase.value} -> {target.value} "
                f"(allowed: {', '.join(p.value for p in sorted(allowed, key=lambda p: p.value))}) "
                f"| caller={caller}"
            )
            if self._on_invalid:
                self._on_invalid(self._phase, target)
            return False

    def force_to(self, target: TurnPhase) -> None:
        """Unconditional transition — for shutdown/error recovery only.

        Always succeeds. Logs at warning level.
        """
        old = self._phase
        self._phase = target
        self._transition_count += 1
        if old != target:
            logger.warning(
                "Turn phase FORCED: %s -> %s | caller=%s",
                old.value, target.value, _caller_name(),
            )


def _caller_name() -> str:
    """Best-effort: return caller's function name (skip our own frame)."""
    try:
        frame = inspect.stack()[2]
        return f"{frame.function}:{frame.lineno}"
    except Exception:
        return "?"
