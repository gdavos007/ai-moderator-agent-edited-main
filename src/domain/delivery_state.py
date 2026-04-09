"""Delivery state machine for question delivery tracking.

Pure functions operating on dicts — no SDK imports.
"""
from typing import Dict, Tuple


def delivery_key(question_num: int, participant_identity: str) -> Tuple[int, str]:
    """Create a hashable key for delivery state lookup."""
    return (question_num, participant_identity)


def set_delivery_state(
    state_dict: Dict[Tuple[int, str], str],
    question_num: int,
    participant_identity: str,
    state: str,
) -> str:
    """Set delivery state and return the old state.

    Args:
        state_dict: The delivery state dictionary to mutate
        question_num: Current question number
        participant_identity: Participant identity string
        state: New state value

    Returns:
        The previous state value (or "none" if not set)
    """
    key = delivery_key(question_num, participant_identity)
    old_state = state_dict.get(key, "none")
    state_dict[key] = state
    return old_state


def is_delivery_confirmed(
    state_dict: Dict[Tuple[int, str], str],
    question_num: int,
    participant_identity: str,
) -> bool:
    """True if question was delivered (fully or partially)."""
    key = delivery_key(question_num, participant_identity)
    return state_dict.get(key) in (
        "delivered_full", "delivered_partial",
        "delivered",  # backward compat with old state
    )


def is_delivery_full(
    state_dict: Dict[Tuple[int, str], str],
    question_num: int,
    participant_identity: str,
) -> bool:
    """True only if TTS completed without truncation."""
    key = delivery_key(question_num, participant_identity)
    return state_dict.get(key) in ("delivered_full", "delivered")
