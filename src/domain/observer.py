"""Observer command detection for survey control.

No SDK imports — only stdlib ``logging``.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def is_observer_command(text: str) -> tuple[bool, Optional[str]]:
    """
    Detect if a response is an observer command (start, pause, resume).

    Args:
        text: The transcribed text to check

    Returns:
        Tuple of (is_command, command_type) where command_type is 'start', 'pause', or 'resume'
    """
    if not text:
        return False, None

    text_lower = text.lower().strip()

    # Start survey commands (including common STT misrecognitions)
    start_phrases = [
        "start survey", "start the survey", "begin survey", "begin the survey",
        "let's start", "lets start", "let's begin", "lets begin",
        "start now", "begin now", "go ahead", "start it",
        # Common STT errors for "start the survey"
        "start the seven", "start this survey", "start this up",
        "start to survey", "started survey", "starting survey",
        "start the serve", "start deserve", "start the server",
    ]

    # Pause survey commands
    pause_phrases = [
        "pause survey", "pause the survey", "pause",
        "hold on", "hold", "stop survey", "stop the survey",
        "wait", "one moment", "hang on",
        # Common STT errors
        "pause it", "pose", "paws", "halls",
    ]

    # Resume survey commands
    resume_phrases = [
        "resume survey", "resume the survey", "resume",
        "continue survey", "continue the survey", "continue",
        "let's continue", "lets continue", "go on", "carry on",
        "unpause", "start again",
        # Common STT errors
        "resume it", "result", "presume",
    ]

    # Check for start commands
    for phrase in start_phrases:
        if phrase in text_lower:
            logger.info(f"Detected OBSERVER START command: '{text}' matches phrase '{phrase}'")
            return True, "start"

    # Check for pause commands
    for phrase in pause_phrases:
        if phrase in text_lower:
            logger.info(f"Detected OBSERVER PAUSE command: '{text}' matches phrase '{phrase}'")
            return True, "pause"

    # Check for resume commands
    for phrase in resume_phrases:
        if phrase in text_lower:
            logger.info(f"Detected OBSERVER RESUME command: '{text}' matches phrase '{phrase}'")
            return True, "resume"

    # Fuzzy matching: Check if key words appear (for STT errors)
    words = text_lower.split()
    start_keywords = ["start", "begin", "go"]
    survey_keywords = ["survey", "seven", "serve", "server"]  # Common STT misrecognitions

    # If "start/begin" + any survey-like word appears, treat as start command
    has_start_word = any(w in words for w in start_keywords)
    has_survey_word = any(w in words for w in survey_keywords)
    if has_start_word and has_survey_word:
        logger.info(f"Detected OBSERVER START command (fuzzy match): '{text}'")
        return True, "start"

    return False, None
