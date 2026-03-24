"""
AI Community Moderator Agent with Aggressive Interruption and Topic Enforcement
Main agent implementation with moderation capabilities, time management, and topic tracking
"""
import logging
import asyncio
import os
import random
import re
import time as _time_mod
from typing import Optional, Dict, Any, List, Set, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass
from difflib import get_close_matches

from livekit import agents, api
from livekit.agents import Agent, AgentSession, RoomInputOptions
from livekit.plugins import openai, silero, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel
import openai as openai_client  # Direct OpenAI client for relevance checks
try:
    from livekit.plugins import deepgram
    DEEPGRAM_AVAILABLE = True
except ImportError:
    DEEPGRAM_AVAILABLE = False
    deepgram = None

try:
    from livekit.plugins import google
    GOOGLE_AVAILABLE = True
except (ImportError, AttributeError) as e:
    GOOGLE_AVAILABLE = False
    google = None
    # Log will be available after logger is configured
    import sys
    print(f"Warning: Google STT plugin not available: {e}", file=sys.stderr)

try:
    from livekit.plugins import elevenlabs
    ELEVENLABS_AVAILABLE = True
except ImportError:
    ELEVENLABS_AVAILABLE = False
    elevenlabs = None

try:
    from livekit.plugins import anam
    ANAM_AVAILABLE = True
except ImportError:
    ANAM_AVAILABLE = False
    anam = None

from .question_loader import QuestionLoader, Question
from .participant_manager import ParticipantManager
from .audit_logger import AuditLogger
from .survey_transcript import SurveyTranscript
from .survey_data_export import SurveyDataExport
from .survey_config import SurveyConfig
from .stt_debug_logger import STTDebugLogger
from .keyword_extractor import KeywordExtractor
from enum import Enum

# Setup logging
logger = logging.getLogger(__name__)


# ── TTS prompt constants ─────────────────────────────────────────────────────
# Centralised here so every TTS utterance is easy to audit / translate.
# Templates with {name} are formatted at call-sites with the participant's
# display name.  Name-free constants must NEVER be changed to include names —
# the question itself already addresses the participant.

# Response timeout nudge (NO name — question already used it)
TIMEOUT_NUDGE_TEXT = "Please go ahead and share your thoughts."

# STT health-check nudge (name needed — may fire outside question context)
STT_NUDGE_TEMPLATE = (
    "I'm sorry {name}, I couldn't quite hear you. "
    "Could you please repeat that a bit louder?"
)

# Turn-time gentle warning
GENTLE_WARNING_TEMPLATE = (
    "{name}, we're going to need to wrap it up so we can get to others. "
    "Can you spend the next ten to fifteen seconds finishing your thoughts?"
)

# Turn-time force end
FORCE_END_TEMPLATE = (
    "Thank you, {name}. We need to move on to ensure we complete all questions."
)

# Repeat-request acknowledgment
REPEAT_INTRO_TEMPLATE = "Of course, {name}. I'll repeat the question."

# Partial-answer repeat acknowledgment
PARTIAL_REPEAT_INTRO_TEMPLATE = "Got it, {name}. Let me repeat the rest of the question."

# Off-topic / irrelevance redirect
RELEVANCE_PROMPT_TEMPLATE = (
    "Thank you {name}, but I don't think you quite answered the question. "
    "I may be wrong, but I'm going to repeat the question and would you mind "
    "answering again after I'm done repeating it?"
)

# Post-encouragement deterministic follow-up (encouragement already used once)
POST_ENCOURAGEMENT_FOLLOWUP_TEMPLATE = (
    "That's perfectly fine, {name}. Let's move on to the next question."
)

# Silence watchdog prompt — fires when no transcript progression for N seconds
SILENCE_WATCHDOG_PROMPT_TEMPLATE = (
    "I just want to make sure we're still connected, {name}. "
    "Would you like me to repeat the question, or shall we move on?"
)

# Silence watchdog: seconds of no transcript progression before firing
SILENCE_WATCHDOG_TIMEOUT = 12.0

# Idle-no-VAD watchdog: seconds after question delivery with zero VAD
# activity before we nudge the participant.  Covers cases where the user
# stays completely quiet and VAD never fires (partial delivery, mic issues).
IDLE_NO_VAD_TIMEOUT = 12.0

# Extra seconds added to estimated TTS duration before idle watchdog can fire
TTS_SAFETY_MARGIN = 3.0

# Partial-delivery fallback nudge (NO name — re-asks question after)
PARTIAL_DELIVERY_NUDGE_TEXT = (
    "I'm not sure if you heard the full question. Let me repeat it."
)

# Avatar disconnect notice (NO name)
AVATAR_DISCONNECT_TEXT = (
    "Please bear with us for a moment — our visual display "
    "had a brief interruption, but the survey will continue."
)

# Avatar reconnect success notice (NO name)
AVATAR_RECONNECT_TEXT = (
    "Our visual display is back. Let's continue."
)

# ── Avatar health states ────────────────────────────────────────────────────
AVATAR_STATE_IDLE = "idle"            # Not started / not configured
AVATAR_STATE_STARTING = "starting"    # start() in progress
AVATAR_STATE_CONNECTED = "connected"  # Running normally
AVATAR_STATE_DISCONNECTED = "disconnected"  # Lost connection
AVATAR_STATE_RECONNECTING = "reconnecting"  # Reconnect attempt in-flight
AVATAR_STATE_FAILED = "failed"        # Gave up after retries

ANAM_AVATAR_IDENTITY = "anam-avatar-agent"

# ── Off-topic short-response guard constants ──────────────────────────────────
MIN_OFFTOPIC_WORDS = 3
MIN_OFFTOPIC_CHARS = 40

# Blatant off-topic topics where even a short answer is worth checking.
_BLATANT_OFFTOPIC_KEYWORDS = frozenset({
    "basketball", "breakfast", "cat", "cheese", "dinner", "dog",
    "football", "lunch", "movie", "movies", "music", "pizza",
    "soccer", "sport", "sports", "weather", "weekend",
})
_BLATANT_OFFTOPIC_PHRASES = (
    "mind your own business",
    "none of your business",
)

# Filler / hedge words that carry no substantive content.
# Stripped before counting to avoid "um uh well like you know so" (7 words)
# from reaching the word threshold.
_FILLER_TOKENS = frozenset({
    "um", "uh", "uhm", "erm", "hmm", "hm", "ah", "oh",
    "like", "so", "well", "yeah", "yes", "no", "okay", "ok",
    "right", "and", "but", "just", "you", "know", "mean",
    "i", "a", "the", "is", "it", "that", "this",
})


_DEBUG_LOG_PATH = "/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log"


def _debug_log_write(payload: str) -> None:
    """Best-effort, non-fatal debug log write.

    Wraps the synchronous file I/O in a try/except so a disk error never
    crashes a live demo.  The writes are still synchronous (not offloaded to
    a thread) because they are tiny appends, but they are now non-fatal.
    """
    try:
        with open(_DEBUG_LOG_PATH, "a") as _f:
            _f.write(payload + "\n")
    except Exception:
        pass  # Best-effort — never crash the event loop for debug logging


def _substantive_word_count(text: str) -> int:
    """Count words that are NOT filler/hedge tokens."""
    return sum(1 for w in text.lower().split() if w.strip(".,!?…") not in _FILLER_TOKENS)


def _is_too_short_for_offtopic(text: str) -> bool:
    """Return True if *text* is too short/insubstantial for an off-topic verdict.

    Uses three gates (any one triggers skip):
      1. Total word count < MIN_OFFTOPIC_WORDS
      2. Total char count < MIN_OFFTOPIC_CHARS
      3. Substantive (non-filler) word count < 4
    """
    words = len(text.split())
    chars = len(text)
    substantive = _substantive_word_count(text)
    if words < MIN_OFFTOPIC_WORDS or chars < MIN_OFFTOPIC_CHARS or substantive < 4:
        return True
    return False


def _estimate_tts_duration(text: str) -> float:
    """Estimate TTS audio duration from character count.

    ElevenLabs / OpenAI TTS speak at approximately 150 words per minute,
    which is roughly 15 characters per second including spaces.
    Intentionally conservative (slightly slow) so we overestimate rather
    than underestimate — better to wait an extra second than to cut off audio.
    """
    return max(len(text) / 15.0, 1.0)


_DISFLUENT_STARTER_TOKENS = frozenset({
    "well", "um", "uh", "uhm", "erm", "hmm", "ah", "oh",
    "like", "so", "i", "think", "guess", "mean",
    "you", "know", "yeah", "yes", "no", "okay", "ok",
    "right", "and", "but", "just", "that", "the", "a",
    "it", "its", "is", "was", "not", "really",
    "hi", "hello", "hey",
})

DISFLUENCY_EXTENSION_BUDGET = 10.0  # Total extra seconds added to _polling_deadline for disfluency


def _is_disfluent_starter(text: str) -> bool:
    """Return True if text consists entirely of disfluent/filler tokens."""
    words = [w.strip(".,!?…'\"") for w in text.lower().split()]
    words = [w for w in words if w]
    if not words:
        return True
    return all(w in _DISFLUENT_STARTER_TOKENS for w in words)


# Greeting / acknowledgment / mic-check tokens that should be ignored as
# non-substantive turn-start chatter on the FIRST utterance only.
# These are NOT in _DISFLUENT_STARTER_TOKENS because they carry meaning in
# later utterances (e.g., "Sure" as agreement to a follow-up).
_FIRST_UTTERANCE_GREETING_TOKENS = frozenset({
    "sure", "thanks", "thank", "morning", "evening", "afternoon",
    "good", "nice", "meet", "to", "how", "are", "doing", "fine",
    "great", "welcome", "greetings",
})

# Multi-word greeting phrases checked via substring match.
_FIRST_UTTERANCE_GREETING_PHRASES = (
    "thank you",
    "good morning",
    "good evening",
    "good afternoon",
    "how are you",
    "nice to meet you",
    "nice to meet",
)


def _is_first_utterance_greeting(text: str) -> bool:
    """Return True if text is a greeting/acknowledgment with no substantive content.

    Uses the same ALL-words semantics as _is_disfluent_starter(): every word
    must be either a disfluent token or a greeting token.  This prevents
    masking real answers that happen to start with a greeting, e.g.
    "Sure, I think the product is great" → False.
    """
    words = [w.strip(".,!?…'\"") for w in text.lower().split()]
    words = [w for w in words if w]
    if not words:
        return False  # Empty text is handled by disfluency guard
    allowed = _DISFLUENT_STARTER_TOKENS | _FIRST_UTTERANCE_GREETING_TOKENS
    return all(w in allowed for w in words)


def _normalize_for_offtopic_compare(text: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace for off-topic repeat detection."""
    import re as _re
    return _re.sub(r'\s+', ' ', _re.sub(r'[^\w\s]', '', text.lower())).strip()


def _has_blatant_offtopic_keywords(text: str) -> bool:
    """Return True for obviously unrelated short-topic cues like food/weather."""
    normalized = _normalize_for_offtopic_compare(text)
    if not normalized:
        return False
    if any(phrase in normalized for phrase in _BLATANT_OFFTOPIC_PHRASES):
        return True
    return bool(set(normalized.split()) & _BLATANT_OFFTOPIC_KEYWORDS)


# Survey state enum for Observer control
class SurveyState(Enum):
    """Track the current state of the survey for Observer control."""
    WELCOME = "welcome"                             # Delivering welcome/greeting (ignore user STT)
    WAITING_FOR_OBSERVER = "waiting_for_observer"  # Observer must trigger start
    RUNNING = "running"                             # Survey in progress
    PAUSED = "paused"                               # Paused by observer
    COMPLETED = "completed"                         # Survey finished


def parse_multi_option_response(text: str, expected_options: List[str], max_selections: int) -> str:
    """
    Parse a multi-option response and match each part to the expected options list.
    Uses the max_selections from the question to know how many options to extract.

    Args:
        text: The transcribed text containing multiple options
        expected_options: List of valid response options
        max_selections: Number of options expected (from question's max_selections field)

    Returns:
        Formatted string with matched options separated by "; "
    """
    if not expected_options or not text or max_selections <= 1:
        return text

    matched_options = []
    text_lower = text.lower()

    # Try to match each expected option against the text
    for option in expected_options:
        option_lower = option.lower()
        # Get significant words from the option (skip short common words)
        option_words = [w for w in option_lower.split() if len(w) > 3 and w not in ('and', 'the', 'for', 'from', 'with')]

        if not option_words:
            # If no significant words, use all words
            option_words = option_lower.split()

        if not option_words:
            continue

        # Count how many significant words from this option appear in the text
        matches = sum(1 for word in option_words if word in text_lower)
        match_ratio = matches / len(option_words) if option_words else 0

        # If more than 50% of significant words match, consider it a match
        if match_ratio >= 0.5:
            # Find position of first matching word for ordering
            first_pos = -1
            for word in option_words:
                pos = text_lower.find(word)
                if pos >= 0:
                    first_pos = pos
                    break
            matched_options.append((option, match_ratio, first_pos if first_pos >= 0 else 999))

    # Sort by position in text (to maintain order user spoke them)
    matched_options.sort(key=lambda x: x[2])

    # Take top N unique matches based on max_selections
    seen = set()
    final_options = []
    for opt, ratio, pos in matched_options:
        if opt not in seen and len(final_options) < max_selections:
            final_options.append(opt)
            seen.add(opt)

    if final_options:
        result = "; ".join(final_options)
        logger.info(f"Multi-option parsing ({max_selections} expected): '{text[:50]}...' → {len(final_options)} matched: {result[:100]}")
        return result

    # Fallback: return original text if no matches found
    logger.warning(f"Multi-option parsing: No matches found for '{text[:50]}...' (expected {max_selections} options)")
    return text


def correct_transcription(text: str, expected_options: List[str]) -> str:
    """
    Correct common STT errors by fuzzy matching to expected options.
    Uses multiple strategies: exact match, common misrecognitions,
    phonetic matching, and fuzzy string matching.

    Args:
        text: The transcribed text from STT
        expected_options: List of valid response options

    Returns:
        Corrected text matching an expected option, or original text if no match
    """
    if not expected_options:
        return text

    # Normalize text - remove punctuation and extra spaces
    text_clean = re.sub(r'[^\w\s]', '', text.lower().strip())
    text_lower = text.lower().strip()

    # STEP 1: Try exact match first
    for option in expected_options:
        option_clean = re.sub(r'[^\w\s]', '', option.lower().strip())
        if option_clean == text_clean or option.lower() == text_lower:
            return option

    # STEP 2: Common STT misrecognition mappings (phonetic confusions)
    common_corrections = {
        # Right track / Wrong track confusions
        'backtrack': 'right track',
        'back track': 'right track',
        'write track': 'right track',
        'like track': 'right track',
        'white track': 'right track',
        'bright track': 'right track',
        'long track': 'wrong track',
        'strong track': 'wrong track',

        # Rating scale confusions - numbers that sound like words
        '4': 'good',  # "four" sounds like "good" in some accents
        'four': 'good',
        'for': 'good',
        'fore': 'good',
        'beri': 'very poor',
        'berry': 'very poor',
        'very': 'very poor',  # Partial match
        'we report': 'very poor',
        'report': 'very poor',
        'po': 'poor',
        'poo': 'poor',
        'pour': 'poor',
        'blink': 'excellent',  # Might be mishearing
        'great': 'very likely',  # Context-based guess
        'guts': 'gangs',

        # Safe/Likely confusions
        'very safe': 'very safe',
        'somewhat safe': 'somewhat safe',
        'not very safe': 'not very safe',
        'not at all safe': 'not at all safe',

        # Agree/Disagree
        'strongly agree': 'strongly agree',
        'somewhat agree': 'somewhat agree',
        'strongly disagree': 'strongly disagree',
        'somewhat disagree': 'somewhat disagree',
    }

    # Check if text matches any common correction
    if text_clean in common_corrections:
        corrected = common_corrections[text_clean]
        # Find the matching option
        for option in expected_options:
            if corrected in option.lower():
                logger.info(f"STT Correction (common mapping): '{text}' → '{option}'")
                return option

    # STEP 3: Check if any expected option is CONTAINED in the text
    for option in expected_options:
        option_words = option.lower().split()
        # Check if key words from option appear in text
        matches = sum(1 for word in option_words if word in text_clean)
        if matches >= len(option_words) * 0.5:  # At least 50% of words match
            logger.info(f"STT Correction (partial match): '{text}' → '{option}'")
            return option

    # STEP 4: Check if text is contained in any option
    for option in expected_options:
        if text_clean in option.lower() or any(word in option.lower() for word in text_clean.split() if len(word) > 2):
            logger.info(f"STT Correction (substring match): '{text}' → '{option}'")
            return option

    # STEP 5: Fuzzy match with LOWER threshold (40% instead of 60%)
    matches = get_close_matches(
        text_clean,
        [re.sub(r'[^\w\s]', '', opt.lower()) for opt in expected_options],
        n=1,
        cutoff=0.4  # Lowered from 0.6 to 0.4 for more aggressive matching
    )

    if matches:
        # Find original case version
        for option in expected_options:
            if re.sub(r'[^\w\s]', '', option.lower()) == matches[0]:
                logger.info(f"STT Correction (fuzzy match): '{text}' → '{option}'")
                return option

    # STEP 6: Try matching just the first word (for truncated responses)
    first_word = text_clean.split()[0] if text_clean.split() else ""
    if len(first_word) >= 3:
        for option in expected_options:
            if option.lower().startswith(first_word) or first_word in option.lower():
                logger.info(f"STT Correction (first word match): '{text}' → '{option}'")
                return option

    # Return original if no good match found
    logger.warning(f"STT: No correction found for '{text}' in options: {expected_options}")
    return text


def is_uncertain_response(text: str) -> bool:
    """
    Detect if a response is ENTIRELY/PRIMARILY an uncertainty statement.

    IMPORTANT: Only returns True if the response is essentially JUST an uncertain
    phrase without any substantive content. If the participant has provided a
    partial answer along with "I don't know", this returns False to allow the
    partial answer to be processed.

    Examples that return True:
    - "I don't know"
    - "I'm not sure"
    - "Honestly, I don't know"
    - "I really have no idea"

    Examples that return False (have partial answers):
    - "I like the product but I don't know what else to say"
    - "The color is nice, but I'm not sure about the price"
    - "I think it's good, don't know"

    Args:
        text: The transcribed response text

    Returns:
        True if the response is ENTIRELY/PRIMARILY uncertain, False otherwise
    """
    if not text:
        return False

    text_lower = text.lower().strip()

    # Common uncertainty phrases
    uncertain_phrases = [
        "i don't know",
        "i do not know",
        "don't know",
        "do not know",
        "i'm not sure",
        "i am not sure",
        "not sure",
        "no idea",
        "i have no idea",
        "no clue",
        "i have no clue",
        "can't say",
        "cannot say",
        "i can't say",
        "i cannot say",
        "not certain",
        "i'm not certain",
        "i am not certain",
        "uncertain",
        "i'm uncertain",
        "beats me",
        "i couldn't tell you",
        "i could not tell you",
        "couldn't tell you",
        "hard to say",
        "it's hard to say",
        "difficult to say",
        "i'm unsure",
        "i am unsure",
        "unsure",
        "i wouldn't know",
        "i would not know",
        "i really don't know",
        "honestly don't know",
        "honestly i don't know",
        "i honestly don't know",
        "no opinion",
        "i have no opinion",
        "pass",
        "skip",
        "next question",
    ]

    # Filler words that can appear around uncertain phrases without adding substance
    filler_words = {
        "um", "uh", "well", "like", "you know", "i mean", "honestly",
        "actually", "really", "just", "so", "yeah", "hmm", "oh", "ah",
        "let me think", "let me see", "i guess", "i think", "maybe",
        "probably", "perhaps", "sorry", "i'm sorry", "to be honest",
        "tbh", "right", "okay", "ok", "man", "dude", "huh", "er"
    }

    # Check if the response matches any uncertainty phrase
    for phrase in uncertain_phrases:
        if phrase in text_lower:
            # Found an uncertain phrase - now check if there's substantive content
            # Remove the uncertain phrase from the text
            remaining = text_lower.replace(phrase, " ", 1).strip()

            # Remove punctuation and common filler words from remaining text
            remaining = re.sub(r'[.,!?;:\'"()-]', ' ', remaining)
            remaining_words = remaining.split()

            # Filter out filler words
            substantive_words = [
                w for w in remaining_words
                if w not in filler_words and len(w) > 1
            ]

            # If there are no substantive words remaining, it's a pure uncertain response
            if len(substantive_words) == 0:
                logger.info(f"Detected pure uncertain response: '{text}' (phrase: '{phrase}', no substantive content)")
                return True
            else:
                logger.info(f"Response contains uncertain phrase but has substantive content: '{text}' (substantive words: {substantive_words})")
                return False

    # Also check for very short responses that might indicate uncertainty
    # (single word responses that are essentially non-answers)
    short_uncertain = ["idk", "dunno", "dk", "na", "n/a", "none", "nothing"]
    text_words = text_lower.split()
    if len(text_words) <= 2:
        for word in text_words:
            if word in short_uncertain:
                logger.info(f"Detected short uncertain response: '{text}' contains '{word}'")
                return True

    # WORKAROUND: STT sometimes mishears "I don't know" as "I know"
    # If response is just "I know" or "I know." without elaboration, treat as uncertain
    # A genuine "I know" response would typically have more context
    if text_lower in ["i know", "i know."]:
        logger.info(f"Detected potential STT misrecognition: '{text}' (might be 'I don't know')")
        return True

    return False


def is_repeat_request(text: str) -> bool:
    """
    Deterministic heuristic to detect if a response is a request to repeat
    the question.  Called BEFORE the LLM analysis so that common phrasing
    is caught instantly without an extra round-trip.

    Matches phrases like:
    - "Can you repeat that?"
    - "Repeat the question"
    - "Say that again"
    - "What was the question?"
    - "I didn't hear you" / "I didn't hear the last part"
    - "Can you say the first part of the question?"
    - "Come again?"
    - etc.

    Args:
        text: The transcribed response text

    Returns:
        True if the response is a repeat request, False otherwise
    """
    if not text:
        return False

    text_lower = text.lower().strip()

    # ── Length gate ──────────────────────────────────────────────────────
    # If the response is long, it likely contains substantive content
    # alongside a trigger phrase (e.g. "I'm an engineer. I live in Dallas.
    # And what's the last part of the question?").  Defer to LLM analysis
    # which can distinguish partial-answer+repeat from a pure repeat request.
    HEURISTIC_MAX_LENGTH = 60
    if len(text_lower) > HEURISTIC_MAX_LENGTH:
        logger.debug(
            f"🔁 Skipping heuristic repeat check — response too long "
            f"({len(text_lower)} chars > {HEURISTIC_MAX_LENGTH}), deferring to LLM"
        )
        return False

    # ── Substring-match phrases ──────────────────────────────────────────
    repeat_phrases = [
        # Explicit "repeat"
        "repeat",
        # "say … again"
        "say that again",
        "say it again",
        "say the question again",
        "say that one more time",
        # "come again"
        "come again",
        # "what was the question / last part / first part"
        "what was the question",
        "what's the question",
        "what is the question",
        "what was the last part",
        "what was the first part",
        "what was that last part",
        "what was that first part",
        # "didn't catch" (non-directional — always a repeat request)
        "didn't catch",
        "did not catch",
        # "can you say …"
        "can you say that again",
        "could you say that again",
        "can you say the last part",
        "can you say the first part",
        "could you say the last part",
        "could you say the first part",
        "can you say that one more time",
        # "can you repeat"
        "can you repeat",
        "could you repeat",
        "please repeat",
        # "one more time"
        "one more time",
        "again please",
        # politeness / mishearing
        "pardon",
        "excuse me",
        "sorry what",
        # "what did you …"
        "what did you say",
        "what did you ask",
        # missed / misunderstood
        "i missed that",
        "missed the question",
        "didn't understand",
        "did not understand",
        "didn't get that",
        "did not get that",
        "i didn't get the question",
        # audio issues (directional "hear" phrases handled via regex below)
        "speak up",
        "louder please",
        # short interjections
        "what again",
        "huh",
        "what?",
        # "last part" / "first part" variants (without "what was")
        "the last part",
        "the first part",
        "last part of the question",
        "first part of the question",
    ]

    for phrase in repeat_phrases:
        if phrase in text_lower:
            logger.info(f"🔁 Heuristic repeat-request detected: '{text}' matches phrase '{phrase}'")
            return True

    # ── Context-aware "hear" phrases ────────────────────────────────────
    # Only match when the *user* is the one who didn't hear (repeat request),
    # NOT when the user is saying the *agent* didn't hear them (complaint).
    # e.g. "I didn't hear you" → repeat request
    #      "you didn't hear me" / "why didn't you hear me" → NOT a repeat request
    import re

    _hear_phrases = [
        r"didn'?t hear", r"did not hear",
        r"couldn'?t hear", r"could not hear",
        r"can'?t hear", r"cannot hear",
    ]
    for hp in _hear_phrases:
        if re.search(hp, text_lower):
            # Check if "you" is the subject (agent didn't hear) → skip
            if re.search(r"\byou\b.{0,10}" + hp, text_lower) or re.search(r"\byou'?re\b.{0,10}" + hp, text_lower):
                logger.debug(f"🔁 Skipping hear-phrase '{hp}' — subject is 'you' (agent complaint, not repeat request): '{text}'")
            else:
                logger.info(f"🔁 Heuristic repeat-request detected (hear-phrase): '{text}' matches /{hp}/")
                return True

    # ── Regex patterns for harder-to-catch variants ──────────────────────
    _repeat_patterns = [
        # "say … again" with anything in between: "can you say the question again"
        r"\bsay\b.{0,30}\bagain\b",
        # "hear … last/first part": "I didn't hear the last part"
        r"\bhear\b.{0,20}\b(last|first)\s*part\b",
        # "what was … part": "what was the second part"
        r"\bwhat\s+was\b.{0,20}\bpart\b",
    ]
    for pattern in _repeat_patterns:
        if re.search(pattern, text_lower):
            logger.info(f"🔁 Heuristic repeat-request detected (regex): '{text}' matches /{pattern}/")
            return True

    # ── Very short responses that are just confusion ─────────────────────
    if text_lower in ["what", "what?", "huh", "huh?", "sorry", "sorry?", "come again", "come again?"]:
        logger.info(f"🔁 Heuristic repeat-request detected (short): '{text}'")
        return True

    return False


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
            logger.info(f"👁️ Detected OBSERVER START command: '{text}' matches phrase '{phrase}'")
            return True, "start"

    # Check for pause commands
    for phrase in pause_phrases:
        if phrase in text_lower:
            logger.info(f"👁️ Detected OBSERVER PAUSE command: '{text}' matches phrase '{phrase}'")
            return True, "pause"

    # Check for resume commands
    for phrase in resume_phrases:
        if phrase in text_lower:
            logger.info(f"👁️ Detected OBSERVER RESUME command: '{text}' matches phrase '{phrase}'")
            return True, "resume"

    # Fuzzy matching: Check if key words appear (for STT errors)
    words = text_lower.split()
    start_keywords = ["start", "begin", "go"]
    survey_keywords = ["survey", "seven", "serve", "server"]  # Common STT misrecognitions

    # If "start/begin" + any survey-like word appears, treat as start command
    has_start_word = any(w in words for w in start_keywords)
    has_survey_word = any(w in words for w in survey_keywords)
    if has_start_word and has_survey_word:
        logger.info(f"👁️ Detected OBSERVER START command (fuzzy match): '{text}'")
        return True, "start"

    return False, None


@dataclass
class ResponseAnalysis:
    """Result of unified response analysis via LLM"""
    is_relevant: bool  # Is the response relevant to the question?
    is_already_answered_claim: bool  # Is participant claiming they already answered?
    is_repeat_request: bool  # Is participant asking to repeat the question?
    partial_repeat_status: str  # "NO_REPEAT", "REPEAT_ONLY", or "PARTIAL"
    partial_answer: str  # The answer portion if PARTIAL, otherwise empty
    unanswered_questions: str  # Verbatim unanswered sub-questions if PARTIAL, otherwise empty


async def analyze_response(question_text: str, response_text: str, survey_description: str = "") -> ResponseAnalysis:
    """
    Unified LLM analysis of participant response. Checks multiple aspects in a single call:
    1. Is the response relevant to the question?
    2. Is the participant claiming they already answered?
    3. Is there a partial answer with a repeat request for remaining parts?

    Args:
        question_text: The question that was asked
        response_text: The participant's response
        survey_description: Description of the survey topic (from meta.description)

    Returns:
        ResponseAnalysis with all detection results
    """
    # Default result (assume everything is fine)
    default_result = ResponseAnalysis(
        is_relevant=True,
        is_already_answered_claim=False,
        is_repeat_request=False,
        partial_repeat_status="NO_REPEAT",
        partial_answer="",
        unanswered_questions=""
    )

    if not question_text or not response_text:
        return default_result

    # Build survey context for relevance check
    survey_context = f"Survey topic: {survey_description}" if survey_description else "Survey topic: General survey"

    try:
        system_prompt = f"""You are a survey response analyzer. Analyze the participant's response for FOUR aspects.

{survey_context}

1. RELEVANCE: Is the response relevant to the question asked?
   Mark as "relevant" if:
   - The response attempts to answer the question, even if brief or incomplete
   - The response mentions topics related to the survey subject described above
   - UNCERTAINTY expressions: "I don't know", "I'm not sure", etc. (these ARE valid responses)
   - AFFIRMATIVE/NEGATIVE responses: "yes", "no", "definitely", etc.
   - The response shows engagement with the topic even if indirect

   Mark as "not_relevant" if:
   - The response is about a completely unrelated topic (weather, sports, random subjects unrelated to survey)
   - The response is hostile/rude and deflects instead of answering (e.g., "Why are you asking me this?", "That's a stupid question")
   - The response ignores the question entirely with irrelevant content
   - Examples: "Weather is nice today", "I had pizza for lunch", "Mind your own business"

2. ALREADY_ANSWERED: Is the participant ONLY claiming they already answered WITHOUT providing an answer?
   - "claim" ONLY if they say "I already answered", "I told you before", etc. AND do NOT provide any actual answer content
   - "no_claim" if they provide an actual answer (even if they ALSO mention "I already answered")

   CRITICAL: If the response contains BOTH a claim AND an actual answer, mark as "no_claim"!
   - "Life changing. I've answered this." = "no_claim" (contains answer "life changing")
   - "I already told you, it's the single point of contact." = "no_claim" (contains answer)
   - "I already answered that question." = "claim" (no actual answer provided)
   - "Same as what I said before." = "claim" (no specific content, just reference)

3. REPEAT_REQUEST: Is the participant asking to repeat/hear the question again?
   - "repeat" if they're asking to hear the question again: "can you repeat that", "what was the question", "I didn't hear you", "say that again", "come again", "pardon", "sorry what"
   - "no_repeat" if they're providing an answer (even if the answer mentions "didn't hear")

   CRITICAL DISTINCTION:
   - "I didn't hear you" or "I didn't catch that" = repeat request (asking to repeat question)
   - "I didn't hear about [topic]" or "I never heard of that" = NOT a repeat request (this is an ANSWER about not having heard of something)

4. PARTIAL_REPEAT: Did they provide a PARTIAL answer AND EXPLICITLY ask to repeat/hear the remaining parts?
   - "NO_REPEAT" - Normal response, even if incomplete (DEFAULT - use this most of the time)
   - "REPEAT_ONLY" - Just asking to repeat the full question, no answer given
   - "PARTIAL" - ONLY use when BOTH conditions are met:
     a) They gave an answer to SOME parts of a multi-part question
     b) They EXPLICITLY asked to hear the rest: "what else?", "what was the other part?", "repeat the rest", "what's the other question?"

   CRITICAL: DO NOT use PARTIAL just because the answer seems incomplete!
   - If they answered some parts but didn't ask for remaining parts → use NO_REPEAT
   - If they asked "what else?" or "what was the other question?" → use PARTIAL

   WHEN PARTIAL: The unanswered_questions field must contain ONLY the sub-question(s) they DID NOT answer.
   - CAREFULLY compare each part of the question against what they said
   - If they mentioned a topic, that part IS answered (even if brief)
   - Example: Question "Where do you live AND what do you do for work?"
     Response "I'm an engineer in Dallas. What else?"
     - They answered BOTH parts (engineer=work, Dallas=where)
     - unanswered_questions should be EMPTY because both were answered
     - This should be NO_REPEAT, not PARTIAL!

   - Example: Question "Where do you live AND what do you do for work?"
     Response "I live in Dallas. What was the other part?"
     - They answered WHERE (Dallas) but NOT work
     - unanswered_questions: "what do you do for work"

