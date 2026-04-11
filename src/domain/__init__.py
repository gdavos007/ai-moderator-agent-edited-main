"""Pure domain logic for the AI moderator agent.

This package contains dependency-light modules that can be imported and tested
without LiveKit, FastAPI, or any heavy SDK dependencies.
"""
from .constants import SurveyState
from .text_analysis import (
    is_uncertain_response,
    is_repeat_request,
    is_avatar_identity,
    _substantive_word_count,
    _is_too_short_for_offtopic,
    _estimate_tts_duration,
    _is_committable,
    _is_disfluent_starter,
    _is_first_utterance_greeting,
    _normalize_for_offtopic_compare,
    _has_blatant_offtopic_keywords,
)
from .transcription import parse_multi_option_response, correct_transcription
from .observer import is_observer_command
from .delivery_state import delivery_key, set_delivery_state, is_delivery_confirmed, is_delivery_full
from .turn_state import TurnResult, TurnInfo
from .response_analysis import ResponseAnalysis, analyze_response, check_response_relevance
from .turn_phase import TurnPhase, TurnPhaseMachine
from .deadline_manager import DeadlineManager, WatchdogSignal
