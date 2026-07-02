"""Centralised constants for the AI moderator agent.

All named constants, token sets, TTS prompt templates, and the SurveyState enum
live here.  No I/O, no SDK imports — only stdlib ``enum``.
"""
from enum import Enum


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

# Partial-delivery fallback nudge (NO name)
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


# ── Timing constants ─────────────────────────────────────────────────────────

# Silence watchdog: seconds of no transcript progression before firing
SILENCE_WATCHDOG_TIMEOUT = 12.0

# Idle-no-VAD watchdog: seconds after question delivery with zero VAD
# activity before we nudge the participant.
IDLE_NO_VAD_TIMEOUT = 12.0

# Extra seconds added to estimated TTS duration before idle watchdog can fire
TTS_SAFETY_MARGIN = 3.0

# Total extra seconds added to _polling_deadline for disfluency
DISFLUENCY_EXTENSION_BUDGET = 10.0

# Post-nudge extension: seconds added to polling deadline when participant
# activity arrives after a timeout nudge has already fired.
POST_NUDGE_EXTENSION_SECS = 15.0

# Substance gate: minimum character count for a transcript to be committed
# as a response.
MIN_COMMITTED_CHARS = 2

# Pause cooldown: seconds after user stops speaking before fragment promotion.
# Quantitative answers are short and high-confidence, so we wait far less than
# for open-ended qualitative answers where the participant may pause mid-thought.
# (Priority 2 latency work, 2026-07-01: quant lowered 1.5→0.6.)
PAUSE_COOLDOWN_QUANTITATIVE = 0.6
PAUSE_COOLDOWN_QUALITATIVE = 2.5

# Stabilization window: how long to wait for STT fragments to stop arriving
# before promoting a response. Quantitative is measured from when the user
# STOPPED speaking (short window); qualitative keeps the longer window measured
# from the first fragment. (Priority 2: quant effectively 2.0→0.6.)
STABILIZATION_QUANTITATIVE = 0.6
STABILIZATION_QUALITATIVE = 2.0

# Polling wait_for cap: max seconds the _await_response loop blocks per iteration
# before re-checking watchdogs. Lower = faster reaction to a ready response.
POLL_WAIT_CAP_QUANTITATIVE = 0.4
POLL_WAIT_CAP_DEFAULT = 2.0


# ── Avatar health states ─────────────────────────────────────────────────────

AVATAR_STATE_IDLE = "idle"
AVATAR_STATE_STARTING = "starting"
AVATAR_STATE_CONNECTED = "connected"
AVATAR_STATE_DISCONNECTED = "disconnected"
AVATAR_STATE_RECONNECTING = "reconnecting"
AVATAR_STATE_FAILED = "failed"

ANAM_AVATAR_IDENTITY = "anam-avatar-agent"


# ── Off-topic short-response guard constants ─────────────────────────────────

MIN_OFFTOPIC_WORDS = 3
MIN_OFFTOPIC_CHARS = 40

_BLATANT_OFFTOPIC_KEYWORDS = frozenset({
    "basketball", "breakfast", "cat", "cheese", "dinner", "dog",
    "football", "lunch", "movie", "movies", "music", "pizza",
    "soccer", "sport", "sports", "weather", "weekend",
})

_BLATANT_OFFTOPIC_PHRASES = (
    "mind your own business",
    "none of your business",
)


# ── Filler / hedge tokens ───────────────────────────────────────────────────

_FILLER_TOKENS = frozenset({
    "um", "uh", "uhm", "erm", "hmm", "hm", "ah", "oh",
    "like", "so", "well", "yeah", "yes", "no", "okay", "ok",
    "right", "and", "but", "just", "you", "know", "mean",
    "i", "a", "the", "is", "it", "that", "this",
})


# ── Disfluent starter tokens ────────────────────────────────────────────────

_DISFLUENT_STARTER_TOKENS = frozenset({
    "well", "um", "uh", "uhm", "erm", "hmm", "ah", "oh",
    "like", "so", "i", "think", "guess", "mean",
    "you", "know", "yeah", "yes", "no", "okay", "ok",
    "right", "and", "but", "just", "that", "the", "a",
    "it", "its", "is", "was", "not", "really",
    "hi", "hello", "hey",
})


# ── First-utterance greeting tokens ─────────────────────────────────────────

_FIRST_UTTERANCE_GREETING_TOKENS = frozenset({
    "sure", "thanks", "thank", "morning", "evening", "afternoon",
    "good", "nice", "meet", "to", "how", "are", "doing", "fine",
    "great", "welcome", "greetings",
})

_FIRST_UTTERANCE_GREETING_PHRASES = (
    "thank you",
    "good morning",
    "good evening",
    "good afternoon",
    "how are you",
    "nice to meet you",
    "nice to meet",
)


# ── Meta-commentary phrases ─────────────────────────────────────────────────

META_COMMENTARY_PHRASES = [
    "you're talking to me",
    "you are talking to me",
    "are you talking to me",
    "are you asking me",
    "are you speaking to me",
    "is that for me",
    "was that for me",
    "is that directed at me",
    "that's for me",
    "oh that's me",
    "you mean me",
    "do you mean me",
    "is it my turn",
    "is that my turn",
]


# ── Survey state enum ───────────────────────────────────────────────────────

class SurveyState(Enum):
    """Track the current state of the survey for Observer control."""
    WELCOME = "welcome"
    WAITING_FOR_OBSERVER = "waiting_for_observer"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