RESPONSE FORMAT (use | as delimiter, exactly 6 fields):
<relevance>|<already_answered>|<repeat_request>|<partial_status>|<partial_answer>|<unanswered_questions>

Examples:
- Normal complete answer: "relevant|no_claim|no_repeat|NO_REPEAT||"
- Incomplete answer (no repeat request): "relevant|no_claim|no_repeat|NO_REPEAT||" (NOT PARTIAL!)
- Asking to repeat full question: "relevant|no_claim|repeat|NO_REPEAT||"
- "I didn't hear about [topic] before": "relevant|no_claim|no_repeat|NO_REPEAT||" (THIS IS AN ANSWER!)
- Off-topic like "Weather is nice": "not_relevant|no_claim|no_repeat|NO_REPEAT||"
- Uncertainty "I don't know": "relevant|no_claim|no_repeat|NO_REPEAT||"
- Already answered claim ONLY (no answer): "relevant|claim|no_repeat|NO_REPEAT||"
- Claim WITH answer "Life changing. I've answered this.": "relevant|no_claim|no_repeat|NO_REPEAT||" (has actual answer!)
- Partial with explicit request: Question "What do you like AND dislike?", Response "I like the food. What else did you ask?": "relevant|no_claim|no_repeat|PARTIAL|I like the food.|what do you dislike"
- Answered both parts: Question "Where do you live AND work?", Response "Dallas, I'm retired. What else?": "relevant|no_claim|no_repeat|NO_REPEAT||" (Both answered, no unanswered parts!)

IMPORTANT: Default to NO_REPEAT. Only use PARTIAL when user EXPLICITLY asks for remaining parts AND there actually ARE unanswered parts."""

        user_prompt = f"""Question: {question_text}

Participant's Response: {response_text}

