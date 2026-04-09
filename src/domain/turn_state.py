"""Turn state data classes and pure reset-flag functions.

Contains TurnResult, TurnInfo dataclasses and duck-typed flag reset functions
that operate on any object with the right attributes.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class TurnResult:
    """Immutable snapshot of who answered and how — used for acknowledgments.

    The ack_name is always derived from the same identity that was used to store
    the response (actual speaker), preventing stale-name bugs when the mutable
    ``last_respondent`` field drifts between turns.
    """
    expected_identity: str
    actual_identity: str
    expected_display: str
    actual_display: str
    was_timeout: bool = False

    @property
    def ack_name(self) -> str:
        """Display name for the acknowledgment — always the actual speaker."""
        return self.actual_display


@dataclass
class TurnInfo:
    """Track information about a participant's turn"""
    participant_identity: str
    start_time: datetime
    duration: float = 0.0
    warned: bool = False
    first_interrupted: bool = False
    force_interrupted: bool = False
    interruption_count: int = 0
    interrupt_denied_count: int = 0
    off_topic_start: Optional[datetime] = None
    off_topic_interrupted: bool = False
    actual_speaking_duration: float = 0.0
    pending_soft_warning: bool = False


def reset_response_flags(obj) -> None:
    """Pure flag mutations for response reset.

    Sets per-turn response/flow-control flags to their defaults.
    obj must have the flag attributes (duck-typed for both real agent and test mocks).

    NOTE: Does NOT clear _response_ready (asyncio.Event), manage tasks,
    or touch deadline/watchdog/epoch state — those are owned by
    DeadlineManager and managed by the class wrapper.
    """

    # Core response state
    obj.captured_response = None
    obj.latest_user_response = None
    obj.response_captured = False
    obj.last_stt_fragment = ""
    obj.pending_stt_transcript = None
    obj.response_fragments = []
    obj.last_fragment_time = None
    obj._first_fragment_time = None
    obj._user_stopped_speaking_at = None
    obj.actual_respondent = None
    obj._turn_accumulated_text = ""

    # Flow control (watchdog flags managed by DeadlineManager, not here)
    obj._gentle_warning_in_progress = False
    obj._gentle_warning_started_at = None
    obj._first_vad_speaking_time = None
    obj.encouragement_given = False
    obj._encouragement_followup_given = False
    obj._last_transcript_progress_time = None
    obj._short_offtopic_count = 0
    obj._last_short_offtopic_norm = None
    obj._had_stt_transcript_this_turn = False
    obj.question_repeated = False
    obj.relevance_prompt_given = False
    obj.partial_repeat_handled = False
    obj.already_answered_prompt_given = False
    obj.accumulated_partial_answer = ""
    obj.turn_time_exceeded = False
    obj._ack_already_spoken = False
    obj._prewarmed_ack_text = None
    obj._first_utterance_greeting_guard_used = False
    obj._transition_filler_said = False
    obj._estimated_remaining_tts = 0.0

    # Timeout / turn monitoring
    obj.waiting_for_response = True
    obj.last_speech_time = None


def reset_for_repeat_flags(obj) -> None:
    """Pure flag mutations for repeat reset.

    Clears response/STT state and flow control flags.
    obj must have the flag attributes (duck-typed).

    NOTE: Does NOT clear _response_ready, manage tasks, create TurnInfo,
    or touch deadline/watchdog/epoch state — those are owned by
    DeadlineManager and managed by the class wrapper.
    """
    # Response / STT state
    obj.captured_response = None
    obj.latest_user_response = None
    obj.response_captured = False
    obj.last_stt_fragment = ""
    obj.pending_stt_transcript = None
    obj.response_fragments = []
    obj.last_fragment_time = None
    obj.actual_respondent = None
    obj._turn_accumulated_text = ""

    # Flow control flags (watchdog flags managed by DeadlineManager, not here)
    obj.encouragement_given = False
    obj._encouragement_followup_given = False
    obj._last_transcript_progress_time = None
    obj._short_offtopic_count = 0
    obj._last_short_offtopic_norm = None
    obj._had_stt_transcript_this_turn = False
    obj.waiting_for_response = True
    obj.last_speech_time = None
    obj._transition_filler_said = False
    obj._ack_already_spoken = False
    obj._prewarmed_ack_text = None
    obj._first_utterance_greeting_guard_used = False

    # STT health-check state
    obj._first_vad_speaking_time = None


def reset_for_off_topic_flags(obj) -> None:
    """Pure flag mutations for off-topic reset.

    Clears response/STT state and flow control flags.
    obj must have the flag attributes (duck-typed).

    NOTE: Does NOT clear _response_ready, manage tasks, create TurnInfo,
    or touch deadline/watchdog/epoch state — those are owned by
    DeadlineManager and managed by the class wrapper.
    """
    # Response / STT state
    obj.captured_response = None
    obj.latest_user_response = None
    obj.response_captured = False
    obj.last_stt_fragment = ""
    obj.pending_stt_transcript = None
    obj.response_fragments = []
    obj.last_fragment_time = None
    obj.actual_respondent = None
    obj._turn_accumulated_text = ""

    # Flow control flags (watchdog flags managed by DeadlineManager, not here)
    obj.encouragement_given = False
    obj._encouragement_followup_given = False
    obj._last_transcript_progress_time = None
    obj.question_repeated = False
    obj._short_offtopic_count = 0
    obj._last_short_offtopic_norm = None
    obj._had_stt_transcript_this_turn = False
    obj.waiting_for_response = True
    obj.last_speech_time = None
    obj._transition_filler_said = False
    obj._ack_already_spoken = False
    obj._prewarmed_ack_text = None

    # STT health-check state
    obj._first_vad_speaking_time = None
