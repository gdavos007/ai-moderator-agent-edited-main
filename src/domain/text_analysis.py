"""Pure text classification functions for the AI moderator agent.

No I/O, no SDK imports — only stdlib ``re``, ``logging``, and domain constants.
"""
import logging
import re

from .constants import (
    _FILLER_TOKENS,
    _DISFLUENT_STARTER_TOKENS,
    _FIRST_UTTERANCE_GREETING_TOKENS,
    _FIRST_UTTERANCE_GREETING_PHRASES,
    _BLATANT_OFFTOPIC_KEYWORDS,
    _BLATANT_OFFTOPIC_PHRASES,
    META_COMMENTARY_PHRASES,
    MIN_OFFTOPIC_WORDS,
    MIN_OFFTOPIC_CHARS,
    MIN_COMMITTED_CHARS,
    ANAM_AVATAR_IDENTITY,
)

logger = logging.getLogger(__name__)


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


def _is_committable(text: str) -> bool:
    """Return True if text is long enough to be committed as a response."""
    return len(text.strip()) >= MIN_COMMITTED_CHARS


def _is_disfluent_starter(text: str) -> bool:
    """Return True if text consists entirely of disfluent/filler tokens."""
    words = [w.strip(".,!?…'\"") for w in text.lower().split()]
    words = [w for w in words if w]
    if not words:
        return True
    return all(w in _DISFLUENT_STARTER_TOKENS for w in words)


def _is_first_utterance_greeting(text: str) -> bool:
    """Return True if text is a greeting/acknowledgment with no substantive content.

    Uses the same ALL-words semantics as _is_disfluent_starter(): every word
    must be either a disfluent token or a greeting token.  This prevents
    masking real answers that happen to start with a greeting, e.g.
    "Sure, I think the product is great" -> False.
    """
    words = [w.strip(".,!?…'\"") for w in text.lower().split()]
    words = [w for w in words if w]
    if not words:
        return False  # Empty text is handled by disfluency guard
    allowed = _DISFLUENT_STARTER_TOKENS | _FIRST_UTTERANCE_GREETING_TOKENS
    return all(w in allowed for w in words)