Analyze this response and output in the exact format: relevance|already_answered|partial_status|partial_answer|unanswered_questions"""

        client = openai_client.AsyncOpenAI()
        llm_response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=500,
            temperature=0.0
        )

        result = llm_response.choices[0].message.content.strip()
        logger.info(f"🔍 Response analysis - Question: '{question_text[:50]}...', Response: '{response_text[:50]}...', LLM says: '{result}'")

        # Parse the response (6 fields separated by |)
        parts = result.split("|")
        if len(parts) >= 6:
            relevance = parts[0].strip().lower()
            already_answered = parts[1].strip().lower()
            repeat_request = parts[2].strip().lower()
            partial_status = parts[3].strip().upper()
            partial_answer = parts[4].strip()
            unanswered = parts[5].strip()

            # Validate and normalize
            is_relevant = "not_relevant" not in relevance and "not relevant" not in relevance
            is_claim = "claim" in already_answered and "no_claim" not in already_answered
            is_repeat = "repeat" in repeat_request and "no_repeat" not in repeat_request

            if partial_status not in ("NO_REPEAT", "REPEAT_ONLY", "PARTIAL"):
                partial_status = "NO_REPEAT"

            analysis = ResponseAnalysis(
                is_relevant=is_relevant,
                is_already_answered_claim=is_claim,
                is_repeat_request=is_repeat,
                partial_repeat_status=partial_status,
                partial_answer=partial_answer if partial_status == "PARTIAL" else "",
                unanswered_questions=unanswered if partial_status in ("REPEAT_ONLY", "PARTIAL") else ""
            )

            # Log warnings for special cases
            if not is_relevant:
                logger.warning(f"⚠️ Off-topic response detected: '{response_text[:100]}'")
            if is_claim:
                logger.warning(f"⚠️ 'Already answered' claim detected: '{response_text[:100]}'")
            if is_repeat:
                logger.info(f"🔁 Repeat request detected: '{response_text[:100]}'")
            if partial_status == "PARTIAL":
                logger.info(f"📝 Partial answer detected. Answer: '{partial_answer[:50]}...', Unanswered: '{unanswered[:50]}...'")

            return analysis
        elif len(parts) >= 5:
            # Backward compatibility: handle old 5-field format
            relevance = parts[0].strip().lower()
            already_answered = parts[1].strip().lower()
            partial_status = parts[2].strip().upper()
            partial_answer = parts[3].strip()
            unanswered = parts[4].strip()

            is_relevant = "not_relevant" not in relevance and "not relevant" not in relevance
            is_claim = "claim" in already_answered and "no_claim" not in already_answered

            if partial_status not in ("NO_REPEAT", "REPEAT_ONLY", "PARTIAL"):
                partial_status = "NO_REPEAT"

            return ResponseAnalysis(
                is_relevant=is_relevant,
                is_already_answered_claim=is_claim,
                is_repeat_request=(partial_status == "REPEAT_ONLY"),
                partial_repeat_status=partial_status,
                partial_answer=partial_answer if partial_status == "PARTIAL" else "",
                unanswered_questions=unanswered if partial_status in ("REPEAT_ONLY", "PARTIAL") else ""
            )
        else:
            logger.warning(f"Unexpected LLM response format (expected 6 parts): {result}")
            return default_result

    except Exception as e:
        logger.error(f"Error analyzing response: {e}")
        return default_result


# Backward compatibility wrapper for existing code
async def check_response_relevance(question_text: str, response_text: str, survey_description: str = "") -> bool:
    """
    Legacy wrapper for analyze_response(). Returns only relevance check result.
    Prefer using analyze_response() directly for full analysis.
    """
    analysis = await analyze_response(question_text, response_text, survey_description)
    return analysis.is_relevant


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
    warned: bool = False  # Whether we've given pre-warning
    first_interrupted: bool = False  # Whether we've aggressively interrupted
    force_interrupted: bool = False  # Whether we've force-ended
    interruption_count: int = 0  # Number of times interrupted
    interrupt_denied_count: int = 0  # session.interrupt() calls that raised RuntimeError
    off_topic_start: Optional[datetime] = None  # When participant went off-topic
    off_topic_interrupted: bool = False  # Whether we've interrupted for off-topic
    actual_speaking_duration: float = 0.0  # Actual speaking time (captured when user stops, doesn't include pauses)
    pending_soft_warning: bool = False  # Flag: time exceeded, waiting to deliver warning when user is speaking


class CommunityModeratorAgent(Agent):
    """AI agent for moderating community discussions with aggressive interruption and topic tracking"""

    def __init__(
        self,
        instructions: str,
        max_turn_duration: int = 20,
        turn_warning_duration: int = 15,
        first_interrupt_grace: int = 5,
        second_interrupt_grace: int = 10,
        enable_turn_limits: bool = True,
        force_interrupt_enabled: bool = True,
        discussion_topic: str = "the current topic",
        off_topic_interrupt_threshold: int = 15,
        enable_topic_enforcement: bool = True,
        question_loader: Optional[QuestionLoader] = None,
        participant_manager: Optional[ParticipantManager] = None,
        survey_config: Optional[SurveyConfig] = None,
    ):
        super().__init__(instructions=instructions)
        self.participant_tracker = {}
        self.moderation_events = []
        self.start_time = datetime.now()

        # Turn duration tracking configuration
        self.max_turn_duration = max_turn_duration
        self.turn_warning_duration = turn_warning_duration
        self.first_interrupt_grace = first_interrupt_grace
        self.second_interrupt_grace = second_interrupt_grace
        self.enable_turn_limits = enable_turn_limits
        self.force_interrupt_enabled = force_interrupt_enabled

        # Calculated thresholds
        self.first_interrupt_threshold = max_turn_duration + first_interrupt_grace
        self.second_interrupt_threshold = self.first_interrupt_threshold + second_interrupt_grace

        # Topic enforcement configuration
        self.discussion_topic = discussion_topic
        self.off_topic_interrupt_threshold = off_topic_interrupt_threshold
        self.enable_topic_enforcement = enable_topic_enforcement

        # Current turn tracking
        self.current_turn: Optional[TurnInfo] = None
        self.turn_monitor_task: Optional[asyncio.Task] = None
        self.off_topic_monitor_task: Optional[asyncio.Task] = None

        # Question-based moderation (optional)
        self.question_loader = question_loader
        self.participant_manager = participant_manager
        self.current_question = None  # Formatted question text for speech
        self.current_question_object: Optional[Question] = None  # Full Question object with options

        # Audit logger for debugging
        self.audit_logger = AuditLogger()

        # Survey configuration and output
        self.survey_config = survey_config
        output_dir = str(survey_config.response_output_dir) if survey_config else "output"

        # Survey transcript for clean output (debug/audit)
        self.survey_transcript = SurveyTranscript(output_dir=output_dir)

        # Survey data export for CSV analysis
        self.survey_data_export = SurveyDataExport(output_dir=output_dir)

        # STT debug logger for capturing RAW transcripts vs corrected responses
        self.stt_debug_logger = STTDebugLogger(output_dir=output_dir)

        self.current_question_num = 0
        self.agent_session: Optional[AgentSession] = None
        self.waiting_for_response = False
        self.response_timeout_task: Optional[asyncio.Task] = None
        self.last_speech_time: Optional[datetime] = None
        self.user_currently_speaking = False  # Track if user is actively speaking (for polling)
        self._last_user_state: Optional[str] = None  # Latest user state from user_state_changed (speaking/listening/away)
        self._vad_speaking_segment_start: Optional[datetime] = None  # When current speaking segment started (for VAD-based duration)
        self.response_captured = False  # Flag set by event handler when STT captures response
        self.latest_user_response = None  # Store latest STT transcript (bypassing LLM context entirely)
        self.response_fragments = []  # DEPRECATED: No longer used (STT sends cumulative transcripts, not fragments)
        self.last_fragment_time = None  # Track when last transcript was received
        self.last_stt_fragment = ""  # Track the last individual STT fragment (for cumulative detection)
        self._turn_accumulated_text = ""  # Accumulates completed STT utterances within a turn
        # Phase 1 additions: Event-based response capture
        self.captured_response: Optional[str] = None  # Final committed response text
        self._response_ready: asyncio.Event = asyncio.Event()  # Signaled when user_speech_committed fires
        # NETWORK LATENCY TRACKING
        self.speaking_start_time: Optional[datetime] = None  # When user started speaking (for STT latency calc)
        self.first_stt_received = False  # Whether we've received first STT for this speaking segment
        self.observed_stt_latency_ms = 0  # Most recent STT latency observed
        self.max_observed_latency_ms = 0  # Maximum latency seen in this session
        self.is_multi_option_question = False  # Track if current question requires multiple answers (e.g., "Choose THREE")
        self.fragment_gap_timeout = 5.0  # Default gap between fragments (seconds) - increased from 3s
        self.last_respondent = None  # Track the participant who just answered (for personalized acknowledgment)
        self._last_turn_result: Optional[TurnResult] = None  # Immutable turn snapshot for ack
        self.encouragement_given = False  # Track if we've already encouraged participant on "I don't know" response
        self.question_repeated = False  # Track if we've already repeated the question for this participant
        self.relevance_prompt_given = False  # Track if we've already asked for relevant response (don't check twice)
        self.partial_repeat_handled = False  # Track if partial repeat done for this question
        self.already_answered_prompt_given = False  # Track if we've asked participant to rephrase after "already answered" claim
        self.accumulated_partial_answer = ""  # Store partial answers to combine with final response for transcript

        # MULTI-PARTICIPANT FIX: Track WHO is speaking
        self.expected_respondent = None  # Who we asked the question to
        self.actual_respondent = None  # Who actually spoke (detected from audio activity)
        self._current_stt_participant: Optional[str] = None  # Cached identity of participant STT is locked to
        self.participant_audio_activity = {}  # participant_identity -> last_audio_time
        self.active_speaker_track_sid = None  # Currently active audio track SID

        # Delivery state tracking to prevent skip-on-drift behavior
        self.question_delivery_state: Dict[Tuple[int, str], str] = {}  # (question_num, participant) -> state
        self.question_delivery_retries: Dict[Tuple[int, str], int] = {}  # bounded retries per question+participant
        self.question_delivery_retry_after: Dict[Tuple[int, str], datetime] = {}  # retry cooldown timestamp
        self.max_delivery_retries_per_question = 3

        # LiveKit API for muting/unmuting participants
        self.livekit_api = None  # Will be set during session creation
        self.room_name = None  # Will be set during session creation

        # Observer/Survey state management
        self.survey_state = SurveyState.WELCOME  # Start in WELCOME phase (ignore user STT until questions begin)
        self.observer_mode_enabled = False  # Set True if observer is present
        self.paused_at_question = None  # Track question number when paused
        self.paused_at_participant = None  # Track participant when paused
        self.pause_start_time: Optional[datetime] = None  # When pause started
        self.accumulated_pause_duration: float = 0.0  # Total paused time for current turn
        self._response_processing_start: Optional[datetime] = None  # LATENCY TRACKING: When response processing began
        self._transition_filler_said: bool = False  # One-time filler guard per turn transition
        self._analysis_start_time: Optional[datetime] = None  # For analysis_ms metric
        self._silence_confirmed_time: Optional[datetime] = None  # For silence_confirmation_ms metric
        self.turn_time_exceeded: bool = False  # Flag set when turn monitor detects time exceeded and user stopped
        # _tts_active property (below) replaces old _tts_active / _tts_lock / _tts_sequence machinery
        self._estimated_remaining_tts: float = 0.0  # Seconds of TTS estimated still audible on client
        self._polling_deadline: Optional[float] = None  # time.time() deadline; extended on repeat
        self._ack_already_spoken: bool = False  # True when polling loop already spoke the ack
        self._prewarmed_ack_text: Optional[str] = None  # Pre-computed ack text for concurrent TTS
        self._gentle_warning_in_progress: bool = False  # True while gentle warning TTS is playing; prevents premature response capture
        self._gentle_warning_started_at: Optional[float] = None  # time.time() when warning flag was set; watchdog clears if stuck > 8s
        self._analysis_in_progress: bool = False  # True while LLM analysis is running; prevents capture from overwriting the snapshot
        self._last_avatar_disconnect_notice: float = 0.0  # time.time() of last avatar disconnect TTS; dedupe cooldown
        self._first_fragment_time: Optional[datetime] = None  # When first STT fragment arrived for current question; used for stabilization delay
        self._user_stopped_speaking_at: Optional[datetime] = None  # Timestamp when user last transitioned from speaking to listening; used for pause cooldown
        self._stt_nudge_given: bool = False  # True after we've nudged the participant due to no STT transcripts
        self._first_vad_speaking_time: Optional[datetime] = None  # When VAD first detected speech for this question; used for STT health check
        self._shutting_down: bool = False  # Set True during survey completion/room teardown; stops all loops and TTS
        self._tts_dedupe_spoken: Set[str] = set()  # Per-turn TTS dedupe keys; cleared on question/participant transitions
        self._encouragement_followup_given: bool = False  # True after deterministic follow-up to a second uncertain response
        self._last_transcript_progress_time: Optional[float] = None  # time.time() when latest_user_response last changed; silence watchdog
        self._silence_watchdog_fired: bool = False  # True after silence watchdog has fired once for this turn
        self._short_offtopic_count: int = 0  # Number of short off-topic responses this turn (for escalation)
        self._last_short_offtopic_norm: Optional[str] = None  # Normalized text of last short off-topic response (for repeat detection)
        self._had_stt_transcript_this_turn: bool = False  # True once any STT transcript has been received this turn; prevents false STT nudges
        self._polling_start_time: Optional[float] = None  # time.time() when polling loop started; idle-no-VAD watchdog reference
        self._idle_no_vad_nudge_fired: bool = False  # True after idle-no-VAD watchdog has fired once

        # ── Avatar lifecycle tracking ───────────────────────────────────────
        self._avatar_state: str = AVATAR_STATE_IDLE
        self._avatar_enabled: bool = False  # True if avatar was configured and started successfully
        self._avatar_connected: bool = False  # True while avatar is connected and rendering
        self._avatar_disconnect_reason: Optional[str] = None  # Human-readable reason for last disconnect
        self._avatar_disconnect_time: Optional[float] = None  # time.time() of last disconnect
        self._avatar_reconnect_task: Optional[asyncio.Task] = None  # Single-flight reconnect task
        self._avatar_reconnect_attempts: int = 0
        self._avatar_notice_task: Optional[asyncio.Task] = None  # Deferred disconnect notice task
        self._avatar_session_ref: Optional[object] = None  # Long-lived reference to anam.AvatarSession
        self._audio_only_mode: bool = os.environ.get("AUDIO_ONLY_MODE", "").lower() in ("1", "true", "yes")
        self._avatar_reconnect_enabled: bool = os.environ.get("AVATAR_RECONNECT_ENABLED", "").lower() in ("1", "true", "yes")

        logger.info(
            f"Agent initialized with graceful survey time management: "
            f"base={max_turn_duration}s, gentle_warning={max_turn_duration + first_interrupt_grace}s, "
            f"force_end={max_turn_duration + first_interrupt_grace + second_interrupt_grace}s, enabled={enable_turn_limits}"
        )
        logger.info(
            f"Topic enforcement: topic='{discussion_topic}', "
            f"threshold={off_topic_interrupt_threshold}s, enabled={enable_topic_enforcement}"
        )
        if question_loader:
            logger.info("Question-based moderation enabled")

    async def manage_participant_muting(self, active_participant: str):
        """
        Mute all participants except the active speaker.
        Uses MutePublishedTrack API for proper track-level muting that
        syncs with frontend TrackMuted/TrackUnmuted events automatically.

        Args:
            active_participant: Identity of the participant who should be unmuted
        """
        if not self.livekit_api or not self.room_name or not self.participant_manager:
            logger.warning("Cannot manage muting - API or room not initialized")
            return

        logger.info(f"🔇 Managing participant muting: unmuting '{active_participant}', muting all others")

        # Only try to mute/unmute participants who are currently available (in the room)
        all_participants = [
            p for p in self.participant_manager.participants
            if p not in self.participant_manager.unavailable_participants
        ]

        # Get current room participants to find their audio track SIDs
        try:
            participants_response = await self.livekit_api.room.list_participants(
                api.ListParticipantsRequest(room=self.room_name)
            )
            room_participants = {p.identity: p for p in (participants_response.participants or [])}
        except Exception as e:
            logger.error(f"❌ Failed to list participants for muting: {e}")
            return

        # SAFETY: Verify the active_participant actually exists in the room
        # before muting everyone else. If not found, abort to avoid silencing
        # the entire room (common in single-participant demos).
        if active_participant not in room_participants:
            logger.warning(
                f"🛡️ MUTING ABORTED: active_participant '{active_participant}' not found "
                f"in room participants {list(room_participants.keys())}. "
                f"Skipping mute to avoid silencing the room."
            )
            return

        # Never mute the Anam avatar participant — it publishes the agent's TTS; muting it would make participants unable to hear the agent
        for participant_identity in all_participants:
            try:
                if participant_identity == ANAM_AVATAR_IDENTITY or "avatar-agent" in participant_identity:
                    logger.debug(f"  Skipping mute for avatar participant: {participant_identity}")
                    continue

                should_mute = (participant_identity != active_participant)

                # Find this participant's info from room
                participant_info = room_participants.get(participant_identity)
                if not participant_info:
                    logger.warning(f"  ⚠️ Participant {participant_identity} not found in room")
                    continue

                # Find their audio/microphone track SID
                audio_track_sid = None
                for track in participant_info.tracks:
                    if track.source == api.TrackSource.MICROPHONE:
                        audio_track_sid = track.sid
                        break

                if not audio_track_sid:
                    logger.warning(f"  ⚠️ No audio track found for {participant_identity} — they may not have published mic yet")
                    continue

                # Use MutePublishedTrack API — this fires TrackMuted/TrackUnmuted events
                # on all connected clients, keeping frontend UI in sync automatically
                await self.livekit_api.room.mute_published_track(
                    api.MuteRoomTrackRequest(
                        room=self.room_name,
                        identity=participant_identity,
                        track_sid=audio_track_sid,
                        muted=should_mute,
                    )
                )

                status = "muted" if should_mute else "unmuted"
                logger.info(f"  ✅ {participant_identity}: {status} (track: {audio_track_sid})")

            except Exception as e:
                logger.error(f"  ❌ Failed to update muting for {participant_identity}: {e}")
                import traceback
                logger.error(traceback.format_exc())

        logger.info(f"✅ Muting management complete - {active_participant} can speak")

    def _active_respondent_count(self) -> int:
        """Return the number of active (connected, non-observer) respondents."""
        if not self.participant_manager:
            return 0
        available = [
            p for p in self.participant_manager.participants
            if p not in self.participant_manager.unavailable_participants
        ]
        return len(available)

    def _get_audio_input(self):
        """Return the RoomIO audio_input handle, or None if unavailable."""
        if not self.agent_session:
            return None
        room_io = getattr(self.agent_session, '_room_io', None)
        if not room_io:
            return None
        return getattr(room_io, '_audio_input', None)

    def _set_stt_participant(self, identity: str, *, context: str = "") -> bool:
        """Direct STT to listen to a single participant. Returns True on success."""
        audio_input = self._get_audio_input()
        if not audio_input:
            logger.error(f"audio_input unavailable — cannot set STT to '{identity}' ({context})")
            return False
        try:
            audio_input.set_participant(identity)
            self._current_stt_participant = identity
            logger.critical(f"STT set_participant('{identity}') — {context}")
            return True
        except Exception as e:
            logger.error(f"set_participant('{identity}') failed ({context}): {e}")
            return False

    async def unmute_all_participants(self):
        """
        Unmute all participants (e.g., at the end of survey or for open discussion).
        Uses MutePublishedTrack API to unmute audio tracks, firing TrackUnmuted events.
        """
        if not self.livekit_api or not self.room_name or not self.participant_manager:
            logger.warning("Cannot unmute all - API or room not initialized")
            return

        logger.info("🔊 Unmuting all participants")

        all_participants = list(self.participant_manager.participants)

        # Get current room participants to find their audio track SIDs
        try:
            participants_response = await self.livekit_api.room.list_participants(
                api.ListParticipantsRequest(room=self.room_name)
            )
            room_participants = {p.identity: p for p in (participants_response.participants or [])}
        except Exception as e:
            logger.error(f"❌ Failed to list participants for unmuting: {e}")
            return

        for participant_identity in all_participants:
            try:
                participant_info = room_participants.get(participant_identity)
                if not participant_info:
                    continue

                for track in participant_info.tracks:
                    # Only unmute microphone tracks that are currently muted
                    if track.source == api.TrackSource.MICROPHONE and track.muted:
                        await self.livekit_api.room.mute_published_track(
                            api.MuteRoomTrackRequest(
                                room=self.room_name,
                                identity=participant_identity,
                                track_sid=track.sid,
                                muted=False,
                            )
                        )
                        logger.info(f"  ✅ {participant_identity}: unmuted (track: {track.sid})")
            except Exception as e:
                logger.error(f"  ❌ Failed to unmute {participant_identity}: {e}")
                import traceback
                logger.error(traceback.format_exc())

        logger.info("✅ All participants unmuted")

    def _delivery_key(self, question_num: int, participant_identity: str) -> Tuple[int, str]:
        return (question_num, participant_identity)

    def _set_delivery_state(self, question_num: int, participant_identity: str, state: str, context: str = ""):
        key = self._delivery_key(question_num, participant_identity)
        old_state = self.question_delivery_state.get(key, "none")
        self.question_delivery_state[key] = state
        logger.debug(
            f"[delivery-state] q={question_num} participant={participant_identity} -> {state}"
            f"{f' ({context})' if context else ''}"
        )
        # #region agent log
        import json as _json
        _debug_log_write(_json.dumps({"location": "moderator_agent.py:_set_delivery_state", "message": f"delivery-state transition", "data": {"question_num": question_num, "participant": participant_identity, "old_state": old_state, "new_state": state, "context": context}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "H_DELIVERY_TRACE"}))
        # #endregion

    def _is_delivery_confirmed(self, question_num: int, participant_identity: str) -> bool:
        """True if question was delivered (fully or partially)."""
        key = self._delivery_key(question_num, participant_identity)
        return self.question_delivery_state.get(key) in ("delivered_full", "delivered_partial",
                                                          # backward compat with old "delivered" state
                                                          "delivered")

    def _is_delivery_full(self, question_num: int, participant_identity: str) -> bool:
        """True only if TTS completed without truncation."""
        key = self._delivery_key(question_num, participant_identity)
        return self.question_delivery_state.get(key) in ("delivered_full", "delivered")

    def _record_turn_result(self, expected: str, actual: str, *, was_timeout: bool = False) -> TurnResult:
        """Create an immutable TurnResult and update last_respondent atomically.

        This is the SINGLE place that decides who gets thanked, eliminating
        stale-name bugs from the mutable ``last_respondent`` field.
        """
        expected_display = (
            self.participant_manager.get_display_name(expected)
            if self.participant_manager else expected.capitalize()
        )
        actual_display = (
            self.participant_manager.get_display_name(actual)
            if self.participant_manager else actual.capitalize()
        )
        result = TurnResult(
            expected_identity=expected,
            actual_identity=actual,
            expected_display=expected_display,
            actual_display=actual_display,
            was_timeout=was_timeout,
        )
        self._last_turn_result = result
        self.last_respondent = actual  # keep legacy field in sync
        logger.info(
            f"📋 TURN RESULT: expected={expected} actual={actual} "
            f"ack_name={result.ack_name} timeout={was_timeout}"
        )

        # #region agent log
        import json as _json
        _debug_log_write(_json.dumps({"location": "moderator_agent.py:_record_turn_result", "message": "Turn result recorded", "data": {"expected": expected, "actual": actual, "ack_name": result.ack_name, "timeout": was_timeout, "question_num": self.current_question_num}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "ACK_NAME"}))
        # #endregion

        return result

    async def _analyze_with_filler(self, question_text: str, response_text: str, survey_desc: str, filler_threshold: float = 2.0):
        """Run LLM analysis with a transition filler if it takes too long.

        Starts the analysis as a concurrent task. If it doesn't complete
        within `filler_threshold` seconds AND no filler has been said this
        turn, a brief "One moment..." is spoken to reduce perceived dead-air.
        Returns the analysis result and the wall-clock duration in seconds.
        """
        self._analysis_start_time = datetime.now()
        analysis_task = asyncio.create_task(analyze_response(question_text, response_text, survey_desc))

        try:
            result = await asyncio.wait_for(asyncio.shield(analysis_task), timeout=filler_threshold)
        except asyncio.TimeoutError:
            if not self._transition_filler_said and self.agent_session:
                self._transition_filler_said = True
                logger.info(f"🔊 Transition filler triggered (analysis > {filler_threshold}s)")
                # #region agent log
                import json as _json
                _debug_log_write(_json.dumps({"location": "moderator_agent.py:_analyze_with_filler", "message": "Filler triggered", "data": {"threshold_s": filler_threshold, "question_num": self.current_question_num}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "FILLER"}))
                # #endregion
                await self._safe_say("One moment...", allow_interruptions=False, context="analysis_filler")
            result = await analysis_task

        analysis_duration = (datetime.now() - self._analysis_start_time).total_seconds()
        logger.info(f"📊 METRIC: analysis_ms={analysis_duration * 1000:.0f}")

        # #region agent log
        import json as _json
        _debug_log_write(_json.dumps({"location": "moderator_agent.py:_analyze_with_filler", "message": "Analysis complete", "data": {"analysis_ms": round(analysis_duration * 1000), "filler_spoken": self._transition_filler_said, "is_relevant": result.is_relevant, "question_num": self.current_question_num}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "METRICS"}))
        # #endregion

        return result, analysis_duration

    async def _prewarm_ack_tts(self, text: str):
        """Best-effort background TTS pre-synthesis to warm engine caches.

        Runs concurrently with the LLM analysis so that when the analysis
        completes and the ack is spoken via session.say(), the TTS engine
        has already seen/cached the text, reducing first-byte latency.
        """
        try:
            if self.agent_session and hasattr(self.agent_session, 'tts') and self.agent_session.tts:
                async for _ in self.agent_session.tts.synthesize(text):
                    pass
                logger.debug(f"🔊 TTS pre-warm complete for: '{text}'")
        except Exception as e:
            logger.debug(f"TTS pre-warm skipped (non-critical): {e}")

    # ── Phase 1: Event-driven response capture methods ────────────────────

    def _reset_response_flags(self, participant: str) -> None:
        """Centralised flag reset before waiting for a new response.

        Called at the start of ``_await_response`` and whenever the turn is
        retried (repeat, off-topic, encouragement).  Mirrors the flag resets
        previously scattered across L3441-3469 and the second loop equivalent.
        """
        import time as _time

        # Core response state
        self.captured_response = None
        self._response_ready.clear()
        self.latest_user_response = None
        self.response_captured = False
        self.last_stt_fragment = ""
        self.pending_stt_transcript = None
        self.response_fragments = []
        self.last_fragment_time = None
        self._first_fragment_time = None
        self._user_stopped_speaking_at = None
        self.actual_respondent = None
        self._turn_accumulated_text = ""

        # Flow control
        self._gentle_warning_in_progress = False
        self._gentle_warning_started_at = None
        self._stt_nudge_given = False
        self._first_vad_speaking_time = None
        self.encouragement_given = False
        self._encouragement_followup_given = False
        self._last_transcript_progress_time = None
        self._silence_watchdog_fired = False
        self._short_offtopic_count = 0
        self._last_short_offtopic_norm = None
        self._had_stt_transcript_this_turn = False
        self._idle_no_vad_nudge_fired = False
        self.question_repeated = False
        self.relevance_prompt_given = False
        self.partial_repeat_handled = False
        self.already_answered_prompt_given = False
        self.accumulated_partial_answer = ""
        self.turn_time_exceeded = False
        self._ack_already_spoken = False
        self._prewarmed_ack_text = None
        self._first_utterance_greeting_guard_used = False
        self._transition_filler_said = False
        self._estimated_remaining_tts = 0.0

        # Timeout / turn monitoring
        self.waiting_for_response = True
        self.last_speech_time = None
        self._polling_start_time = _time.time()

    async def _await_response(
        self,
        participant: str,
        polling_timeout: float,
        tts_fully_spoken: bool,
    ) -> Optional[str]:
        """Wait for a committed user response, running watchdog checks.

        Replaces both SMART POLLING loops.  Returns the captured response
        text, ``None`` on timeout/shutdown, or ``"PAUSED"`` sentinel.
        """
        import time as _time

        _saved_tts_estimate = self._estimated_remaining_tts
        self._reset_response_flags(participant)
        self._estimated_remaining_tts = _saved_tts_estimate

        # Start timeout monitor
        if self.response_timeout_task:
            self.response_timeout_task.cancel()
            self.response_timeout_task = None
        self.response_timeout_task = asyncio.create_task(
            self.monitor_response_timeout(participant)
        )

        self._polling_deadline = _time.time() + polling_timeout

        # Observer STT switching task
        observer_stt_task: Optional[asyncio.Task] = None
        if self.observer_mode_enabled:
            observer_stt_task = asyncio.create_task(
                self._observer_stt_polling(participant)
            )

        try:
            while True:
                # Wait on the event with a 2s granularity for watchdog checks
                remaining = self._polling_deadline - _time.time()
                if remaining <= 0 and not self.user_currently_speaking and not self._gentle_warning_in_progress:
                    if self.captured_response is not None:
                        logger.info("Deadline reached but captured_response exists — processing")
                    else:
                        logger.warning(f"Polling deadline reached ({polling_timeout}s effective)")
                        break

                try:
                    await asyncio.wait_for(
                        self._response_ready.wait(),
                        timeout=min(max(remaining, 0.1), 2.0),
                    )
                except asyncio.TimeoutError:
                    pass  # Fall through to watchdog checks

                # ── Shutdown guard ──
                if self._shutting_down:
                    logger.info("_await_response exiting: _shutting_down=True")
                    break

                # ── Timeout monitor signal ──
                if not self.waiting_for_response:
                    logger.warning("Timeout monitor signaled no-response — exiting")
                    break

                # ── Pause check ──
                if self.survey_state == SurveyState.PAUSED:
                    logger.info("Survey PAUSED during _await_response — waiting for resume")
                    # Switch STT to observer while paused
                    observer_identity = self.participant_manager.get_observer_identity()
                    if observer_identity:
                        self._set_stt_participant(observer_identity, context="pause_in_await_response")
                    while self.survey_state == SurveyState.PAUSED:
                        await asyncio.sleep(0.5)
                    logger.info("Survey RESUMED — exiting _await_response for re-ask")
                    return "PAUSED"

                # ── Gentle warning guard ──
                if self._gentle_warning_in_progress:
                    if self._gentle_warning_started_at and (_time.time() - self._gentle_warning_started_at) > 8.0:
                        logger.error("WATCHDOG: _gentle_warning_in_progress stuck >8s — force-clearing")
                        self._gentle_warning_in_progress = False
                        self._gentle_warning_started_at = None
                    else:
                        # While gentle warning plays, clear event and keep waiting
                        if self._response_ready.is_set():
                            self.captured_response = None
                            self._response_ready.clear()
                        continue

                # ── Deadline extension while speaking ──
                if _time.time() > self._polling_deadline:
                    if self.user_currently_speaking or self._gentle_warning_in_progress:
                        self._polling_deadline = _time.time() + 5
                        continue
                    if self.captured_response is None and self.latest_user_response is None:
                        logger.warning(f"Polling deadline reached ({polling_timeout}s effective)")
                        break

                # ── Watchdog: STT health ──
                await self._check_stt_health(participant)

                # ── Watchdog: Silence ──
                await self._check_silence_watchdog(participant)

                # ── Watchdog: Idle no-VAD ──
                await self._check_idle_no_vad(participant, tts_fully_spoken)

                # ── Response ready? ──
                if self._response_ready.is_set() and self.captured_response is not None:
                    # User speaking guard: wait for silence (unless turn time exceeded)
                    if self.user_currently_speaking and not self.turn_time_exceeded:
                        continue

                    # Stabilization delay: if first fragment arrived <2s ago, wait
                    if (self._first_fragment_time is not None
                        and not self.turn_time_exceeded
                        and (datetime.now() - self._first_fragment_time).total_seconds() < 2.0):
                        continue

                    # Pause cooldown: wait after user stops speaking
                    if (not self.user_currently_speaking
                        and not self.turn_time_exceeded
                        and self._user_stopped_speaking_at is not None):
                        pause_cooldown = 2.5 if (self.current_question_object and self.current_question_object.is_qualitative()) else 1.0
                        since_stopped = (datetime.now() - self._user_stopped_speaking_at).total_seconds()
                        if since_stopped < pause_cooldown:
                            continue

                    # Delivery-state double-check
                    if not self._is_delivery_confirmed(self.current_question_num, participant):
                        _ds_key = self._delivery_key(self.current_question_num, participant)
                        _ds = self.question_delivery_state.get(_ds_key, "unknown")
                        logger.warning(
                            f"DELIVERY GUARD (_await_response): Discarding response — "
                            f"delivery_state={_ds} for Q#{self.current_question_num}/{participant}"
                        )
                        self.captured_response = None
                        self._response_ready.clear()
                        continue

                    self._silence_confirmed_time = datetime.now()
                    return self.captured_response

                # ── Legacy fallback: check latest_user_response from fragment handler ──
                if (self.latest_user_response is not None
                    and not self._response_ready.is_set()
                    and not self.user_currently_speaking
                    and not self.turn_time_exceeded):
                    # Fragment handler captured something but committed event hasn't fired yet
                    # Check stabilization and pause cooldown same as above
                    if (self._first_fragment_time is not None
                        and (datetime.now() - self._first_fragment_time).total_seconds() < 2.0):
                        continue
                    if (self._user_stopped_speaking_at is not None):
                        pause_cooldown = 2.5 if (self.current_question_object and self.current_question_object.is_qualitative()) else 1.0
                        since_stopped = (datetime.now() - self._user_stopped_speaking_at).total_seconds()
                        if since_stopped < pause_cooldown:
                            continue
                    # Promote fragment to captured_response
                    if self._is_delivery_confirmed(self.current_question_num, participant):
                        self.captured_response = self.latest_user_response
                        self._silence_confirmed_time = datetime.now()
                        return self.captured_response

        finally:
            # Cancel observer STT task
            if observer_stt_task and not observer_stt_task.done():
                observer_stt_task.cancel()
                try:
                    await observer_stt_task
                except asyncio.CancelledError:
                    pass

        # Last-resort rescue: if we have STT fragments, use them
        if self.captured_response:
            return self.captured_response
        if self.latest_user_response and self.latest_user_response.strip():
            logger.info(f"Rescuing STT fragments as response: '{self.latest_user_response[:100]}...'")
            return self.latest_user_response.strip()

        return None

    async def _check_stt_health(self, participant: str) -> None:
        """STT health check: nudge if VAD detected speech but no STT arrived."""
        if (self.captured_response is None
            and self.latest_user_response is None
            and not self.response_captured
            and not self._stt_nudge_given
            and not self._had_stt_transcript_this_turn
            and self._first_vad_speaking_time is not None
            and not self.user_currently_speaking):
            since_first_vad = (datetime.now() - self._first_vad_speaking_time).total_seconds()
            if since_first_vad > 6:
                self._stt_nudge_given = True
                display_name = self.participant_manager.get_display_name(participant)
                nudge_text = STT_NUDGE_TEMPLATE.format(name=display_name)
                logger.warning(
                    f"STT HEALTH CHECK: VAD detected speech {since_first_vad:.0f}s ago "
                    f"but no STT transcripts received for {participant}! Nudging."
                )
                await self._safe_say(nudge_text, allow_interruptions=False, context="stt_nudge")
                self.survey_transcript.add_acknowledgment(nudge_text)

    async def _check_silence_watchdog(self, participant: str) -> None:
        """Silence watchdog: prompt if no transcript progress after encouragement."""
        import time as _time
        if (not self._silence_watchdog_fired
            and not self.user_currently_speaking
            and not self._gentle_warning_in_progress
            and self.encouragement_given
            and self.captured_response is None
            and self.latest_user_response is None
            and self._last_transcript_progress_time is not None):
            silence_elapsed = _time.time() - self._last_transcript_progress_time
            if silence_elapsed > SILENCE_WATCHDOG_TIMEOUT:
                self._silence_watchdog_fired = True
                display_name = self.participant_manager.get_display_name(participant)
                watchdog_text = SILENCE_WATCHDOG_PROMPT_TEMPLATE.format(name=display_name)
                logger.warning(
                    f"SILENCE WATCHDOG: No transcript progression for "
                    f"{silence_elapsed:.0f}s after encouragement — prompting {participant}"
                )
                await self._safe_say(watchdog_text, allow_interruptions=True, context="silence_watchdog")
                self.survey_transcript.add_acknowledgment(watchdog_text)
                if self._polling_deadline is not None:
                    new_deadline = _time.time() + 8.0
                    if new_deadline > self._polling_deadline:
                        self._polling_deadline = new_deadline

    async def _check_idle_no_vad(self, participant: str, tts_fully_spoken: bool) -> None:
        """Idle-no-VAD watchdog: re-prompt if no VAD/STT activity at all."""
        import time as _time
        if (not self._idle_no_vad_nudge_fired
            and not self.user_currently_speaking
            and not self._gentle_warning_in_progress
            and self._first_vad_speaking_time is None
            and not self._had_stt_transcript_this_turn
            and self.captured_response is None
            and self.latest_user_response is None
            and self._polling_start_time is not None):
            _idle_elapsed = _time.time() - self._polling_start_time
            _remaining_tts = getattr(self, '_estimated_remaining_tts', 0.0)
            _tts_offset = _remaining_tts + TTS_SAFETY_MARGIN if _remaining_tts > 0 else 0.0
            if _idle_elapsed - _tts_offset > IDLE_NO_VAD_TIMEOUT:
                self._idle_no_vad_nudge_fired = True
                logger.warning(
                    f"IDLE-NO-VAD WATCHDOG: {_idle_elapsed:.0f}s since polling start "
                    f"with no VAD/STT activity"
                )

                if self.response_timeout_task:
                    self.response_timeout_task.cancel()
                    self.response_timeout_task = None

                if not tts_fully_spoken:
                    await self._safe_say(
                        PARTIAL_DELIVERY_NUDGE_TEXT,
                        allow_interruptions=False,
                        context="idle_no_vad_partial_reprompt",
                    )
                    question_text = self.current_question_object.question if self.current_question_object else self.current_question
                    if question_text and not self._shutting_down and self.agent_session:
                        try:
                            _rq_tts_start = _time.time()
                            rq_handle = self.agent_session.say(question_text, allow_interruptions=True)
                            await rq_handle
                            try:
                                await asyncio.wait_for(rq_handle.wait_for_playout(), timeout=120.0)
                            except asyncio.TimeoutError:
                                logger.warning("Idle-no-VAD repeat playout timed out")
                            _rq_elapsed = _time.time() - _rq_tts_start
                            _rq_est = _estimate_tts_duration(question_text)
                            self._estimated_remaining_tts = max(_rq_est - _rq_elapsed, 0.0)
                        except (asyncio.CancelledError, RuntimeError) as e:
                            logger.warning(f"Idle-no-VAD repeat TTS failed: {e}")
                else:
                    await self._safe_say(
                        TIMEOUT_NUDGE_TEXT,
                        allow_interruptions=False,
                        context="idle_no_vad_nudge",
                    )

                self.last_speech_time = None
                self.response_timeout_task = asyncio.create_task(
                    self.monitor_response_timeout(participant)
                )
                logger.info("Restarted timeout monitor after idle-no-VAD re-prompt")

                if self._polling_deadline is not None:
                    new_deadline = _time.time() + 15.0
                    if new_deadline > self._polling_deadline:
                        self._polling_deadline = new_deadline

    async def _observer_stt_polling(self, participant: str) -> None:
        """Standalone task: periodically switch STT to observer for commands."""
        try:
            while True:
                await asyncio.sleep(2.0)
                if self._shutting_down or self.survey_state == SurveyState.PAUSED:
                    return
                observer_identity = self.participant_manager.get_observer_identity()
                if not observer_identity:
                    continue
                self._set_stt_participant(observer_identity, context="observer_polling_check")
                await asyncio.sleep(0.3)
                if self.survey_state != SurveyState.PAUSED:
                    self._set_stt_participant(participant, context="observer_polling_restore")
        except asyncio.CancelledError:
            pass

    async def _process_captured_response(
        self,
        participant: str,
        captured_text: str,
        question_context: str,
    ) -> str:
        """Consolidated response processing. Returns 'accepted', 'retry', or 'move_on'."""
        import time as _time

        # LATENCY TRACKING
        response_processing_start = datetime.now()
        self._response_processing_start = response_processing_start
        logger.critical(f"[{question_context}] LATENCY TRACKING: Response processing started")

        speaker_name = (self.actual_respondent if self.actual_respondent else participant).capitalize()

        # ── CHECK 1: Uncertain response ──
        if is_uncertain_response(captured_text):
            _uncertain_result = await self._handle_uncertain_response(
                captured_text, participant, question_context, loop_label="unified")
            if _uncertain_result == "encouraged":
                return "retry"
            elif _uncertain_result == "move_on":
                return "move_on"

        # ── CHECK 2: Deterministic repeat pre-check ──
        if is_repeat_request(captured_text) and not self.question_repeated:
            self.question_repeated = True
            logger.info(f"[{question_context}] HEURISTIC repeat detected — repeating question")

            if self.response_timeout_task:
                self.response_timeout_task.cancel()
                self.response_timeout_task = None

            import time as _time
            repeat_intro = REPEAT_INTRO_TEMPLATE.format(name=speaker_name)
            await self.agent_session.say(repeat_intro, allow_interruptions=False)
            _repeat_tts_start = _time.time()
            q_handle = self.agent_session.say(self.current_question, allow_interruptions=True)
            await q_handle
            try:
                await asyncio.wait_for(q_handle.wait_for_playout(), timeout=120.0)
            except asyncio.TimeoutError:
                logger.warning("Heuristic repeat playout timed out")
            _elapsed = _time.time() - _repeat_tts_start
            _est = _estimate_tts_duration(self.current_question)
            self._estimated_remaining_tts = max(_est - _elapsed, 0.0)
            logger.info(f"Repeated question (heuristic): '{self.current_question[:100]}...'")

            self._reset_for_repeat(participant, context="heuristic_repeat")
            return "retry"

        # ── DISFLUENCY GUARD: one-budget extension anchored to _polling_deadline ──
        _disfluency_budget_used = False
        while (_is_disfluent_starter(captured_text)
               and not self.encouragement_given
               and not self.relevance_prompt_given
               and self._short_offtopic_count == 0):
            if not _disfluency_budget_used and self._polling_deadline is not None:
                self._polling_deadline += DISFLUENCY_EXTENSION_BUDGET
                _disfluency_budget_used = True
                logger.info(
                    f"[{question_context}] Disfluent starter — extended polling deadline "
                    f"by {DISFLUENCY_EXTENSION_BUDGET}s: '{captured_text[:60]}'"
                )
            # Local deadline: min of budget window and polling deadline
            import time as _time
            _budget_deadline = _time.time() + DISFLUENCY_EXTENSION_BUDGET
            _disfluency_deadline = min(_budget_deadline, self._polling_deadline) if self._polling_deadline else _budget_deadline

            self.captured_response = None
            self._response_ready.clear()

            _got_new_text = False
            while _time.time() < _disfluency_deadline:
                if self._shutting_down:
                    return "move_on"
                await asyncio.sleep(0.3)
                # Primary path: committed response arrived
                if self._response_ready.is_set() and self.captured_response:
                    if not self.user_currently_speaking:
                        captured_text = self.captured_response
                        _got_new_text = True
                        break
                # Legacy fallback: fragment accumulation
                if (self.latest_user_response
                    and not self._response_ready.is_set()
                    and not self.user_currently_speaking
                    and self._user_stopped_speaking_at is not None):
                    _pc = 2.5 if (self.current_question_object and
                                  self.current_question_object.is_qualitative()) else 1.0
                    _since = (datetime.now() - self._user_stopped_speaking_at).total_seconds()
                    if _since >= _pc:
                        new_text = self.latest_user_response.strip()
                        if new_text and new_text != captured_text:
                            captured_text = new_text
                            _got_new_text = True
                            break
            if not _got_new_text:
                break  # Deadline hit or no new speech → fall through to LLM
            # Loop back: re-check if new captured_text is still disfluent

        # ── FIRST-UTTERANCE GREETING GUARD ──────────────────────────────────
        # If the very first captured utterance after question delivery is a
        # greeting / acknowledgment / mic-check token (e.g. "Sure", "Thanks",
        # "Good morning"), treat it as non-substantive turn-start chatter:
        # skip off-topic analysis, do NOT increment _short_offtopic_count,
        # and continue polling with a one-time deadline extension.
        if (not self._first_utterance_greeting_guard_used
                and not self.encouragement_given
                and not self.relevance_prompt_given
                and self._short_offtopic_count == 0
                and _is_first_utterance_greeting(captured_text)):
            self._first_utterance_greeting_guard_used = True
            logger.info(
                f"[{question_context}] First-utterance greeting guard — treating as "
                f"non-substantive turn-start chatter: '{captured_text[:60]}'"
            )
            if self._polling_deadline is not None:
                import time as _time
                new_deadline = _time.time() + DISFLUENCY_EXTENSION_BUDGET
                if new_deadline > self._polling_deadline:
                    self._polling_deadline = new_deadline
                    logger.info(
                        f"[{question_context}] Extended polling deadline by "
                        f"{DISFLUENCY_EXTENSION_BUDGET}s after greeting guard"
                    )
            self.captured_response = None
            self._response_ready.clear()
            self.latest_user_response = None
            self.response_captured = False
            self.last_stt_fragment = ""
            self._turn_accumulated_text = ""
            self.pending_stt_transcript = None
            self.response_fragments = []
            self.last_fragment_time = None
            self.actual_respondent = None
            self.waiting_for_response = True
            self.last_speech_time = None
            self._had_stt_transcript_this_turn = False
            self._idle_no_vad_nudge_fired = False
            self._stt_nudge_given = False
            self._first_fragment_time = None
            self._user_stopped_speaking_at = None
            self._silence_watchdog_fired = False
            if not self.user_currently_speaking:
                self._first_vad_speaking_time = None
            return "retry"

        response_to_analyze = captured_text

        # ── CHECK 3: Unified LLM analysis ──
        question_for_analysis = self.current_question_object.question if self.current_question_object else self.current_question

        if self.partial_repeat_handled and self.accumulated_partial_answer:
            response_to_analyze = f"{self.accumulated_partial_answer} {captured_text}"
            logger.info(f"[{question_context}] Combined partial + new response for analysis")

        survey_desc = self.question_loader.survey_meta.description if self.question_loader and self.question_loader.survey_meta else ""

        if self.response_timeout_task:
            self.response_timeout_task.cancel()
            self.response_timeout_task = None

        if self._response_processing_start:
            _latency = (datetime.now() - self._response_processing_start).total_seconds()
            logger.info(f"METRIC: response_end_to_next_tts_ms={_latency * 1000:.0f}")

        analysis, analysis_secs = await self._analyze_with_filler(question_for_analysis, response_to_analyze, survey_desc)
        logger.info(
            f"[{question_context}] Analysis: relevant={analysis.is_relevant}, "
            f"repeat={analysis.is_repeat_request}, already_answered={analysis.is_already_answered_claim}, "
            f"partial_status={analysis.partial_repeat_status}"
        )

        # --- CHECK 3a: PARTIAL ANSWER + REPEAT REQUEST ---
        if analysis.partial_repeat_status == "PARTIAL" and not self.partial_repeat_handled:
            self.partial_repeat_handled = True
            self.accumulated_partial_answer = analysis.partial_answer

            partial_intro = PARTIAL_REPEAT_INTRO_TEMPLATE.format(name=speaker_name)
            await self._safe_say(partial_intro, allow_interruptions=False, context="partial_repeat_intro")

            import time as _time
            _partial_tts_start = _time.time()
            _partial_handle = self.agent_session.say(analysis.unanswered_questions, allow_interruptions=True)
            self._estimated_remaining_tts = 0.0  # Clear stale immediately
            await _partial_handle
            try:
                await asyncio.wait_for(_partial_handle.wait_for_playout(), timeout=120.0)
            except asyncio.TimeoutError:
                logger.warning("Partial repeat playout timed out")
            _elapsed = _time.time() - _partial_tts_start
            _est = _estimate_tts_duration(analysis.unanswered_questions)
            self._estimated_remaining_tts = max(_est - _elapsed, 0.0)

            # Reset for new response (keep accumulated_partial_answer)
            self.captured_response = None
            self._response_ready.clear()
            self.latest_user_response = None
            self.response_captured = False
            self.last_stt_fragment = ""
            self.encouragement_given = False
            self.waiting_for_response = True
            self.last_speech_time = None
            if self.response_timeout_task:
                self.response_timeout_task.cancel()
            self.response_timeout_task = asyncio.create_task(
                self.monitor_response_timeout(participant)
            )
            if self.turn_monitor_task:
                self.turn_monitor_task.cancel()
                self.turn_monitor_task = None
            self.user_currently_speaking = False
            self.turn_time_exceeded = False
            self.current_turn = TurnInfo(participant_identity=participant, start_time=datetime.now())
            self.turn_monitor_task = asyncio.create_task(
                self.monitor_turn_duration(self.agent_session)
            )
            return "retry"

        # --- CHECK 3b: "ALREADY ANSWERED" CLAIM ---
        if analysis.is_already_answered_claim and not self.already_answered_prompt_given:
            self.already_answered_prompt_given = True
            responder = self.actual_respondent if self.actual_respondent else participant
            self.survey_transcript.add_response(
                question_number=self.current_question_num, participant=responder,
                response_text=f"[Initial response before rephrase request] {captured_text}")

            rephrase_prompt = (
                f"I appreciate that, {speaker_name}, but I may not have captured your response correctly. "
                f"Could you please rephrase or elaborate on your answer? "
                f"This helps ensure we have your thoughts recorded accurately."
            )
            await self._safe_say(rephrase_prompt, allow_interruptions=False, context="rephrase_prompt")
            self.survey_transcript.add_acknowledgment(rephrase_prompt)

            self.captured_response = None
            self._response_ready.clear()
            self.latest_user_response = None
            self.response_captured = False
            self.last_stt_fragment = ""
            self.waiting_for_response = True
            self.last_speech_time = None
            if self.response_timeout_task:
                self.response_timeout_task.cancel()
            self.response_timeout_task = asyncio.create_task(
                self.monitor_response_timeout(participant)
            )
            if self.turn_monitor_task:
                self.turn_monitor_task.cancel()
                self.turn_monitor_task = None
            self.user_currently_speaking = False
            self.turn_time_exceeded = False
            self.current_turn = TurnInfo(participant_identity=participant, start_time=datetime.now())
            self.turn_monitor_task = asyncio.create_task(
                self.monitor_turn_duration(self.agent_session)
            )
            return "retry"

        # --- CHECK 3c: FULL REPEAT REQUEST ---
        is_full_repeat = analysis.is_repeat_request or (analysis.partial_repeat_status == "REPEAT_ONLY")
        if is_full_repeat:
            import time as _time
            self.question_repeated = True
            repeat_intro = REPEAT_INTRO_TEMPLATE.format(name=speaker_name)
            await self.agent_session.say(repeat_intro, allow_interruptions=False)
            _repeat_tts_start = _time.time()
            q_handle = self.agent_session.say(self.current_question, allow_interruptions=True)
            await q_handle
            try:
                await asyncio.wait_for(q_handle.wait_for_playout(), timeout=120.0)
            except asyncio.TimeoutError:
                logger.warning("LLM repeat playout timed out")
            _elapsed = _time.time() - _repeat_tts_start
            _est = _estimate_tts_duration(self.current_question)
            self._estimated_remaining_tts = max(_est - _elapsed, 0.0)
            self._reset_for_repeat(participant, context="llm_repeat")
            return "retry"

        # --- CHECK 3d: OFF-TOPIC/IRRELEVANT ---
        # After any prior redirect (short nudge or full relevance prompt), accept
        # the response to prevent looping.  Maximum = 1 redirect + 1 acceptance.
        _already_redirected = self.relevance_prompt_given or self._short_offtopic_count > 0
        if not analysis.is_relevant and _already_redirected:
            logger.info(
                f"[{question_context}] Off-topic detected but already redirected "
                f"(relevance_prompt={self.relevance_prompt_given}, "
                f"short_count={self._short_offtopic_count}) — accepting response"
            )
            # Fall through to acceptance below

        if not analysis.is_relevant and _is_too_short_for_offtopic(captured_text) and not _already_redirected:
            _sw = _substantive_word_count(captured_text)
            _norm = _normalize_for_offtopic_compare(captured_text)
            _has_blatant_keyword = _has_blatant_offtopic_keywords(captured_text)
            _is_repeat_short = (_norm == self._last_short_offtopic_norm) if self._last_short_offtopic_norm else False
            self._short_offtopic_count += 1
            self._last_short_offtopic_norm = _norm

            _should_escalate = (
                self.encouragement_given or _is_repeat_short or self._short_offtopic_count >= 2
            )

            if not _should_escalate:
                await self._nudge_for_short_offtopic_retry(
                    participant, speaker_name, captured_text,
                    question_context=question_context, loop_label="unified",
                )
                return "retry"

        if not analysis.is_relevant and not self.relevance_prompt_given and not _already_redirected:
            self.relevance_prompt_given = True
            responder = self.actual_respondent if self.actual_respondent else participant
            self.survey_transcript.add_response(
                question_number=self.current_question_num, participant=responder,
                response_text=f"[Initial off-topic response] {captured_text}")

            relevance_prompt = RELEVANCE_PROMPT_TEMPLATE.format(name=speaker_name)
            await self._safe_say(relevance_prompt, allow_interruptions=False, context="relevance_prompt")
            self.survey_transcript.add_acknowledgment(relevance_prompt)

            question_text = self.current_question_object.question if self.current_question_object else ""
            if question_text:
                import time as _time
                _reask_tts_start = _time.time()
                _reask_handle = self.agent_session.say(question_text, allow_interruptions=False)
                self._estimated_remaining_tts = 0.0  # Clear stale immediately
                await _reask_handle
                try:
                    await asyncio.wait_for(_reask_handle.wait_for_playout(), timeout=120.0)
                except asyncio.TimeoutError:
                    logger.warning("Off-topic re-ask playout timed out")
                _elapsed = _time.time() - _reask_tts_start
                _est = _estimate_tts_duration(question_text)
                self._estimated_remaining_tts = max(_est - _elapsed, 0.0)

            self._reset_for_off_topic(participant, context="offtopic_unified")
            return "retry"

        # ── Response accepted — fire acknowledgment ──
        await self._acknowledge_response(participant, speaker_name)

        return "accepted"

    async def _acknowledge_response(self, participant: str, speaker_name: str) -> None:
        """Fire a brief acknowledgment after a valid response."""
        _ack_speaker = speaker_name
        self._prewarmed_ack_text = f"Thank you, {_ack_speaker}."
        try:
            _ack_handle = self.agent_session.say(self._prewarmed_ack_text, allow_interruptions=False)
            self._estimated_remaining_tts = 0.0  # Clear stale immediately
            self.survey_transcript.add_acknowledgment(self._prewarmed_ack_text)
            self._ack_already_spoken = True
            self._transition_filler_said = True
            logger.info(f"Ack fired (post-analysis): '{self._prewarmed_ack_text}'")
            await _ack_handle
        except Exception as e:
            logger.warning(f"generate_reply/ack failed, using static fallback: {e}")
            fallback = f"Thank you, {speaker_name}."
            await self._safe_say(fallback, allow_interruptions=False, context="ack_fallback")
            self.survey_transcript.add_acknowledgment(fallback)
            self._ack_already_spoken = True
            self._transition_filler_said = True

    def _record_response_to_exports(
        self,
        participant: str,
        captured_text: str,
        question_id: str,
    ) -> None:
        """Record accepted response to all export targets (transcript, CSV, STT debug)."""
        # Combine partial answers if any
        if self.accumulated_partial_answer:
            captured_text = f"{self.accumulated_partial_answer} {captured_text}"
            self.accumulated_partial_answer = ""

        actual_speaker = self.actual_respondent if self.actual_respondent else participant

        if self.actual_respondent and self.actual_respondent != participant:
            logger.warning(f"MISMATCH: Expected {participant} to respond, but {self.actual_respondent} spoke!")
            logger.info(f"ACCEPTING response from {self.actual_respondent}")

        # Apply STT correction
        corrected_response = captured_text
        if self.current_question_object and self.current_question_object.response_options:
            max_sel = self.current_question_object.max_selections or 1
            if max_sel > 1:
                corrected_response = parse_multi_option_response(
                    captured_text, self.current_question_object.response_options, max_sel)
            else:
                corrected_response = correct_transcription(
                    captured_text, self.current_question_object.response_options)
            if corrected_response != captured_text:
                logger.info(f"Response corrected: '{captured_text[:50]}' -> '{corrected_response[:50]}'")

        question_text = self.current_question_object.question if self.current_question_object else ""
        response_options = self.current_question_object.response_options if self.current_question_object else []

        # STT debug log
        self.stt_debug_logger.log_question_response(
            question_num=self.current_question_num,
            question_id=question_id,
            question_text=question_text,
            participant=actual_speaker,
            raw_transcript=captured_text,
            corrected_response=corrected_response,
            response_options=response_options,
            expected_respondent=participant,
        )

        # Survey transcript (JSON)
        self.survey_transcript.add_response(
            question_number=self.current_question_num,
            participant=actual_speaker,
            response_text=corrected_response,
        )

        # CSV export
        self.survey_data_export.add_response(
            participant=actual_speaker,
            question_number=self.current_question_num,
            question_id=question_id,
            question_text=question_text,
            response_options=response_options,
            response_text=corrected_response,
        )

        # Mark participant as answered
        self.participant_manager.mark_participant_answered(actual_speaker, self.current_question_num)
        self._set_delivery_state(self.current_question_num, actual_speaker, "answered", context="response_received")
        self._record_turn_result(expected=participant, actual=actual_speaker)

        # Clean up
        self.waiting_for_response = False
        if self.response_timeout_task:
            self.response_timeout_task.cancel()
            self.response_timeout_task = None
        self.captured_response = None
        self._response_ready.clear()
        self.latest_user_response = None

    def _record_timeout_to_exports(self, participant: str, question_id: str) -> None:
        """Record a timeout/no-response to all export targets."""
        question_text = self.current_question_object.question if self.current_question_object else ""
        response_options = self.current_question_object.response_options if self.current_question_object else []
        timeout_marker = "[NO RESPONSE - TIMEOUT]"

        self.stt_debug_logger.log_question_response(
            question_num=self.current_question_num,
            question_id=question_id,
            question_text=question_text,
            participant=participant,
            raw_transcript=timeout_marker,
            corrected_response=timeout_marker,
            response_options=response_options,
            expected_respondent=participant,
        )

        self.survey_transcript.add_response(
            question_number=self.current_question_num,
            participant=participant,
            response_text=timeout_marker,
        )

        self.survey_data_export.add_response(
            participant=participant,
            question_number=self.current_question_num,
            question_id=question_id,
            question_text=question_text,
            response_options=response_options,
            response_text=timeout_marker,
        )

        if self._is_delivery_confirmed(self.current_question_num, participant):
            self.participant_manager.mark_participant_answered(participant, self.current_question_num)
            self._set_delivery_state(self.current_question_num, participant, "timeout", context="timeout_after_delivery")
        else:
            logger.warning(f"Skipping answered-mark on timeout for {participant}: delivery not confirmed; requeueing.")
            self._register_missing_participant_for_retry(participant, context="timeout_without_delivery")

        self._record_turn_result(expected=participant, actual=participant, was_timeout=True)

    # ── End Phase 1 methods ────────────────────────────────────────────────

    @property
    def _tts_active(self) -> bool:
        """True if the agent is currently playing TTS audio (SDK-native check)."""
        if not self.agent_session:
            return False
        speech = self.agent_session.current_speech
        return speech is not None and not speech.done()

    async def _speak_question_safely(
        self,
        text: str,
        *,
        retry_text: Optional[str] = None,
        context: str = "",
        max_retries: int = 2,
        allow_interruptions_first: bool = True,
        dedupe_key: Optional[str] = None,
    ) -> bool:
        """Speak *text* via SDK say(). SDK serializes and manages TTS internally.

        Args:
            text: Text to speak.
            retry_text: Accepted for signature compat (unused — no retry loop).
            context: Logging context string.
            max_retries: Accepted for signature compat (unused).
            allow_interruptions_first: Whether speech allows user interruption.
            dedupe_key: Per-turn key to prevent re-speaking the same prompt.

        Returns ``True`` if speech completed without interruption.
        """
        # Per-turn dedupe
        if dedupe_key:
            if dedupe_key in self._tts_dedupe_spoken:
                logger.info(f"TTS DEDUPE: '{dedupe_key}' already spoken this turn — skipping")
                return True
            self._tts_dedupe_spoken.add(dedupe_key)

        if self._shutting_down or not self.agent_session:
            return False

        try:
            import time as _time
            _tts_dispatch_time = _time.time()
            handle = self.agent_session.say(text, allow_interruptions=allow_interruptions_first)
            await handle
            try:
                await asyncio.wait_for(handle.wait_for_playout(), timeout=120.0)
            except asyncio.TimeoutError:
                logger.warning(f"Question playout timed out after 120s ({context})")
            # wait_for_playout() resolves when frames are queued (~0.5s), not when
            # the client finishes playback. Track estimated remaining duration so
            # the idle-no-VAD watchdog doesn't fire while audio is still playing.
            _elapsed = _time.time() - _tts_dispatch_time
            _estimated = _estimate_tts_duration(text)
            self._estimated_remaining_tts = max(_estimated - _elapsed, 0.0)
            if self._estimated_remaining_tts > 0:
                logger.info(
                    f"Estimated {self._estimated_remaining_tts:.1f}s remaining TTS playback "
                    f"({len(text)} chars, est={_estimated:.1f}s, elapsed={_elapsed:.1f}s)"
                )
            return not handle.interrupted
        except asyncio.CancelledError:
            return False
        except RuntimeError as e:
            if "closing" in str(e).lower() or "closed" in str(e).lower():
                self._shutting_down = True
            return False

    def _reset_for_repeat(self, participant: str, *, context: str = "") -> None:
        """Reset all mutable state after a repeat so the SAME participant gets
        a fresh turn.  Centralised here so every repeat path is consistent.

        Invariant enforced: ``expected_respondent`` MUST remain ``participant``
        after this call — repeating never changes who we're listening to.
        """
        saved_expected = self.expected_respondent

        # ── Response / STT state ───────────────────────────────────────
        self.captured_response = None
        self._response_ready.clear()
        self.latest_user_response = None
        self.response_captured = False
        self.last_stt_fragment = ""
        self.pending_stt_transcript = None
        self.response_fragments = []
        self.last_fragment_time = None
        self.actual_respondent = None
        self._turn_accumulated_text = ""

        # ── Flow control flags ─────────────────────────────────────────
        self.encouragement_given = False
        self._encouragement_followup_given = False
        self._last_transcript_progress_time = None
        self._silence_watchdog_fired = False
        self._short_offtopic_count = 0
        self._last_short_offtopic_norm = None
        self._had_stt_transcript_this_turn = False
        self._idle_no_vad_nudge_fired = False
        self.waiting_for_response = True
        self.last_speech_time = None
        self._transition_filler_said = False
        self._ack_already_spoken = False
        self._prewarmed_ack_text = None

        # ── STT health-check state ────────────────────────────────────
        self._first_vad_speaking_time = None
        self._stt_nudge_given = False

        # ── Timeout monitoring ─────────────────────────────────────────
        if self.response_timeout_task:
            self.response_timeout_task.cancel()
        self.response_timeout_task = asyncio.create_task(
            self.monitor_response_timeout(participant)
        )

        # ── Turn monitoring ────────────────────────────────────────────
        if self.turn_monitor_task:
            self.turn_monitor_task.cancel()
            self.turn_monitor_task = None
        # NOTE: Do NOT reset user_currently_speaking — let VAD events drive it.
        # If the user is already speaking when this reset runs, forcing False
        # causes the polling loop to capture a partial fragment immediately.
        self._first_fragment_time = None  # Reset stabilization timer
        self._user_stopped_speaking_at = None  # Reset pause cooldown
        self.turn_time_exceeded = False
        self.current_turn = TurnInfo(
            participant_identity=participant,
            start_time=datetime.now(),
        )
        self.turn_monitor_task = asyncio.create_task(
            self.monitor_turn_duration(self.agent_session)
        )

        # ── Spillover guard timestamp ──────────────────────────────────
        self.turn_transition_time = datetime.now()

        # ── Extend polling deadline so user gets full time after repeat ─
        import time as _time
        polling_timeout = (
            self.max_turn_duration
            + self.first_interrupt_grace
            + self.second_interrupt_grace
            + 10
        )
        self._polling_deadline = _time.time() + polling_timeout
        logger.info(f"⏱️  Polling deadline extended by {polling_timeout}s after repeat")

        # ── Invariant: expected_respondent MUST NOT change ─────────────
        assert self.expected_respondent == saved_expected, (
            f"REPEAT BUG: expected_respondent changed from "
            f"'{saved_expected}' to '{self.expected_respondent}' during reset"
        )

        logger.info(
            f"🔄 _reset_for_repeat({participant}, ctx={context}): "
            f"expected_respondent={self.expected_respondent}, "
            f"actual_respondent=None (cleared)"
        )

        # #region agent log
        import json as _json
        _debug_log_write(_json.dumps({"location": "moderator_agent.py:_reset_for_repeat", "message": "Repeat state reset", "data": {"participant": participant, "context": context, "expected_respondent": self.expected_respondent, "actual_respondent": self.actual_respondent, "question_num": self.current_question_num}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "H_IDENTITY"}))
        # #endregion

    async def _handle_uncertain_response(self, captured_text: str, participant: str, question_context: str, *, loop_label: str = "1st") -> str:
        """Unified uncertain-response handler for all polling loops.

        Returns: "encouraged" (continue polling), "move_on" (skip to next), or "accepted" (accept as-is).
        """
        import time as _time
        logger.critical(f"🔍 [{question_context}] UNCERTAIN RESPONSE ({loop_label}): '{captured_text}'")
        logger.critical(f"   encouragement_given={self.encouragement_given}, followup_given={self._encouragement_followup_given}")
        speaker_name = (self.actual_respondent if self.actual_respondent else participant).capitalize()

        # ── FIRST uncertain response: encourage ──────────────────────────
        if not self.encouragement_given:
            self.encouragement_given = True
            responder = self.actual_respondent if self.actual_respondent else participant
            self.survey_transcript.add_response(
                question_number=self.current_question_num, participant=responder,
                response_text=f"[Initial uncertain response before encouragement] {captured_text}")
            if self.current_question_object and self.current_question_object.is_qualitative():
                encouragement = (f"Are you sure, {speaker_name}? There's no right or wrong answer here. "
                                 f"Feel free to share whatever comes to mind, even if it's just a quick thought.")
            else:
                encouragement = (f"Are you sure, {speaker_name}? Take a moment to think about it. "
                                 f"Any answer you give is valuable.")
            await self._safe_say(encouragement, allow_interruptions=False, context="encouragement")
            self.survey_transcript.add_acknowledgment(encouragement)
            # Reset response state for retry
            self.captured_response = None
            self._response_ready.clear()
            self.latest_user_response = None
            self.response_captured = False
            self.last_stt_fragment = ""
            self.pending_stt_transcript = None
            if not self.user_currently_speaking:
                self._first_vad_speaking_time = None
            self._stt_nudge_given = False
            self._first_fragment_time = None
            self._user_stopped_speaking_at = None
            self._last_transcript_progress_time = _time.time()
            self._silence_watchdog_fired = False
            if self.turn_monitor_task:
                self.turn_monitor_task.cancel()
                self.turn_monitor_task = None
            self.turn_time_exceeded = False
            self.current_turn = TurnInfo(participant_identity=participant, start_time=datetime.now())
            self.turn_monitor_task = asyncio.create_task(self.monitor_turn_duration(self.agent_session))
            logger.info(f"🔄 Reset turn monitoring after encouragement ({loop_label})")
            # Bounded wait: extend polling deadline by 15s max
            if self._polling_deadline is not None:
                new_deadline = _time.time() + 15.0
                if new_deadline > self._polling_deadline:
                    self._polling_deadline = new_deadline
                    logger.info(f"⏱️  Extended polling deadline by 15s for post-encouragement wait")
            return "encouraged"

        # ── SECOND uncertain response: deterministic follow-up ────────────
        if not self._encouragement_followup_given:
            self._encouragement_followup_given = True
            logger.info(f"🤷 [{question_context}] Still uncertain after encouragement ({loop_label}), follow-up")
            followup = POST_ENCOURAGEMENT_FOLLOWUP_TEMPLATE.format(name=speaker_name)
            await self._safe_say(followup, allow_interruptions=False, context=f"uncertain_followup_{loop_label}")
            self.survey_transcript.add_acknowledgment(followup)
            responder = self.actual_respondent if self.actual_respondent else participant
            self.survey_transcript.add_response(
                question_number=self.current_question_num, participant=responder,
                response_text=f"[Uncertain response accepted after encouragement] {captured_text}")
            return "move_on"

        # ── Already followed up: just accept ─────────────────────────────
        logger.info(f"🤷 [{question_context}] Still uncertain after follow-up ({loop_label}), accepting")
        return "accepted"

    async def _nudge_for_short_offtopic_retry(
        self,
        participant: str,
        speaker_name: str,
        captured_text: str,
        *,
        question_context: str,
        loop_label: str,
    ) -> None:
        """Prompt for a more complete answer instead of silently waiting forever."""
        import time as _time

        nudge_text = (
            f"Could you say a bit more about your answer, {speaker_name}? "
            f"I want to make sure it responds to the question."
        )
        logger.info(
            f"📢 [{question_context}] Short off-topic response needs clarification "
            f"({loop_label}): '{captured_text[:100]}'"
        )
        await self._safe_say(
            nudge_text,
            allow_interruptions=False,
            context=f"short_offtopic_nudge_{loop_label}",
        )
        self.survey_transcript.add_acknowledgment(nudge_text)

        # Fresh retry state, but preserve short off-topic counters so a repeat
        # or second non-sequitur can still escalate on the next attempt.
        self.captured_response = None
        self._response_ready.clear()
        self.latest_user_response = None
        self.response_captured = False
        self.last_stt_fragment = ""
        self._turn_accumulated_text = ""
        self.pending_stt_transcript = None
        self.response_fragments = []
        self.last_fragment_time = None
        self.actual_respondent = None

        self.waiting_for_response = True
        self.last_speech_time = None
        self._had_stt_transcript_this_turn = False
        self._idle_no_vad_nudge_fired = False
        self._stt_nudge_given = False
        self._first_fragment_time = None
        self._user_stopped_speaking_at = None
        self._last_transcript_progress_time = _time.time()
        self._silence_watchdog_fired = False
        if not self.user_currently_speaking:
            self._first_vad_speaking_time = None

        if self.response_timeout_task:
            self.response_timeout_task.cancel()
        self.response_timeout_task = asyncio.create_task(
            self.monitor_response_timeout(participant)
        )

        if self.turn_monitor_task:
            self.turn_monitor_task.cancel()
            self.turn_monitor_task = None
        self.turn_time_exceeded = False
        self.current_turn = TurnInfo(
            participant_identity=participant,
            start_time=datetime.now(),
        )
        self.turn_monitor_task = asyncio.create_task(
            self.monitor_turn_duration(self.agent_session)
        )

        if self._polling_deadline is not None:
            new_deadline = _time.time() + 15.0
            if new_deadline > self._polling_deadline:
                self._polling_deadline = new_deadline
                logger.info(
                    f"⏱️  Extended polling deadline by 15s after short off-topic nudge ({loop_label})"
                )

    def _reset_for_off_topic(self, participant: str, *, context: str = "") -> None:
        """Reset all mutable state after an off-topic redirect so the participant
        gets a fresh turn to answer the same question.

        Centralised here so the 1st loop, 2nd loop, and METHOD 2 fallback share
        identical reset logic.  Key invariants:
          - ``user_currently_speaking`` is NEVER forced False — VAD events are
            the single source of truth.
          - ``_polling_deadline`` is ALWAYS extended.
          - Timeout and turn monitor tasks are safely cancelled then restarted.
        """
        import time as _time

        logger.critical(f"OFF-TOPIC RESET START for {participant} (ctx={context})")

        # ── Response / STT state ───────────────────────────────────────
        self.captured_response = None
        self._response_ready.clear()
        self.latest_user_response = None
        self.response_captured = False
        self.last_stt_fragment = ""
        self.pending_stt_transcript = None
        self.response_fragments = []
        self.last_fragment_time = None
        self.actual_respondent = None

        # ── Flow control flags ─────────────────────────────────────────
        self.encouragement_given = False
        self.question_repeated = False
        self._short_offtopic_count = 0
        self._last_short_offtopic_norm = None
        self._had_stt_transcript_this_turn = False
        self._idle_no_vad_nudge_fired = False
        self.waiting_for_response = True
        self.last_speech_time = None
        self._transition_filler_said = False
        self._ack_already_spoken = False
        self._prewarmed_ack_text = None

        # ── STT health-check state ────────────────────────────────────
        self._first_vad_speaking_time = None
        self._stt_nudge_given = False

        # ── Timeout monitoring ─────────────────────────────────────────
        if self.response_timeout_task:
            self.response_timeout_task.cancel()
        self.response_timeout_task = asyncio.create_task(
            self.monitor_response_timeout(participant)
        )

        # ── Turn monitoring ────────────────────────────────────────────
        if self.turn_monitor_task:
            self.turn_monitor_task.cancel()
            self.turn_monitor_task = None
        # NOTE: Do NOT reset user_currently_speaking — let VAD events drive it.
        # If the user is already speaking when this reset runs, forcing False
        # causes the polling loop to capture a partial fragment immediately
        # (the user never triggers a new 'speaking' event).
        self.current_speaking_duration = 0.0
        self.accumulated_pause_duration = 0.0
        self._first_fragment_time = None
        self._user_stopped_speaking_at = None
        self.turn_time_exceeded = False
        self.current_turn = TurnInfo(
            participant_identity=participant,
            start_time=datetime.now(),
        )
        self.turn_monitor_task = asyncio.create_task(
            self.monitor_turn_duration(self.agent_session)
        )

        # ── Spillover guard timestamp ──────────────────────────────────
        self.turn_transition_time = datetime.now()

        # ── Extend polling deadline so user gets full time after redirect ─
        max_turn_time = (
            self.max_turn_duration
            + self.first_interrupt_grace
            + self.second_interrupt_grace
        )
        off_topic_extension = max_turn_time + 10
        self._polling_deadline = _time.time() + off_topic_extension
        logger.info(f"⏱️  Polling deadline extended by {off_topic_extension}s after off-topic reset")

        logger.critical(
            f"🔄 OFF-TOPIC RESET COMPLETE (ctx={context}) — "
            f"participant gets full {self.max_turn_duration}s again"
        )

    async def _get_room_participant_identities(self) -> Optional[Set[str]]:
        if not self.livekit_api or not self.room_name:
            return None
        try:
            participants_response = await self.livekit_api.room.list_participants(
                api.ListParticipantsRequest(room=self.room_name)
            )
            return {p.identity for p in (participants_response.participants or [])}
        except Exception as e:
            logger.warning(f"Could not fetch room roster for delivery validation: {e}")
            return None

    def _register_missing_participant_for_retry(self, participant_identity: str, context: str):
        question_num = self.current_question_num
        key = self._delivery_key(question_num, participant_identity)
        retries = self.question_delivery_retries.get(key, 0) + 1
        self.question_delivery_retries[key] = retries

        if retries >= self.max_delivery_retries_per_question:
            self._set_delivery_state(question_num, participant_identity, "unavailable", context=context)
            logger.warning(
                f"Participant {participant_identity} exhausted delivery retries for question {question_num}; "
                "marking unavailable."
            )
            # Sync with participant_manager's unavailable set (single source of truth)
            if participant_identity not in self.participant_manager.unavailable_participants:
                logger.warning(f"🔄 Syncing: marking {participant_identity} as unavailable in participant_manager after {retries} delivery retries")
                self.participant_manager.unavailable_participants.add(participant_identity)
            if self.participant_manager:
                self.participant_manager.remove_participant(participant_identity, immediate=True)
            return

        backoff_seconds = min(2 ** (retries - 1), 4)
        self.question_delivery_retry_after[key] = datetime.now() + timedelta(seconds=backoff_seconds)
        self._set_delivery_state(question_num, participant_identity, "pending_retry", context=context)
        logger.warning(
            f"Participant {participant_identity} missing for delivery (q={question_num}, retry={retries}/"
            f"{self.max_delivery_retries_per_question}); requeueing after {backoff_seconds}s."
        )

    async def _prepare_participant_for_delivery(self, participant_identity: str, context: str) -> bool:
        if not self.participant_manager:
            return False

        question_num = self.current_question_num
        key = self._delivery_key(question_num, participant_identity)

        if participant_identity in self.participant_manager.unavailable_participants:
            self._set_delivery_state(question_num, participant_identity, "unavailable", context=context)
            return False

        retry_after = self.question_delivery_retry_after.get(key)
        if retry_after and datetime.now() < retry_after:
            return False

        room_ids = await self._get_room_participant_identities()
        if room_ids is not None and participant_identity not in room_ids:
            self._register_missing_participant_for_retry(participant_identity, context=f"{context}:not_in_room")
            return False

        self._set_delivery_state(question_num, participant_identity, "queued", context=context)
        return True

    async def _select_next_deliverable_participant(self, question_num: int, context: str) -> Optional[str]:
        if not self.participant_manager:
            return None

        candidates = self.participant_manager.get_unanswered_participants(question_num)
        if not candidates:
            return None

        random.shuffle(candidates)
        for candidate in candidates:
            if await self._prepare_participant_for_delivery(candidate, context=context):
                return candidate

        return None

    async def _run_observer_triggered_survey(self):
        """
        Run the full survey flow when triggered by observer's 'start survey' command.
        This includes welcome, greeting, and questions.
        """
        logger.info("👁️ Running observer-triggered survey flow")

        # Get stored references
        pending = getattr(self, 'pending_survey_start', None)
        if not pending:
            logger.error("👁️ No pending survey start data found!")
            await self.agent_session.say(
                "An error occurred. Please restart the survey.",
                allow_interruptions=False
            )
            return

        session = pending.get('session')
        question_loader = pending.get('question_loader')
        config = pending.get('config')
        ctx = pending.get('ctx')

        # CRITICAL: Clear STT participant filter so we can hear ALL participants
        # We need to set it to a valid participant first, then the room will handle mixed audio
        # Note: set_participant(None) breaks STT - it stops listening entirely
        # Instead, we'll rely on NOT calling set_participant for specific participants in observer mode
        # This means STT will listen to whatever the default room audio is
        # Reset STT to first participant to "wake up" STT
        participants = list(self.participant_manager.participants)
        if participants:
            self._set_stt_participant(participants[0], context="observer_start_survey_reset")
        else:
            logger.warning("No participants available to reset STT")

        # 1. WELCOME MESSAGE — enter WELCOME phase so STT transcripts are discarded
        self.survey_state = SurveyState.WELCOME
        logger.info("👁️ Step 1: Delivering welcome message (survey_state → WELCOME)")
        if question_loader.use_unified_format and question_loader.welcome_section and question_loader.welcome_section.enabled:
            welcome = question_loader.welcome_section
            if welcome.audio_check and welcome.audio_check.enabled:
                audio_check_text = welcome.audio_check.message
                wait_seconds = welcome.audio_check.wait_seconds
            else:
                audio_check_text = ""
                wait_seconds = 0
            # Get agent name: use survey-specific name if available, otherwise config default
            agent_name = question_loader.survey_meta.agent_name if question_loader.survey_meta.agent_name else config.agent_name
            logger.info(f"Using agent name for welcome: {agent_name}")
            # Substitute {agent_name} placeholder in greeting and instructions
            greeting_text = welcome.greeting.replace("{agent_name}", agent_name)
            instructions_text = welcome.instructions.replace("{agent_name}", agent_name)
            full_welcome = f"{greeting_text} {instructions_text} {audio_check_text}"
        else:
            full_welcome = f"Hello! I'm {config.agent_name}, your AI survey moderator. Please make sure your microphone is unmuted and working. I'll wait 1 minute for everyone to get ready before we begin."
            wait_seconds = 10

        logger.info(f"🎤 WELCOME TEXT SENT TO TTS: '{full_welcome}'")
        await self.agent_session.say(full_welcome, allow_interruptions=False)
        logger.info("✅ Welcome TTS completed")

        # 2. WAIT FOR PARTICIPANTS
        logger.info(f"👁️ Step 2: Waiting {wait_seconds} seconds for participants")
        await asyncio.sleep(wait_seconds)

        # Add any new participants who joined during wait
        for participant in ctx.room.remote_participants.values():
            if self.participant_manager and participant.identity not in self.participant_manager.participants:
                if not self.participant_manager.is_observer(participant.identity):
                    logger.info(f"Adding participant who joined during wait: {participant.identity}")
                    self.participant_manager.add_participant(participant.identity, display_name=participant.name)
                    self.participant_audio_activity[participant.identity] = None

        # 3. INITIAL GREETING
        logger.info("👁️ Step 3: Delivering initial greeting")
        participant_count = len(self.participant_manager.participants)
        if participant_count == 1:
            greeting_text = "Great! Thank you. We have our participant ready. Let's begin with our first question."
        else:
            greeting_text = f"Great! Thank you. We have {participant_count} participants ready. Let's begin with our first question."
        await self.agent_session.say(greeting_text, allow_interruptions=False)

        # Record greeting
        self.survey_transcript.add_greeting(greeting_text)
        self.survey_data_export.set_greeting(greeting_text)

        await asyncio.sleep(2)

        # 4. START ASKING QUESTIONS — transition OUT of WELCOME phase
        self.survey_state = SurveyState.RUNNING
        logger.info(f"🟢 Survey state: WELCOME → RUNNING (welcome/greeting complete, questions starting)")

        # Clear any response fragments that may have accumulated during welcome
        self.response_fragments = []
        self.latest_user_response = None
        self.pending_stt_transcript = None
        self.last_stt_fragment = ""
        self.response_captured = False
        logger.info("🧹 Cleared response buffers before first question")

        logger.info("👁️ Step 4: Starting questions")
        await self.ask_next_question()

    async def handle_observer_command(self, command: str):
        """
        Handle control commands from the observer.

        Args:
            command: The command type ('start', 'pause', or 'resume')
        """
        logger.info(f"👁️ Processing observer command: {command}")

        if command == "start":
            if self.survey_state == SurveyState.WAITING_FOR_OBSERVER:
                self.survey_state = SurveyState.WELCOME  # Will transition to RUNNING after welcome/greeting
                logger.info("👁️ Survey STARTED by observer - entering WELCOME phase")

                # Run the full survey flow
                await self._run_observer_triggered_survey()
            else:
                logger.warning(f"👁️ Cannot start survey - current state: {self.survey_state}")
                await self.agent_session.say(
                    "The survey has already started.",
                    allow_interruptions=False
                )

        elif command == "pause":
            if self.survey_state == SurveyState.RUNNING:
                self.survey_state = SurveyState.PAUSED
                self.paused_at_question = self.current_question_num
                self.paused_at_participant = self.expected_respondent
                self.pause_start_time = datetime.now()  # Track when pause started
                logger.info(f"👁️ Survey PAUSED at question {self.current_question_num}")
                logger.info(f"👁️ Pause started at {self.pause_start_time.isoformat()}")

                # IMMEDIATELY interrupt agent speech if it's currently speaking
                try:
                    await self.agent_session.interrupt()
                    logger.info("👁️ Agent speech interrupted by pause command")
                except Exception as e:
                    logger.warning(f"Could not interrupt agent: {e}")

                # Cancel any active timeout monitoring
                if self.response_timeout_task:
                    self.response_timeout_task.cancel()
                    self.response_timeout_task = None
                if self.turn_monitor_task:
                    self.turn_monitor_task.cancel()
                    self.turn_monitor_task = None

                # Announce pause
                await self.agent_session.say(
                    "The survey has been paused by the observer. Please wait.",
                    allow_interruptions=False
                )

                # Set STT to listen to observer for 'resume' command
                # (set_participant(None) breaks STT - we must set to a valid participant)
                observer_identity = self.participant_manager.get_observer_identity()
                if observer_identity:
                    self._set_stt_participant(observer_identity, context="pause_command_stt_to_observer")
            else:
                logger.warning(f"👁️ Cannot pause - current state: {self.survey_state}")

        elif command == "resume":
            if self.survey_state == SurveyState.PAUSED:
                self.survey_state = SurveyState.RUNNING

                # Calculate pause duration for time adjustment
                pause_duration = 0.0
                if self.pause_start_time:
                    pause_duration = (datetime.now() - self.pause_start_time).total_seconds()
                    self.accumulated_pause_duration += pause_duration
                    logger.info(f"👁️ Pause duration: {pause_duration:.1f}s (total accumulated: {self.accumulated_pause_duration:.1f}s)")
                    self.pause_start_time = None  # Clear pause start time

                logger.info(f"👁️ Survey RESUMED from question {self.paused_at_question}")

                # CRITICAL: Re-ask the SAME question that was paused (not the next one!)
                # Decrement question_loader index and current_question_num so ask_next_question
                # will re-ask the paused question
                if self.question_loader and self.paused_at_question is not None:
                    if self.question_loader.current_question_index > 0:
                        self.question_loader.current_question_index -= 1
                        logger.info(f"👁️ Decremented question_loader index to {self.question_loader.current_question_index}")
                    if self.current_question_num > 0:
                        self.current_question_num -= 1
                        logger.info(f"👁️ Decremented current_question_num to {self.current_question_num}")

                # Announce resume
                await self.agent_session.say(
                    "The survey is resuming. I'll repeat the question.",
                    allow_interruptions=False
                )
                await asyncio.sleep(1)

                # Continue asking questions - this will RE-ASK the paused question
                await self.ask_next_question()
            else:
                logger.warning(f"👁️ Cannot resume - current state: {self.survey_state}")

    async def _safe_interrupt(self, session: AgentSession, *, context: str = "") -> bool:
        """Call session.interrupt(), treating ALL RuntimeErrors as non-fatal.

        On denial, waits for current speech to finish via SDK wait_for_playout().
        Returns True if interrupt succeeded, False if denied/failed.
        """
        try:
            session.interrupt()
            return True
        except RuntimeError as e:
            if self.current_turn:
                self.current_turn.interrupt_denied_count += 1
            logger.warning(f"session.interrupt() denied ({context}): {e}")
            speech = session.current_speech
            if speech and not speech.done():
                try:
                    await asyncio.wait_for(speech.wait_for_playout(), timeout=3.0)
                except asyncio.TimeoutError:
                    pass
            return False

    async def _safe_say(self, text: str, *, allow_interruptions: bool = True, context: str = "") -> bool:
        """Wrapper for session.say() that is safe during shutdown.

        Returns True if speech completed, False on failure/shutdown.
        SDK serializes say() calls internally — no manual lock needed.
        """
        if self._shutting_down:
            logger.info(f"_safe_say suppressed (shutting down): {context} — '{text[:60]}'")
            return False
        if not self.agent_session:
            logger.warning(f"_safe_say skipped (no session): {context}")
            return False
        try:
            handle = self.agent_session.say(text, allow_interruptions=allow_interruptions)
            self._estimated_remaining_tts = 0.0  # Stale question-TTS estimate is now invalid
            await handle
            return True
        except asyncio.CancelledError:
            return False
        except RuntimeError as e:
            if "closing" in str(e).lower() or "closed" in str(e).lower():
                self._shutting_down = True
            return False

    def _cancel_all_monitor_tasks(self):
        """Central cleanup: cancel response_timeout_task, turn_monitor_task, and avatar tasks."""
        if self.response_timeout_task:
            self.response_timeout_task.cancel()
            self.response_timeout_task = None
        if self.turn_monitor_task:
            self.turn_monitor_task.cancel()
            self.turn_monitor_task = None
        self.current_turn = None
        self._cancel_avatar_tasks()
        logger.info("🧹 All monitor tasks cancelled (central cleanup)")

    def _cancel_avatar_tasks(self):
        """Cancel all outstanding avatar-related async tasks (notice + reconnect)."""
        if self._avatar_notice_task and not self._avatar_notice_task.done():
            self._avatar_notice_task.cancel()
            self._avatar_notice_task = None
            logger.info("🧹 Avatar disconnect notice task cancelled")
        if self._avatar_reconnect_task and not self._avatar_reconnect_task.done():
            self._avatar_reconnect_task.cancel()
            self._avatar_reconnect_task = None
            logger.info("🧹 Avatar reconnect task cancelled")

    async def _cleanup_avatar(self):
        """Idempotent avatar teardown.  Safe to call multiple times.

        Cancels all avatar tasks, attempts to stop the avatar session if the
        Anam SDK supports it, and logs the final lifecycle status.
        """
        self._cancel_avatar_tasks()

        avatar_ref = self._avatar_session_ref
        if avatar_ref is not None:
            self._avatar_session_ref = None
            self._avatar_connected = False
            # The Anam SDK may expose .stop(), .close(), or .disconnect().
            # Try each in order; swallow errors to keep teardown non-fatal.
            for method_name in ("stop", "close", "disconnect"):
                method = getattr(avatar_ref, method_name, None)
                if callable(method):
                    try:
                        result = method()
                        # Handle both sync and async teardown methods
                        if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                            try:
                                await asyncio.wait_for(result, timeout=5.0)
                            except asyncio.TimeoutError:
                                logger.info(
                                    f"AVATAR_LIFECYCLE teardown via {method_name}() "
                                    f"timed out (5s, expected post-disconnect cleanup noise)"
                                )
                        logger.info(f"AVATAR_LIFECYCLE teardown via {method_name}() succeeded")
                    except Exception as exc:
                        _severity = logger.debug if not self._avatar_connected else logger.warning
                        _severity(
                            f"AVATAR_LIFECYCLE teardown via {method_name}() "
                            f"failed (non-fatal, connected={self._avatar_connected}): {exc}"
                        )
                    break  # Only call the first available method
            else:
                logger.info("AVATAR_LIFECYCLE avatar session released (no stop/close/disconnect method)")

        logger.info(
            f"AVATAR_LIFECYCLE cleanup_complete state={self._avatar_state} "
            f"reconnect_attempts={self._avatar_reconnect_attempts} "
            f"disconnect_reason={self._avatar_disconnect_reason} "
            f"audio_only={self._audio_only_mode}"
        )

    @staticmethod
    def _is_avatar_identity(identity: str) -> bool:
        """Return True if the identity belongs to the Anam avatar agent.

        Centralised check so all event handlers use the same logic.
        Matches the constant ``ANAM_AVATAR_IDENTITY`` ("anam-avatar-agent")
        and any identity containing "avatar-agent" (handles suffixed variants).
        """
        if not identity:
            return False
        return identity == ANAM_AVATAR_IDENTITY or "avatar-agent" in identity

    def _set_avatar_state(self, new_state: str, *, reason: Optional[str] = None):
        """Transition avatar state with structured logging."""
        old_state = self._avatar_state
        self._avatar_state = new_state
        if new_state == AVATAR_STATE_CONNECTED:
            self._avatar_connected = True
        elif new_state in (AVATAR_STATE_DISCONNECTED, AVATAR_STATE_FAILED):
            self._avatar_connected = False
        if reason:
            self._avatar_disconnect_reason = reason
        extra = f" reason={reason}" if reason else ""
        logger.info(
            f"AVATAR_LIFECYCLE state={new_state} prev={old_state}{extra} "
            f"reconnect_attempts={self._avatar_reconnect_attempts} "
            f"connected={self._avatar_connected}"
        )

    async def _survey_loop(self, initial_action: str):
        """Iterative trampoline replacing recursive ask_next_question ↔ move_to_next_participant.

        Each step method returns the next action string ("ask", "move") or None to stop.
        This eliminates unbounded recursion depth that previously caused stack growth
        proportional to (questions × participants).
        """
        action = initial_action
        _iteration = 0
        while action and not self._shutting_down:
            _iteration += 1
            logger.info(f"📋 SURVEY_LOOP: iteration={_iteration} action={action}")
            try:
                if action == "ask":
                    action = await self._ask_next_question_impl()
                elif action == "move":
                    action = await self._move_to_next_participant_impl()
                else:
                    logger.error(f"📋 SURVEY_LOOP: unknown action '{action}' — stopping")
                    break
            except RuntimeError as e:
                if "closing" in str(e).lower() or "closed" in str(e).lower():
                    self._shutting_down = True
                    logger.warning(f"🛑 SURVEY_LOOP caught session-closing RuntimeError: {e}")
                    self._cancel_all_monitor_tasks()
                    break
                raise  # Re-raise non-session RuntimeErrors
        if self._shutting_down:
            logger.info(f"🛑 SURVEY_LOOP exiting: _shutting_down=True (after {_iteration} iterations)")
            self._cancel_all_monitor_tasks()

    async def monitor_turn_duration(self, session: AgentSession):
        """
        Monitor the current turn duration with graceful time extension for surveys.

        Implements graceful approach for surveys:
        1. Silent grace period at max_turn_duration - allow first_interrupt_grace more to complete
        2. Gentle warning at max_turn_duration + first_interrupt_grace - give second_interrupt_grace to wrap up
        3. FORCE END at max_turn_duration + first_interrupt_grace + second_interrupt_grace - politely end turn

        Example with max=20s, first_grace=10s, second_grace=10s:
        - 0-20s: Normal speaking
        - 20-30s: Silent grace (first 10s)
        - 30s: Warning + 10s more to wrap up
        - 40s: Force end
        """
        if not self.current_turn or not self.enable_turn_limits:
            return

        turn_info = self.current_turn
        participant_id = turn_info.participant_identity

        # Survey-optimized thresholds (configurable via FIRST_INTERRUPT_GRACE and SECOND_INTERRUPT_GRACE)
        grace_period_1 = self.max_turn_duration + self.first_interrupt_grace  # When to give warning
        grace_period_2 = grace_period_1 + self.second_interrupt_grace  # When to force end

        import time as _time
        warning_start_time = None  # Timestamp when gentle warning TTS finished (for cooldown)

        logger.info(f"Started graceful turn monitoring for {participant_id}")
        logger.info(
            f"Survey thresholds: base={self.max_turn_duration}s, "
            f"warning_at={grace_period_1}s, "
            f"force_end={grace_period_2}s"
        )

        try:
            while self.current_turn == turn_info:  # Still the same turn
                # Shutdown guard
                if self._shutting_down:
                    logger.info(f"🛑 monitor_turn_duration exiting: _shutting_down=True ({participant_id})")
                    return

                # Check if survey was paused - stop monitoring
                if self.survey_state == SurveyState.PAUSED:
                    logger.info(f"👁️ Survey paused - stopping turn monitoring for {participant_id}")
                    return

                # CRITICAL: Use VAD-based actual speaking duration (not wall-clock)
                # Wall-clock includes STT latency, silence, LLM analysis, and agent speech
                # which inflates the timer and causes premature warnings/force-ends
                elapsed_speaking = turn_info.actual_speaking_duration
                if self.user_currently_speaking and self._vad_speaking_segment_start:
                    elapsed_speaking += (datetime.now() - self._vad_speaking_segment_start).total_seconds()
                elapsed_wall = (datetime.now() - turn_info.start_time).total_seconds()
                turn_info.duration = elapsed_wall  # keep wall-clock for reporting
                elapsed = elapsed_speaking  # use VAD time for ALL threshold decisions


                # DEBUG: Log every 5 seconds
                if int(elapsed) % 5 == 0 and elapsed > 0:
                    logger.critical(f"⏱️  MONITORING: {participant_id} speaking={elapsed:.1f}s wall={elapsed_wall:.1f}s (base={self.max_turn_duration}s, warn@{grace_period_1}s, end@{grace_period_2}s)")

                # Stage 1: Silent grace period (at 20s testing / 60s prod)
                # Just allow participant to continue for 10 more seconds
                if (not turn_info.warned and
                    elapsed >= self.max_turn_duration):

                    turn_info.warned = True
                    logger.info(
                        f"⏰ Base time reached: {participant_id} "
                        f"has spoken for {elapsed:.1f}s. Allowing 10s grace period to complete."
                    )

                    # Log moderation event
                    self.moderation_events.append({
                        "type": "grace_period_started",
                        "participant": participant_id,
                        "duration": elapsed,
                        "threshold": self.max_turn_duration,
                        "timestamp": datetime.now().isoformat(),
                    })

                # Stage 2: GENTLE WARNING (at 30s testing / 70s prod)
                # Politely inform participant and give 10 more seconds to wrap up
                # Uses pending_soft_warning flag to ensure warning is delivered even if user briefly pauses

                # Check if time threshold exceeded and we haven't warned yet
                if (not turn_info.first_interrupted and
                    not turn_info.pending_soft_warning and
                    elapsed >= grace_period_1):
                    # Mark that we need to give a warning (will deliver when user is speaking)
                    turn_info.pending_soft_warning = True
                    logger.info(f"⏰ Time threshold {grace_period_1}s exceeded, pending soft warning")

                # Deliver the pending warning when user is speaking
                if (turn_info.pending_soft_warning and
                    not turn_info.first_interrupted and
                    self.user_currently_speaking):  # ← Only deliver when speaking!

                    turn_info.first_interrupted = True
                    turn_info.interruption_count += 1

                    logger.critical("=" * 80)
                    logger.critical(
                        f"⏱️  GENTLE WARNING TRIGGERED: {participant_id} "
                        f"has spoken for {elapsed:.1f}s (limit: {grace_period_1}s). Giving wrap-up notice."
                    )
                    logger.critical("=" * 80)

                    # Log moderation event
                    self.moderation_events.append({
                        "type": "gentle_warning",
                        "participant": participant_id,
                        "duration": elapsed,
                        "threshold": grace_period_1,
                        "timestamp": datetime.now().isoformat(),
                    })

                    # Interrupt agent's own speech first (may fail if current
                    # speech was created with allow_interruptions=False).
                    await self._safe_interrupt(session, context="gentle_warning")

                    display_name = participant_id.capitalize()
                    warning_text = GENTLE_WARNING_TEMPLATE.format(name=display_name)
                    logger.critical(f"🔊 SENDING GENTLE WARNING VIA TTS: '{warning_text}'")

                    # CRITICAL FIX: Block the polling loop from capturing the response
                    # while the warning is being spoken. Without this, the user pauses
                    # to listen to the warning and the polling loop grabs their partial
                    # response as the final answer.
                    self._gentle_warning_in_progress = True
                    self._gentle_warning_started_at = _time.time()

                    try:
                        # Clear accumulated response NOW (before TTS starts) so neither
                        # the polling loop nor _capture_user_response_immediately() can
                        # grab stale pre-warning text while the warning plays.
                        self.latest_user_response = None
                        self.last_stt_fragment = ""
                        self._first_fragment_time = None
                        self.pending_stt_transcript = None
                        self.response_captured = False
                        self._first_vad_speaking_time = None
                        # Keep _stt_nudge_given = True to suppress echo-triggered nudges
                        self._stt_nudge_given = True

                        warning_start_time = datetime.now()
                        await session.say(warning_text, allow_interruptions=False)
                        warning_duration = (datetime.now() - warning_start_time).total_seconds()

                        # Reset start_time to NOW so user gets a TRUE
                        # second_interrupt_grace window from when warning finishes.
                        old_start = turn_info.start_time
                        old_elapsed = (datetime.now() - old_start).total_seconds()
                        turn_info.start_time = datetime.now()
                        new_elapsed = (datetime.now() - turn_info.start_time).total_seconds()

                        # Recalculate grace periods relative to the reset timer
                        grace_period_2 = self.second_interrupt_grace
                        grace_period_1 = max(self.second_interrupt_grace - 2.0, 3.0)

                        # Reset VAD accumulator so user gets a full wrap-up window of actual speaking time
                        turn_info.actual_speaking_duration = 0.0

                        # Clear again after TTS — any STT that arrived while the
                        # warning played is stale echo / cross-talk, not wrap-up speech.
                        self.latest_user_response = None
                        self.last_stt_fragment = ""
                        self._first_fragment_time = None
                        self.pending_stt_transcript = None
                        self.response_captured = False
                        self._first_vad_speaking_time = None
                        # NOTE: Do NOT reset _stt_nudge_given here.  The warning TTS
                        # can bleed into the mic as echo, causing VAD to detect
                        # "speech" without real STT transcripts.  If we reset the
                        # nudge flag, the STT health-check fires "I didn't hear you"
                        # immediately after the warning — confusing the participant.
                        self._stt_nudge_given = True

                        # CRITICAL FIX (Bug 2): Extend the polling deadline so the
                        # polling loop does not timeout while the user is wrapping up.
                        # Without this, the original deadline (set at question start)
                        # fires immediately after the warning, discarding wrap-up speech.
                        wrap_up_extension = self.second_interrupt_grace + 10  # wrap-up window + buffer
                        new_deadline = _time.time() + wrap_up_extension
                        if self._polling_deadline is None or new_deadline > self._polling_deadline:
                            self._polling_deadline = new_deadline
                        logger.info(f"⏱️  Polling deadline extended by {wrap_up_extension}s after gentle warning")

                        logger.critical(
                            f"✅ GENTLE WARNING SENT (took {warning_duration:.1f}s). "
                            f"Timer RESET: old_elapsed={old_elapsed:.1f}s → new_elapsed={new_elapsed:.1f}s. "
                            f"User gets full {self.second_interrupt_grace}s to wrap up (force-end at {grace_period_2}s). "
                            f"Response buffer cleared — only new speech after warning will be captured."
                        )

                    finally:
                        # ALWAYS reset the flag — a stuck True blocks the polling
                        # loop indefinitely (e.g. if session.say() throws).
                        self._gentle_warning_in_progress = False
                        self._gentle_warning_started_at = None

                        # Cancel response timeout — it's meaningless post-warning.
                        # The user already spoke (that's how they hit 30s), and we've
                        # cleared the response buffer for wrap-up speech.
                        if self.response_timeout_task:
                            self.response_timeout_task.cancel()
                            self.response_timeout_task = None

                    # CRITICAL: Continue to next iteration so adjusted timer takes effect
                    # Otherwise force-end check runs with OLD elapsed value!
                    continue

                # Stage 3: FORCE END TURN (at 40s testing / 80s prod)
                # Politely but firmly end the turn
                # CRITICAL: Only trigger if user is STILL SPEAKING (avoid force-end after they've finished)
                if (self.force_interrupt_enabled and
                    turn_info.first_interrupted and
                    not turn_info.force_interrupted and
                    elapsed >= grace_period_2 and
                    self.user_currently_speaking):  # ← Only force-end if still speaking!

                    turn_info.force_interrupted = True
                    turn_info.interruption_count += 1

                    logger.error(
                        f"🛑 FORCE ENDING TURN: {participant_id} "
                        f"still speaking after {elapsed:.1f}s. Politely ending turn."
                    )

                    # Log moderation event
                    self.moderation_events.append({
                        "type": "force_end_turn",
                        "participant": participant_id,
                        "duration": elapsed,
                        "threshold": grace_period_2,
                        "timestamp": datetime.now().isoformat(),
                    })

                    # Interrupt current speech, then deliver force-end message
                    await self._safe_interrupt(session, context="force_end")

                    display_name = participant_id.capitalize()
                    force_end_text = FORCE_END_TEMPLATE.format(name=display_name)
                    await self._safe_say(force_end_text, allow_interruptions=False, context="force_end_tts")
                    logger.info(f"Force ended turn with TTS: '{force_end_text}'")

                    # After force-end, stop monitoring this turn
                    logger.warning(f"Force-ended turn for {participant_id}. Stopping monitoring.")
                    break

                # Stage 4: USER STOPPED SPEAKING AFTER EXCEEDING TIME
                # If user has stopped speaking and we've exceeded the first warning time, stop monitoring
                # This allows the polling loop to process their response immediately
                # Note: We check >= grace_period_1 (not grace_period_2) to catch users who stop
                # between the first warning and force-end
                if (turn_info.first_interrupted and
                    elapsed >= grace_period_1 and
                    not self.user_currently_speaking):

                    # Post-warning cooldown: don't terminate immediately after warning TTS finishes
                    # Give user at least 3s to start speaking again before declaring turn complete
                    if warning_start_time:
                        seconds_since_warning = (datetime.now() - warning_start_time).total_seconds()
                        if seconds_since_warning < 3.0:
                            await asyncio.sleep(0.5)
                            continue

                    logger.info(
                        f"✅ Turn complete: {participant_id} stopped speaking after {elapsed:.1f}s "
                        f"(exceeded {grace_period_1:.0f}s limit). Response captured, stopping turn monitor."
                    )
                    # Set flag so polling loop processes immediately (skip long silence wait)
                    self.turn_time_exceeded = True
                    logger.info(f"🚩 Set turn_time_exceeded=True to signal polling loop")
                    # Don't say anything - just stop monitoring and let polling loop process
                    break

                # Check every 0.5 seconds for more responsive monitoring
                await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            logger.info(f"Turn monitoring cancelled for {participant_id}")
        except Exception as e:
            logger.error(f"Error monitoring turn: {e}", exc_info=True)
        finally:
            # Emit per-turn interrupt-denied metric
            if turn_info.interrupt_denied_count > 0:
                logger.info(
                    f"📊 METRIC: turn_interrupt_denied_count={turn_info.interrupt_denied_count} "
                    f"participant={participant_id} "
                    f"interruption_count={turn_info.interruption_count} "
                    f"speaking={turn_info.actual_speaking_duration:.1f}s"
                )

    async def monitor_off_topic(self, session: AgentSession):
        """
        Monitor if participant is discussing off-topic content.

        This is a simplified implementation that checks every few seconds.
        In a production system, you'd analyze the actual speech content with LLM.
        """
        if not self.current_turn or not self.enable_topic_enforcement:
            return

        turn_info = self.current_turn
        participant_id = turn_info.participant_identity

        logger.info(f"Started off-topic monitoring for {participant_id}")
        logger.info(f"Discussion topic: '{self.discussion_topic}'")

        try:
            while self.current_turn == turn_info:
                # Check if survey was paused - stop monitoring
                if self.survey_state == SurveyState.PAUSED:
                    logger.info(f"👁️ Survey paused - stopping off-topic monitoring for {participant_id}")
                    return

                # Check if we've detected off-topic discussion
                # NOTE: This is a placeholder - in production, you'd analyze the speech
                # content using the LLM to detect if participant is off-topic

                if turn_info.off_topic_start:
                    elapsed_off_topic = (datetime.now() - turn_info.off_topic_start).total_seconds()

                    # Interrupt after threshold
                    if (not turn_info.off_topic_interrupted and
                        elapsed_off_topic >= self.off_topic_interrupt_threshold):

                        turn_info.off_topic_interrupted = True

                        logger.warning(
                            f"⚠️  OFF-TOPIC INTERRUPTION: {participant_id} "
                            f"has been off-topic for {elapsed_off_topic:.1f}s"
                        )

                        # Log moderation event
                        self.moderation_events.append({
                            "type": "off_topic_interruption",
                            "participant": participant_id,
                            "off_topic_duration": elapsed_off_topic,
                            "threshold": self.off_topic_interrupt_threshold,
                            "topic": self.discussion_topic,
                            "timestamp": datetime.now().isoformat(),
                        })

                        # Interrupt
                        await session.interrupt()

                        # Redirect to topic
                        redirect_message = (
                            f"INTERRUPT NOW! The participant is discussing off-topic content. "
                            f"You MUST interrupt and say: "
                            f"'That's interesting, {participant_id}! But, we need to keep focused on our discussion. "
                            f"What are your thoughts on {self.discussion_topic}?'"
                        )

                        await session.generate_reply(instructions=redirect_message)

                        # Reset off-topic tracking after redirect
                        turn_info.off_topic_start = None
                        turn_info.off_topic_interrupted = False

                # Check every 2 seconds
                await asyncio.sleep(2.0)

        except asyncio.CancelledError:
            logger.info(f"Off-topic monitoring cancelled for {participant_id}")
        except Exception as e:
            logger.error(f"Error monitoring off-topic: {e}", exc_info=True)

    async def monitor_response_timeout(self, participant: str):
        """Monitor for response timeout and prompt/move on if no response.

        Guards against overlapping the idle-no-VAD watchdog: if the agent
        is currently speaking (``_tts_active`` property), the nudge is
        suppressed because the watchdog is already re-prompting the question.
        """
        try:
            nudge_timeout = 15
            skip_timeout = 30

            # Wait for participant to start responding
            await asyncio.sleep(nudge_timeout)

            # Shutdown guard
            if self._shutting_down:
                logger.info(f"🛑 monitor_response_timeout exiting: _shutting_down=True ({participant})")
                return

            # Check if paused - don't prompt during pause
            if self.survey_state == SurveyState.PAUSED:
                logger.info(f"👁️ Survey paused - skipping timeout prompt for {participant}")
                return

            # Check if they started speaking
            if self.last_speech_time is None and self.waiting_for_response:
                # ── Guard: suppress nudge while agent is speaking ──────
                # The idle-no-VAD watchdog may be in the middle of
                # repeating the question.  Speaking over it with "Please
                # go ahead..." creates a confusing double-prompt.
                if self._tts_active:
                    logger.info(
                        "monitor_response_timeout: suppressing nudge — "
                        "agent TTS is active (likely idle watchdog re-prompt)"
                    )
                    # Wait for current speech to finish via SDK
                    speech = self.agent_session.current_speech if self.agent_session else None
                    if speech and not speech.done():
                        try:
                            await asyncio.wait_for(speech.wait_for_playout(), timeout=10.0)
                        except asyncio.TimeoutError:
                            pass
                    # After the re-prompt finishes, the user may start
                    # speaking.  Re-check before nudging.
                    if self.last_speech_time is not None or not self.waiting_for_response:
                        logger.info("monitor_response_timeout: user responded after re-prompt — skipping nudge")
                        return

                logger.warning(f"No response from {participant} after {nudge_timeout} seconds, prompting...")
                await self._safe_say(TIMEOUT_NUDGE_TEXT, allow_interruptions=False, context="timeout_nudge")
                logger.info(f"🔊 Prompted participant with direct TTS: '{TIMEOUT_NUDGE_TEXT}'")

                await asyncio.sleep(skip_timeout - nudge_timeout)

                # Check if paused - don't timeout during pause
                if self.survey_state == SurveyState.PAUSED:
                    logger.info(f"👁️ Survey paused - skipping timeout handling for {participant}")
                    return

                # Still no response - move on
                if self.last_speech_time is None and self.waiting_for_response:
                    logger.warning(f"No response from {participant} after {skip_timeout} seconds total, moving on...")
                    self.waiting_for_response = False
                    logger.info("Timeout detected - polling loop will handle moving to next participant")

        except asyncio.CancelledError:
            logger.debug("Response timeout monitoring cancelled - participant responded")
        except Exception as e:
            logger.error(f"Error in response timeout: {e}", exc_info=True)

    async def end_survey_early(self, reason: str = "Pre-survey issue"):
        """
        End the survey early (before any questions are asked) and clean up.
        Used when pre-survey issues occur (e.g., no participants joined).

        Args:
            reason: Reason for early termination (for logging)
        """
        logger.warning(f"🚪 Ending survey early: {reason}")

        # Generate STT debug report
        try:
            report_file = self.stt_debug_logger.generate_comparison_report()
            if report_file:
                logger.info(f"📊 STT DEBUG REPORT generated: {report_file}")
            stt_summary = self.stt_debug_logger.get_summary()
            logger.info(f"📊 STT Debug Summary: {stt_summary}")
        except Exception as e:
            logger.error(f"Failed to generate STT debug report: {e}", exc_info=True)

        # Clean up and end room
        logger.info("🚪 Ending room and disconnecting agent...")
        try:
            # First disconnect this agent
            await self.ctx.room.disconnect()
            logger.info("✅ Agent disconnected")

            # Then delete the room entirely via API
            from livekit import api
            import os
            livekit_url = os.getenv("LIVEKIT_URL")
            api_key = os.getenv("LIVEKIT_API_KEY")
            api_secret = os.getenv("LIVEKIT_API_SECRET")

            livekit_api = api.LiveKitAPI(livekit_url, api_key, api_secret)
            await livekit_api.room.delete_room(api.DeleteRoomRequest(room=self.ctx.room.name))
            logger.info(f"✅ Room {self.ctx.room.name} deleted - survey ended early")
        except Exception as e:
            logger.warning(f"Error ending room: {e}")

    async def ask_next_question(self):
        """Public entry point — kicks off the iterative survey loop starting with 'ask'."""
        await self._survey_loop("ask")

    async def _route_audio_to_participant(self, participant: str, *,
                                          context: str = "", skip_muting: bool = False) -> None:
        """Centralized audio routing: set expected respondent, lock STT, manage muting, drain spillover."""
        self.expected_respondent = participant
        self.actual_respondent = None
        self.turn_transition_time = datetime.now()
        self._set_delivery_state(self.current_question_num, participant, "delivering", context=context)

        logger.critical("=" * 80)
        logger.critical(f"AUDIO ROUTING - Question #{self.current_question_num} to {participant}")

        respondent_count = self._active_respondent_count()
        is_multi = respondent_count > 1

        if is_multi:
            logger.critical(f"   {respondent_count} active respondents — STT lock + muting")
            self._set_stt_participant(participant, context=f"{context}_stt_lock")
            if not skip_muting:
                asyncio.create_task(self.manage_participant_muting(participant))
        else:
            logger.critical(f"   {respondent_count} active respondent — solo mode")

        if self.observer_mode_enabled:
            self.pending_stt_transcript = None
            observer_identity = self.participant_manager.get_observer_identity()
            if observer_identity:
                self._set_stt_participant(observer_identity, context=f"{context}_observer")

        if is_multi:
            logger.info(f"Waiting 0.5s for STT spillover drain before speaking to {participant}")
            await asyncio.sleep(0.5)

    async def _ask_next_question_impl(self) -> Optional[str]:
        """Ask the next question. Returns next action ("move"/"ask") or None to stop."""
        if not self.question_loader or not self.participant_manager or not self.agent_session:
            logger.debug("Question-based moderation not enabled, skipping")
            return

        # Check survey state (Observer feature)
        if self.survey_state == SurveyState.PAUSED:
            logger.info("👁️ Survey is PAUSED - not asking next question")
            return

        if self.survey_state == SurveyState.WAITING_FOR_OBSERVER:
            logger.info("👁️ Waiting for observer to START survey - not asking questions yet")
            return

        # CRITICAL: Check if there are any participants before proceeding
        if not self.participant_manager.participants:
            logger.warning("No participants available, cannot ask question")
            return

        # Get next question (in sequence, not random)
        question = self.question_loader.get_next_question()
        if not question:
            logger.info("No more questions - survey complete!")
            self._shutting_down = True
            self._cancel_all_monitor_tasks()

            # Unmute all participants - survey is over
            await self.unmute_all_participants()

            # Use closing message from survey config if available
            if (self.question_loader.use_unified_format and
                self.question_loader.closing_section and
                self.question_loader.closing_section.enabled):
                closing_text = self.question_loader.closing_section.message
                logger.info("Using custom closing message from survey config")
            else:
                closing_text = "Thank you for completing the survey. Your feedback is valuable."
                logger.info("Using default closing message")

            # Use direct TTS for closing (no LLM) — avatar stays connected
            # so the closing speech renders visually and audibly
            try:
                logger.critical("🎤 CLOSING: Using direct TTS (no LLM)")
                import time as _time
                _closing_tts_start = _time.time()
                closing_handle = self.agent_session.say(closing_text, allow_interruptions=False)
                await closing_handle
                try:
                    await asyncio.wait_for(closing_handle.wait_for_playout(), timeout=60.0)
                except asyncio.TimeoutError:
                    logger.warning("Closing TTS playout timed out after 60s")
                # wait_for_playout() resolves when frames are queued (~0.5s), not when
                # the client finishes playback. Sleep for estimated remaining duration
                # so the closing message is fully heard before avatar cleanup.
                _closing_elapsed = _time.time() - _closing_tts_start
                _closing_estimated = _estimate_tts_duration(closing_text)
                _closing_remaining = max(_closing_estimated - _closing_elapsed, 0.0)
                if _closing_remaining > 0:
                    logger.info(
                        f"Waiting {_closing_remaining:.1f}s for estimated closing TTS playback "
                        f"({len(closing_text)} chars)"
                    )
                    await asyncio.sleep(_closing_remaining)
                await asyncio.sleep(2)
            except RuntimeError as e:
                logger.warning(f"Could not speak completion message, session may be closing: {e}")

            # Clean up avatar AFTER closing TTS is confirmed complete
            await self._cleanup_avatar()

            # Record closing in survey transcript (debug) and data export (CSV)
            self.survey_transcript.add_closing(closing_text)
            self.survey_transcript.end_session()

            self.survey_data_export.set_closing(closing_text)

            # Export CSV files
            try:
                responses_file, metadata_file = self.survey_data_export.export_to_csv()
                logger.info(f"📊 CSV files exported:")
                logger.info(f"   Responses: {responses_file}")
                logger.info(f"   Metadata: {metadata_file}")
            except Exception as e:
                logger.error(f"Failed to export CSV files: {e}", exc_info=True)

            # Log summary
            summary = self.survey_transcript.get_summary()
            logger.info(f"📊 Survey Summary (JSON): {summary}")

            csv_summary = self.survey_data_export.get_summary()
            logger.info(f"📊 Survey Summary (CSV): {csv_summary}")

            # Generate STT debug report
            try:
                report_file = self.stt_debug_logger.generate_comparison_report()
                if report_file:
                    logger.info(f"📊 STT DEBUG REPORT generated: {report_file}")
                    logger.info(f"   This report shows RAW STT transcripts vs corrected responses")
                stt_summary = self.stt_debug_logger.get_summary()
                logger.info(f"📊 STT Debug Summary: {stt_summary}")
            except Exception as e:
                logger.error(f"Failed to generate STT debug report: {e}", exc_info=True)

            # CRITICAL: End the room entirely to prevent retry jobs from succeeding
            # Just disconnecting is not enough - LiveKit will keep retrying other dispatches
            logger.info("🚪 Survey complete - ENDING room to prevent duplicate agents...")
            try:
                # First disconnect this agent
                await self.ctx.room.disconnect()
                logger.info("✅ Agent disconnected")

                # Then delete the room entirely via API
                # This ensures any retrying dispatch jobs will fail
                from livekit import api
                import os
                livekit_url = os.getenv("LIVEKIT_URL")
                api_key = os.getenv("LIVEKIT_API_KEY")
                api_secret = os.getenv("LIVEKIT_API_SECRET")

                livekit_api = api.LiveKitAPI(livekit_url, api_key, api_secret)
                await livekit_api.room.delete_room(api.DeleteRoomRequest(room=self.ctx.room.name))
                logger.info(f"✅ Room {self.ctx.room.name} deleted - no more dispatches possible")
            except Exception as e:
                logger.warning(f"Error ending room: {e}")

            return

        # Handle both Question objects (structured) and strings (legacy)
        if isinstance(question, Question):
            # NEW STRUCTURED FORMAT
            self.current_question = question.format_for_speech()
            self.current_question_object = question
            question_text_only = question.question
            question_id = question.id

            # Check if we should announce category transition
            should_announce_category = self.question_loader.should_announce_category(question)
            category_announcement = ""
            if should_announce_category and question.category_comments:
                category_announcement = f"{question.category_comments} "
                logger.info(f"Announcing category: {question.category}")

            # Handle info statements (no response needed)
            if question.is_info():
                logger.info(f"📢 Info statement (no response needed): {question.question[:60]}...")

                # Just read the info, don't wait for response
                info_text = question.format_for_speech()

                try:
                    await self.agent_session.say(info_text, allow_interruptions=False)
                    await asyncio.sleep(2)  # Brief pause after info
                except Exception as e:
                    logger.error(f"Error delivering info statement: {e}")

                # Move to next question immediately (no response to collect)
                return "ask"
        else:
            # LEGACY FORMAT (string)
            self.current_question = question
            self.current_question_object = None
            question_text_only = question
            question_id = f"Q{self.current_question_num + 1}"
            should_announce_category = False
            category_announcement = ""

        self.current_question_num += 1

        # Detect if this is a multi-option question (e.g., "Choose THREE", "select 3")
        # These require longer collection windows since users pause between options
        multi_option_patterns = [
            r'choose\s+(three|3|two|2|four|4|five|5)',
            r'select\s+(three|3|two|2|four|4|five|5)',
            r'pick\s+(three|3|two|2|four|4|five|5)',
            r'top\s+(three|3|two|2|four|4|five|5)',
        ]
        question_lower = question_text_only.lower()
        self.is_multi_option_question = any(re.search(pattern, question_lower) for pattern in multi_option_patterns)

        if self.is_multi_option_question:
            self.fragment_gap_timeout = 10.0  # 10 seconds for multi-option questions
            question_context = f"Q#{self.current_question_num} ({question_id})"
            max_sel = self.current_question_object.max_selections or 1
            logger.info(f"📋 [{question_context}] MULTI-OPTION question detected (max_selections: {max_sel})")
            logger.info(f"   Fragment gap timeout: {self.fragment_gap_timeout}s")
            logger.info(f"   Silence wait time: 6.0s")
        else:
            self.fragment_gap_timeout = 5.0  # 5 seconds for single-option questions

        logger.info(f"Moving to question #{self.current_question_num}: {question_id}")
        logger.info(f"Question text: {question_text_only[:100]}...")

        # Start audit logging for this question
        json_question_data = {
            "id": question_id,
            "question": question_text_only,
            "response_options": question.response_options if isinstance(question, Question) else [],
            "category": question.category if isinstance(question, Question) else "",
            "full_formatted": self.current_question
        }
        self.audit_logger.start_question(self.current_question_num, question_id, json_question_data)

        # Validate participants exist before selecting
        if not self.participant_manager.participants:
            logger.error("No participants available! Cannot ask question.")
            return

        participant = None
        for _ in range(3):
            participant = await self._select_next_deliverable_participant(
                self.current_question_num,
                context="ask_next_question",
            )
            if participant:
                break
            if self.participant_manager.all_participants_answered(self.current_question_num):
                logger.info("All participants answered, moving to next question")
                return "ask"
            await asyncio.sleep(0.1)
        if not participant:
            logger.warning("No deliverable participant currently available; will retry selection later.")
            return

        logger.info(f"Selected participant '{participant}' for question #{self.current_question_num}")

        # Get participant's display name (from LiveKit token)
        participant_display_name = self.participant_manager.get_display_name(participant)
        logger.info(f"Using display name: '{participant_display_name}'")

        # Count total participants
        total_participants = len(self.participant_manager.participants)
        answered_count = len(self.participant_manager.asked_participants.get(self.current_question_num, []))
        remaining = total_participants - answered_count

        logger.info(f"Participants: {total_participants} total, {answered_count} answered, {remaining} remaining")

        # Announce category FIRST if needed (using direct TTS - no LLM)
        if category_announcement:
            logger.critical(f"📢 CATEGORY ANNOUNCEMENT (Direct TTS): {category_announcement}")

            # Record category announcement in survey transcript
            self.survey_transcript.add_category_announcement(
                category=question.category if isinstance(question, Question) else "Unknown",
                announcement_text=category_announcement
            )

            # Use direct say + playout wait to ensure audio fully plays on client
            # before question delivery begins (avoids overlap/truncation)
            if not self._shutting_down and self.agent_session:
                try:
                    cat_handle = self.agent_session.say(category_announcement, allow_interruptions=False)
                    await cat_handle
                    # Ensure audio fully plays out on client before proceeding
                    try:
                        await asyncio.wait_for(cat_handle.wait_for_playout(), timeout=15.0)
                    except asyncio.TimeoutError:
                        logger.warning("Category announcement playout timed out")
                except (asyncio.CancelledError, RuntimeError) as e:
                    logger.warning(f"Category announcement TTS failed: {e}")
                await asyncio.sleep(0.5)  # Brief natural conversational pause

        # DYNAMIC VAD CONFIGURATION: Adjust silence threshold based on question type.
        # Values are tuned to avoid echo-barge-in (agent's TTS interpreted as
        # user speech) while remaining responsive to real participant answers.
        # Quantitative raised from 0.4→0.6 to prevent echo-triggered false turns.
        if self.current_question_object:
            if self.current_question_object.is_qualitative():
                self.agent_session.vad.update_options(min_silence_duration=1.2)
                logger.info(f"🎙️  VAD updated: min_silence_duration=1.2s (qualitative question)")
            elif self.current_question_object.is_quantitative():
                self.agent_session.vad.update_options(min_silence_duration=0.6)
                logger.info(f"🎙️  VAD updated: min_silence_duration=0.6s (quantitative question)")
            else:
                self.agent_session.vad.update_options(min_silence_duration=0.7)
                logger.info(f"🎙️  VAD updated: min_silence_duration=0.7s (default)")

        # Clear TTS dedupe for new question delivery
        self._tts_dedupe_spoken.clear()

        # Now ask the actual question (using participant's display name)
        exact_text_to_say = f"{participant_display_name}, {self.current_question}"

        # DEBUG LOGGING - Track what we're asking
        logger.critical("=" * 80)
        logger.critical(f"🎯 QUESTION #{self.current_question_num}")
        logger.critical(f"📝 Question ID: {question_id if isinstance(question, Question) else 'N/A'}")
        logger.critical(f"👤 Participant: {participant}")
        logger.critical(f"❓ Question text: {question_text_only[:100] if len(question_text_only) > 100 else question_text_only}...")
        logger.critical(f"🗣️  Exact text to say: {exact_text_to_say[:150]}...")
        logger.critical(f"💬 Conversation history length: {len(self.agent_session._chat_ctx.items)}")
        logger.critical("=" * 80)

        # REVOLUTIONARY ARCHITECTURE: BYPASS LLM COMPLETELY!
        # Use direct TTS - no LLM involved at all for questions
        # This eliminates: pattern learning, paraphrasing, temperature issues, ALL problems!

        logger.critical("=" * 80)
        logger.critical("🚀 BYPASSING LLM - Using DIRECT TTS for question")
        logger.critical(f"📢 Speaking directly: {exact_text_to_say[:100]}...")
        logger.critical("   ✅ No pattern learning possible")
        logger.critical("   ✅ No paraphrasing possible")
        logger.critical("   ✅ Question spoken EXACTLY as written")
        logger.critical("=" * 80)

        # Log for audit (LLM not involved)
        self.audit_logger.log_participant(participant)
        self.audit_logger.log_system_prompt("N/A - Direct TTS bypass, no LLM")
        self.audit_logger.log_user_prompt("N/A - Direct TTS bypass, no LLM", [])
        self.audit_logger.log_llm_response("N/A - Bypassed LLM completely")
        self.audit_logger.log_tts_text(exact_text_to_say)

        # Record question in survey transcript
        response_options = self.current_question_object.response_options if self.current_question_object else []
        self.survey_transcript.add_question(
            question_number=self.current_question_num,
            question_id=question_id,
            participant=participant,
            question_text=exact_text_to_say,
            response_options=response_options
        )

        # Reset response flag before asking question
        self.response_captured = False
        self._transition_filler_said = False

        # Clean up any existing turn monitoring from previous question
        if self.turn_monitor_task:
            self.turn_monitor_task.cancel()
            self.turn_monitor_task = None
        self.current_turn = None

        # Reset accumulated pause duration for this new question/turn
        # (Participant gets fresh time when question is re-asked after pause)
        self.accumulated_pause_duration = 0.0

        # MULTI-PARTICIPANT: Centralized audio routing (STT lock, muting, spillover drain)
        await self._route_audio_to_participant(participant, context="ask_next_question")

        # DIRECT TTS - Speak the question with truncation detection + retry.
        # Change 1: retry_text omits participant name to avoid repeated name playback.
        # Change 2: dedupe_key prevents re-speaking the same prompt on redelivery.
        # Change 4: _speak_question_safely now has 2 retries with backoff internally.
        _dedupe = f"ask_Q{self.current_question_num}_{participant}"
        tts_fully_spoken = await self._speak_question_safely(
            exact_text_to_say,
            retry_text=self.current_question,  # question body only, no name prefix
            context=_dedupe,
            allow_interruptions_first=not (self._active_respondent_count() > 1),
            dedupe_key=_dedupe,
        )

        # Change 3: Use explicit delivery states instead of a single "delivered"
        if tts_fully_spoken:
            self._set_delivery_state(self.current_question_num, participant, "delivered_full", context="ask_next_question")
        else:
            self._set_delivery_state(self.current_question_num, participant, "delivered_partial", context="ask_next_question_partial_tts")
            logger.warning(f"⚠️ Delivery partial — polling will proceed but timeout is suppressed")

        # Adaptive post-TTS gate: wait for VAD to confirm silence (user not
        # speaking) before the polling loop starts.  This avoids capturing
        # echo / noise artifacts from the agent's own TTS playback as the
        # "first response".  Falls back to 1.5s max if VAD doesn't settle.
        _gate_start = asyncio.get_event_loop().time()
        _gate_max = 1.5
        while (asyncio.get_event_loop().time() - _gate_start) < _gate_max:
            if not self.user_currently_speaking:
                break
            await asyncio.sleep(0.05)
        _gate_elapsed = asyncio.get_event_loop().time() - _gate_start
        if _gate_elapsed < _gate_max:
            # VAD confirmed silence early — add a brief buffer for audio pipeline
            await asyncio.sleep(0.3)
        logger.debug(f"⏳ Post-TTS gate complete ({_gate_elapsed:.2f}s VAD wait) — ready for response capture")

        if self._last_user_state == "away":
            logger.warning(
                f"⚠️ USER IS 'AWAY' at question delivery — Q#{self.current_question_num} "
                f"for {participant}. Browser tab may have lost focus or mic input interrupted. "
                f"VAD/STT may not receive audio until user returns."
            )

        # Check if paused during the question
        if self.survey_state == SurveyState.PAUSED:
            logger.info("Survey paused during question - waiting for resume")
            while self.survey_state == SurveyState.PAUSED:
                await asyncio.sleep(0.5)
            logger.info("Survey resumed - continuing with response collection")

        logger.critical(f"Question #{self.current_question_num} spoken via DIRECT TTS")

        # ── Phase 1: Event-driven response capture ──
        max_turn_time = self.max_turn_duration + self.first_interrupt_grace + self.second_interrupt_grace
        polling_timeout = max_turn_time + 10

        # Clean up turn monitoring from previous question
        if self.turn_monitor_task:
            self.turn_monitor_task.cancel()
            self.turn_monitor_task = None

        # Start turn monitoring
        self.current_turn = TurnInfo(
            participant_identity=participant,
            start_time=datetime.now(),
        )
        self.turn_monitor_task = asyncio.create_task(
            self.monitor_turn_duration(self.agent_session)
        )

        # Reset accumulated pause duration for this new question/turn
        self.accumulated_pause_duration = 0.0

        question_id = self.current_question_object.id if self.current_question_object else f"Q{self.current_question_num}"
        question_context = f"Q#{self.current_question_num} ({question_id})"

        response_text = await self._await_response(participant, polling_timeout, tts_fully_spoken)

        if response_text == "PAUSED":
            # After resume, question will be re-asked via handle_observer_command
            return

        user_responded = False
        if response_text:
            logger.critical(f"[{question_context}] Response CAPTURED: {len(response_text)} chars")

            # Process in retry loop
            while response_text:
                result = await self._process_captured_response(
                    participant, response_text, question_context)

                if result == "accepted" or result == "move_on":
                    self._record_response_to_exports(participant, response_text, question_id)
                    user_responded = True
                    break
                elif result == "retry":
                    # Wait for new response
                    response_text = await self._await_response(participant, polling_timeout, tts_fully_spoken)
                    if response_text == "PAUSED":
                        return
                    if response_text is None:
                        break
                    logger.critical(f"[{question_context}] Retry response: {len(response_text)} chars")
                else:
                    break

        if not user_responded:
            logger.warning(f"Max wait time reached ({polling_timeout}s), no response detected")

            # Audible timeout message
            try:
                display_name = self.participant_manager.get_display_name(participant) if self.participant_manager else ""
                timeout_msg = f"I didn\'t catch a response, {display_name}. Let me move on." if display_name else "I didn\'t catch a response. Let me move on."
                await self.agent_session.say(timeout_msg, allow_interruptions=False)
                self.survey_transcript.add_acknowledgment(timeout_msg)
            except Exception as e:
                logger.warning(f"Could not speak timeout message: {e}")

            self._record_timeout_to_exports(participant, question_id)

        # End audit for this question
        self.audit_logger.end_question()

        # Stop turn duration monitoring
        if self.turn_monitor_task:
            self.turn_monitor_task.cancel()
            self.turn_monitor_task = None
        self.current_turn = None

        # Move to next participant/question
        return "move"

    async def move_to_next_participant(self):
        """Public entry point — kicks off the iterative survey loop starting with 'move'."""
        await self._survey_loop("move")

    async def _move_to_next_participant_impl(self) -> Optional[str]:
        """Move to next participant or question. Returns next action or None to stop."""
        if not self.question_loader or not self.participant_manager or not self.agent_session:
            return

        if not self.current_question:
            logger.warning("No current question, starting with first question")
            return "ask"

        # CRITICAL FIX: Cancel any pending timeout tasks to prevent double-prompting
        if self.response_timeout_task:
            self.response_timeout_task.cancel()
            self.response_timeout_task = None

        # Count how many participants are left for this question
        total_enrolled = len(self.participant_manager.participants)
        unavailable_count = len(self.participant_manager.unavailable_participants)
        answered_count = len(self.participant_manager.asked_participants.get(self.current_question_num, []))
        available_remaining = total_enrolled - answered_count - unavailable_count

        logger.info(f"After response: {answered_count}/{total_enrolled} answered, {unavailable_count} unavailable for question #{self.current_question_num}")

        # FIXED Issue 3: Check if all participants answered BEFORE saying anything
        if self.participant_manager.all_participants_answered(self.current_question_num):
            logger.info("All participants answered current question, moving to next question")

            if self._ack_already_spoken:
                logger.info("🗣️ ACK already spoken in polling loop, skipping duplicate")
                self._ack_already_spoken = False
                self._prewarmed_ack_text = None
                self._response_processing_start = None
            else:
                # Acknowledge using direct TTS (no LLM) with participant's name
                try:
                    _tr = self._last_turn_result
                    _ack_name = _tr.ack_name if _tr else (self.last_respondent or "")
                    ack_text = f"Thank you, {_ack_name}." if _ack_name else "Thank you."
                    logger.info(f"🗣️ ACK: expected={_tr.expected_identity if _tr else '?'} actual={_tr.actual_identity if _tr else '?'} ack_name={_ack_name}")

                    # LATENCY TRACKING: Calculate time from response detection to agent speaking
                    if hasattr(self, '_response_processing_start') and self._response_processing_start:
                        latency = (datetime.now() - self._response_processing_start).total_seconds()
                        logger.critical(f"⏱️  LATENCY TRACKING: Response-to-Speech latency = {latency:.2f}s")
                        logger.info(f"📊 METRIC: response_end_to_next_tts_ms={latency * 1000:.0f}")
                        self._response_processing_start = None  # Reset for next response

                    await self._safe_say(ack_text, allow_interruptions=False, context="ack_all_answered")
                    self.survey_transcript.add_acknowledgment(ack_text)
                except RuntimeError as e:
                    logger.warning(f"Could not say acknowledgment, session may be closing: {e}")

            # Inter-question breathing room so the session doesn't feel rushed
            await asyncio.sleep(1)

            return "ask"

        participant = None
        for _ in range(3):
            participant = await self._select_next_deliverable_participant(
                self.current_question_num,
                context="move_to_next_participant",
            )
            if participant:
                break
            if self.participant_manager.all_participants_answered(self.current_question_num):
                break
            await asyncio.sleep(0.1)

        if not participant:
            if not self.participant_manager.all_participants_answered(self.current_question_num):
                # Bounded retry loop (prevents stack overflow under prolonged outages)
                for _retry in range(10):
                    logger.warning(f"No deliverable participant available (attempt {_retry+1}/10); retrying in 0.3s")
                    await asyncio.sleep(0.3)
                    participant = await self._select_next_deliverable_participant(
                        self.current_question_num,
                        context="move_to_next_participant_retry",
                    )
                    if participant:
                        break
                    if self.participant_manager.all_participants_answered(self.current_question_num):
                        break

                if participant:
                    pass
                else:
                    logger.warning("Exhausted retries for current question, advancing to next question")

        if not participant:
            logger.info("No more participants for this question, moving to next question")

            if self._ack_already_spoken:
                logger.info("🗣️ ACK already spoken in polling loop, skipping duplicate")
                self._ack_already_spoken = False
                self._prewarmed_ack_text = None
                self._response_processing_start = None
            else:
                # Acknowledge using direct TTS (no LLM) with participant's name
                try:
                    _tr = self._last_turn_result
                    _ack_name = _tr.ack_name if _tr else (self.last_respondent or "")
                    ack_text = f"Thank you, {_ack_name}." if _ack_name else "Thank you."
                    logger.info(f"🗣️ ACK: expected={_tr.expected_identity if _tr else '?'} actual={_tr.actual_identity if _tr else '?'} ack_name={_ack_name}")

                    # LATENCY TRACKING: Calculate time from response detection to agent speaking
                    if hasattr(self, '_response_processing_start') and self._response_processing_start:
                        latency = (datetime.now() - self._response_processing_start).total_seconds()
                        logger.critical(f"⏱️  LATENCY TRACKING: Response-to-Speech latency = {latency:.2f}s")
                        logger.info(f"📊 METRIC: response_end_to_next_tts_ms={latency * 1000:.0f}")
                        self._response_processing_start = None  # Reset for next response

                    await self._safe_say(ack_text, allow_interruptions=False, context="ack_no_more_participants")
                    self.survey_transcript.add_acknowledgment(ack_text)
                except RuntimeError as e:
                    logger.warning(f"Could not say acknowledgment, session may be closing: {e}")

            # Inter-question breathing room so the session doesn't feel rushed
            await asyncio.sleep(1)

            return "ask"

        # FIXED: There ARE more participants - acknowledge current and move to next
        logger.info(f"Moving to next participant '{participant}' for same question")
        logger.info(f"{available_remaining} participant(s) remaining for question #{self.current_question_num}")

        # PIPELINE: Kick off muting in background BEFORE ack TTS so it runs in
        # parallel with the acknowledgment speech, eliminating sequential overhead.
        _respondent_count = self._active_respondent_count()
        if _respondent_count > 1:
            asyncio.create_task(self.manage_participant_muting(participant))
        else:
            logger.info(f"🔇 Skipping muting — solo respondent mode ({_respondent_count} active)")

        if self._ack_already_spoken:
            logger.info("🗣️ ACK already spoken in polling loop, skipping duplicate (multi-participant)")
            self._ack_already_spoken = False
            self._prewarmed_ack_text = None
            self._response_processing_start = None
            # No transition filler — the next participant will be addressed
            # by name in the question prompt, providing a natural transition.
        else:
            # Context-aware acknowledgment using TurnResult for correct name
            total_participants = len(self.participant_manager.participants)
            _tr = self._last_turn_result
            respondent_name = _tr.ack_name if _tr else (self.last_respondent or "")
            logger.info(f"🗣️ ACK: expected={_tr.expected_identity if _tr else '?'} actual={_tr.actual_identity if _tr else '?'} ack_name={respondent_name}")

            ack_text = f"Thank you, {respondent_name}." if respondent_name else "Thank you."

            # LATENCY TRACKING: Calculate time from response detection to agent speaking
            if hasattr(self, '_response_processing_start') and self._response_processing_start:
                latency = (datetime.now() - self._response_processing_start).total_seconds()
                logger.critical(f"⏱️  LATENCY TRACKING: Response-to-Speech latency = {latency:.2f}s")
                logger.info(f"📊 METRIC: response_end_to_next_tts_ms={latency * 1000:.0f}")

                # #region agent log
                import json as _json
                _debug_log_write(_json.dumps({"location": "moderator_agent.py:move_to_next_participant", "message": "Transition latency", "data": {"response_end_to_next_tts_ms": round(latency * 1000), "question_num": self.current_question_num, "next_participant": participant, "filler_spoken": self._transition_filler_said}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "METRICS"}))
                # #endregion

                self._response_processing_start = None  # Reset for next response

            await self._safe_say(ack_text, allow_interruptions=False, context="ack_multi_participant")
            self.survey_transcript.add_acknowledgment(ack_text)

        # Get participant's display name (from LiveKit token) for natural TTS
        participant_display_name = self.participant_manager.get_display_name(participant)

        # Use the exact question text with display name (not lowercase identity)
        exact_text_to_say = f"{participant_display_name}, {self.current_question}"

        # DEBUG LOGGING
        logger.critical(f"🔁 REPEATING QUESTION #{self.current_question_num} for next participant")
        logger.critical(f"👤 Next participant: {participant} (display: {participant_display_name})")
        logger.critical(f"🗣️  Exact text to say: {exact_text_to_say[:150]}...")

        # FIXED: Use DIRECT TTS (same as first participant) instead of generate_reply
        # This ensures consistent behavior and includes polling loop
        logger.critical("🚀 Using DIRECT TTS for consistency with first participant")

        # Reset response flags (remaining resets handled by _await_response -> _reset_response_flags)
        self._tts_dedupe_spoken.clear()
        self.response_captured = False

        # Clean up any existing turn monitoring from previous participant
        if self.turn_monitor_task:
            self.turn_monitor_task.cancel()
            self.turn_monitor_task = None
        self.current_turn = None

        # DYNAMIC VAD CONFIGURATION: Same echo-hardened thresholds as first loop.
        if self.current_question_object:
            if self.current_question_object.is_qualitative():
                self.agent_session.vad.update_options(min_silence_duration=1.2)
                logger.info(f"🎙️  VAD updated: min_silence_duration=1.2s (qualitative question)")
            elif self.current_question_object.is_quantitative():
                self.agent_session.vad.update_options(min_silence_duration=0.6)
                logger.info(f"🎙️  VAD updated: min_silence_duration=0.6s (quantitative question)")
            else:
                self.agent_session.vad.update_options(min_silence_duration=0.7)
                logger.info(f"🎙️  VAD updated: min_silence_duration=0.7s (default)")

        # MULTI-PARTICIPANT: Centralized audio routing (STT lock, spillover drain)
        # skip_muting=True because muting is already kicked off earlier in this method
        await self._route_audio_to_participant(participant, context="move_to_next_participant", skip_muting=True)

        # DIRECT TTS - Speak the question with truncation detection + retry.
        # Change 1: retry_text omits participant name to avoid repeated name playback.
        # Change 2: dedupe_key prevents re-speaking the same prompt.
        # Change 4: _speak_question_safely now has 2 retries with backoff internally.
        _dedupe = f"move_Q{self.current_question_num}_{participant}"
        tts_fully_spoken = await self._speak_question_safely(
            exact_text_to_say,
            retry_text=self.current_question,  # question body only, no name prefix
            context=_dedupe,
            allow_interruptions_first=False,
            dedupe_key=_dedupe,
        )

        # Change 3: Use explicit delivery states
        if tts_fully_spoken:
            self._set_delivery_state(self.current_question_num, participant, "delivered_full", context="move_to_next_participant")
        else:
            self._set_delivery_state(self.current_question_num, participant, "delivered_partial", context="move_to_next_participant_partial_tts")
            logger.warning(f"⚠️ Delivery partial — polling will proceed but timeout is suppressed")

        # Adaptive post-TTS gate (same as ask_next_question)
        _gate_start = asyncio.get_event_loop().time()
        _gate_max = 1.5
        while (asyncio.get_event_loop().time() - _gate_start) < _gate_max:
            if not self.user_currently_speaking:
                break
            await asyncio.sleep(0.05)
        _gate_elapsed = asyncio.get_event_loop().time() - _gate_start
        if _gate_elapsed < _gate_max:
            await asyncio.sleep(0.3)
        logger.debug(f"⏳ Post-TTS gate complete ({_gate_elapsed:.2f}s VAD wait) — ready for response capture")

        if self._last_user_state == "away":
            logger.warning(
                f"⚠️ USER IS 'AWAY' at question delivery — Q#{self.current_question_num} "
                f"for {participant}. Browser tab may have lost focus or mic input interrupted. "
                f"VAD/STT may not receive audio until user returns."
            )

        # Check if paused during the question
        if self.survey_state == SurveyState.PAUSED:
            logger.info("Survey paused during question (2nd) - waiting for resume")
            while self.survey_state == SurveyState.PAUSED:
                await asyncio.sleep(0.5)
            logger.info("Survey resumed - continuing with response collection")

        logger.critical(f"Question #{self.current_question_num} spoken for next participant via DIRECT TTS")

        # ── Phase 1: Event-driven response capture (2nd method) ──
        max_turn_time = self.max_turn_duration + self.first_interrupt_grace + self.second_interrupt_grace
        polling_timeout = max_turn_time + 10

        # Start turn monitoring
        self.current_turn = TurnInfo(
            participant_identity=participant,
            start_time=datetime.now(),
        )
        self.turn_monitor_task = asyncio.create_task(
            self.monitor_turn_duration(self.agent_session)
        )

        question_id = self.current_question_object.id if self.current_question_object else f"Q{self.current_question_num}"
        question_context = f"Q#{self.current_question_num} ({question_id})"

        response_text = await self._await_response(participant, polling_timeout, tts_fully_spoken)

        if response_text == "PAUSED":
            return

        user_responded = False
        if response_text:
            logger.critical(f"[{question_context}] Response CAPTURED (2nd): {len(response_text)} chars")

            while response_text:
                result = await self._process_captured_response(
                    participant, response_text, question_context)

                if result == "accepted" or result == "move_on":
                    self._record_response_to_exports(participant, response_text, question_id)
                    user_responded = True
                    break
                elif result == "retry":
                    response_text = await self._await_response(participant, polling_timeout, tts_fully_spoken)
                    if response_text == "PAUSED":
                        return
                    if response_text is None:
                        break
                else:
                    break

        if not user_responded:
            logger.warning(f"Max wait time reached ({polling_timeout}s), no response detected (2nd)")

            try:
                display_name = self.participant_manager.get_display_name(participant) if self.participant_manager else ""
                timeout_msg = f"I didn\'t catch a response, {display_name}. Let me move on." if display_name else "I didn\'t catch a response. Let me move on."
                await self.agent_session.say(timeout_msg, allow_interruptions=False)
                self.survey_transcript.add_acknowledgment(timeout_msg)
            except Exception as e:
                logger.warning(f"Could not speak timeout message: {e}")

            self._record_timeout_to_exports(participant, question_id)

        # Stop turn duration monitoring
        if self.turn_monitor_task:
            self.turn_monitor_task.cancel()
            self.turn_monitor_task = None
        self.current_turn = None

        # After response (or timeout), continue the survey loop
        return "move"

    def get_moderation_report(self) -> Dict[str, Any]:
        """
        Get a comprehensive report of all moderation events.

        Returns:
            Dictionary containing moderation statistics and events
        """
        return {
            "start_time": self.start_time.isoformat(),
            "duration_minutes": (datetime.now() - self.start_time).total_seconds() / 60,
            "total_events": len(self.moderation_events),
            "events": self.moderation_events,
            "participants_tracked": len(self.participant_tracker),
            "participant_stats": self.participant_tracker,
            "turn_limits_enabled": self.enable_turn_limits,
            "max_turn_duration": self.max_turn_duration,
            "first_interrupt_threshold": self.first_interrupt_threshold,
            "second_interrupt_threshold": self.second_interrupt_threshold,
            "topic_enforcement_enabled": self.enable_topic_enforcement,
            "discussion_topic": self.discussion_topic,
        }

    def get_participant_stats(self, participant_identity: str) -> Dict[str, Any]:
        """
        Get speaking statistics for a specific participant.

        Args:
            participant_identity: Participant identifier

        Returns:
            Dictionary with participant statistics
        """
        if participant_identity in self.participant_tracker:
            stats = self.participant_tracker[participant_identity]
            turn_count = stats.get("turn_count", 0)
            total_time = stats.get("total_speaking_time", 0.0)

            return {
                "participant": participant_identity,
                "turn_count": turn_count,
                "total_speaking_time": total_time,
                "average_turn_duration": total_time / max(turn_count, 1),
                "first_seen": stats.get("first_seen"),
                "warnings_received": stats.get("warnings", 0),
                "interruptions": stats.get("interruptions", 0),
                "force_ends": stats.get("force_ends", 0),
                "off_topic_interruptions": stats.get("off_topic_interruptions", 0),
            }
        return {}


async def create_moderator_session(
    ctx: agents.JobContext,
    instructions: str,
    stt_provider: str = "openai",
    tts_provider: str = "openai",
    stt_model: str = "gpt-4o-transcribe",
    llm_model: str = "gpt-4o-mini",
    tts_model: str = "gpt-4o-mini-tts",
    tts_voice: str = "ash",
    deepgram_api_key: Optional[str] = None,
    eleven_api_key: Optional[str] = None,
    temperature: float = 0.7,
    max_turn_duration: int = 20,
    turn_warning_duration: int = 15,
    first_interrupt_grace: int = 5,
    second_interrupt_grace: int = 10,
    enable_turn_limits: bool = True,
    force_interrupt_enabled: bool = True,
    discussion_topic: str = "the current topic",
    off_topic_interrupt_threshold: int = 15,
    enable_topic_enforcement: bool = True,
    question_loader: Optional[QuestionLoader] = None,
    participant_manager: Optional[ParticipantManager] = None,
    survey_config: Optional[SurveyConfig] = None,
) -> AgentSession:
    """
    Create and configure the moderator agent session with aggressive interruption and topic tracking.

    Args:
        ctx: Job context from LiveKit
        instructions: Instructions for the agent
        stt_model: Speech-to-text model to use
        llm_model: Language model to use
        tts_model: Text-to-speech model to use
        tts_voice: Voice to use for TTS
        temperature: LLM temperature setting
        max_turn_duration: Maximum speaking time per turn (seconds)
        turn_warning_duration: When to give pre-warning (seconds)
        first_interrupt_grace: Grace time before first aggressive interrupt (seconds)
        second_interrupt_grace: Grace time before force-end (seconds)
        enable_turn_limits: Whether to enforce turn limits
        force_interrupt_enabled: Whether to force-end turns
        discussion_topic: Current discussion topic
        off_topic_interrupt_threshold: When to interrupt off-topic discussion (seconds)
        enable_topic_enforcement: Whether to enforce topic
        question_loader: Optional QuestionLoader for question-based moderation
        participant_manager: Optional ParticipantManager for random participant selection

    Returns:
        Configured AgentSession with aggressive interruption and topic tracking enabled
    """
    logger.info("Creating moderator agent session with graceful survey time management")
    logger.info(f"Room: {ctx.room.name}, SID: {ctx.room.sid}")
    logger.info(
        f"Survey time limits: base={max_turn_duration}s, "
        f"gentle_warning={max_turn_duration + first_interrupt_grace}s, "
        f"force_end={max_turn_duration + first_interrupt_grace + second_interrupt_grace}s, "
        f"enabled={enable_turn_limits}"
    )
    logger.info(
        f"Topic enforcement: topic='{discussion_topic}', "
        f"threshold={off_topic_interrupt_threshold}s, enabled={enable_topic_enforcement}"
    )

    # Create the moderator agent with aggressive interruption and topic tracking
    moderator = CommunityModeratorAgent(
        instructions=instructions,
        max_turn_duration=max_turn_duration,
        turn_warning_duration=turn_warning_duration,
        first_interrupt_grace=first_interrupt_grace,
        second_interrupt_grace=second_interrupt_grace,
        enable_turn_limits=enable_turn_limits,
        force_interrupt_enabled=force_interrupt_enabled,
        discussion_topic=discussion_topic,
        off_topic_interrupt_threshold=off_topic_interrupt_threshold,
        enable_topic_enforcement=enable_topic_enforcement,
        question_loader=question_loader,
        participant_manager=participant_manager,
        survey_config=survey_config,
    )

    # Store room context for disconnection
    moderator.ctx = ctx

    # Initialize LiveKit API for participant muting/unmuting
    import os
    livekit_url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    moderator.livekit_api = api.LiveKitAPI(livekit_url, api_key, api_secret)
    moderator.room_name = ctx.room.name
    logger.info("✅ LiveKit API initialized for participant muting control")

    # Setup the agent session with AI models (all OpenAI)
    # CRITICAL: Manual turn detection prevents automatic responses and hallucinations
    # We explicitly control all agent speech via generate_reply() calls

    logger.critical("=" * 80)
    logger.critical("🔧 AGENT CONFIGURATION")
    logger.critical(f"🤖 LLM Model: {llm_model}")
    logger.critical(f"🌡️  Temperature: {temperature} (MUST be 0.0 to prevent question generation)")
    logger.critical(f"🎤 STT Model: {stt_model}")
    logger.critical(f"🔊 TTS Model: {tts_model}")
    logger.critical(f"🗣️  TTS Voice: {tts_voice}")
    logger.critical("=" * 80)

    # Build survey vocabulary for STT prompt (only supported with whisper-1)
    # This helps improve transcription accuracy for expected responses
    survey_vocabulary_prompt = None
    if stt_model == "whisper-1" and question_loader:
        # Build a comprehensive list of expected response words from all questions
        all_options = set()
        for q in question_loader.questions:
            if hasattr(q, 'response_options') and q.response_options:
                for opt in q.response_options:
                    all_options.add(opt.strip())

        if all_options:
            # Create prompt with expected vocabulary
            options_list = ", ".join(sorted(all_options))
            survey_vocabulary_prompt = f"This is a survey with multiple choice responses. Expected answers include: {options_list}. Common responses: excellent, good, fair, poor, very poor, right track, wrong track, strongly agree, somewhat agree, somewhat disagree, strongly disagree, very likely, somewhat likely, not very likely, not at all likely, very safe, somewhat safe, not very safe, not at all safe."
            logger.info(f"STT prompt enabled for whisper-1 with {len(all_options)} expected options")

    # Configure STT based on provider
    if stt_provider == "deepgram":
        if not DEEPGRAM_AVAILABLE:
            raise RuntimeError("Deepgram provider selected but livekit-plugins-deepgram is not installed. Run: pip install livekit-plugins-deepgram")
        if not deepgram_api_key:
            raise ValueError("DEEPGRAM_API_KEY is required when using deepgram STT provider")

        # Determine model type for logging and features
        # Supported models: nova-2, nova-3 (keyterm prompting), flux-general-en (conversational)
        model_to_use = stt_model or "nova-2"
        is_flux_model = "flux" in model_to_use.lower()
        is_nova3_model = model_to_use.startswith("nova-3")
        supports_keyterms = is_nova3_model or is_flux_model  # Both Nova-3 and Flux support keyterms

        # Extract keywords for keyterm prompting (supported by Nova-3 AND Flux)
        keyterms = None
        if question_loader and supports_keyterms:
            try:
                keyword_extractor = KeywordExtractor(question_loader)
                keyterms = keyword_extractor.extract_keywords()
                logger.info(f"Extracted {len(keyterms)} keywords for Deepgram keyterm prompting")
                logger.info(f"Keywords (first 30): {', '.join(keyterms[:30])}")  # Log first 30 for debugging
            except Exception as e:
                logger.warning(f"Failed to extract keywords for STT: {e}. Continuing without keyterms.")
                keyterms = None

        if is_flux_model:
            logger.info(f"Using Deepgram Flux STT (conversational model with built-in turn detection): {model_to_use}")
        elif is_nova3_model:
            logger.info(f"Using Deepgram Nova-3 STT with keyterm prompting: {model_to_use}")
        else:
            logger.info(f"Using Deepgram STT with model: {model_to_use}")

        # Build STT configuration with enhanced parameters for better accuracy
        # Note: LiveKit's Deepgram plugin supports a subset of Deepgram API parameters
        stt_config = {
            "model": model_to_use,
            "api_key": deepgram_api_key,
            "interim_results": True,  # Interim results populate latest_user_response while user speaks, eliminating the gap between VAD silence detection and final transcript arrival
            "language": "en-US",  # Explicit language for better accuracy
            "punctuate": True,  # Add punctuation - helps with sentence structure
            "smart_format": True,  # Format numbers, dates, currency for readability
            "endpointing_ms": 500,  # Wait 500ms of silence before finalizing transcript (default 25ms is too aggressive)
        }

        # Add keyterms if available (supported by Nova-3 AND Flux)
        if keyterms and supports_keyterms:
            stt_config["keyterms"] = keyterms
            logger.info(f"Keyterms enabled with {len(keyterms)} terms")
            logger.debug(f"Keyterms: {keyterms[:20]}...")  # Log first 20 for debugging

        stt_instance = deepgram.STT(**stt_config)
    elif stt_provider == "google":
        # Google Cloud Speech-to-Text
        if not GOOGLE_AVAILABLE:
            raise RuntimeError(
                "Google provider selected but livekit-plugins-google is not available.\n"
                "Try: pip install --upgrade livekit-agents livekit-plugins-google\n"
                "Or switch to deepgram/openai STT provider in .env.local"
            )

        # Google Cloud STT models:
        # - "long" : For longer audio (optimized for accuracy)
        # - "short" : For short utterances (optimized for latency)
        # - "telephony" : For telephony audio
        # - "telephony_short" : For short telephony audio
        # - "medical_dictation" : Medical dictation
        # - "medical_conversation" : Medical conversation
        # - "latest_long" : Latest long-form model (best accuracy)
        # - "latest_short" : Latest short-form model (best latency)
        model_to_use = stt_model or "latest_short"

        # Extract keywords from survey for improved accuracy (similar to Deepgram keyterms)
        # Google STT uses keywords parameter: List[tuple[str, float]] - (phrase, boost_value)
        keywords_with_boost = None
        if question_loader:
            try:
                keyword_extractor = KeywordExtractor(question_loader)
                keywords = keyword_extractor.extract_keywords(max_keywords=500)  # Google supports up to 500 phrases
                # Convert to tuple format with boost value (15.0 is a good default boost)
                keywords_with_boost = [(kw, 15.0) for kw in keywords]
                logger.info(f"Extracted {len(keywords_with_boost)} keywords for Google Cloud STT")
                logger.info(f"Keywords (first 30): {', '.join(keywords[:30])}")
            except Exception as e:
                logger.warning(f"Failed to extract keywords for Google STT: {e}. Continuing without keywords.")
                keywords_with_boost = None

        logger.info(f"Using Google Cloud STT with model: {model_to_use}")

        # Build Google STT configuration
        # Note: Google Cloud credentials must be set via GOOGLE_APPLICATION_CREDENTIALS env var
        stt_config = {
            "model": model_to_use,
            "languages": "en-US",  # Single language string
            "interim_results": True,  # Interim results populate latest_user_response while user speaks
            "punctuate": True,  # Add punctuation
            "use_streaming": True,  # Enable streaming for real-time transcription
        }

        # Add keywords if available (phrase hints with boost values)
        if keywords_with_boost:
            stt_config["keywords"] = keywords_with_boost
            logger.info(f"Google STT keywords enabled with {len(keywords_with_boost)} phrases (boost=15.0)")

        stt_instance = google.STT(**stt_config)
    else:  # openai (default)
        # Configure STT with optional prompt (only for whisper-1)
        if stt_model == "whisper-1" and survey_vocabulary_prompt:
            stt_instance = openai.STT(model=stt_model, prompt=survey_vocabulary_prompt)
            logger.info("Using OpenAI whisper-1 with survey vocabulary prompt for improved accuracy")
        else:
            stt_instance = openai.STT(model=stt_model)
            logger.info(f"Using OpenAI {stt_model} for STT")

    # Configure TTS based on provider
    if tts_provider == "deepgram":
        if not DEEPGRAM_AVAILABLE:
            raise RuntimeError("Deepgram provider selected but livekit-plugins-deepgram is not installed. Run: pip install livekit-plugins-deepgram")
        if not deepgram_api_key:
            raise ValueError("DEEPGRAM_API_KEY is required when using deepgram TTS provider")

        # Deepgram TTS uses 'model' parameter, not 'voice'
        # The model name IS the voice:
        #   - Aura 1: aura-asteria-en, aura-arcas-en, etc.
        #   - Aura 2 (enterprise): aura-2-thalia-en, aura-2-orpheus-en, etc.
        tts_model_name = tts_voice or "aura-asteria-en"
        is_aura2 = "aura-2" in tts_model_name.lower()
        if is_aura2:
            logger.info(f"Using Deepgram Aura-2 TTS (enterprise-grade, sub-200ms latency): {tts_model_name}")
        else:
            logger.info(f"Using Deepgram Aura TTS: {tts_model_name}")
        tts_instance = deepgram.TTS(
            model=tts_model_name,
            api_key=deepgram_api_key,
        )
    elif tts_provider == "elevenlabs":
        if not ELEVENLABS_AVAILABLE:
            raise RuntimeError(
                "ElevenLabs provider selected but livekit-plugins-elevenlabs is not installed. "
                "Run: pip install livekit-plugins-elevenlabs"
            )
        logger.info(f"Using ElevenLabs TTS with voice ID: {tts_voice}")
        tts_instance = elevenlabs.TTS(voice_id=tts_voice)
    else:  # openai
        logger.info(f"Using OpenAI TTS with model: {tts_model}, voice: {tts_voice}")
        tts_instance = openai.TTS(
            model=tts_model,
            voice=tts_voice,
        )

    # Configure VAD with echo-barge-in prevention:
    #
    # activation_threshold=0.5 (Silero default)
    #   Previous value 0.35 was too sensitive — the agent's own TTS leaked
    #   through BVC echo cancellation and was classified as user speech,
    #   causing the moderator to stutter or stop mid-sentence.  0.5 requires
    #   higher confidence before firing, filtering out residual echo while
    #   still detecting normal-volume speech reliably.
    #
    # min_speech_duration=0.15s
    #   Raised from 0.1s.  Brief echo spikes from TTS playback are typically
    #   50-120ms; requiring 150ms of sustained speech avoids false triggers
    #   from echo remnants while still being responsive to real speech.
    #
    # prefix_padding_duration=0.6s
    #   Slightly above default 0.5s.  Gives BVC an extra 100ms to suppress
    #   echo before the buffered audio reaches the VAD decision window.
    #
    # min_silence_duration=0.55s (Silero default)
    #   Base value; dynamically adjusted per question type before each turn.
    #   Previous 0.4s was too aggressive during agent TTS — brief pauses
    #   between words fired end-of-speech prematurely.
    vad_instance = silero.VAD.load(
        min_silence_duration=0.55,
        min_speech_duration=0.15,
        prefix_padding_duration=0.6,
        activation_threshold=0.5,
    )
    logger.info(
        "VAD configured: min_silence=0.55s, min_speech=0.15s, "
        "prefix_pad=0.6s, activation=0.5 (echo-barge-in hardened)"
    )

    session = AgentSession(
        stt=stt_instance,
        llm=openai.LLM(
            model=llm_model,
            temperature=temperature,  # CRITICAL: Must be 0.0 to prevent question generation
        ),
        tts=tts_instance,
        vad=vad_instance,
        turn_detection="server_vad",  # Enable turn detection for event handling (FIXED: was "manual")
        allow_interruptions=True,               # Session default; per-call overrides for warnings/acks
        min_interruption_duration=0.5,          # Min user speech to trigger interrupt
        discard_audio_if_uninterruptible=True,  # Drop user audio during non-interruptible TTS
    )

    # Set up room event handlers for participant management BEFORE starting session
    @ctx.room.on("participant_connected")
    def on_participant_connected(participant):
        """Track new participants for question-based moderation."""
        from livekit.rtc import ParticipantKind

        # Skip the agent itself (check if it's a local participant or an agent)
        if hasattr(participant, 'is_local') and participant.is_local:
            return

        # Skip other agents (kind == AGENT or identity starts with 'agent')
        if hasattr(participant, 'kind') and participant.kind == ParticipantKind.PARTICIPANT_KIND_AGENT:
            logger.info(f"Skipping agent participant: {participant.identity}")
            return
        if participant.identity.startswith('agent'):
            logger.info(f"Skipping agent participant (by identity): {participant.identity}")
            return

        # Skip avatar — it is not a survey participant
        if moderator._is_avatar_identity(participant.identity):
            logger.info(f"AVATAR_LIFECYCLE participant_connected skipped for avatar: {participant.identity}")
            return

        logger.info(f"Participant connected: {participant.identity} (name: {participant.name})")
        if moderator.participant_manager:
            # Pass both identity and display name from LiveKit token
            moderator.participant_manager.add_participant(participant.identity, display_name=participant.name)

        # Initialize audio activity tracking
        moderator.participant_audio_activity[participant.identity] = None

    @ctx.room.on("track_published")
    def on_track_published(publication, participant):
        """Subscribe to audio tracks from all participants (including late joiners).
        Skip avatar tracks — we do not want STT/moderation logic on avatar audio."""
        from livekit.rtc import TrackKind

        # Never subscribe STT to avatar audio tracks
        if moderator._is_avatar_identity(participant.identity):
            logger.info(f"AVATAR_LIFECYCLE track_published skipped for avatar: {participant.identity} (kind={publication.kind})")
            return

        logger.critical(f"🔥 TRACK_PUBLISHED EVENT: {participant.identity}, kind={publication.kind}, sid={publication.sid}")
        if publication.kind == TrackKind.KIND_AUDIO:
            logger.critical(f"🎤 Audio track published by {participant.identity}, subscribing...")
            publication.set_subscribed(True)
            logger.critical(f"✅ Subscribed to {participant.identity}'s audio track")
        else:
            logger.info(f"  Non-audio track published: kind={publication.kind}")

    @ctx.room.on("track_unmuted")
    def on_track_unmuted(publication, participant):
        """Track when a participant starts speaking (unmutes their audio).
        Skip avatar — must never be treated as actual_respondent."""
        from livekit.rtc import TrackKind

        identity = getattr(participant, 'identity', None)
        if identity is None:
            identity = getattr(getattr(publication, 'participant', None), 'identity', None)
        if identity is None:
            logger.debug(f"track_unmuted: could not resolve participant identity, skipping")
            return

        if moderator._is_avatar_identity(identity):
            return  # Avatar unmute is irrelevant to survey logic

        if publication.kind == TrackKind.KIND_AUDIO:
            logger.critical(f"🎙️  AUDIO ACTIVE: {identity} unmuted (track: {publication.sid})")
            moderator.participant_audio_activity[identity] = datetime.now()
            moderator.actual_respondent = identity
            moderator.active_speaker_track_sid = publication.sid

    @ctx.room.on("track_muted")
    def on_track_muted(publication, participant):
        """Track when a participant stops speaking (mutes their audio)."""
        from livekit.rtc import TrackKind

        identity = getattr(participant, 'identity', None)
        if identity is None:
            identity = getattr(getattr(publication, 'participant', None), 'identity', None)
        if identity is None:
            logger.debug(f"track_muted: could not resolve participant identity, skipping")
            return

        if moderator._is_avatar_identity(identity):
            return  # Avatar mute is irrelevant to survey logic

        if publication.kind == TrackKind.KIND_AUDIO:
            logger.info(f"🔇 Audio muted: {identity} (track: {publication.sid})")
            # Don't clear actual_respondent yet - we might still get STT results

    # ── Step 1: Start the moderator session FIRST ─────────────────────────
    # The session must be fully started before the avatar can bind to it.
    # Previous ordering (avatar first) caused the avatar to start against an
    # un-started session, leading to playback sync errors and mid-session crashes.
    await session.start(
        room=ctx.room,
        agent=moderator,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
            close_on_disconnect=False,  # Don't close session if participant goes "away"
        ),
    )

    # Store session reference in agent for question-based moderation
    moderator.agent_session = session
    logger.info("AVATAR_LIFECYCLE session.start() completed — session is ready")

    # ── Step 2: Start Anam avatar AFTER session is ready ─────────────────
    anam_avatar_id = os.environ.get("ANAM_AVATAR_ID")

    if moderator._audio_only_mode:
        logger.info("AVATAR_LIFECYCLE AUDIO_ONLY_MODE enabled — skipping avatar entirely")
        moderator._set_avatar_state(AVATAR_STATE_IDLE, reason="audio_only_mode")
    elif ANAM_AVAILABLE and anam_avatar_id:
        logger.info(f"AVATAR_LIFECYCLE starting avatar_id={anam_avatar_id} (session already started)")
        moderator._set_avatar_state(AVATAR_STATE_STARTING)
        try:
            avatar = anam.AvatarSession(
                persona_config=anam.PersonaConfig(
                    name="Survey Moderator Avatar",
                    avatarId=anam_avatar_id,
                ),
            )
            await avatar.start(session, room=ctx.room)
            moderator._avatar_session_ref = avatar
            moderator._avatar_enabled = True
            moderator._avatar_connected = True
            moderator._set_avatar_state(AVATAR_STATE_CONNECTED)
        except Exception as e:
            moderator._set_avatar_state(AVATAR_STATE_FAILED, reason=str(e))
            moderator._avatar_enabled = False
            moderator._avatar_connected = False
            # Downgrade to audio-only mode on startup failure
            moderator._audio_only_mode = True
            logger.warning(
                "Anam avatar failed to start — downgrading to audio-only mode: %s",
                e,
                exc_info=False,
            )
            logger.warning(
                "Check that ANAM_AVATAR_ID exists in your Anam Lab account and matches the API key. "
                "See https://lab.anam.ai/avatars or the Anam avatar gallery for valid IDs."
            )
    else:
        if not ANAM_AVAILABLE:
            logger.info("AVATAR_LIFECYCLE plugin not installed — running without avatar")
        elif not anam_avatar_id:
            logger.info("AVATAR_LIFECYCLE ANAM_AVATAR_ID not set — running without avatar")
        moderator._set_avatar_state(AVATAR_STATE_IDLE, reason="not_configured")

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant):
        """Handle participant disconnection with grace period."""
        # The Anam avatar is not a survey participant — log but skip roster management
        if moderator._is_avatar_identity(participant.identity):
            moderator._avatar_disconnect_time = _time_mod.time()
            moderator._set_avatar_state(
                AVATAR_STATE_DISCONNECTED,
                reason=f"participant_disconnected:{participant.identity}",
            )

            # ── Optional single-flight reconnect ────────────────────────────
            if (
                moderator._avatar_reconnect_enabled
                and not moderator._shutting_down
                and not moderator._audio_only_mode
                and ANAM_AVAILABLE
                and anam_avatar_id
                and (moderator._avatar_reconnect_task is None
                     or moderator._avatar_reconnect_task.done())
            ):
                async def _attempt_avatar_reconnect():
                    _MAX_RECONNECT_ATTEMPTS = 2
                    _RECONNECT_BACKOFF = 5.0  # seconds between attempts
                    while moderator._avatar_reconnect_attempts < _MAX_RECONNECT_ATTEMPTS:
                        if moderator._shutting_down:
                            logger.info("AVATAR_LIFECYCLE reconnect aborted — shutting down")
                            return
                        moderator._avatar_reconnect_attempts += 1
                        moderator._set_avatar_state(AVATAR_STATE_RECONNECTING)
                        logger.info(
                            f"AVATAR_LIFECYCLE reconnect attempt "
                            f"{moderator._avatar_reconnect_attempts}/{_MAX_RECONNECT_ATTEMPTS}"
                        )
                        try:
                            new_avatar = anam.AvatarSession(
                                persona_config=anam.PersonaConfig(
                                    name="Survey Moderator Avatar",
                                    avatarId=anam_avatar_id,
                                ),
                            )
                            await new_avatar.start(session, room=ctx.room)
                            moderator._avatar_session_ref = new_avatar
                            moderator._set_avatar_state(AVATAR_STATE_CONNECTED, reason="reconnected")
                            # Notify participants of successful reconnect
                            if not moderator._shutting_down:
                                await moderator._safe_say(
                                    AVATAR_RECONNECT_TEXT,
                                    allow_interruptions=False,
                                    context="avatar_reconnect_success",
                                )
                            return
                        except Exception as exc:
                            logger.warning(f"AVATAR_LIFECYCLE reconnect failed: {exc}")
                            await asyncio.sleep(_RECONNECT_BACKOFF)

                    # Exhausted retries — downgrade to audio-only mode
                    moderator._set_avatar_state(
                        AVATAR_STATE_FAILED,
                        reason=f"reconnect_exhausted_after_{moderator._avatar_reconnect_attempts}_attempts",
                    )
                    moderator._audio_only_mode = True
                    logger.info("AVATAR_LIFECYCLE downgraded to audio-only mode after reconnect exhaustion")

                moderator._avatar_reconnect_task = asyncio.create_task(
                    _attempt_avatar_reconnect()
                )

            # ── Deferred disconnect notice ──────────────────────────────────
            # Dedupe cooldown — at most one notice every 60s
            _AVATAR_NOTICE_COOLDOWN = 60
            _last = moderator._last_avatar_disconnect_notice
            if (_time_mod.time() - _last) < _AVATAR_NOTICE_COOLDOWN:
                logger.info(
                    f"AVATAR_LIFECYCLE disconnect notice suppressed — "
                    f"last notice {_time_mod.time() - _last:.0f}s ago (cooldown={_AVATAR_NOTICE_COOLDOWN}s)"
                )
                return

            # Cancel any existing notice task before creating a new one
            moderator._cancel_avatar_tasks()

            async def _notify_avatar_disconnect_deferred():
                _MAX_WAIT = 15  # seconds — don't hold the notice forever
                _POLL = 0.25
                _waited = 0.0

                # Wait for a safe window
                while _waited < _MAX_WAIT:
                    # Abort if survey is completing, shut down, or avatar reconnected
                    if moderator._shutting_down or moderator.survey_state in (SurveyState.COMPLETED,):
                        logger.info("AVATAR_LIFECYCLE disconnect notice skipped — session ending")
                        return
                    if moderator._avatar_state == AVATAR_STATE_CONNECTED:
                        logger.info("AVATAR_LIFECYCLE disconnect notice skipped — avatar reconnected")
                        return

                    # Safe when: not speaking, no warning TTS, no question TTS in flight
                    if (not moderator.user_currently_speaking
                            and not moderator._gentle_warning_in_progress
                            and not moderator._tts_active):
                        break

                    await asyncio.sleep(_POLL)
                    _waited += _POLL

                # Final checks
                if moderator._shutting_down or moderator.survey_state in (SurveyState.COMPLETED,):
                    logger.info("AVATAR_LIFECYCLE disconnect notice skipped — session ending")
                    return
                if moderator._avatar_state == AVATAR_STATE_CONNECTED:
                    logger.info("AVATAR_LIFECYCLE disconnect notice skipped — avatar reconnected")
                    return

                moderator._last_avatar_disconnect_notice = _time_mod.time()
                await moderator._safe_say(
                    AVATAR_DISCONNECT_TEXT,
                    allow_interruptions=False,
                    context="avatar_disconnect_notice",
                )
                logger.info(f"AVATAR_LIFECYCLE disconnect notice delivered after {_waited:.1f}s wait")

            moderator._avatar_notice_task = asyncio.create_task(
                _notify_avatar_disconnect_deferred()
            )
            return

        logger.warning(f"⚠️  Participant disconnected: {participant.identity}")

        if moderator.participant_manager:
            # Mark as disconnected (starts grace period)
            moderator.participant_manager.mark_participant_disconnected(participant.identity)

            # Schedule removal after grace period
            async def remove_after_grace_period():
                await asyncio.sleep(moderator.participant_manager.disconnect_grace_period)
                if participant.identity not in moderator.participant_manager.pending_disconnects:
                    logger.info(
                        f"Skipping delayed removal for {participant.identity}: no longer pending disconnect."
                    )
                    return
                moderator.participant_manager.remove_participant(participant.identity, immediate=False)

            # Run in background
            asyncio.create_task(remove_after_grace_period())

    @ctx.room.on("data_received")
    def on_data_received(data_packet):
        """Handle data channel messages from Web UI (observer commands via button clicks)."""
        try:
            # Parse the data packet
            data = data_packet.data.decode('utf-8') if isinstance(data_packet.data, bytes) else str(data_packet.data)
            sender = getattr(data_packet, 'participant', None)
            sender_identity = sender.identity if sender else 'unknown'

            logger.critical(f"📨 DATA CHANNEL received from {sender_identity}: {data}")

            # Try to parse as JSON
            import json
            try:
                message = json.loads(data)
                command = message.get('command', '').lower()
                msg_type = message.get('type', '')
            except json.JSONDecodeError:
                # Plain text command
                command = data.lower().strip()
                msg_type = 'command'

            # Only process observer commands if observer mode is enabled
            if moderator.observer_mode_enabled and msg_type == 'observer_command':
                observer_identity = moderator.participant_manager.get_observer_identity() if moderator.participant_manager else None

                logger.critical(f"👁️ DATA CHANNEL COMMAND: '{command}' from {sender_identity}")
                logger.critical(f"   Observer identity: {observer_identity}")
                logger.critical(f"   Survey state: {moderator.survey_state}")

                # Verify sender is the observer
                if sender_identity and observer_identity and sender_identity == observer_identity:
                    if command in ['start', 'start survey', 'begin']:
                        logger.critical(f"✅ OBSERVER START COMMAND via DATA CHANNEL from {sender_identity}")
                        asyncio.create_task(moderator.handle_observer_command('start'))
                    elif command in ['pause', 'hold']:
                        logger.critical(f"✅ OBSERVER PAUSE COMMAND via DATA CHANNEL from {sender_identity}")
                        asyncio.create_task(moderator.handle_observer_command('pause'))
                    elif command in ['resume', 'continue']:
                        logger.critical(f"✅ OBSERVER RESUME COMMAND via DATA CHANNEL from {sender_identity}")
                        asyncio.create_task(moderator.handle_observer_command('resume'))
                    else:
                        logger.warning(f"❓ Unknown observer command: {command}")
                else:
                    logger.warning(f"❌ DATA CHANNEL command rejected - sender '{sender_identity}' is not observer '{observer_identity}'")
        except Exception as e:
            logger.error(f"Error processing data channel message: {e}")

    # NEW: Track STT transcripts directly (bypassing conversation context)
    moderator.pending_stt_transcript = None

    # Helper function to capture user response immediately after they stop speaking
    async def _capture_user_response_immediately():
        """
        Check for pending STT transcript and capture it immediately.
        Since we bypass LLM flow, transcripts won't be in conversation context.
        """
        # Wait briefly for STT to deliver final fragment after silence detected
        await asyncio.sleep(0.5)

        if not moderator.participant_manager or not moderator.current_question_num:
            return

        # GENTLE WARNING GUARD: Do not capture while the warning TTS is playing.
        # The user naturally pauses to listen and we must not treat that as
        # "finished speaking."  The gentle-warning code will clear the response
        # buffer and reset timers once the warning finishes.
        if moderator._gentle_warning_in_progress:
            logger.info("⏳ Gentle warning in progress — skipping immediate capture")
            return

        # ANALYSIS FREEZE GUARD: While LLM analysis is running on a snapshot,
        # do not overwrite latest_user_response — the polling loop is working
        # with the snapshot and will reset state itself when done.
        if moderator._analysis_in_progress:
            logger.info("🔒 Analysis in progress — skipping immediate capture to preserve snapshot")
            return

        # Check if already captured (avoid duplicates)
        if moderator.response_captured:
            logger.info("Response already captured, skipping duplicate check")
            return

        # ── DELIVERY-STATE GUARD ─────────────────────────────────────
        # Only accept a response if the question has been fully spoken
        # to the expected participant.  Transcripts that arrive during
        # the "delivering" state are stale spillover from the previous
        # turn and must be discarded.
        expected = moderator.expected_respondent
        if expected and moderator.current_question_num:
            if not moderator._is_delivery_confirmed(moderator.current_question_num, expected):
                ds_key = moderator._delivery_key(moderator.current_question_num, expected)
                current_ds = moderator.question_delivery_state.get(ds_key, "unknown")
                logger.warning(
                    f"🛡️ DELIVERY GUARD (immediate_capture): Discarding transcript — "
                    f"delivery_state={current_ds} for Q#{moderator.current_question_num}/{expected} "
                    f"(need 'delivered')"
                )
                # #region agent log
                import json as _json
                _debug_log_write(_json.dumps({"location": "moderator_agent.py:immediate_capture:delivery_guard", "message": "Delivery guard blocked capture", "data": {"delivery_state": current_ds, "expected": expected, "question_num": moderator.current_question_num, "pending_transcript": str(moderator.pending_stt_transcript)[:100] if moderator.pending_stt_transcript else None}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "H_CAPTURE_GUARD"}))
                # #endregion
                moderator.pending_stt_transcript = None
                return
        # ─────────────────────────────────────────────────────────────

        # Check for unanswered participants
        current_participants = moderator.participant_manager.participants
        answered_participants = moderator.participant_manager.asked_participants.get(moderator.current_question_num, [])
        unanswered = [p for p in current_participants if p not in answered_participants]

        if not unanswered:
            return

        participant_id = unanswered[0]

        # METHOD 1: Check pending STT transcript (set by STT event handler)
        # CRITICAL FIX: Skip this if latest_user_response already populated by fragment accumulation
        # Otherwise we'd overwrite the accumulated fragments with just the last fragment!
        if moderator.latest_user_response:
            logger.debug("Skipping immediate capture - latest_user_response already populated by fragment accumulation")
            return

        if moderator.pending_stt_transcript:
            transcript = moderator.pending_stt_transcript
            moderator.pending_stt_transcript = None  # Clear after reading

            logger.critical(f"⚡ IMMEDIATE CAPTURE: Got transcript from STT event!")
            logger.critical(f"📝 Raw transcript: {participant_id}, text: '{transcript[:50]}...'")

            # Apply STT correction - handle multi-option questions differently
            corrected_text = transcript
            if moderator.current_question_object and moderator.current_question_object.response_options:
                max_sel = moderator.current_question_object.max_selections or 1
                if max_sel > 1:
                    # Multi-option question - parse and match multiple responses
                    corrected_text = parse_multi_option_response(
                        transcript,
                        moderator.current_question_object.response_options,
                        max_sel
                    )
                else:
                    # Single option question - use standard correction
                    corrected_text = correct_transcription(transcript, moderator.current_question_object.response_options)
                if corrected_text != transcript:
                    logger.info(f"Response corrected: '{transcript}' → '{corrected_text}'")

            # Store in latest_user_response for polling code to handle
            # The polling code will log to JSON/CSV and mark as answered
            moderator.latest_user_response = corrected_text

            # Log to audit
            moderator.audit_logger.log_stt_text(transcript)

            # Set flag so polling knows response was captured
            moderator.response_captured = True
            logger.critical(f"💾 Stored in latest_user_response (polling will handle logging/marking)")
            return

        # METHOD 2: Fallback - Check conversation context (may not work when bypassing LLM)
        user_messages = [item for item in session._chat_ctx.items if hasattr(item, 'role') and item.role == 'user']

        if not user_messages:
            logger.debug("No user messages in context yet (expected when bypassing LLM flow)")
            return

        # Get the most recent user message
        latest_user_msg = user_messages[-1]

        # Extract transcript
        if hasattr(latest_user_msg, 'content'):
            if isinstance(latest_user_msg.content, list):
                transcript = " ".join(str(c) for c in latest_user_msg.content)
            else:
                transcript = str(latest_user_msg.content)
        else:
            transcript = str(latest_user_msg)

        logger.critical(f"⚡ IMMEDIATE CAPTURE: Got transcript from context (fallback method)")
        logger.critical(f"📝 Raw transcript: {participant_id}, text: '{transcript[:50]}...'")

        # Apply STT correction - handle multi-option questions differently
        corrected_text = transcript
        if moderator.current_question_object and moderator.current_question_object.response_options:
            max_sel = moderator.current_question_object.max_selections or 1
            if max_sel > 1:
                # Multi-option question - parse and match multiple responses
                corrected_text = parse_multi_option_response(
                    transcript,
                    moderator.current_question_object.response_options,
                    max_sel
                )
            else:
                # Single option question - use standard correction
                corrected_text = correct_transcription(transcript, moderator.current_question_object.response_options)
            if corrected_text != transcript:
                logger.info(f"Response corrected: '{transcript}' → '{corrected_text}'")

        # Store in latest_user_response for polling code to handle
        # The polling code will log to JSON/CSV and mark as answered
        moderator.latest_user_response = corrected_text

        # Log to audit
        moderator.audit_logger.log_stt_text(transcript)

        # Set flag so polling knows response was captured
        moderator.response_captured = True
        logger.critical(f"💾 Stored in latest_user_response (polling will handle logging/marking)")

    # Set up event handlers for turn tracking and topic enforcement
    # PRIMARY EVENT: user_input_transcribed - This is the correct event!

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event):
        """
        Called when user speech is transcribed (STT complete).
        This event ALWAYS fires, even when bypassing LLM conversation flow!
        Accumulates multiple fragments for multi-part answers.
        """
        logger.critical(f"🔥 EVENT FIRED: user_input_transcribed")

        # ── WELCOME-PHASE GUARD ──────────────────────────────────────────
        # During welcome/greeting/wait, STT is active but transcripts are
        # meaningless chatter ("okay", "hello").  Discard them so they
        # never pollute response_fragments / latest_user_response.
        if moderator.survey_state == SurveyState.WELCOME:
            # Extract transcript for logging only
            _raw = getattr(event, 'transcript', None) or getattr(event, 'text', None) or str(event)
            logger.info(
                f"🛡️ WELCOME-PHASE GUARD: Discarding user transcript during welcome/greeting: '{str(_raw)[:80]}'"
            )
            # #region agent log
            import json as _json
            _debug_log_write(_json.dumps({"location": "moderator_agent.py:user_input_transcribed:welcome_guard", "message": "WELCOME guard fired — discarding transcript", "data": {"transcript": str(_raw)[:200], "survey_state": moderator.survey_state.value}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "A"}))
            # #endregion
            return
        # ─────────────────────────────────────────────────────────────────

        # AUDIO ROUTING TRACE: Show which participant's audio was transcribed
        logger.critical("🔍 STT EVENT TRACE:")
        current_stt_participant = moderator._current_stt_participant or 'UNKNOWN'
        logger.critical(f"   STT was listening to: {current_stt_participant}")
        logger.critical(f"   Expected respondent: {moderator.expected_respondent}")
        logger.critical(f"   Actual respondent (from track_unmuted): {moderator.actual_respondent}")

        # Never assign avatar identity as actual_respondent
        if moderator._is_avatar_identity(current_stt_participant):
            logger.info(f"AVATAR_LIFECYCLE ignoring STT from avatar identity: {current_stt_participant}")
            return

        if current_stt_participant and current_stt_participant != 'UNKNOWN':
            moderator.actual_respondent = current_stt_participant
            if current_stt_participant != moderator.expected_respondent:
                logger.warning(f"STT MISMATCH: Listening to {current_stt_participant} but expecting {moderator.expected_respondent}")

        # Extract transcript from UserInputTranscribedEvent
        transcript = ""
        if hasattr(event, 'transcript'):
            transcript = event.transcript
        elif hasattr(event, 'text'):
            transcript = event.text
        else:
            transcript = str(event)

        logger.critical(f"📝 USER TRANSCRIPT CAPTURED via user_input_transcribed: '{transcript[:100]}'")

        # Guard against STT spillover from previous turn transition
        if hasattr(moderator, 'turn_transition_time') and moderator.turn_transition_time:
            elapsed_since_transition = (datetime.now() - moderator.turn_transition_time).total_seconds()
            if elapsed_since_transition < 2.0:
                logger.warning(
                    f"⚠️ Discarding STT transcript arrived {elapsed_since_transition:.1f}s after turn transition "
                    f"(likely spillover from previous participant): '{transcript[:60]}'"
                )
                return

        # NETWORK LATENCY TRACKING: Calculate time from speaking start to first STT
        # IMPORTANT: Only track INITIAL latency (first STT after speaking starts)
        # Inter-fragment gaps are usually user pauses, NOT network latency
        if not moderator.first_stt_received and moderator.speaking_start_time:
            stt_latency_ms = (datetime.now() - moderator.speaking_start_time).total_seconds() * 1000
            moderator.first_stt_received = True
            moderator.observed_stt_latency_ms = stt_latency_ms

            # Track maximum latency for session
            if stt_latency_ms > moderator.max_observed_latency_ms:
                moderator.max_observed_latency_ms = stt_latency_ms

        # Log inter-fragment delays for debugging only
        # Large gaps between fragments are usually user pauses, not network issues
        if moderator.last_fragment_time:
            fragment_gap_ms = (datetime.now() - moderator.last_fragment_time).total_seconds() * 1000
            if fragment_gap_ms > 5000:  # Only log gaps > 5 seconds
                logger.debug(f"📡 Gap between STT fragments: {fragment_gap_ms:.0f}ms (user pause, not adjusting buffer)")

        # OBSERVER COMMAND DETECTION: Check if this is an observer command
        # Only process FINAL transcripts to avoid acting on interim partials
        _is_final = getattr(event, 'is_final', True)
        if moderator.observer_mode_enabled and _is_final:
            is_command, command_type = is_observer_command(transcript)

            if is_command:
                # This looks like a command - now verify the speaker is the observer
                speaker_identity = moderator.actual_respondent
                observer_identity = moderator.participant_manager.get_observer_identity()

                logger.critical(f"👁️ POTENTIAL COMMAND '{command_type}' detected in: '{transcript}'")
                logger.critical(f"   Speaker (actual_respondent): {speaker_identity}")
                logger.critical(f"   Observer identity: {observer_identity}")
                logger.critical(f"   Survey state: {moderator.survey_state}")

                # SPECIAL CASE: In WAITING_FOR_OBSERVER state, STT is set to listen ONLY to observer
                # So if we receive a transcript in this state, it MUST be from the observer
                # (even if actual_respondent is None due to track_unmuted not firing)
                if moderator.survey_state == SurveyState.WAITING_FOR_OBSERVER:
                    if observer_identity and command_type == "start":
                        logger.critical(f"✅ OBSERVER START COMMAND (WAITING state) - accepting from observer")
                        asyncio.create_task(moderator.handle_observer_command(command_type))
                        return  # Don't process as regular response

                # SPECIAL CASE: In PAUSED state, STT is set to listen ONLY to observer
                # So if we receive a transcript in this state, it MUST be from the observer
                if moderator.survey_state == SurveyState.PAUSED:
                    if observer_identity and command_type == "resume":
                        logger.critical(f"✅ OBSERVER RESUME COMMAND (PAUSED state) - accepting from observer")
                        asyncio.create_task(moderator.handle_observer_command(command_type))
                        return  # Don't process as regular response

                # Check if STT is currently set to observer (means any transcript is from observer)
                current_stt_participant = moderator._current_stt_participant

                # If STT is listening to observer, accept the command (we set it that way while asking questions)
                if current_stt_participant and observer_identity and current_stt_participant == observer_identity:
                    logger.critical(f"✅ OBSERVER COMMAND CONFIRMED (STT on observer): {command_type}")
                    asyncio.create_task(moderator.handle_observer_command(command_type))
                    return  # Don't process as regular response

                # STRICT CHECK for pause/resume: Only process if speaker is EXACTLY the observer
                # This is for cases when STT is listening to everyone
                if speaker_identity and observer_identity and speaker_identity == observer_identity:
                    logger.critical(f"✅ OBSERVER COMMAND CONFIRMED (by speaker): {command_type} from {speaker_identity}")
                    asyncio.create_task(moderator.handle_observer_command(command_type))
                    return  # Don't process as regular response
                else:
                    # NOT the observer - ignore the command-like phrase
                    logger.critical(f"❌ IGNORING command - speaker '{speaker_identity}' is NOT the observer '{observer_identity}'")
                    # Continue processing as a normal participant response

        # ── Simplified fragment tracking (Phase 1) ─────────────────────
        # The primary response capture is now in on_user_speech_committed.
        # This handler only updates latest_user_response as a legacy fallback
        # and maintains tracking variables.
        import time as _time_mod

        new_fragment = transcript.strip()
        if not new_fragment:
            return

        # Detect utterance boundary: when fragment length drops significantly,
        # STT has started a new utterance rather than extending the previous one.
        # Accumulate the previous utterance so multi-utterance answers aren't lost.
        _prev = moderator.last_stt_fragment or ""
        if _prev and len(new_fragment) < len(_prev) * 0.6 and len(_prev) > 5:
            # Previous utterance complete — accumulate it
            if moderator._turn_accumulated_text:
                moderator._turn_accumulated_text += " " + _prev
            else:
                moderator._turn_accumulated_text = _prev

        # Full turn response = accumulated previous utterances + current fragment
        if moderator._turn_accumulated_text:
            moderator.latest_user_response = moderator._turn_accumulated_text + " " + new_fragment
        else:
            moderator.latest_user_response = new_fragment

        moderator.pending_stt_transcript = transcript
        moderator.last_stt_fragment = new_fragment
        moderator.last_fragment_time = datetime.now()
        if moderator._first_fragment_time is None:
            moderator._first_fragment_time = datetime.now()

        # Update silence watchdog progress tracker
        moderator._last_transcript_progress_time = _time_mod.time()
        moderator._had_stt_transcript_this_turn = True
        logger.info(f"STT fragment ({len(new_fragment)} chars): '{new_fragment[:80]}'")

    # PRIMARY EVENT: user_speech_committed — fires after VAD silence under server_vad
    @session.on("user_speech_committed")
    def on_user_speech_committed(message):
        """Primary handler: fires when VAD confirms the user finished speaking.

        Single write to captured_response + _response_ready.set() drives
        the _await_response() loop.
        """
        import time as _time_mod

        logger.critical(f"EVENT FIRED: user_speech_committed")

        # Extract transcript text
        transcript = ""
        if hasattr(message, 'alternatives') and len(message.alternatives) > 0:
            transcript = message.alternatives[0].text
        elif hasattr(message, 'text'):
            transcript = message.text
        elif hasattr(message, 'transcript'):
            transcript = message.transcript
        else:
            transcript = str(message)

        if not transcript or not transcript.strip():
            return

        transcript = transcript.strip()

        # ── Welcome-phase guard ──
        if moderator.survey_state == SurveyState.WELCOME:
            logger.info(f"WELCOME guard (committed): discarding '{transcript[:80]}'")
            return

        # ── TTS echo guard ──
        if moderator._tts_active:
            logger.info(f"ECHO guard (committed): discarding during TTS: '{transcript[:60]}'")
            return

        # ── Delivery-state guard ──
        _expected = moderator.expected_respondent
        if _expected and moderator.current_question_num:
            if not moderator._is_delivery_confirmed(moderator.current_question_num, _expected):
                _ds_key = moderator._delivery_key(moderator.current_question_num, _expected)
                _ds = moderator.question_delivery_state.get(_ds_key, "unknown")
                logger.warning(
                    f"DELIVERY GUARD (committed): Discarding — "
                    f"delivery_state={_ds} for Q#{moderator.current_question_num}/{_expected}"
                )
                return

        # ── Apply STT correction if response options exist ──
        corrected = transcript
        if moderator.current_question_object and moderator.current_question_object.response_options:
            corrected = correct_transcription(
                transcript, moderator.current_question_object.response_options)
            if corrected != transcript:
                logger.info(f"STT correction (committed): '{transcript[:50]}' -> '{corrected[:50]}'")

        # ── Include accumulated text from earlier utterances in this turn ──
        if moderator._turn_accumulated_text:
            corrected = f"{moderator._turn_accumulated_text} {corrected}"
            moderator._turn_accumulated_text = ""  # Reset after incorporation
        # ── Single write ──
        moderator.captured_response = corrected
        moderator._response_ready.set()

        # Also update legacy variables for compatibility
        moderator.latest_user_response = corrected
        moderator.pending_stt_transcript = corrected
        moderator.response_captured = True

        # Update tracking
        moderator._last_transcript_progress_time = _time_mod.time()
        moderator._had_stt_transcript_this_turn = True
        if moderator._first_fragment_time is None:
            moderator._first_fragment_time = datetime.now()

        logger.critical(f"COMMITTED response ({len(corrected)} chars): '{corrected[:120]}'")

        # Audit logging
        moderator.audit_logger.log_stt_text(transcript)

    @session.on("user_state_changed")
    def on_user_state_changed(event):
        """Called when user state changes (speaking/listening/away)"""
        logger.critical(f"🔥 EVENT FIRED: user_state_changed - {event.old_state} -> {event.new_state}")
        print(f"🔥 EVENT FIRED: user_state_changed - {event.old_state} -> {event.new_state}")
        moderator._last_user_state = event.new_state

        # ── ECHO BARGE-IN GUARD ──────────────────────────────────────────
        # While the agent's TTS is actively playing (_tts_active or
        # _gentle_warning_in_progress), a VAD "speaking" transition is
        # almost certainly the microphone picking up the agent's own audio
        # (echo) rather than the participant.  Suppress the state change to
        # prevent the agent from interrupting itself.
        if event.new_state == "speaking" and (moderator._tts_active or moderator._gentle_warning_in_progress):
            logger.info(
                f"🔇 ECHO GUARD: suppressing VAD speaking event — "
                f"tts_in_flight={moderator._tts_active}, "
                f"gentle_warning={moderator._gentle_warning_in_progress}"
            )
            return  # Discard this event entirely

        # Track speaking state for polling
        if event.new_state == "speaking":
            moderator.user_currently_speaking = True
            # Record when this speaking segment started (for VAD-based duration)
            moderator._vad_speaking_segment_start = datetime.now()
        elif event.old_state == "speaking":
            # User stopped speaking (moved to listening or away)
            moderator.user_currently_speaking = False
            moderator._user_stopped_speaking_at = datetime.now()

            # ACCUMULATE actual VAD speaking duration (not wall-clock from turn start)
            # Each speaking→listening transition adds the segment length
            if moderator.current_turn and hasattr(moderator, '_vad_speaking_segment_start') and moderator._vad_speaking_segment_start:
                segment_duration = (datetime.now() - moderator._vad_speaking_segment_start).total_seconds()
                moderator.current_turn.actual_speaking_duration += segment_duration
                moderator._vad_speaking_segment_start = None  # Reset for next segment
                logger.debug(f"📊 Captured actual speaking duration: {moderator.current_turn.actual_speaking_duration:.1f}s (segment: {segment_duration:.1f}s)")


            # DON'T stop turn monitoring here - user might just be pausing to breathe!
            # Turn monitoring will continue running and track cumulative time
            # It will be stopped when response is captured or question times out
            logger.debug("User paused speaking (turn monitoring continues)")

            # IMMEDIATE CAPTURE: Check for response right after user stops speaking
            # This is faster than waiting for conversation_item_added which can be 15+ seconds late
            asyncio.create_task(_capture_user_response_immediately())

        # When user starts speaking
        if event.new_state == "speaking" and event.old_state != "speaking":
            logger.info("User started speaking")

            # Track the very first VAD event for STT health check
            if moderator._first_vad_speaking_time is None:
                moderator._first_vad_speaking_time = datetime.now()

            # Cancel response timeout - they're now speaking
            if moderator.response_timeout_task:
                moderator.response_timeout_task.cancel()
                moderator.response_timeout_task = None
            moderator.last_speech_time = datetime.now()
            # NOTE: Do NOT set waiting_for_response = False here.
            # The polling loop interprets that flag as "timeout confirmed no-response."
            # Cancelling the timeout task + setting last_speech_time is sufficient
            # to prevent the monitor from firing. waiting_for_response is only
            # set to False when: (a) the 20s timeout truly fires, or
            # (b) a transcribed response is captured in the polling loop.

            # NETWORK LATENCY TRACKING: Record speaking start time
            moderator.speaking_start_time = datetime.now()
            moderator.first_stt_received = False

            # START TURN DURATION MONITORING for graceful time management (only if not already running)
            # Get participant ID from expected_respondent (who we asked the question to)
            participant_id = moderator.expected_respondent or "unknown"

            # Only start turn monitoring if not already running (to avoid resetting timer on pauses)
            if moderator.turn_monitor_task is None and moderator.enable_turn_limits and participant_id != "unknown":
                logger.critical("=" * 80)
                logger.critical("🔍 TURN MONITORING DEBUG:")
                logger.critical(f"   expected_respondent: {moderator.expected_respondent}")
                logger.critical(f"   participant_id: {participant_id}")
                logger.critical(f"   enable_turn_limits: {moderator.enable_turn_limits}")
                logger.critical(f"   force_interrupt_enabled: {moderator.force_interrupt_enabled}")
                logger.critical(f"   max_turn_duration: {moderator.max_turn_duration}s")
                logger.critical(f"   first_interrupt_grace: {moderator.first_interrupt_grace}s")
                logger.critical(f"   second_interrupt_grace: {moderator.second_interrupt_grace}s")
                logger.critical("=" * 80)

                # Create turn info
                moderator.current_turn = TurnInfo(
                    participant_identity=participant_id,
                    start_time=datetime.now()
                )

                # Start turn duration monitoring
                logger.critical(f"🚀 STARTING TURN DURATION MONITORING for {participant_id}")
                moderator.turn_monitor_task = asyncio.create_task(
                    moderator.monitor_turn_duration(session)
                )
                logger.critical(f"✅ Turn monitoring task created: {moderator.turn_monitor_task}")
            elif moderator.turn_monitor_task is not None:
                logger.debug(f"Turn monitoring already running, not restarting (user resumed speaking)")
            else:
                logger.critical(f"❌ NOT starting turn monitoring - enable_turn_limits={moderator.enable_turn_limits}, participant_id={participant_id}")

            # We don't know participant ID yet from this event
            # Will check conversation context after they stop speaking

    @session.on("conversation_item_added")
    def on_conversation_item_added(event):
        """Called when a message is added to the conversation (WORKS WITH REGULAR LLM!)"""
        # The event object is ConversationItemAddedEvent, which contains an 'item' attribute
        if not hasattr(event, 'item'):
            logger.warning("conversation_item_added event has no 'item' attribute")
            return

        message = event.item
        logger.critical(f"🔥 EVENT FIRED: conversation_item_added - role={message.role if hasattr(message, 'role') else 'unknown'}")
        print(f"🔥 EVENT FIRED: conversation_item_added - role={message.role if hasattr(message, 'role') else 'unknown'}")

        # ── WELCOME-PHASE GUARD ──────────────────────────────────────────
        if moderator.survey_state == SurveyState.WELCOME:
            if hasattr(message, 'role') and message.role == "user":
                logger.info(f"🛡️ WELCOME-PHASE GUARD: Discarding user conversation_item during welcome")
                return
        # ─────────────────────────────────────────────────────────────────

        # Log assistant messages (LLM/TTS output)
        # DISABLED: We now capture this synchronously immediately after generate_reply()
        # The async event arrives too late and causes out-of-order logging
        if hasattr(message, 'role') and message.role == "assistant":
            logger.info(f"   Skipping assistant message (already captured synchronously)")
            return

        # Only process user messages
        if not hasattr(message, 'role') or message.role != "user":
            logger.info(f"   Skipping non-user message (role={message.role if hasattr(message, 'role') else 'N/A'})")
            return

        # Get the message content
        if hasattr(message, 'content'):
            if isinstance(message.content, list):
                transcript = " ".join(str(c) for c in message.content)
            else:
                transcript = str(message.content)
        else:
            transcript = str(message)

        # Log participant's STT response (user's answer to the question)
        moderator.audit_logger.log_stt_text(transcript)

        # End the audit for this question after getting the response
        moderator.audit_logger.end_question()

        # We don't have participant ID from this event, so use the one we're waiting for
        # This is a workaround - we'll use the current expected participant
        if not moderator.participant_manager or not moderator.current_question_num:
            return

        # Get the participant we're currently waiting for
        current_participants = moderator.participant_manager.participants
        answered_participants = moderator.participant_manager.asked_participants.get(moderator.current_question_num, [])
        unanswered = [p for p in current_participants if p not in answered_participants]

        if not unanswered:
            logger.warning("Received user message but all participants already answered!")
            return

        # MULTI-PARTICIPANT FIX: Use ACTUAL speaker (from track_unmuted event), not assumption
        expected_participant = unanswered[0]  # Who we asked
        actual_speaker = moderator.actual_respondent if moderator.actual_respondent else expected_participant

        # Validation: Check if the right person responded
        if moderator.actual_respondent and moderator.actual_respondent != expected_participant:
            logger.warning(f"⚠️  MISMATCH: Expected {expected_participant} to respond, but {moderator.actual_respondent} spoke!")
            logger.info(f"✅ ACCEPTING response from {moderator.actual_respondent} (being permissive, not kicking)")

        logger.critical(f"📝 User message captured: {actual_speaker}, text: '{transcript[:50]}...'")
        logger.critical(f"👤 Response from: {actual_speaker} (expected: {expected_participant})")

        # Use actual_speaker for all operations
        participant_id = actual_speaker

        # Apply STT correction - handle multi-option questions differently
        corrected_text = transcript
        if moderator.current_question_object and moderator.current_question_object.response_options:
            max_sel = moderator.current_question_object.max_selections or 1
            if max_sel > 1:
                # Multi-option question - parse and match multiple responses
                corrected_text = parse_multi_option_response(
                    transcript,
                    moderator.current_question_object.response_options,
                    max_sel
                )
            else:
                # Single option question - use standard correction
                corrected_text = correct_transcription(
                    transcript,
                    moderator.current_question_object.response_options
                )
            if corrected_text != transcript:
                logger.info(f"Response corrected: '{transcript}' → '{corrected_text}'")

        # STORE RESPONSE IN VARIABLE (bypassing LLM context - this is the key!)
        moderator.latest_user_response = corrected_text
        logger.critical(f"💾 Stored response in variable: '{corrected_text[:50]}...'")

        # IMMEDIATE LOGGING: Record participant response in survey transcript right away (debug/audit)
        moderator.survey_transcript.add_response(
            question_number=moderator.current_question_num,
            participant=participant_id,
            response_text=corrected_text
        )
        logger.critical(f"✅ Response logged to JSON (debug): Q#{moderator.current_question_num}, {participant_id}")

        # Record turn result for acknowledgment
        expected = moderator.expected_respondent if moderator.expected_respondent else participant_id
        moderator._record_turn_result(expected=expected, actual=participant_id)

        # Mark participant as answered
        logger.info(f"Marking {participant_id} as answered for question #{moderator.current_question_num}")
        if moderator._is_delivery_confirmed(moderator.current_question_num, participant_id):
            moderator.participant_manager.mark_participant_answered(
                participant_id, moderator.current_question_num
            )
            moderator._set_delivery_state(
                moderator.current_question_num,
                participant_id,
                "answered",
                context="conversation_item_added",
            )
        else:
            logger.warning(
                f"Skipping mark_participant_answered for {participant_id} because delivery is unconfirmed."
            )
            # #region agent log
            import json as _json
            _debug_log_write(_json.dumps({"location": "moderator_agent.py:conversation_item_added:unconfirmed_delivery", "message": "Skipped mark_answered due to unconfirmed delivery", "data": {"participant_id": participant_id, "question_num": moderator.current_question_num, "expected_respondent": moderator.expected_respondent}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "H_ANSWERED_GATE"}))
            # #endregion
            return

        # Set flag so polling knows response was captured (fixes wait time issue)
        moderator.response_captured = True
        logger.critical(f"🚩 Flag set: response_captured = True (polling will detect and proceed quickly)")

        # Note: We don't call move_to_next_participant() here anymore
        # The polling code will detect the flag and call it, giving user time to finish speaking

    logger.critical("=" * 80)
    logger.critical("🚀 AGENT SESSION STARTED WITH NEW EVENT HANDLERS")
    logger.critical("🔍 Listening for: user_input_transcribed (primary), user_state_changed, conversation_item_added (fallback)")
    logger.critical("🔧 Turn detection: server_vad")
    logger.critical("🎙️  STT: Capturing responses via user_input_transcribed event (bypasses LLM flow)")
    logger.critical("⏱️  Polling: Waits for latest_user_response variable (max 30s) to let user finish speaking")
    logger.critical("💾 Response storage: latest_user_response variable -> polling -> JSON/CSV export")
    logger.critical("=" * 80)
    logger.critical("🎯 MULTI-PARTICIPANT FIX ENABLED:")
    logger.critical("   ✅ track_unmuted events identify WHO is speaking")
    logger.critical("   ✅ actual_respondent tracks who spoke vs who was asked")
    logger.critical("   ✅ Responses attributed to ACTUAL speaker, not expected")
    logger.critical("   ✅ Permissive mode: accepts all responses, just logs mismatches")
    logger.critical("   ✅ Won't kick participants - only warns if wrong person speaks")
    logger.critical("=" * 80)
    logger.critical("🔊 CRITICAL STT FIX FOR MULTI-PARTICIPANT:")
    logger.critical("   🎙️  set_participant() called before EACH question")
    logger.critical("   🎙️  Switches STT to listen to that specific participant's audio track")
    logger.critical("   🎙️  Ensures ONLY the asked participant's voice is transcribed")
    logger.critical("   🎙️  Prevents one participant's audio being ignored while waiting for another")
    logger.critical("=" * 80)
    print("=" * 80)
    print("🚀 AGENT SESSION STARTED WITH NEW EVENT HANDLERS")
    print("=" * 80)

    return session