def _normalize_for_offtopic_compare(text: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace for off-topic repeat detection."""
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', '', text.lower())).strip()


def _has_blatant_offtopic_keywords(text: str) -> bool:
    """Return True for obviously unrelated short-topic cues like food/weather."""
    normalized = _normalize_for_offtopic_compare(text)
    if not normalized:
        return False
    if any(phrase in normalized for phrase in _BLATANT_OFFTOPIC_PHRASES):
        return True
    return bool(set(normalized.split()) & _BLATANT_OFFTOPIC_KEYWORDS)


def is_uncertain_response(text: str) -> bool:
    """
    Detect if a response is ENTIRELY/PRIMARILY an uncertainty statement.

    IMPORTANT: Only returns True if the response is essentially JUST an uncertain
    phrase without any substantive content. If the participant has provided a
    partial answer along with "I don't know", this returns False to allow the
    partial answer to be processed.

    Meta-commentary phrases (e.g. "you're talking to me") are stripped before
    the substantive-content check so they don't mask an otherwise pure uncertain
    response.

    Examples that return True:
    - "I don't know"
    - "I'm not sure"
    - "Honestly, I don't know"
    - "I really have no idea"
    - "Oh, you're talking to me. Uh, I don't know."

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

    # ── Strip meta-commentary phrases before substantive-content check ──
    for meta_phrase in META_COMMENTARY_PHRASES:
        if meta_phrase in text_lower:
            logger.info(f"Stripping meta-commentary phrase '{meta_phrase}' from: '{text}'")
            text_lower = text_lower.replace(meta_phrase, " ", 1)
    # Clean up leftover whitespace and punctuation fragments
    text_lower = re.sub(r'\s+', ' ', text_lower).strip()

    # Common uncertainty phrases — longer phrases MUST come before shorter
    # sub-phrases so the first match removes the most specific variant.
    # E.g. "i have no idea" must precede "no idea".
    uncertain_phrases = [
        "i really don't know",
        "honestly i don't know",
        "i honestly don't know",
        "honestly don't know",
        "i don't know",
        "i do not know",
        "don't know",
        "do not know",
        "i'm not sure",
        "i am not sure",
        "not sure",
        "i have no idea",
        "no idea",
        "i have no clue",
        "no clue",
        "i can't say",
        "i cannot say",
        "can't say",
        "cannot say",
        "i'm not certain",
        "i am not certain",
        "not certain",
        "i'm uncertain",
        "uncertain",
        "beats me",
        "i couldn't tell you",
        "i could not tell you",
        "couldn't tell you",
        "it's hard to say",
        "hard to say",
        "difficult to say",
        "i'm unsure",
        "i am unsure",
        "unsure",
        "i wouldn't know",
        "i would not know",
        "i have no opinion",
        "no opinion",
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
        "tbh", "right", "okay", "ok", "man", "dude", "huh", "er",
        "have", "got", "do", "did", "am", "is", "was",
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
            f"Skipping heuristic repeat check — response too long "
            f"({len(text_lower)} chars > {HEURISTIC_MAX_LENGTH}), deferring to LLM"
        )
        return False

    # ── Substring-match phrases ──────────────────────────────────────────
    repeat_phrases = [
        "say that again",
        "say it again",
        "say the question again",
        "say that one more time",
        "come again",
        "what was the question",
        "what's the question",
        "what is the question",
        "what was the last part",
        "what was the first part",
        "what was that last part",
        "what was that first part",
        "didn't catch",
        "did not catch",
        "can you say that again",
        "could you say that again",
        "can you say the last part",
        "can you say the first part",
        "could you say the last part",
        "could you say the first part",
        "can you say that one more time",
        "can you repeat",
        "could you repeat",
        "please repeat",
        "again please",
        "pardon",
        "excuse me",
        "sorry what",
        "what did you say",
        "what did you ask",
        "i missed that",
        "missed the question",
        "didn't understand",
        "did not understand",
        "didn't get that",
        "did not get that",
        "i didn't get the question",
        "speak up",
        "louder please",
        "what again",
        "huh",
        "what?",
    ]

    for phrase in repeat_phrases:
        if phrase in text_lower:
            logger.info(f"Heuristic repeat-request detected: '{text}' matches phrase '{phrase}'")
            return True

    # ── Context-aware "hear" phrases ────────────────────────────────────
    _hear_phrases = [
        r"didn'?t hear", r"did not hear",
        r"couldn'?t hear", r"could not hear",
        r"can'?t hear", r"cannot hear",
    ]
    for hp in _hear_phrases:
        if re.search(hp, text_lower):
            if re.search(r"\byou\b.{0,10}" + hp, text_lower) or re.search(r"\byou'?re\b.{0,10}" + hp, text_lower):
                logger.debug(f"Skipping hear-phrase '{hp}' — subject is 'you' (agent complaint, not repeat request): '{text}'")
            else:
                logger.info(f"Heuristic repeat-request detected (hear-phrase): '{text}' matches /{hp}/")
                return True

    # ── Regex patterns for harder-to-catch variants ──────────────────────
    _repeat_patterns = [
        r"^\s*repeat(?:\s+(?:that|it|the question))?(?:\s+please)?[.?!]*$",
        r"^\s*one more time(?:\s+please)?[.?!]*$",
        r"\bsay\b.{0,30}\bagain\b",
        r"\bhear\b.{0,20}\b(last|first)\s*part\b",
        r"\bwhat\s+was\b.{0,20}\bpart\b",
        r"\b(?:can|could)\s+you\b.{0,20}\b(last|first)\s+part(?:\s+of\s+the\s+question)?\b",
    ]
    for pattern in _repeat_patterns:
        if re.search(pattern, text_lower):
            logger.info(f"Heuristic repeat-request detected (regex): '{text}' matches /{pattern}/")
            return True

    # ── Very short responses that are just confusion ─────────────────────
    if text_lower in ["what", "what?", "huh", "huh?", "sorry", "sorry?", "come again", "come again?"]:
        logger.info(f"Heuristic repeat-request detected (short): '{text}'")
        return True

    return False


def is_avatar_identity(identity: str) -> bool:
    """Return True if the identity belongs to the Anam avatar agent.

    Centralised check so all event handlers use the same logic.
    Matches the constant ``ANAM_AVATAR_IDENTITY`` ("anam-avatar-agent")
    and any identity containing "avatar-agent" (handles suffixed variants).
    """
    if not identity:
        return False
    return identity == ANAM_AVATAR_IDENTITY or "avatar-agent" in identity
