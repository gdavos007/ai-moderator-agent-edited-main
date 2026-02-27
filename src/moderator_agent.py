"""
AI Community Moderator Agent with Aggressive Interruption and Topic Enforcement
Main agent implementation with moderation capabilities, time management, and topic tracking
"""
import logging
import asyncio
import random
import re
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
        # "didn't hear / catch"
        "didn't hear",
        "did not hear",
        "didn't catch",
        "did not catch",
        "couldn't hear",
        "could not hear",
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
        # audio issues
        "can't hear",
        "cannot hear",
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

    # ── Regex patterns for harder-to-catch variants ──────────────────────
    import re
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
        self._vad_speaking_segment_start: Optional[datetime] = None  # When current speaking segment started (for VAD-based duration)
        self.response_captured = False  # Flag set by event handler when STT captures response
        self.latest_user_response = None  # Store latest STT transcript (bypassing LLM context entirely)
        self.response_fragments = []  # DEPRECATED: No longer used (STT sends cumulative transcripts, not fragments)
        self.last_fragment_time = None  # Track when last transcript was received
        self.last_stt_fragment = ""  # Track the last individual STT fragment (for cumulative detection)
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
        self._tts_in_flight: bool = False  # Single-flight guard: True while a TTS say() is active
        self._tts_sequence: int = 0  # Monotonic counter to detect overlapping TTS calls
        self._polling_deadline: Optional[float] = None  # time.time() deadline; extended on repeat
        self._ack_already_spoken: bool = False  # True when polling loop already spoke the ack
        self._prewarmed_ack_text: Optional[str] = None  # Pre-computed ack text for concurrent TTS
        self._gentle_warning_in_progress: bool = False  # True while gentle warning TTS is playing; prevents premature response capture
        self._first_fragment_time: Optional[datetime] = None  # When first STT fragment arrived for current question; used for stabilization delay
        self._user_stopped_speaking_at: Optional[datetime] = None  # Timestamp when user last transitioned from speaking to listening; used for pause cooldown

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

        for participant_identity in all_participants:
            try:
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
        with open("/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log", "a") as _f:
            _f.write(_json.dumps({"location": "moderator_agent.py:_set_delivery_state", "message": f"delivery-state transition", "data": {"question_num": question_num, "participant": participant_identity, "old_state": old_state, "new_state": state, "context": context}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "H_DELIVERY_TRACE"}) + "\n")
        # #endregion

    def _is_delivery_confirmed(self, question_num: int, participant_identity: str) -> bool:
        key = self._delivery_key(question_num, participant_identity)
        return self.question_delivery_state.get(key) == "delivered"

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
        with open("/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log", "a") as _f:
            _f.write(_json.dumps({"location": "moderator_agent.py:_record_turn_result", "message": "Turn result recorded", "data": {"expected": expected, "actual": actual, "ack_name": result.ack_name, "timeout": was_timeout, "question_num": self.current_question_num}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "ACK_NAME"}) + "\n")
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
                with open("/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log", "a") as _f:
                    _f.write(_json.dumps({"location": "moderator_agent.py:_analyze_with_filler", "message": "Filler triggered", "data": {"threshold_s": filler_threshold, "question_num": self.current_question_num}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "FILLER"}) + "\n")
                # #endregion
                await self.agent_session.say("One moment...", allow_interruptions=False)
            result = await analysis_task

        analysis_duration = (datetime.now() - self._analysis_start_time).total_seconds()
        logger.info(f"📊 METRIC: analysis_ms={analysis_duration * 1000:.0f}")

        # #region agent log
        import json as _json
        with open("/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log", "a") as _f:
            _f.write(_json.dumps({"location": "moderator_agent.py:_analyze_with_filler", "message": "Analysis complete", "data": {"analysis_ms": round(analysis_duration * 1000), "filler_spoken": self._transition_filler_said, "is_relevant": result.is_relevant, "question_num": self.current_question_num}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "METRICS"}) + "\n")
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

    # ── Single-flight TTS with truncation detection + retry ──────────────
    _TTS_WORDS_PER_SEC = 2.5  # conservative TTS speech rate (real TTS ~1.7-2.8 wps)

    async def _speak_question_safely(
        self,
        text: str,
        *,
        context: str = "",
        max_retries: int = 1,
    ) -> bool:
        """Speak *text* with truncation detection and bounded retry.

        On the first attempt the question is spoken with
        ``allow_interruptions=True`` (natural conversational flow).
        If the TTS returns suspiciously fast (< 50 % of estimated minimum
        duration), we assume it was interrupted by spillover audio / noise
        and retry **once** with ``allow_interruptions=False``.

        A single-flight guard prevents overlapping ``say()`` calls: if a
        previous TTS is still active the new call waits up to 30 s for it
        to finish.

        Returns ``True`` if the question was (likely) fully spoken.
        """
        word_count = len(text.split())
        estimated_min_sec = word_count / self._TTS_WORDS_PER_SEC

        for attempt in range(1 + max_retries):
            allow_int = attempt == 0  # first try: interruptible; retry: not
            seq = self._tts_sequence + 1
            self._tts_sequence = seq

            # ── Single-flight guard ────────────────────────────────────
            if self._tts_in_flight:
                logger.warning(
                    f"⚠️ TTS OVERLAP: new say() requested while previous TTS still in flight "
                    f"[{context}]. Waiting up to 30 s…"
                )
                # #region agent log
                import json as _json
                with open("/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log", "a") as _f:
                    _f.write(_json.dumps({"location": "moderator_agent.py:_speak_question_safely:overlap", "message": "TTS overlap detected", "data": {"context": context, "seq": seq, "attempt": attempt}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "H_OVERLAP"}) + "\n")
                # #endregion
                for _ in range(60):
                    await asyncio.sleep(0.5)
                    if not self._tts_in_flight:
                        break
                if self._tts_in_flight:
                    logger.error("🛑 Previous TTS did not finish in 30 s — proceeding anyway")

            # ── Speak ──────────────────────────────────────────────────
            self._tts_in_flight = True
            tts_start = datetime.now()
            logger.critical(
                f"🔊 TTS START [{context}] attempt={attempt+1}/{1+max_retries} "
                f"allow_interruptions={allow_int} words={word_count} "
                f"est_min={estimated_min_sec:.1f}s text='{text[:80]}…'"
            )

            # #region agent log
            import json as _json
            with open("/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log", "a") as _f:
                _f.write(_json.dumps({"location": "moderator_agent.py:_speak_question_safely:start", "message": "TTS start", "data": {"context": context, "attempt": attempt, "seq": seq, "allow_interruptions": allow_int, "word_count": word_count, "est_min_sec": round(estimated_min_sec, 1), "text_len": len(text)}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "H_TRUNCATION"}) + "\n")
            # #endregion

            try:
                await self.agent_session.say(text, allow_interruptions=allow_int)
            except Exception as e:
                logger.error(f"🛑 TTS say() raised: {e}")
            finally:
                self._tts_in_flight = False

            tts_duration = (datetime.now() - tts_start).total_seconds()
            truncated = tts_duration < (estimated_min_sec * 0.5) and estimated_min_sec > 2.0

            logger.critical(
                f"🔊 TTS END   [{context}] duration={tts_duration:.1f}s "
                f"est_min={estimated_min_sec:.1f}s truncated={truncated}"
            )

            # #region agent log
            import json as _json
            with open("/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log", "a") as _f:
                _f.write(_json.dumps({"location": "moderator_agent.py:_speak_question_safely:end", "message": "TTS end", "data": {"context": context, "attempt": attempt, "seq": seq, "duration_s": round(tts_duration, 2), "est_min_sec": round(estimated_min_sec, 1), "truncated": truncated, "word_count": word_count}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "H_TRUNCATION"}) + "\n")
            # #endregion

            if not truncated:
                return True

            if attempt < max_retries:
                logger.warning(
                    f"⚠️ TTS TRUNCATED [{context}]: {tts_duration:.1f}s actual vs "
                    f"{estimated_min_sec:.1f}s expected. Retrying with allow_interruptions=False…"
                )
                await asyncio.sleep(0.3)  # brief settle before retry
            else:
                logger.error(
                    f"🛑 TTS TRUNCATED [{context}] after {1+max_retries} attempts — "
                    f"question may not have been fully spoken"
                )

        return False

    def _reset_for_repeat(self, participant: str, *, context: str = "") -> None:
        """Reset all mutable state after a repeat so the SAME participant gets
        a fresh turn.  Centralised here so every repeat path is consistent.

        Invariant enforced: ``expected_respondent`` MUST remain ``participant``
        after this call — repeating never changes who we're listening to.
        """
        saved_expected = self.expected_respondent

        # ── Response / STT state ───────────────────────────────────────
        self.latest_user_response = None
        self.response_captured = False
        self.last_stt_fragment = ""
        self.pending_stt_transcript = None
        self.response_fragments = []
        self.last_fragment_time = None
        self.actual_respondent = None

        # ── Flow control flags ─────────────────────────────────────────
        self.encouragement_given = False
        self.waiting_for_response = True
        self.last_speech_time = None
        self._transition_filler_said = False
        self._ack_already_spoken = False
        self._prewarmed_ack_text = None

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
        self.user_currently_speaking = False
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
        with open("/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log", "a") as _f:
            _f.write(_json.dumps({"location": "moderator_agent.py:_reset_for_repeat", "message": "Repeat state reset", "data": {"participant": participant, "context": context, "expected_respondent": self.expected_respondent, "actual_respondent": self.actual_respondent, "question_num": self.current_question_num}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "H_IDENTITY"}) + "\n")
        # #endregion

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
        try:
            # Get first participant to reset STT to a working state
            participants = list(self.participant_manager.participants)
            if participants:
                audio_input = self.agent_session._room_io._audio_input
                # Temporarily set to first participant to "wake up" STT
                audio_input.set_participant(participants[0])
                logger.info(f"👁️ STT reset to first participant: {participants[0]}")
            else:
                logger.warning("👁️ No participants available to reset STT")
        except Exception as e:
            logger.warning(f"Could not reset STT: {e}")

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
                    try:
                        audio_input = self.agent_session._room_io._audio_input
                        audio_input.set_participant(observer_identity)
                        logger.info(f"👁️ STT set to observer for 'resume' command: {observer_identity}")
                    except Exception as e:
                        logger.warning(f"Could not set STT to observer: {e}")
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

        warning_start_time = None  # Timestamp when gentle warning TTS finished (for cooldown)

        logger.info(f"Started graceful turn monitoring for {participant_id}")
        logger.info(
            f"Survey thresholds: base={self.max_turn_duration}s, "
            f"warning_at={grace_period_1}s, "
            f"force_end={grace_period_2}s"
        )

        try:
            while self.current_turn == turn_info:  # Still the same turn
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

                    # Interrupt agent's own speech first
                    await session.interrupt()

                    # FIXED: Use direct TTS for exact message (like timeout prompt fix)
                    # participant_id IS the display name (participants is a list, not a dict)
                    display_name = participant_id.capitalize()  # Capitalize first letter for proper display

                    warning_text = (
                        f"{display_name}, we're going to need to wrap it up so we can get to others. "
                        f"Can you spend the next ten to fifteen seconds finishing your thoughts?"
                    )
                    logger.critical(f"🔊 SENDING GENTLE WARNING VIA TTS: '{warning_text}'")

                    # CRITICAL FIX: Block the polling loop from capturing the response
                    # while the warning is being spoken. Without this, the user pauses
                    # to listen to the warning and the polling loop grabs their partial
                    # response as the final answer.
                    self._gentle_warning_in_progress = True

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

                    # Clear accumulated response so only NEW speech after the warning counts.
                    # The pre-warning response was already captured; we want the wrap-up.
                    self.latest_user_response = None
                    self.last_stt_fragment = ""
                    self._first_fragment_time = None

                    # Now unblock the polling loop
                    self._gentle_warning_in_progress = False

                    logger.critical(
                        f"✅ GENTLE WARNING SENT (took {warning_duration:.1f}s). "
                        f"Timer RESET: old_elapsed={old_elapsed:.1f}s → new_elapsed={new_elapsed:.1f}s. "
                        f"User gets full {self.second_interrupt_grace}s to wrap up (force-end at {grace_period_2}s). "
                        f"Response buffer cleared — only new speech after warning will be captured."
                    )


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

                    # Multiple interrupts to force agent to speak
                    await session.interrupt()
                    await asyncio.sleep(0.1)
                    await session.interrupt()
                    await asyncio.sleep(0.1)

                    # FIXED: Use direct TTS for polite but firm ending
                    # participant_id IS the display name (participants is a list, not a dict)
                    display_name = participant_id.capitalize()  # Capitalize first letter for proper display

                    force_end_text = f"Thank you, {display_name}. We need to move on to ensure we complete all questions."
                    await session.say(force_end_text, allow_interruptions=False)
                    logger.info(f"🔊 Force ended turn with direct TTS: '{force_end_text}'")

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
        """Monitor for response timeout and prompt/move on if no response."""
        try:
            base_timeout = 10

            # Wait for participant to start responding
            await asyncio.sleep(base_timeout)

            # Check if paused - don't prompt during pause
            if self.survey_state == SurveyState.PAUSED:
                logger.info(f"👁️ Survey paused - skipping timeout prompt for {participant}")
                return

            # Check if they started speaking
            if self.last_speech_time is None and self.waiting_for_response:
                logger.warning(f"No response from {participant} after {base_timeout} seconds, prompting...")

                # FIXED: Use direct TTS instead of LLM generate_reply to ensure exact text
                # The LLM was ignoring instructions and saying "Thank you" instead
                display_name = self.participant_manager.get_display_name(participant)
                prompt_text = f"{display_name}, please let me know what you think."
                await self.agent_session.say(prompt_text, allow_interruptions=False)
                logger.info(f"🔊 Prompted participant with direct TTS: '{prompt_text}'")

                await asyncio.sleep(base_timeout)

                # Check if paused - don't timeout during pause
                if self.survey_state == SurveyState.PAUSED:
                    logger.info(f"👁️ Survey paused - skipping timeout handling for {participant}")
                    return

                # Still no response - move on
                if self.last_speech_time is None and self.waiting_for_response:
                    logger.warning(f"No response from {participant} after 20 seconds total, moving on...")
                    self.waiting_for_response = False

                    # Mark as timeout - the polling loop will detect this and handle it
                    # DON'T call move_to_next_participant here - it creates a race condition
                    # The polling loop is already handling the timeout case
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
        """Ask the next question to a random participant (if question-based mode is enabled)."""
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

            # Use direct TTS for closing (no LLM)
            try:
                logger.critical("🎤 CLOSING: Using direct TTS (no LLM)")
                await self.agent_session.say(closing_text, allow_interruptions=False)
                # Give time for speech to complete
                await asyncio.sleep(2)
            except RuntimeError as e:
                logger.warning(f"Could not speak completion message, session may be closing: {e}")

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
                await self.ask_next_question()
                return
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
                await self.ask_next_question()
                return
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

            # Use direct TTS for category announcement - no LLM
            await self.agent_session.say(category_announcement, allow_interruptions=False)
            await asyncio.sleep(1.0)  # Brief pause after category announcement

        # DYNAMIC VAD CONFIGURATION: Adjust silence threshold based on question type
        # Reduced for faster response times while still allowing natural pauses
        if self.current_question_object:
            if self.current_question_object.is_qualitative():
                self.agent_session.vad.update_options(min_silence_duration=0.8)
                logger.info(f"🎙️  VAD updated: min_silence_duration=0.8s (qualitative question)")
            elif self.current_question_object.is_quantitative():
                self.agent_session.vad.update_options(min_silence_duration=0.4)
                logger.info(f"🎙️  VAD updated: min_silence_duration=0.4s (quantitative question)")
            else:
                self.agent_session.vad.update_options(min_silence_duration=0.5)
                logger.info(f"🎙️  VAD updated: min_silence_duration=0.5s (default)")

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

        # MULTI-PARTICIPANT FIX: Set who we're expecting to respond
        self.expected_respondent = participant
        self.actual_respondent = None  # Will be set when we detect audio activity
        self.turn_transition_time = datetime.now()  # For spillover detection
        self._set_delivery_state(self.current_question_num, participant, "delivering", context="ask_next_question")

        logger.critical("=" * 80)
        logger.critical(f"🔧 AUDIO ROUTING TRACE - Question #{self.current_question_num} to {participant}")

        _respondent_count = self._active_respondent_count()
        if _respondent_count > 1:
            # MULTI-PARTICIPANT: Lock STT + mute others to prevent cross-talk
            logger.critical(f"   {_respondent_count} active respondents — enabling STT lock + muting")
            if self.agent_session and hasattr(self.agent_session, '_room_io') and self.agent_session._room_io:
                room_io = self.agent_session._room_io
                if hasattr(room_io, '_audio_input') and room_io._audio_input:
                    audio_input = room_io._audio_input
                    logger.critical(f"🎙️  EXECUTING set_participant('{participant}')...")
                    try:
                        audio_input.set_participant(participant)
                        logger.critical(f"✅ STT set_participant('{participant}') — immediate")
                    except Exception as e:
                        logger.error(f"❌ EXCEPTION during set_participant: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                else:
                    logger.error(f"❌ audio_input not available! Cannot switch STT to {participant}")
            else:
                logger.error(f"❌ room_io not available! Cannot switch STT to {participant}")

            asyncio.create_task(self.manage_participant_muting(participant))
        else:
            logger.critical(f"   {_respondent_count} active respondent — skipping STT lock + muting (solo mode)")

        # In observer mode: Keep STT on observer while speaking (to hear pause command)
        if self.observer_mode_enabled:
            # CRITICAL: Clear any pending STT transcripts from previous participant
            # (Response is already saved to transcript/export, so this won't lose data)
            self.pending_stt_transcript = None

            observer_identity = self.participant_manager.get_observer_identity()
            if observer_identity and self.agent_session and hasattr(self.agent_session, '_room_io') and self.agent_session._room_io:
                try:
                    audio_input = self.agent_session._room_io._audio_input
                    if audio_input:
                        audio_input.set_participant(observer_identity)
                        logger.critical(f"👁️ STT set to observer while asking question (can hear pause)")
                except Exception as e:
                    logger.warning(f"Could not set STT to observer: {e}")

        # DIRECT TTS - Speak the question with truncation detection + retry
        await self._speak_question_safely(
            exact_text_to_say,
            context=f"ask_Q{self.current_question_num}_{participant}",
        )
        self._set_delivery_state(self.current_question_num, participant, "delivered", context="ask_next_question")

        # Check if paused during the question
        if self.survey_state == SurveyState.PAUSED:
            logger.info("👁️ Survey paused during question - waiting for resume")
            while self.survey_state == SurveyState.PAUSED:
                await asyncio.sleep(0.5)
            logger.info("👁️ Survey resumed - continuing with response collection")

        logger.critical("=" * 80)
        logger.critical(f"✅ Question spoken via DIRECT TTS (0% LLM involvement)")

        # Calculate max polling time: Must be longer than max turn duration to allow graceful time management
        # Max turn duration = base + first_grace + second_grace + buffer
        max_turn_time = self.max_turn_duration + self.first_interrupt_grace + self.second_interrupt_grace
        polling_timeout = max_turn_time + 10  # Add 10s buffer for silence detection and processing
        max_checks = int(polling_timeout / 0.1)  # Convert to number of 0.1s checks

        logger.critical(f"⏳ SMART POLLING: Waiting for user response (max {polling_timeout}s)...")
        logger.critical(f"   - Max turn duration: {max_turn_time}s (base={self.max_turn_duration}s + graces={self.first_interrupt_grace + self.second_interrupt_grace}s)")
        logger.critical(f"   - Polling timeout: {polling_timeout}s (turn + 10s buffer)")
        logger.critical(f"   - Will break immediately once response captured (VAD-trusted)")

        # Reset the response variables before waiting (including fragments for multi-part answers)
        self.latest_user_response = None
        self.pending_stt_transcript = None  # Prevent stale transcripts from previous turn
        self.response_fragments = []
        self.last_fragment_time = None
        self.last_stt_fragment = ""  # Reset fragment tracking for new question
        self._first_fragment_time = None  # Reset stabilization timer for new question
        self._user_stopped_speaking_at = None  # Reset pause-cooldown timer for new question
        self._gentle_warning_in_progress = False  # Ensure clean state for new question
        self.encouragement_given = False  # Reset encouragement flag for new participant
        self.question_repeated = False  # Reset repeat flag for new participant
        self.relevance_prompt_given = False  # Reset relevance flag for new participant
        self.partial_repeat_handled = False  # Reset partial repeat flag for new question
        self.already_answered_prompt_given = False  # Reset already-answered flag for new question
        self.accumulated_partial_answer = ""  # Clear any accumulated partial answers
        self.turn_time_exceeded = False  # Reset turn time exceeded flag for new question
        self._ack_already_spoken = False  # Reset ack flag for new question
        self._prewarmed_ack_text = None  # Clear pre-warmed ack text
        logger.critical(f"🔄 [{question_id}] RESET encouragement/repeat/relevance/partial_repeat/already_answered/turn_time flags (new question)")

        # Initialize timeout monitoring variables
        self.waiting_for_response = True
        self.last_speech_time = None

        # Start timeout monitoring task (will prompt after 10s, move on after 20s)
        if self.response_timeout_task:
            self.response_timeout_task.cancel()
        self.response_timeout_task = asyncio.create_task(
            self.monitor_response_timeout(participant)
        )
        logger.info(f"⏱️  Started timeout monitoring for {participant} (prompt at 10s, skip at 20s)")

        # SMART POLLING: Check if event handler stored a response in our variable
        # BUT: Wait for user to finish speaking before processing!
        user_responded = False
        initial_user_msg_count = len([item for item in self.agent_session._chat_ctx.items
                                       if hasattr(item, 'role') and item.role == 'user'])

        import time as _time
        self._polling_deadline = _time.time() + polling_timeout

        for check_num in range(max_checks * 3):  # generous upper bound; deadline is the real limiter
            await asyncio.sleep(0.1)  # Fast 100ms polling — trust VAD for silence

            if _time.time() > self._polling_deadline:
                logger.warning(f"⏱️  Polling deadline reached ({polling_timeout}s effective)")
                break

            # TIMEOUT MONITOR SIGNAL: monitor_response_timeout sets this to False
            # after 20s of total silence — break immediately so we don't wait
            # for the full polling_timeout (~45s).
            if not self.waiting_for_response:
                logger.warning(f"⏱️  Timeout monitor signaled no-response — exiting poll early")
                break

            # OBSERVER COMMAND CHECK: Periodically switch STT to observer to hear pause/resume commands
            # This allows observer to say "pause" at any time during response collection
            if self.observer_mode_enabled and check_num % 20 == 10:  # Every ~2 seconds
                observer_identity = self.participant_manager.get_observer_identity()
                if observer_identity and self.agent_session and hasattr(self.agent_session, '_room_io') and self.agent_session._room_io:
                    try:
                        audio_input = self.agent_session._room_io._audio_input
                        if audio_input:
                            # Briefly switch to observer to check for commands
                            audio_input.set_participant(observer_identity)
                            logger.debug(f"👁️ STT briefly switched to observer for command check")
                            await asyncio.sleep(0.3)  # Brief listen window for observer

                            # Switch back to participant (unless paused)
                            if self.survey_state != SurveyState.PAUSED:
                                audio_input.set_participant(participant)
                                logger.debug(f"👁️ STT switched back to participant: {participant}")
                    except Exception as e:
                        logger.warning(f"Observer command check failed: {e}")

            # CRITICAL: Check if survey was paused by observer - wait until resumed
            if self.survey_state == SurveyState.PAUSED:
                logger.info(f"👁️ Survey PAUSED during response polling - waiting for resume")
                # Keep STT on observer while paused so we can hear resume
                observer_identity = self.participant_manager.get_observer_identity()
                if observer_identity and self.agent_session and hasattr(self.agent_session, '_room_io') and self.agent_session._room_io:
                    try:
                        audio_input = self.agent_session._room_io._audio_input
                        if audio_input:
                            audio_input.set_participant(observer_identity)
                            logger.info(f"👁️ STT set to observer for 'resume' command")
                    except Exception as e:
                        logger.warning(f"Could not set STT to observer: {e}")
                while self.survey_state == SurveyState.PAUSED:
                    await asyncio.sleep(0.5)
                logger.info(f"👁️ Survey RESUMED - continuing response polling")
                # After resume, the question will be re-asked via handle_observer_command
                # Exit this polling loop to avoid duplicate processing
                return

            # GENTLE WARNING GUARD: While the turn monitor is speaking the wrap-up
            # warning, do NOT capture or process responses. The user naturally pauses
            # to listen, and we must not mistake that for "finished speaking."
            if self._gentle_warning_in_progress:
                if check_num % 20 == 0:
                    logger.info(f"⏳ Gentle warning in progress — holding response capture (check {check_num})")
                continue

            # CRITICAL: If user is currently speaking, don't process yet - wait for them to finish!
            # EXCEPTION: If turn_time_exceeded is True, process anyway - user has exceeded time limit
            if self.user_currently_speaking and not self.turn_time_exceeded:
                if check_num % 20 == 0:  # Log every ~2 seconds while waiting
                    logger.info(f"⏳ User is speaking... waiting for them to finish (check {check_num})")
                continue  # Skip to next check, don't process response yet

            # If turn time exceeded but user is still speaking, force processing
            if self.turn_time_exceeded and self.user_currently_speaking:
                logger.warning(f"⚡ FORCE PROCESSING: Turn time exceeded but user still speaking - proceeding with accumulated response")

            # STABILIZATION DELAY: Prevent capturing a tiny first fragment (e.g. "How")
            # before VAD has even detected the user as speaking.  STT events can
            # arrive 200-400ms ahead of the user_state_changed → speaking event.
            # If the first fragment arrived very recently, give the VAD time to catch up.
            if (self.latest_user_response is not None
                and self._first_fragment_time is not None
                and not self.user_currently_speaking
                and not self.turn_time_exceeded):
                since_first_frag = (datetime.now() - self._first_fragment_time).total_seconds()
                if since_first_frag < 2.0:
                    # Too soon since first fragment — VAD may not have fired yet
                    continue

            # PAUSE COOLDOWN: When user pauses mid-thought (e.g. "uhh...", "umm..."),
            # VAD fires speaking→listening but the user intends to continue. Wait a
            # cooldown period after the last speaking→listening transition before
            # treating the response as final. Qualitative questions get more slack.
            if (self.latest_user_response is not None
                and not self.user_currently_speaking
                and not self.turn_time_exceeded
                and self._user_stopped_speaking_at is not None):
                pause_cooldown = 1.5 if (self.current_question_object and self.current_question_object.is_qualitative()) else 1.0
                since_stopped = (datetime.now() - self._user_stopped_speaking_at).total_seconds()
                if since_stopped < pause_cooldown:
                    continue

            # Trust VAD + STT events: proceed immediately once response is
            # captured and the user has stopped speaking (or turn time exceeded).
            if self.latest_user_response is not None and (not self.user_currently_speaking or self.turn_time_exceeded):
                self._silence_confirmed_time = datetime.now()

                # FOUND USER RESPONSE VIA EVENT!
                question_context = f"Q#{self.current_question_num} ({self.current_question_object.id if self.current_question_object else 'N/A'})"
                elapsed_time = (check_num + 1) * 0.1

                # ── Delivery-state double-check at acceptance ──
                if not self._is_delivery_confirmed(self.current_question_num, participant):
                    _ds_key = self._delivery_key(self.current_question_num, participant)
                    _ds = self.question_delivery_state.get(_ds_key, "unknown")
                    logger.warning(
                        f"🛡️ DELIVERY GUARD (polling-accept): Discarding response — "
                        f"delivery_state={_ds} for Q#{self.current_question_num}/{participant}"
                    )
                    # #region agent log
                    import json as _json
                    with open("/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log", "a") as _f:
                        _f.write(_json.dumps({"location": "moderator_agent.py:ask_next_q:polling_accept_guard", "message": "Polling acceptance blocked by delivery guard", "data": {"delivery_state": _ds, "participant": participant, "question_num": self.current_question_num, "response": str(self.latest_user_response)[:100]}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "H_POLL_GUARD"}) + "\n")
                    # #endregion
                    self.latest_user_response = None
                    self.pending_stt_transcript = None
                    self.response_captured = False
                    continue

                # LATENCY TRACKING: Record when response processing starts
                response_processing_start = datetime.now()
                self._response_processing_start = response_processing_start  # Store for later tracking
                logger.critical(f"⏱️  [{question_context}] LATENCY TRACKING: Response processing started at {response_processing_start.isoformat()}")

                # Snapshot the captured response into a local so that a concurrent
                # gentle-warning reset (which clears self.latest_user_response)
                # cannot cause a NoneType crash downstream.
                _captured_response_text: str = self.latest_user_response

                logger.critical(f"✅ [{question_context}] Response CAPTURED via EVENT (elapsed: {elapsed_time}s from question)")
                logger.critical(f"📝 [{question_context}] Final response: {len(_captured_response_text)} chars")
                logger.critical(f"   Text: '{_captured_response_text}'")

                # CHECK FOR "I DON'T KNOW" RESPONSES - Encourage participant to try again
                logger.critical(f"🔍 [{question_context}] CHECKING FOR UNCERTAIN RESPONSE...")
                logger.critical(f"   Response text: '{_captured_response_text}'")
                logger.critical(f"   encouragement_given flag: {self.encouragement_given}")
                is_uncertain = is_uncertain_response(_captured_response_text)
                logger.critical(f"   is_uncertain_response() returned: {is_uncertain}")

                if is_uncertain:
                    if not self.encouragement_given:
                        # First "I don't know" - encourage them to try
                        self.encouragement_given = True
                        logger.info(f"🤔 [{question_context}] Detected uncertain response, encouraging participant to try again")

                        # IMPORTANT: Log the initial uncertain response BEFORE encouragement
                        responder = self.actual_respondent if self.actual_respondent else participant
                        self.survey_transcript.add_response(
                            question_number=self.current_question_num,
                            participant=responder,
                            response_text=f"[Initial uncertain response before encouragement] {_captured_response_text}"
                        )
                        logger.info(f"📝 Logged uncertain response before encouragement: '{_captured_response_text[:100]}...'")

                        # Get participant name for personalized encouragement
                        speaker_name = (self.actual_respondent if self.actual_respondent else participant).capitalize()

                        # Encouraging message - varies based on question type
                        if self.current_question_object and self.current_question_object.is_qualitative():
                            encouragement = (
                                f"Are you sure, {speaker_name}? "
                                f"There's no right or wrong answer here. "
                                f"Feel free to share whatever comes to mind, even if it's just a quick thought."
                            )
                        else:
                            encouragement = (
                                f"Are you sure, {speaker_name}? "
                                f"Take a moment to think about it. "
                                f"Any answer you give is valuable."
                            )

                        # Say encouragement via TTS
                        await self.agent_session.say(encouragement, allow_interruptions=False)
                        self.survey_transcript.add_acknowledgment(encouragement)
                        logger.info(f"🔊 Encouraged participant: '{encouragement}'")

                        # Reset response capture to wait for new response
                        self.latest_user_response = None
                        self.response_captured = False
                        self.last_stt_fragment = ""

                        # CRITICAL FIX: Reset turn duration monitoring to prevent time warning after encouragement
                        if self.turn_monitor_task:
                            self.turn_monitor_task.cancel()
                            self.turn_monitor_task = None

                        # Reset speech timing
                        self.user_currently_speaking = False
                        self.turn_time_exceeded = False

                        # Restart turn monitor for the new response
                        self.current_turn = TurnInfo(
                            participant_identity=participant,
                            start_time=datetime.now(),
                        )
                        self.turn_monitor_task = asyncio.create_task(
                            self.monitor_turn_duration(self.agent_session)
                        )
                        logger.info(f"🔄 Reset turn monitoring after encouragement")
                        logger.info(f"⏱️  Restarted turn monitor for {participant}")

                        # Continue polling for their new response
                        logger.info(f"⏳ Waiting for new response after encouragement...")
                        continue
                    else:
                        # Already encouraged once - accept the "I don't know" response
                        logger.info(f"🤷 [{question_context}] Participant still uncertain after encouragement, accepting response")

                # ============================================================
                # DETERMINISTIC REPEAT PRE-CHECK (before LLM)
                # Catches "didn't hear the last part", "say that again", etc.
                # instantly — no LLM round-trip needed.
                # ============================================================
                speaker_name = (self.actual_respondent if self.actual_respondent else participant).capitalize()
                if is_repeat_request(_captured_response_text) and not self.question_repeated:
                    self.question_repeated = True
                    logger.info(f"🔁 [{question_context}] HEURISTIC repeat detected — skipping LLM analysis, repeating question immediately")

                    # #region agent log
                    import json as _json
                    with open("/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log", "a") as _f:
                        _f.write(_json.dumps({"location": "moderator_agent.py:ask_next_question:heuristic_repeat", "message": "Heuristic repeat pre-check fired (1st loop)", "data": {"transcript": str(_captured_response_text)[:200], "question_num": self.current_question_num}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "C"}) + "\n")
                    # #endregion

                    repeat_intro = f"Of course, {speaker_name}. I'll repeat the question."
                    await self.agent_session.say(repeat_intro, allow_interruptions=False)
                    await self.agent_session.say(self.current_question, allow_interruptions=True)
                    logger.info(f"🔊 Repeated question (heuristic): '{self.current_question[:100]}...'")

                    self._reset_for_repeat(participant, context="heuristic_repeat_1st_loop")
                    continue

                # ============================================================
                # UNIFIED RESPONSE ANALYSIS (LLM-based)
                # Checks: repeat request, partial answer, already-answered claim, relevance
                # ============================================================
                question_for_analysis = self.current_question_object.question if self.current_question_object else self.current_question
                response_to_analyze = _captured_response_text

                # PARTIAL REPEAT FIX: If we already handled a partial repeat, combine the earlier partial answer
                # with the new response BEFORE analysis. This ensures relevance check sees the full combined answer.
                if self.partial_repeat_handled and self.accumulated_partial_answer:
                    response_to_analyze = f"{self.accumulated_partial_answer} {_captured_response_text}"
                    logger.info(f"📝 [{question_context}] Combined partial + new response for analysis: '{response_to_analyze[:100]}...'")

                logger.info(f"🔍 [{question_context}] Running unified response analysis...")
                survey_desc = self.question_loader.survey_meta.description if self.question_loader and self.question_loader.survey_meta else ""

                # IMMEDIATE ACK: Fire acknowledgment NOW so the user hears
                # feedback within ~1s.  The LLM analysis runs concurrently;
                # if it discovers an issue the correction follows naturally.
                _ack_speaker = (self.actual_respondent if self.actual_respondent else participant).capitalize()
                self._prewarmed_ack_text = f"Thank you, {_ack_speaker}."
                _ack_handle = self.agent_session.say(self._prewarmed_ack_text, allow_interruptions=False)
                self.survey_transcript.add_acknowledgment(self._prewarmed_ack_text)
                self._ack_already_spoken = True
                self._transition_filler_said = True  # ack replaces the "One moment…" filler
                logger.info(f"🗣️ Immediate ack fired (async): '{self._prewarmed_ack_text}'")

                if hasattr(self, '_response_processing_start') and self._response_processing_start:
                    _latency = (datetime.now() - self._response_processing_start).total_seconds()
                    logger.critical(f"⏱️  LATENCY TRACKING: Response-to-Speech latency = {_latency:.2f}s")
                    logger.info(f"📊 METRIC: response_end_to_next_tts_ms={_latency * 1000:.0f}")

                # LLM analysis runs while the ack TTS is playing
                analysis, analysis_secs = await self._analyze_with_filler(question_for_analysis, response_to_analyze, survey_desc)

                # Ensure ack finishes before any follow-up correction
                await _ack_handle

                logger.info(f"📊 [{question_context}] Analysis result: relevant={analysis.is_relevant}, repeat_request={analysis.is_repeat_request}, already_answered={analysis.is_already_answered_claim}, partial_status={analysis.partial_repeat_status}")

                # --- CHECK 1: PARTIAL ANSWER + REPEAT REQUEST ---
                if analysis.partial_repeat_status == "PARTIAL" and not self.partial_repeat_handled:
                    self.partial_repeat_handled = True
                    logger.info(f"📝 [{question_context}] Partial answer detected with repeat request")

                    # Store partial answer for later combination with final response
                    self.accumulated_partial_answer = analysis.partial_answer
                    logger.info(f"💾 Stored partial answer: '{self.accumulated_partial_answer[:100]}...'")

                    # Acknowledge and repeat ONLY unanswered parts
                    partial_intro = f"Got it, {speaker_name}. Let me repeat the rest of the question."
                    await self.agent_session.say(partial_intro, allow_interruptions=False)

                    # Speak only the unanswered sub-questions verbatim
                    await self.agent_session.say(analysis.unanswered_questions, allow_interruptions=True)
                    logger.info(f"🔊 Repeated unanswered parts: '{analysis.unanswered_questions[:100]}...'")

                    # Reset for new response (but keep accumulated_partial_answer)
                    self.latest_user_response = None
                    self.response_captured = False
                    self.last_stt_fragment = ""
                    self.encouragement_given = False

                    # Reset timeout/turn monitoring
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
                    self.current_turn = TurnInfo(
                        participant_identity=participant,
                        start_time=datetime.now(),
                    )
                    self.turn_monitor_task = asyncio.create_task(
                        self.monitor_turn_duration(self.agent_session)
                    )
                    logger.info(f"🔄 Reset timing after partial repeat, waiting for remaining answer...")
                    continue

                # --- CHECK 2: "ALREADY ANSWERED" CLAIM ---
                if analysis.is_already_answered_claim and not self.already_answered_prompt_given:
                    self.already_answered_prompt_given = True
                    logger.info(f"📢 [{question_context}] 'Already answered' claim detected, asking to rephrase")

                    # IMPORTANT: Log the original response BEFORE asking for rephrase
                    # This ensures we capture what participant said even if they just claim "already answered"
                    # Use self.actual_respondent if available, otherwise fall back to expected participant
                    responder = self.actual_respondent if self.actual_respondent else participant
                    self.survey_transcript.add_response(
                        question_number=self.current_question_num,
                        participant=responder,
                        response_text=f"[Initial response before rephrase request] {_captured_response_text}"
                    )
                    logger.info(f"📝 Logged initial response before rephrase: '{_captured_response_text[:100]}...'")

                    rephrase_prompt = (
                        f"I appreciate that, {speaker_name}, but I may not have captured your response correctly. "
                        f"Could you please rephrase or elaborate on your answer? "
                        f"This helps ensure we have your thoughts recorded accurately."
                    )

                    await self.agent_session.say(rephrase_prompt, allow_interruptions=False)
                    self.survey_transcript.add_acknowledgment(rephrase_prompt)
                    logger.info(f"🔊 Asked to rephrase: '{rephrase_prompt}'")

                    # Reset for new response
                    self.latest_user_response = None
                    self.response_captured = False
                    self.last_stt_fragment = ""

                    # Reset timeout/turn monitoring
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
                    self.current_turn = TurnInfo(
                        participant_identity=participant,
                        start_time=datetime.now(),
                    )
                    self.turn_monitor_task = asyncio.create_task(
                        self.monitor_turn_duration(self.agent_session)
                    )
                    logger.info(f"🔄 Reset timing after already-answered prompt, waiting for new response...")
                    continue

                # --- CHECK 3: FULL REPEAT REQUEST (no partial answer) ---
                # Use LLM analysis for repeat detection (context-aware, handles "didn't hear about X" correctly)
                is_full_repeat = analysis.is_repeat_request or (analysis.partial_repeat_status == "REPEAT_ONLY")
                if is_full_repeat and not self.question_repeated:
                    self.question_repeated = True
                    logger.info(f"🔁 [{question_context}] Participant requested to repeat the question")

                    repeat_intro = f"Of course, {speaker_name}. I'll repeat the question."
                    await self.agent_session.say(repeat_intro, allow_interruptions=False)

                    await self.agent_session.say(self.current_question, allow_interruptions=True)
                    logger.info(f"🔊 Repeated question: '{self.current_question[:100]}...'")

                    self._reset_for_repeat(participant, context="llm_repeat_1st_loop")
                    continue
                elif is_full_repeat and self.question_repeated:
                    logger.info(f"🔁 [{question_context}] Already repeated question once, proceeding with response")

                # --- CHECK 4: OFF-TOPIC/IRRELEVANT RESPONSE ---
                if not analysis.is_relevant and not self.relevance_prompt_given:
                    self.relevance_prompt_given = True
                    logger.info(f"📢 [{question_context}] Off-topic response detected, asking for relevant answer")

                    # IMPORTANT: Log the off-topic response BEFORE asking for relevant answer
                    responder = self.actual_respondent if self.actual_respondent else participant
                    self.survey_transcript.add_response(
                        question_number=self.current_question_num,
                        participant=responder,
                        response_text=f"[Initial off-topic response] {_captured_response_text}"
                    )
                    logger.info(f"📝 Logged off-topic response: '{_captured_response_text[:100]}...'")

                    relevance_prompt = (
                        f"Thank you {speaker_name}, but I don't think you quite answered the question. "
                        f"I may be wrong, but I'm going to repeat the question and would you mind answering again after I'm done repeating it?"
                    )

                    await self.agent_session.say(relevance_prompt, allow_interruptions=False)
                    self.survey_transcript.add_acknowledgment(relevance_prompt)
                    logger.info(f"🔊 Asked for relevant response: '{relevance_prompt}'")

                    question_text = self.current_question_object.question if self.current_question_object else ""
                    if question_text:
                        await self.agent_session.say(question_text, allow_interruptions=False)
                        logger.info(f"🔊 Repeated question after off-topic: '{question_text[:60]}...'")

                    # RESET ALL TIMING AND MONITORING
                    logger.critical(f"🔄 OFF-TOPIC RESET START for {participant}")
                    self.latest_user_response = None
                    self.response_captured = False
                    self.last_stt_fragment = ""
                    self.pending_stt_transcript = None
                    self.actual_respondent = None
                    self.encouragement_given = False
                    self.question_repeated = False

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
                    self.current_speaking_duration = 0.0
                    self.accumulated_pause_duration = 0.0
                    self.turn_time_exceeded = False
                    self.current_turn = TurnInfo(
                        participant_identity=participant,
                        start_time=datetime.now(),
                    )
                    self.turn_monitor_task = asyncio.create_task(
                        self.monitor_turn_duration(self.agent_session)
                    )
                    logger.critical(f"🔄 OFF-TOPIC RESET COMPLETE - participant gets full {self.max_turn_duration}s again")
                    logger.critical(f"⏳ Continuing polling loop - waiting for relevant response from {participant}...")
                    continue

                # ============================================================
                # COMBINE PARTIAL ANSWERS (if any) WITH FINAL RESPONSE
                # ============================================================
                if self.accumulated_partial_answer:
                    combined_response = f"{self.accumulated_partial_answer} {_captured_response_text}"
                    logger.info(f"📝 [{question_context}] Combined partial + final response: '{combined_response[:100]}...'")
                    _captured_response_text = combined_response
                    self.latest_user_response = combined_response
                    self.accumulated_partial_answer = ""  # Clear after combining

                # MULTI-PARTICIPANT FIX: Determine WHO actually spoke
                actual_speaker = self.actual_respondent if self.actual_respondent else participant

                # Validation: Check if the right person responded
                if self.actual_respondent and self.actual_respondent != participant:
                    logger.warning(f"⚠️  MISMATCH: Expected {participant} to respond, but {self.actual_respondent} spoke!")
                    logger.info(f"✅ ACCEPTING response from {self.actual_respondent} (being permissive, not kicking)")

                logger.critical(f"👤 Response from: {actual_speaker} (expected: {participant})")

                # Apply STT correction - handle multi-option questions differently
                corrected_response = _captured_response_text
                if self.current_question_object and self.current_question_object.response_options:
                    max_sel = self.current_question_object.max_selections or 1
                    if max_sel > 1:
                        corrected_response = parse_multi_option_response(
                            _captured_response_text,
                            self.current_question_object.response_options,
                            max_sel
                        )
                    else:
                        corrected_response = correct_transcription(
                            _captured_response_text,
                            self.current_question_object.response_options
                        )
                    if corrected_response != _captured_response_text:
                        logger.info(f"Response corrected: '{_captured_response_text[:50]}' → '{corrected_response[:50]}'")

                # Get question details for logging
                question_text = self.current_question_object.question if self.current_question_object else ""

                # Log RAW STT vs corrected response for debugging
                self.stt_debug_logger.log_question_response(
                    question_num=self.current_question_num,
                    question_id=question_id,
                    question_text=question_text,
                    participant=actual_speaker,
                    raw_transcript=_captured_response_text,
                    corrected_response=corrected_response,
                    response_options=self.current_question_object.response_options if self.current_question_object else None,
                    expected_respondent=participant
                )

                # Record in survey transcript (JSON) with ACTUAL speaker
                self.survey_transcript.add_response(
                    question_number=self.current_question_num,
                    participant=actual_speaker,
                    response_text=corrected_response
                )
                logger.critical(f"✅ Response logged to JSON: Q#{self.current_question_num}, {actual_speaker}")

                # Add to CSV DataFrame for analysis with ACTUAL speaker
                question_text = self.current_question_object.question if self.current_question_object else ""
                response_options = self.current_question_object.response_options if self.current_question_object else []

                self.survey_data_export.add_response(
                    participant=actual_speaker,
                    question_number=self.current_question_num,
                    question_id=question_id,
                    question_text=question_text,
                    response_options=response_options,
                    response_text=corrected_response
                )
                logger.critical(f"✅ Response added to CSV DataFrame: Q#{self.current_question_num}")

                # Mark ACTUAL participant as answered (not the expected one)
                self.participant_manager.mark_participant_answered(actual_speaker, self.current_question_num)
                self._set_delivery_state(self.current_question_num, actual_speaker, "answered", context="response_received")
                logger.info(f"✅ Marked {actual_speaker} as answered for question #{self.current_question_num}")

                # Record turn result for acknowledgment (immutable snapshot)
                self._record_turn_result(expected=participant, actual=actual_speaker)

                # Cancel timeout monitoring since we got a response
                self.waiting_for_response = False
                if self.response_timeout_task:
                    self.response_timeout_task.cancel()
                    self.response_timeout_task = None

                # Clear the variable
                self.latest_user_response = None

                user_responded = True
                break

            # METHOD 2: Check conversation context directly (fallback if events fail)
            current_user_msgs = [item for item in self.agent_session._chat_ctx.items
                                  if hasattr(item, 'role') and item.role == 'user']

            if check_num % 10 == 0:  # Log every 5 seconds
                logger.debug(f"Polling check {check_num}: user_msgs={len(current_user_msgs)}, initial={initial_user_msg_count}, waiting_for_response={self.waiting_for_response}")

            if len(current_user_msgs) > initial_user_msg_count:
                # NEW USER MESSAGE FOUND IN CONTEXT!
                latest_msg = current_user_msgs[-1]

                # Extract transcript
                transcript = ""
                if hasattr(latest_msg, 'content'):
                    if isinstance(latest_msg.content, list):
                        transcript = " ".join(str(c) for c in latest_msg.content)
                    else:
                        transcript = str(latest_msg.content)
                else:
                    transcript = str(latest_msg)

                logger.critical(f"✅ Response detected in CONTEXT DIRECTLY (after {(check_num + 1) * 0.5}s)")
                logger.critical(f"📝 Captured response: '{transcript[:50]}...'")

                # CHECK FOR "I DON'T KNOW" RESPONSES - Encourage participant to try again (METHOD 2)
                question_context = f"Q#{self.current_question_num} ({self.current_question_object.id if self.current_question_object else 'N/A'})"
                logger.critical(f"🔍 [{question_context}] CHECKING FOR UNCERTAIN RESPONSE (METHOD 2)...")
                logger.critical(f"   Response text: '{transcript}'")
                logger.critical(f"   encouragement_given flag: {self.encouragement_given}")
                is_uncertain = is_uncertain_response(transcript)
                logger.critical(f"   is_uncertain_response() returned: {is_uncertain}")

                if is_uncertain:
                    if not self.encouragement_given:
                        # First "I don't know" - encourage them to try
                        self.encouragement_given = True
                        question_context = f"Q#{self.current_question_num} ({self.current_question_object.id if self.current_question_object else 'N/A'})"
                        logger.info(f"🤔 [{question_context}] Detected uncertain response (METHOD 2), encouraging participant to try again")

                        # IMPORTANT: Log the initial uncertain response BEFORE encouragement
                        self.survey_transcript.add_response(
                            question_number=self.current_question_num,
                            participant=participant,
                            response_text=f"[Initial uncertain response before encouragement] {transcript}"
                        )
                        logger.info(f"📝 Logged uncertain response before encouragement (METHOD 2): '{transcript[:100] if transcript else 'N/A'}...'")

                        # Get participant name for personalized encouragement
                        speaker_name = participant.capitalize()

                        # Encouraging message - varies based on question type
                        if self.current_question_object and self.current_question_object.is_qualitative():
                            encouragement = (
                                f"Are you sure, {speaker_name}? "
                                f"There's no right or wrong answer here. "
                                f"Feel free to share whatever comes to mind, even if it's just a quick thought."
                            )
                        else:
                            encouragement = (
                                f"Are you sure, {speaker_name}? "
                                f"Take a moment to think about it. "
                                f"Any answer you give is valuable."
                            )

                        # Say encouragement via TTS
                        await self.agent_session.say(encouragement, allow_interruptions=False)
                        self.survey_transcript.add_acknowledgment(encouragement)
                        logger.info(f"🔊 Encouraged participant: '{encouragement}'")

                        # CRITICAL FIX: Reset turn duration monitoring to prevent time warning after encouragement
                        if self.turn_monitor_task:
                            self.turn_monitor_task.cancel()
                            self.turn_monitor_task = None

                        # Reset speech timing
                        self.user_currently_speaking = False
                        self.turn_time_exceeded = False

                        # Restart turn monitor for the new response
                        self.current_turn = TurnInfo(
                            participant_identity=participant,
                            start_time=datetime.now(),
                        )
                        self.turn_monitor_task = asyncio.create_task(
                            self.monitor_turn_duration(self.agent_session)
                        )
                        logger.info(f"🔄 Reset turn monitoring after encouragement (METHOD 2)")
                        logger.info(f"⏱️  Restarted turn monitor for {participant}")

                        # Continue polling for their new response
                        logger.info(f"⏳ Waiting for new response after encouragement...")
                        initial_user_msg_count = len(current_user_msgs)  # Reset baseline for context check
                        continue
                    else:
                        # Already encouraged once - accept the "I don't know" response
                        question_context = f"Q#{self.current_question_num} ({self.current_question_object.id if self.current_question_object else 'N/A'})"
                        logger.info(f"🤷 [{question_context}] Participant still uncertain after encouragement (METHOD 2), accepting response")

                # CHECK FOR REPEAT QUESTION REQUESTS (METHOD 2)
                # Deterministic heuristic first, then fall back to LLM
                is_method2_repeat = is_repeat_request(transcript)
                if not is_method2_repeat:
                    question_for_analysis = self.current_question_object.question if self.current_question_object else self.current_question
                    survey_desc = self.question_loader.survey_meta.description if self.question_loader and self.question_loader.survey_meta else ""
                    method2_analysis = await analyze_response(question_for_analysis, transcript, survey_desc)
                    is_method2_repeat = method2_analysis.is_repeat_request or (method2_analysis.partial_repeat_status == "REPEAT_ONLY")

                if is_method2_repeat:
                    if not self.question_repeated:
                        # First repeat request - repeat the question
                        self.question_repeated = True
                        question_context = f"Q#{self.current_question_num} ({self.current_question_object.id if self.current_question_object else 'N/A'})"
                        logger.info(f"🔁 [{question_context}] Participant requested to repeat the question (METHOD 2)")

                        # Acknowledge and repeat the question
                        repeat_intro = f"Of course, {participant.capitalize()}. I'll repeat the question."
                        await self.agent_session.say(repeat_intro, allow_interruptions=False)

                        # Repeat the actual question
                        await self.agent_session.say(self.current_question, allow_interruptions=True)
                        logger.info(f"🔊 Repeated question: '{self.current_question[:100]}...'")

                        # RESET ALL TIMING AND MONITORING - treat as fresh question
                        # Reset encouragement flag so they can be encouraged again if needed
                        self.encouragement_given = False

                        # Reset timeout monitoring
                        self.waiting_for_response = True
                        self.last_speech_time = None
                        if self.response_timeout_task:
                            self.response_timeout_task.cancel()
                        self.response_timeout_task = asyncio.create_task(
                            self.monitor_response_timeout(participant)
                        )

                        # Reset turn duration monitoring
                        if self.turn_monitor_task:
                            self.turn_monitor_task.cancel()
                            self.turn_monitor_task = None

                        # Reset speech timing
                        self.user_currently_speaking = False
                        self.turn_time_exceeded = False

                        # Restart turn monitor for the new response
                        self.current_turn = TurnInfo(
                            participant_identity=participant,
                            start_time=datetime.now(),
                        )
                        self.turn_monitor_task = asyncio.create_task(
                            self.monitor_turn_duration(self.agent_session)
                        )
                        logger.info(f"🔄 Reset all timing/monitoring after repeating question (METHOD 2)")
                        logger.info(f"⏱️  Restarted turn monitor for {participant}")

                        # Continue polling for their response
                        logger.info(f"⏳ Waiting for response after repeating question...")
                        initial_user_msg_count = len(current_user_msgs)  # Reset baseline for context check
                        continue
                    else:
                        # Already repeated once - accept whatever response they give
                        question_context = f"Q#{self.current_question_num} ({self.current_question_object.id if self.current_question_object else 'N/A'})"
                        logger.info(f"🔁 [{question_context}] Already repeated question once (METHOD 2), proceeding with response")

                # CHECK FOR OFF-TOPIC/IRRELEVANT RESPONSES (only on first attempt) - METHOD 2
                if not self.relevance_prompt_given:
                    # Get the question text for relevance check
                    question_for_check = self.current_question_object.question if self.current_question_object else self.current_question

                    # Check relevance via LLM
                    survey_desc = self.question_loader.survey_meta.description if self.question_loader and self.question_loader.survey_meta else ""
                    is_relevant = await check_response_relevance(question_for_check, transcript, survey_desc)

                    if not is_relevant:
                        # Response is off-topic - ask for relevant response
                        self.relevance_prompt_given = True
                        question_context = f"Q#{self.current_question_num} ({self.current_question_object.id if self.current_question_object else 'N/A'})"
                        logger.info(f"📢 [{question_context}] Off-topic response detected (METHOD 2), asking for relevant answer")

                        # IMPORTANT: Log the off-topic response BEFORE asking for relevant answer
                        self.survey_transcript.add_response(
                            question_number=self.current_question_num,
                            participant=participant,
                            response_text=f"[Initial off-topic response] {transcript}"
                        )
                        logger.info(f"📝 Logged off-topic response (METHOD 2): '{transcript[:100] if transcript else 'N/A'}...'")

                        # Polite request for relevant response
                        relevance_prompt = (
                            f"Thank you {participant.capitalize()}, but I don't think you quite answered the question. "
                            f"I may be wrong, but I'm going to repeat the question and would you mind answering again after I'm done repeating it?"
                        )

                        # Say the prompt via TTS
                        await self.agent_session.say(relevance_prompt, allow_interruptions=False)
                        self.survey_transcript.add_acknowledgment(relevance_prompt)
                        logger.info(f"🔊 Asked for relevant response: '{relevance_prompt}'")

                        question_text = self.current_question_object.question if self.current_question_object else ""
                        if question_text:
                            await self.agent_session.say(question_text, allow_interruptions=False)
                            logger.info(f"🔊 Repeated question after off-topic: '{question_text[:60]}...'")

                        # RESET ALL TIMING AND MONITORING - treat as fresh question
                        self.encouragement_given = False
                        self.question_repeated = False

                        # Reset timeout monitoring
                        self.waiting_for_response = True
                        self.last_speech_time = None
                        if self.response_timeout_task:
                            self.response_timeout_task.cancel()
                        self.response_timeout_task = asyncio.create_task(
                            self.monitor_response_timeout(participant)
                        )

                        # Reset turn duration monitoring
                        if self.turn_monitor_task:
                            self.turn_monitor_task.cancel()
                            self.turn_monitor_task = None

                        # Reset speech timing
                        self.user_currently_speaking = False
                        self.current_speaking_duration = 0.0
                        self.accumulated_pause_duration = 0.0
                        self.turn_time_exceeded = False

                        # CRITICAL: Restart turn monitor for the new response
                        self.current_turn = TurnInfo(
                            participant_identity=participant,
                            start_time=datetime.now(),
                        )
                        self.turn_monitor_task = asyncio.create_task(
                            self.monitor_turn_duration(self.agent_session)
                        )
                        logger.info(f"🔄 Reset all timing/monitoring after relevance prompt (METHOD 2)")
                        logger.info(f"⏱️  Restarted turn monitor for {participant}")

                        # Continue polling for their relevant response
                        logger.info(f"⏳ Waiting for relevant response...")
                        initial_user_msg_count = len(current_user_msgs)  # Reset baseline for context check
                        continue

                # Apply STT correction - handle multi-option questions differently
                corrected_text = transcript
                if self.current_question_object and self.current_question_object.response_options:
                    max_sel = self.current_question_object.max_selections or 1
                    if max_sel > 1:
                        # Multi-option question - parse and match multiple responses
                        corrected_text = parse_multi_option_response(
                            transcript,
                            self.current_question_object.response_options,
                            max_sel
                        )
                    else:
                        # Single option question - use standard correction
                        corrected_text = correct_transcription(transcript, self.current_question_object.response_options)
                    if corrected_text != transcript:
                        logger.info(f"Response corrected: '{transcript}' → '{corrected_text}'")

                # Get question details for logging
                question_text = self.current_question_object.question if self.current_question_object else ""

                # Log RAW STT vs corrected response for debugging
                self.stt_debug_logger.log_question_response(
                    question_num=self.current_question_num,
                    question_id=question_id,
                    question_text=question_text,
                    participant=participant,
                    raw_transcript=transcript,
                    corrected_response=corrected_text,
                    response_options=self.current_question_object.response_options if self.current_question_object else None,
                    expected_respondent=participant
                )

                # Record in survey transcript (JSON)
                self.survey_transcript.add_response(
                    question_number=self.current_question_num,
                    participant=participant,
                    response_text=corrected_text
                )
                logger.critical(f"✅ Response logged to JSON: Q#{self.current_question_num}, {participant}")

                # Add to CSV DataFrame for analysis
                question_text = self.current_question_object.question if self.current_question_object else ""
                response_options = self.current_question_object.response_options if self.current_question_object else []

                self.survey_data_export.add_response(
                    participant=participant,
                    question_number=self.current_question_num,
                    question_id=question_id,
                    question_text=question_text,
                    response_options=response_options,
                    response_text=corrected_text
                )
                logger.critical(f"✅ Response added to CSV DataFrame: Q#{self.current_question_num}")

                user_responded = True
                break

        if not user_responded:
            logger.warning(f"⏱️  Max wait time reached ({polling_timeout}s), no response detected via EVENT or CONTEXT")

            # Audible acknowledgment so the user knows the system is still alive
            try:
                display_name = self.participant_manager.get_display_name(participant) if self.participant_manager else ""
                timeout_msg = f"I didn't catch a response, {display_name}. Let me move on." if display_name else "I didn't catch a response. Let me move on."
                await self.agent_session.say(timeout_msg, allow_interruptions=False)
                self.survey_transcript.add_acknowledgment(timeout_msg)
            except Exception as e:
                logger.warning(f"Could not speak timeout message: {e}")

            # Record timeout/no-response in ALL outputs so question is not lost
            question_id = self.current_question_object.id if self.current_question_object else f"Q{self.current_question_num}"
            question_text = self.current_question_object.question if self.current_question_object else ""
            response_options = self.current_question_object.response_options if self.current_question_object else []
            timeout_marker = "[NO RESPONSE - TIMEOUT]"

            # Log to STT debug logger (for comparison report)
            self.stt_debug_logger.log_question_response(
                question_num=self.current_question_num,
                question_id=question_id,
                question_text=question_text,
                participant=participant,
                raw_transcript=timeout_marker,
                corrected_response=timeout_marker,
                response_options=response_options,
                expected_respondent=participant
            )
            logger.info(f"📝 Recorded timeout for Q#{self.current_question_num} in STT debug log")

            # Record in survey transcript (JSON) with timeout marker
            self.survey_transcript.add_response(
                question_number=self.current_question_num,
                participant=participant,
                response_text=timeout_marker
            )
            logger.info(f"📝 Recorded timeout for Q#{self.current_question_num} in JSON transcript")

            # Add to CSV DataFrame with timeout marker
            self.survey_data_export.add_response(
                participant=participant,
                question_number=self.current_question_num,
                question_id=question_id,
                question_text=question_text,
                response_options=response_options,
                response_text=timeout_marker
            )
            logger.info(f"📝 Recorded timeout for Q#{self.current_question_num} in CSV DataFrame")

            # Only count timeout as answered if question delivery was confirmed.
            if self._is_delivery_confirmed(self.current_question_num, participant):
                self.participant_manager.mark_participant_answered(participant, self.current_question_num)
                self._set_delivery_state(self.current_question_num, participant, "timeout", context="timeout_after_delivery")
            else:
                logger.warning(
                    f"Skipping answered-mark on timeout for {participant}: delivery was not confirmed; requeueing."
                )
                self._register_missing_participant_for_retry(participant, context="timeout_without_delivery")

            # Record turn result for timeout — expected == actual since no one else spoke
            self._record_turn_result(expected=participant, actual=participant, was_timeout=True)

        # End audit for this question (save to JSON)
        self.audit_logger.end_question()

        # NOTE: last_respondent is set inside the response capture blocks above
        # (using actual_speaker, not the expected participant)

        # Stop turn duration monitoring (response captured)
        if self.turn_monitor_task:
            self.turn_monitor_task.cancel()
            self.turn_monitor_task = None
        self.current_turn = None
        logger.info("✅ Stopped turn duration monitoring (response captured)")

        # Move to next participant/question
        await self.move_to_next_participant()

    async def move_to_next_participant(self):
        """
        After receiving a response, acknowledge it briefly and move to next participant or next question.
        FIXED: Checks participant count BEFORE speaking, prevents loops.
        """
        if not self.question_loader or not self.participant_manager or not self.agent_session:
            return

        if not self.current_question:
            logger.warning("No current question, starting with first question")
            await self.ask_next_question()
            return

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

                    await self.agent_session.say(ack_text, allow_interruptions=False)
                    self.survey_transcript.add_acknowledgment(ack_text)
                except RuntimeError as e:
                    logger.warning(f"Could not say acknowledgment, session may be closing: {e}")

            # Inter-question breathing room so the session doesn't feel rushed
            await asyncio.sleep(2)

            await self.ask_next_question()
            return

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

                    await self.agent_session.say(ack_text, allow_interruptions=False)
                    self.survey_transcript.add_acknowledgment(ack_text)
                except RuntimeError as e:
                    logger.warning(f"Could not say acknowledgment, session may be closing: {e}")

            # Inter-question breathing room so the session doesn't feel rushed
            await asyncio.sleep(2)

            await self.ask_next_question()
            return

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

            # For multi-participant, still append the transition prompt
            total_participants = len(self.participant_manager.participants)
            if total_participants > 1 and available_remaining > 1:
                transition_text = "Let's hear from the next participant."
                await self.agent_session.say(transition_text, allow_interruptions=False)
                self.survey_transcript.add_acknowledgment(transition_text)
        else:
            # Context-aware acknowledgment using TurnResult for correct name
            total_participants = len(self.participant_manager.participants)
            _tr = self._last_turn_result
            respondent_name = _tr.ack_name if _tr else (self.last_respondent or "")
            logger.info(f"🗣️ ACK: expected={_tr.expected_identity if _tr else '?'} actual={_tr.actual_identity if _tr else '?'} ack_name={respondent_name}")

            if total_participants == 1:
                ack_text = f"Thank you, {respondent_name}." if respondent_name else "Thank you."
            elif available_remaining > 1:
                ack_text = f"Thank you, {respondent_name}. Let's hear from the next participant." if respondent_name else "Thank you. Let's hear from the next participant."
            else:
                ack_text = f"Thank you, {respondent_name}." if respondent_name else "Thank you."

            # LATENCY TRACKING: Calculate time from response detection to agent speaking
            if hasattr(self, '_response_processing_start') and self._response_processing_start:
                latency = (datetime.now() - self._response_processing_start).total_seconds()
                logger.critical(f"⏱️  LATENCY TRACKING: Response-to-Speech latency = {latency:.2f}s")
                logger.info(f"📊 METRIC: response_end_to_next_tts_ms={latency * 1000:.0f}")

                # #region agent log
                import json as _json
                with open("/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log", "a") as _f:
                    _f.write(_json.dumps({"location": "moderator_agent.py:move_to_next_participant", "message": "Transition latency", "data": {"response_end_to_next_tts_ms": round(latency * 1000), "question_num": self.current_question_num, "next_participant": participant, "filler_spoken": self._transition_filler_said}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "METRICS"}) + "\n")
                # #endregion

                self._response_processing_start = None  # Reset for next response

            await self.agent_session.say(ack_text, allow_interruptions=False)
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

        # Reset response flags and fragments before asking question
        self.response_captured = False
        self._transition_filler_said = False
        self.latest_user_response = None
        self.pending_stt_transcript = None  # Prevent stale transcripts from previous turn
        self.response_fragments = []
        self.last_fragment_time = None
        self.last_stt_fragment = ""  # Reset fragment tracking for new question
        self._user_stopped_speaking_at = None  # Reset pause-cooldown timer
        self.encouragement_given = False  # Reset encouragement flag for new participant
        self.question_repeated = False  # Reset repeat flag for new participant
        self.relevance_prompt_given = False  # Reset relevance flag for new participant
        self.partial_repeat_handled = False  # Reset partial repeat flag for new participant
        self.already_answered_prompt_given = False  # Reset already-answered flag for new participant
        self.accumulated_partial_answer = ""  # Clear any accumulated partial answers
        self.turn_time_exceeded = False  # Reset turn time exceeded flag for new participant
        self._ack_already_spoken = False  # Reset ack flag for new participant
        self._prewarmed_ack_text = None  # Clear pre-warmed ack text

        # Clean up any existing turn monitoring from previous participant
        if self.turn_monitor_task:
            self.turn_monitor_task.cancel()
            self.turn_monitor_task = None
        self.current_turn = None

        # DYNAMIC VAD CONFIGURATION: Adjust silence threshold based on question type
        # Reduced for faster response times while still allowing natural pauses
        if self.current_question_object:
            if self.current_question_object.is_qualitative():
                self.agent_session.vad.update_options(min_silence_duration=0.8)
                logger.info(f"🎙️  VAD updated: min_silence_duration=0.8s (qualitative question)")
            elif self.current_question_object.is_quantitative():
                self.agent_session.vad.update_options(min_silence_duration=0.4)
                logger.info(f"🎙️  VAD updated: min_silence_duration=0.4s (quantitative question)")
            else:
                self.agent_session.vad.update_options(min_silence_duration=0.5)
                logger.info(f"🎙️  VAD updated: min_silence_duration=0.5s (default)")

        # MULTI-PARTICIPANT FIX: Set who we're expecting to respond
        self.expected_respondent = participant
        self.actual_respondent = None  # Will be set when we detect audio activity
        self.turn_transition_time = datetime.now()  # For spillover detection
        self._set_delivery_state(self.current_question_num, participant, "delivering", context="move_to_next_participant")

        logger.critical("=" * 80)
        logger.critical(f"🔧 AUDIO ROUTING TRACE - Question #{self.current_question_num} to {participant}")

        _respondent_count_move = self._active_respondent_count()
        if _respondent_count_move > 1:
            # MULTI-PARTICIPANT: Lock STT + mute others to prevent cross-talk
            logger.critical(f"   {_respondent_count_move} active respondents — enabling STT lock")
            if self.agent_session and hasattr(self.agent_session, '_room_io') and self.agent_session._room_io:
                room_io = self.agent_session._room_io
                if hasattr(room_io, '_audio_input') and room_io._audio_input:
                    audio_input = room_io._audio_input
                    logger.critical(f"🎙️  EXECUTING set_participant('{participant}')...")
                    try:
                        audio_input.set_participant(participant)
                        logger.critical(f"✅ STT set_participant('{participant}') — immediate")
                    except Exception as e:
                        logger.error(f"❌ EXCEPTION during set_participant: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                else:
                    logger.error(f"❌ audio_input not available! Cannot switch STT to {participant}")
            else:
                logger.error(f"❌ room_io not available! Cannot switch STT to {participant}")
        else:
            logger.critical(f"   {_respondent_count_move} active respondent — skipping STT lock (solo mode)")

        # In observer mode: Keep STT on observer while speaking (to hear pause command)
        if self.observer_mode_enabled:
            # CRITICAL: Clear any pending STT transcripts from previous participant
            # (Response is already saved to transcript/export, so this won't lose data)
            self.pending_stt_transcript = None

            observer_identity = self.participant_manager.get_observer_identity()
            if observer_identity and self.agent_session and hasattr(self.agent_session, '_room_io') and self.agent_session._room_io:
                try:
                    audio_input = self.agent_session._room_io._audio_input
                    if audio_input:
                        audio_input.set_participant(observer_identity)
                        logger.critical(f"👁️ STT set to observer while asking question (can hear pause)")
                except Exception as e:
                    logger.warning(f"Could not set STT to observer: {e}")

        # DIRECT TTS - Speak the question with truncation detection + retry
        await self._speak_question_safely(
            exact_text_to_say,
            context=f"move_Q{self.current_question_num}_{participant}",
        )
        self._set_delivery_state(self.current_question_num, participant, "delivered", context="move_to_next_participant")

        # Check if paused during the question
        if self.survey_state == SurveyState.PAUSED:
            logger.info("👁️ Survey paused during question - waiting for resume")
            while self.survey_state == SurveyState.PAUSED:
                await asyncio.sleep(0.5)
            logger.info("👁️ Survey resumed - continuing with response collection")

        logger.critical("=" * 80)
        logger.critical(f"✅ Question spoken via DIRECT TTS (0% LLM involvement)")

        # Calculate max polling time: Must be longer than max turn duration to allow graceful time management
        # Max turn duration = base + first_grace + second_grace + buffer
        max_turn_time = self.max_turn_duration + self.first_interrupt_grace + self.second_interrupt_grace
        polling_timeout = max_turn_time + 10  # Add 10s buffer for silence detection and processing
        max_checks = int(polling_timeout / 0.1)  # Convert to number of 0.1s checks

        logger.critical(f"⏳ SMART POLLING: Waiting for user response (max {polling_timeout}s)...")
        logger.critical(f"   - Max turn duration: {max_turn_time}s (base={self.max_turn_duration}s + graces={self.first_interrupt_grace + self.second_interrupt_grace}s)")
        logger.critical(f"   - Polling timeout: {polling_timeout}s (turn + 10s buffer)")

        # Initialize timeout monitoring variables
        self.waiting_for_response = True
        self.last_speech_time = None

        # Start timeout monitoring task (will prompt after 10s, move on after 20s)
        if self.response_timeout_task:
            self.response_timeout_task.cancel()
        self.response_timeout_task = asyncio.create_task(
            self.monitor_response_timeout(participant)
        )
        logger.info(f"⏱️  Started timeout monitoring for {participant} (prompt at 10s, skip at 20s)")

        # SMART POLLING: Same as in ask_next_question()
        user_responded = False
        initial_user_msg_count = len([item for item in self.agent_session._chat_ctx.items
                                       if hasattr(item, 'role') and item.role == 'user'])

        import time as _time
        self._polling_deadline = _time.time() + polling_timeout

        for check_num in range(max_checks * 3):  # generous upper bound; deadline is the real limiter
            await asyncio.sleep(0.1)  # Fast 100ms polling — trust VAD for silence

            if _time.time() > self._polling_deadline:
                logger.warning(f"⏱️  Polling deadline reached ({polling_timeout}s effective)")
                break

            # TIMEOUT MONITOR SIGNAL: break early when monitor confirms no-response
            if not self.waiting_for_response:
                logger.warning(f"⏱️  Timeout monitor signaled no-response — exiting poll early (2nd loop)")
                break

            # OBSERVER COMMAND CHECK: Periodically switch STT to observer to hear pause/resume commands
            if self.observer_mode_enabled and check_num % 20 == 10:  # Every ~2 seconds
                observer_identity = self.participant_manager.get_observer_identity()
                if observer_identity and self.agent_session and hasattr(self.agent_session, '_room_io') and self.agent_session._room_io:
                    try:
                        audio_input = self.agent_session._room_io._audio_input
                        if audio_input:
                            audio_input.set_participant(observer_identity)
                            logger.debug(f"👁️ STT briefly switched to observer for command check")
                            await asyncio.sleep(0.3)
                            if self.survey_state != SurveyState.PAUSED:
                                audio_input.set_participant(participant)
                    except Exception as e:
                        logger.warning(f"Observer command check failed: {e}")

            # CRITICAL: Check if survey was paused by observer - wait until resumed
            if self.survey_state == SurveyState.PAUSED:
                logger.info(f"👁️ Survey PAUSED during response polling - waiting for resume")
                observer_identity = self.participant_manager.get_observer_identity()
                if observer_identity and self.agent_session and hasattr(self.agent_session, '_room_io') and self.agent_session._room_io:
                    try:
                        audio_input = self.agent_session._room_io._audio_input
                        if audio_input:
                            audio_input.set_participant(observer_identity)
                            logger.info(f"👁️ STT set to observer for 'resume' command")
                    except Exception as e:
                        logger.warning(f"Could not set STT to observer: {e}")
                while self.survey_state == SurveyState.PAUSED:
                    await asyncio.sleep(0.5)
                logger.info(f"👁️ Survey RESUMED - continuing response polling")
                # After resume, the question will be re-asked via handle_observer_command
                # Exit this polling loop to avoid duplicate processing
                return

            # CRITICAL: If user is currently speaking, don't process yet - wait for them to finish!
            if self.user_currently_speaking:
                if check_num % 20 == 0:  # Log every ~2 seconds while waiting
                    logger.info(f"⏳ User is speaking... waiting for them to finish (check {check_num})")
                continue  # Skip to next check, don't process response yet

            # PAUSE COOLDOWN (2nd loop): Same as first loop — wait after the user
            # stops speaking to tolerate "uhh" / "umm" mid-thought pauses.
            if (self.latest_user_response is not None
                and not self.user_currently_speaking
                and not self.turn_time_exceeded
                and self._user_stopped_speaking_at is not None):
                pause_cooldown = 1.5 if (self.current_question_object and self.current_question_object.is_qualitative()) else 1.0
                since_stopped = (datetime.now() - self._user_stopped_speaking_at).total_seconds()
                if since_stopped < pause_cooldown:
                    continue

            # Trust VAD + STT events: proceed immediately once response is
            # captured and the user has stopped speaking.
            if self.latest_user_response is not None and not self.user_currently_speaking:
                self._silence_confirmed_time = datetime.now()

                # FOUND USER RESPONSE VIA EVENT!
                question_context = f"Q#{self.current_question_num} ({self.current_question_object.id if self.current_question_object else 'N/A'})"
                elapsed_time = (check_num + 1) * 0.1

                # ── Delivery-state double-check at acceptance ──
                if not self._is_delivery_confirmed(self.current_question_num, participant):
                    _ds_key = self._delivery_key(self.current_question_num, participant)
                    _ds = self.question_delivery_state.get(_ds_key, "unknown")
                    logger.warning(
                        f"🛡️ DELIVERY GUARD (polling-accept-2nd): Discarding response — "
                        f"delivery_state={_ds} for Q#{self.current_question_num}/{participant}"
                    )
                    # #region agent log
                    import json as _json
                    with open("/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log", "a") as _f:
                        _f.write(_json.dumps({"location": "moderator_agent.py:move_to_next:polling_accept_guard", "message": "Polling acceptance blocked by delivery guard (2nd loop)", "data": {"delivery_state": _ds, "participant": participant, "question_num": self.current_question_num, "response": str(self.latest_user_response)[:100]}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "H_POLL_GUARD"}) + "\n")
                    # #endregion
                    self.latest_user_response = None
                    self.pending_stt_transcript = None
                    self.response_captured = False
                    continue

                # Snapshot the captured response into a local so that a concurrent
                # gentle-warning reset (which clears self.latest_user_response)
                # cannot cause a NoneType crash downstream.
                _captured_response_text: str = self.latest_user_response

                logger.critical(f"✅ [{question_context}] Response CAPTURED via EVENT (elapsed: {elapsed_time}s from question)")
                logger.critical(f"📝 [{question_context}] Final response: {len(_captured_response_text)} chars")
                logger.critical(f"   Text: '{_captured_response_text}'")

                # CHECK FOR "I DON'T KNOW" RESPONSES - Encourage participant to try again (2nd polling loop)
                logger.critical(f"🔍 [{question_context}] CHECKING FOR UNCERTAIN RESPONSE (2nd loop)...")
                logger.critical(f"   Response text: '{_captured_response_text}'")
                logger.critical(f"   encouragement_given flag: {self.encouragement_given}")
                is_uncertain = is_uncertain_response(_captured_response_text)
                logger.critical(f"   is_uncertain_response() returned: {is_uncertain}")

                if is_uncertain:
                    if not self.encouragement_given:
                        # First "I don't know" - encourage them to try
                        self.encouragement_given = True
                        logger.info(f"🤔 [{question_context}] Detected uncertain response (2nd loop), encouraging participant to try again")

                        # IMPORTANT: Log the initial uncertain response BEFORE encouragement
                        responder = self.actual_respondent if self.actual_respondent else participant
                        self.survey_transcript.add_response(
                            question_number=self.current_question_num,
                            participant=responder,
                            response_text=f"[Initial uncertain response before encouragement] {_captured_response_text}"
                        )
                        logger.info(f"📝 Logged uncertain response before encouragement (2nd loop): '{_captured_response_text[:100]}...'")

                        # Get participant name for personalized encouragement
                        speaker_name = (self.actual_respondent if self.actual_respondent else participant).capitalize()

                        # Encouraging message - varies based on question type
                        if self.current_question_object and self.current_question_object.is_qualitative():
                            encouragement = (
                                f"Are you sure, {speaker_name}? "
                                f"There's no right or wrong answer here. "
                                f"Feel free to share whatever comes to mind, even if it's just a quick thought."
                            )
                        else:
                            encouragement = (
                                f"Are you sure, {speaker_name}? "
                                f"Take a moment to think about it. "
                                f"Any answer you give is valuable."
                            )

                        # Say encouragement via TTS
                        await self.agent_session.say(encouragement, allow_interruptions=False)
                        self.survey_transcript.add_acknowledgment(encouragement)
                        logger.info(f"🔊 Encouraged participant: '{encouragement}'")

                        # Reset response capture to wait for new response
                        self.latest_user_response = None
                        self.response_captured = False
                        self.last_stt_fragment = ""

                        # CRITICAL FIX: Reset turn duration monitoring to prevent time warning after encouragement
                        if self.turn_monitor_task:
                            self.turn_monitor_task.cancel()
                            self.turn_monitor_task = None

                        # Reset speech timing
                        self.user_currently_speaking = False
                        self.turn_time_exceeded = False

                        # Restart turn monitor for the new response
                        self.current_turn = TurnInfo(
                            participant_identity=participant,
                            start_time=datetime.now(),
                        )
                        self.turn_monitor_task = asyncio.create_task(
                            self.monitor_turn_duration(self.agent_session)
                        )
                        logger.info(f"🔄 Reset turn monitoring after encouragement (2nd loop)")
                        logger.info(f"⏱️  Restarted turn monitor for {participant}")

                        # Continue polling for their new response
                        logger.info(f"⏳ Waiting for new response after encouragement...")
                        continue
                    else:
                        # Already encouraged once - accept the "I don't know" response
                        logger.info(f"🤷 [{question_context}] Participant still uncertain after encouragement (2nd loop), accepting response")

                # ============================================================
                # DETERMINISTIC REPEAT PRE-CHECK (before LLM) - 2nd polling loop
                # ============================================================
                speaker_name = (self.actual_respondent if self.actual_respondent else participant).capitalize()
                if is_repeat_request(_captured_response_text) and not self.question_repeated:
                    self.question_repeated = True
                    logger.info(f"🔁 [{question_context}] HEURISTIC repeat detected (2nd loop) — skipping LLM, repeating question")

                    repeat_intro = f"Of course, {speaker_name}. I'll repeat the question."
                    await self.agent_session.say(repeat_intro, allow_interruptions=False)
                    await self.agent_session.say(self.current_question, allow_interruptions=True)
                    logger.info(f"🔊 Repeated question (heuristic, 2nd loop): '{self.current_question[:100]}...'")

                    self._reset_for_repeat(participant, context="heuristic_repeat_2nd_loop")
                    continue

                # ============================================================
                # UNIFIED RESPONSE ANALYSIS (LLM-based) - 2nd polling loop
                # Checks: repeat request, partial answer, already-answered claim, relevance
                # ============================================================
                question_for_analysis = self.current_question_object.question if self.current_question_object else self.current_question
                response_to_analyze = _captured_response_text

                # PARTIAL REPEAT FIX: If we already handled a partial repeat, combine the earlier partial answer
                # with the new response BEFORE analysis. This ensures relevance check sees the full combined answer.
                if self.partial_repeat_handled and self.accumulated_partial_answer:
                    response_to_analyze = f"{self.accumulated_partial_answer} {_captured_response_text}"
                    logger.info(f"📝 [{question_context}] Combined partial + new response for analysis (2nd loop): '{response_to_analyze[:100]}...'")

                logger.info(f"🔍 [{question_context}] Running unified response analysis (2nd loop)...")
                survey_desc = self.question_loader.survey_meta.description if self.question_loader and self.question_loader.survey_meta else ""

                # IMMEDIATE ACK: Fire acknowledgment NOW so the user hears
                # feedback within ~1s.  The LLM analysis runs concurrently;
                # if it discovers an issue the correction follows naturally.
                _ack_speaker = (self.actual_respondent if self.actual_respondent else participant).capitalize()
                self._prewarmed_ack_text = f"Thank you, {_ack_speaker}."
                _ack_handle = self.agent_session.say(self._prewarmed_ack_text, allow_interruptions=False)
                self.survey_transcript.add_acknowledgment(self._prewarmed_ack_text)
                self._ack_already_spoken = True
                self._transition_filler_said = True  # ack replaces the "One moment…" filler
                logger.info(f"🗣️ Immediate ack fired (async, 2nd loop): '{self._prewarmed_ack_text}'")

                if hasattr(self, '_response_processing_start') and self._response_processing_start:
                    _latency = (datetime.now() - self._response_processing_start).total_seconds()
                    logger.critical(f"⏱️  LATENCY TRACKING: Response-to-Speech latency = {_latency:.2f}s")
                    logger.info(f"📊 METRIC: response_end_to_next_tts_ms={_latency * 1000:.0f}")

                # LLM analysis runs while the ack TTS is playing
                analysis, analysis_secs = await self._analyze_with_filler(question_for_analysis, response_to_analyze, survey_desc)

                # Ensure ack finishes before any follow-up correction
                await _ack_handle

                logger.info(f"📊 [{question_context}] Analysis result: relevant={analysis.is_relevant}, repeat_request={analysis.is_repeat_request}, already_answered={analysis.is_already_answered_claim}, partial_status={analysis.partial_repeat_status}")

                # --- CHECK 1: PARTIAL ANSWER + REPEAT REQUEST ---
                if analysis.partial_repeat_status == "PARTIAL" and not self.partial_repeat_handled:
                    self.partial_repeat_handled = True
                    logger.info(f"📝 [{question_context}] Partial answer detected with repeat request (2nd loop)")

                    # Store partial answer for later combination with final response
                    self.accumulated_partial_answer = analysis.partial_answer
                    logger.info(f"💾 Stored partial answer: '{self.accumulated_partial_answer[:100]}...'")

                    # Acknowledge and repeat ONLY unanswered parts
                    partial_intro = f"Got it, {speaker_name}. Let me repeat the rest of the question."
                    await self.agent_session.say(partial_intro, allow_interruptions=False)

                    # Speak only the unanswered sub-questions verbatim
                    await self.agent_session.say(analysis.unanswered_questions, allow_interruptions=True)
                    logger.info(f"🔊 Repeated unanswered parts: '{analysis.unanswered_questions[:100]}...'")

                    # Reset for new response (but keep accumulated_partial_answer)
                    self.latest_user_response = None
                    self.response_captured = False
                    self.last_stt_fragment = ""
                    self.encouragement_given = False

                    # Reset timeout/turn monitoring
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
                    self.current_turn = TurnInfo(
                        participant_identity=participant,
                        start_time=datetime.now(),
                    )
                    self.turn_monitor_task = asyncio.create_task(
                        self.monitor_turn_duration(self.agent_session)
                    )
                    logger.info(f"🔄 Reset timing after partial repeat (2nd loop), waiting for remaining answer...")
                    continue

                # --- CHECK 2: "ALREADY ANSWERED" CLAIM ---
                if analysis.is_already_answered_claim and not self.already_answered_prompt_given:
                    self.already_answered_prompt_given = True
                    logger.info(f"📢 [{question_context}] 'Already answered' claim detected (2nd loop), asking to rephrase")

                    # IMPORTANT: Log the original response BEFORE asking for rephrase
                    # This ensures we capture what participant said even if they just claim "already answered"
                    self.survey_transcript.add_response(
                        question_number=self.current_question_num,
                        participant=participant,
                        response_text=f"[Initial response before rephrase request] {_captured_response_text}"
                    )
                    logger.info(f"📝 Logged initial response before rephrase (2nd loop): '{_captured_response_text[:100]}...'")

                    rephrase_prompt = (
                        f"I appreciate that, {speaker_name}, but I may not have captured your response correctly. "
                        f"Could you please rephrase or elaborate on your answer? "
                        f"This helps ensure we have your thoughts recorded accurately."
                    )

                    await self.agent_session.say(rephrase_prompt, allow_interruptions=False)
                    self.survey_transcript.add_acknowledgment(rephrase_prompt)
                    logger.info(f"🔊 Asked to rephrase: '{rephrase_prompt}'")

                    # Reset for new response
                    self.latest_user_response = None
                    self.response_captured = False
                    self.last_stt_fragment = ""

                    # Reset timeout/turn monitoring
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
                    self.current_turn = TurnInfo(
                        participant_identity=participant,
                        start_time=datetime.now(),
                    )
                    self.turn_monitor_task = asyncio.create_task(
                        self.monitor_turn_duration(self.agent_session)
                    )
                    logger.info(f"🔄 Reset timing after already-answered prompt (2nd loop), waiting for new response...")
                    continue

                # --- CHECK 3: FULL REPEAT REQUEST (no partial answer) ---
                # Use LLM analysis for repeat detection (context-aware, handles "didn't hear about X" correctly)
                is_full_repeat = analysis.is_repeat_request or (analysis.partial_repeat_status == "REPEAT_ONLY")
                if is_full_repeat and not self.question_repeated:
                    self.question_repeated = True
                    logger.info(f"🔁 [{question_context}] Participant requested to repeat the question (2nd loop)")

                    repeat_intro = f"Of course, {speaker_name}. I'll repeat the question."
                    await self.agent_session.say(repeat_intro, allow_interruptions=False)

                    await self.agent_session.say(self.current_question, allow_interruptions=True)
                    logger.info(f"🔊 Repeated question: '{self.current_question[:100]}...'")

                    self._reset_for_repeat(participant, context="llm_repeat_2nd_loop")
                    continue
                elif is_full_repeat and self.question_repeated:
                    logger.info(f"🔁 [{question_context}] Already repeated question once (2nd loop), proceeding with response")

                # --- CHECK 4: OFF-TOPIC/IRRELEVANT RESPONSE ---
                if not analysis.is_relevant and not self.relevance_prompt_given:
                    self.relevance_prompt_given = True
                    logger.info(f"📢 [{question_context}] Off-topic response detected (2nd loop), asking for relevant answer")

                    # IMPORTANT: Log the off-topic response BEFORE asking for relevant answer
                    responder = self.actual_respondent if self.actual_respondent else participant
                    self.survey_transcript.add_response(
                        question_number=self.current_question_num,
                        participant=responder,
                        response_text=f"[Initial off-topic response] {_captured_response_text}"
                    )
                    logger.info(f"📝 Logged off-topic response (2nd loop): '{_captured_response_text[:100]}...'")

                    relevance_prompt = (
                        f"Thank you {speaker_name}, but I don't think you quite answered the question. "
                        f"I may be wrong, but I'm going to repeat the question and would you mind answering again after I'm done repeating it?"
                    )

                    await self.agent_session.say(relevance_prompt, allow_interruptions=False)
                    self.survey_transcript.add_acknowledgment(relevance_prompt)
                    logger.info(f"🔊 Asked for relevant response: '{relevance_prompt}'")

                    question_text = self.current_question_object.question if self.current_question_object else ""
                    if question_text:
                        await self.agent_session.say(question_text, allow_interruptions=False)
                        logger.info(f"🔊 Repeated question after off-topic: '{question_text[:60]}...'")

                    # RESET ALL TIMING AND MONITORING
                    self.latest_user_response = None
                    self.response_captured = False
                    self.last_stt_fragment = ""
                    self.encouragement_given = False
                    self.question_repeated = False

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
                    self.current_speaking_duration = 0.0
                    self.accumulated_pause_duration = 0.0
                    self.turn_time_exceeded = False
                    self.current_turn = TurnInfo(
                        participant_identity=participant,
                        start_time=datetime.now(),
                    )
                    self.turn_monitor_task = asyncio.create_task(
                        self.monitor_turn_duration(self.agent_session)
                    )
                    logger.info(f"🔄 Reset all timing/monitoring after relevance prompt (2nd loop)")
                    logger.info(f"⏳ Waiting for relevant response...")
                    continue

                # ============================================================
                # COMBINE PARTIAL ANSWERS (if any) WITH FINAL RESPONSE - 2nd loop
                # ============================================================
                if self.accumulated_partial_answer:
                    combined_response = f"{self.accumulated_partial_answer} {_captured_response_text}"
                    logger.info(f"📝 [{question_context}] Combined partial + final response (2nd loop): '{combined_response[:100]}...'")
                    _captured_response_text = combined_response
                    self.latest_user_response = combined_response
                    self.accumulated_partial_answer = ""  # Clear after combining

                # Get question details
                question_id = self.current_question_object.id if self.current_question_object else f"Q{self.current_question_num}"
                question_text = self.current_question_object.question if self.current_question_object else ""
                response_options = self.current_question_object.response_options if self.current_question_object else []

                # MULTI-PARTICIPANT FIX: Determine WHO actually spoke
                actual_speaker = self.actual_respondent if self.actual_respondent else participant

                # Validation: Check if the right person responded
                if self.actual_respondent and self.actual_respondent != participant:
                    logger.warning(f"⚠️  MISMATCH: Expected {participant} to respond, but {self.actual_respondent} spoke!")
                    logger.info(f"✅ ACCEPTING response from {self.actual_respondent} (being permissive, not kicking)")

                logger.critical(f"👤 Response from: {actual_speaker} (expected: {participant})")

                # Record in survey transcript (JSON) with ACTUAL speaker
                self.survey_transcript.add_response(
                    question_number=self.current_question_num,
                    participant=actual_speaker,
                    response_text=_captured_response_text
                )
                logger.critical(f"✅ Response logged to JSON: Q#{self.current_question_num}, {actual_speaker}")

                # Add to CSV DataFrame for analysis with ACTUAL speaker
                self.survey_data_export.add_response(
                    participant=actual_speaker,
                    question_number=self.current_question_num,
                    question_id=question_id,
                    question_text=question_text,
                    response_options=response_options,
                    response_text=_captured_response_text
                )
                logger.critical(f"✅ Response added to CSV DataFrame: Q#{self.current_question_num}")

                # Mark ACTUAL participant as answered (not the expected one)
                self.participant_manager.mark_participant_answered(actual_speaker, self.current_question_num)
                self._set_delivery_state(self.current_question_num, actual_speaker, "answered", context="response_received")
                logger.info(f"✅ Marked {actual_speaker} as answered for question #{self.current_question_num}")

                # Record turn result for acknowledgment (immutable snapshot)
                self._record_turn_result(expected=participant, actual=actual_speaker)

                # Cancel timeout monitoring since we got a response
                self.waiting_for_response = False
                if self.response_timeout_task:
                    self.response_timeout_task.cancel()
                    self.response_timeout_task = None

                # Clear the variable
                self.latest_user_response = None

                user_responded = True
                break

        if not user_responded:
            logger.warning(f"⏱️  Max wait time reached ({polling_timeout}s), no response detected (2nd loop)")

            # Audible acknowledgment so the user knows the system is still alive
            try:
                display_name = self.participant_manager.get_display_name(participant) if self.participant_manager else ""
                timeout_msg = f"I didn't catch a response, {display_name}. Let me move on." if display_name else "I didn't catch a response. Let me move on."
                await self.agent_session.say(timeout_msg, allow_interruptions=False)
                self.survey_transcript.add_acknowledgment(timeout_msg)
            except Exception as e:
                logger.warning(f"Could not speak timeout message: {e}")

            # Record timeout/no-response in ALL outputs so question is not lost
            question_id = self.current_question_object.id if self.current_question_object else f"Q{self.current_question_num}"
            question_text = self.current_question_object.question if self.current_question_object else ""
            response_options = self.current_question_object.response_options if self.current_question_object else []
            timeout_marker = "[NO RESPONSE - TIMEOUT]"

            # Log to STT debug logger (for comparison report)
            self.stt_debug_logger.log_question_response(
                question_num=self.current_question_num,
                question_id=question_id,
                question_text=question_text,
                participant=participant,
                raw_transcript=timeout_marker,
                corrected_response=timeout_marker,
                response_options=response_options,
                expected_respondent=participant
            )
            logger.info(f"📝 Recorded timeout for Q#{self.current_question_num} in STT debug log")

            # Record in survey transcript (JSON) with timeout marker
            self.survey_transcript.add_response(
                question_number=self.current_question_num,
                participant=participant,
                response_text=timeout_marker
            )
            logger.info(f"📝 Recorded timeout for Q#{self.current_question_num} in JSON transcript")

            # Add to CSV DataFrame with timeout marker
            self.survey_data_export.add_response(
                participant=participant,
                question_number=self.current_question_num,
                question_id=question_id,
                question_text=question_text,
                response_options=response_options,
                response_text=timeout_marker
            )
            logger.info(f"📝 Recorded timeout for Q#{self.current_question_num} in CSV DataFrame")

            # Only count timeout as answered if question delivery was confirmed.
            if self._is_delivery_confirmed(self.current_question_num, participant):
                self.participant_manager.mark_participant_answered(participant, self.current_question_num)
                self._set_delivery_state(self.current_question_num, participant, "timeout", context="timeout_after_delivery")
            else:
                logger.warning(
                    f"Skipping answered-mark on timeout for {participant}: delivery was not confirmed; requeueing."
                )
                self._register_missing_participant_for_retry(participant, context="timeout_without_delivery")

            # FIX: Record turn result for timeout (was MISSING — caused stale-name acks)
            self._record_turn_result(expected=participant, actual=participant, was_timeout=True)

        # Stop turn duration monitoring (response captured or timeout)
        if self.turn_monitor_task:
            self.turn_monitor_task.cancel()
            self.turn_monitor_task = None
        self.current_turn = None
        logger.info("✅ Stopped turn duration monitoring (response captured or timeout)")

        # After response (or timeout), call move_to_next_participant to continue flow
        await self.move_to_next_participant()

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

    # Configure VAD with:
    # - Lower activation_threshold (0.35) for better detection of speech during TTS playback
    #   (helps detect observer "pause" commands even with echo cancellation)
    # - Middle-ground silence duration (1.5s) - will be dynamically adjusted per question:
    #   - Quantitative: 0.8s (fast response for short answers)
    #   - Qualitative: 2.5s (allow natural pauses for long explanations)
    vad_instance = silero.VAD.load(
        min_silence_duration=0.4,      # VAD-level silence threshold; polling trusts this directly
        min_speech_duration=0.1,       # Minimum speech duration to trigger (default: 0.05s)
        activation_threshold=0.35,     # Lower than default 0.5 for better sensitivity to soft speech/observer commands
    )
    logger.info("VAD configured: min_silence_duration=0.4s, activation_threshold=0.35 (sensitive for observer commands)")

    session = AgentSession(
        stt=stt_instance,
        llm=openai.LLM(
            model=llm_model,
            temperature=temperature,  # CRITICAL: Must be 0.0 to prevent question generation
        ),
        tts=tts_instance,
        vad=vad_instance,
        turn_detection="server_vad",  # Enable turn detection for event handling (FIXED: was "manual")
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

        logger.info(f"Participant connected: {participant.identity} (name: {participant.name})")
        if moderator.participant_manager:
            # Pass both identity and display name from LiveKit token
            moderator.participant_manager.add_participant(participant.identity, display_name=participant.name)

        # Initialize audio activity tracking
        moderator.participant_audio_activity[participant.identity] = None

    @ctx.room.on("track_published")
    def on_track_published(publication, participant):
        """Subscribe to audio tracks from all participants (including late joiners)."""
        from livekit.rtc import TrackKind
        logger.critical(f"🔥 TRACK_PUBLISHED EVENT: {participant.identity}, kind={publication.kind}, sid={publication.sid}")
        if publication.kind == TrackKind.KIND_AUDIO:
            logger.critical(f"🎤 Audio track published by {participant.identity}, subscribing...")
            publication.set_subscribed(True)
            logger.critical(f"✅ Subscribed to {participant.identity}'s audio track")
        else:
            logger.info(f"  Non-audio track published: kind={publication.kind}")

    @ctx.room.on("track_unmuted")
    def on_track_unmuted(publication, participant):
        """Track when a participant starts speaking (unmutes their audio)."""
        from livekit.rtc import TrackKind
        if publication.kind == TrackKind.KIND_AUDIO:
            logger.critical(f"🎙️  AUDIO ACTIVE: {participant.identity} unmuted (track: {publication.sid})")
            moderator.participant_audio_activity[participant.identity] = datetime.now()
            moderator.actual_respondent = participant.identity
            moderator.active_speaker_track_sid = publication.sid

    @ctx.room.on("track_muted")
    def on_track_muted(publication, participant):
        """Track when a participant stops speaking (mutes their audio)."""
        from livekit.rtc import TrackKind
        if publication.kind == TrackKind.KIND_AUDIO:
            logger.info(f"🔇 Audio muted: {participant.identity} (track: {publication.sid})")
            # Don't clear actual_respondent yet - we might still get STT results

    # Start the session with the agent
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

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant):
        """Handle participant disconnection with grace period."""
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
                with open("/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log", "a") as _f:
                    _f.write(_json.dumps({"location": "moderator_agent.py:immediate_capture:delivery_guard", "message": "Delivery guard blocked capture", "data": {"delivery_state": current_ds, "expected": expected, "question_num": moderator.current_question_num, "pending_transcript": str(moderator.pending_stt_transcript)[:100] if moderator.pending_stt_transcript else None}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "H_CAPTURE_GUARD"}) + "\n")
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
            with open("/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log", "a") as _f:
                _f.write(_json.dumps({"location": "moderator_agent.py:user_input_transcribed:welcome_guard", "message": "WELCOME guard fired — discarding transcript", "data": {"transcript": str(_raw)[:200], "survey_state": moderator.survey_state.value}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "A"}) + "\n")
            # #endregion
            return
        # ─────────────────────────────────────────────────────────────────

        # AUDIO ROUTING TRACE: Show which participant's audio was transcribed
        logger.critical("🔍 STT EVENT TRACE:")
        if moderator.agent_session and hasattr(moderator.agent_session, '_room_io') and moderator.agent_session._room_io:
            if hasattr(moderator.agent_session._room_io, '_audio_input') and moderator.agent_session._room_io._audio_input:
                audio_input = moderator.agent_session._room_io._audio_input
                current_stt_participant = getattr(audio_input, '_participant_identity', 'UNKNOWN')
                logger.critical(f"   STT was listening to: {current_stt_participant}")
                logger.critical(f"   Expected respondent: {moderator.expected_respondent}")
                logger.critical(f"   Actual respondent (from track_unmuted): {moderator.actual_respondent}")

                if current_stt_participant != moderator.expected_respondent:
                    logger.error(f"⚠️  STT MISMATCH: Listening to {current_stt_participant} but expecting {moderator.expected_respondent}")
                    # Use STT source as actual respondent — this is more reliable than
                    # track_unmuted events (which don't fire for server-initiated unmutes)
                    if current_stt_participant and current_stt_participant != 'UNKNOWN':
                        moderator.actual_respondent = current_stt_participant
                        logger.warning(f"🔄 Setting actual_respondent to STT source: {current_stt_participant}")
                else:
                    # STT source matches expected — still set actual_respondent explicitly
                    # so it's never None (track_unmuted events don't reliably fire)
                    if current_stt_participant and current_stt_participant != 'UNKNOWN':
                        moderator.actual_respondent = current_stt_participant

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
                current_stt_participant = None
                try:
                    if moderator.agent_session and hasattr(moderator.agent_session, '_room_io') and moderator.agent_session._room_io:
                        audio_input = moderator.agent_session._room_io._audio_input
                        if audio_input:
                            current_stt_participant = getattr(audio_input, '_participant_identity', None)
                except Exception:
                    pass

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

        # ── DELIVERY-STATE GUARD ─────────────────────────────────────
        # Reject transcripts that arrive while the question is still being
        # spoken (delivery_state != "delivered").  These are stale spillover
        # from the previous turn and would pollute the current response.
        _expected = moderator.expected_respondent
        if _expected and moderator.current_question_num:
            if not moderator._is_delivery_confirmed(moderator.current_question_num, _expected):
                _ds_key = moderator._delivery_key(moderator.current_question_num, _expected)
                _current_ds = moderator.question_delivery_state.get(_ds_key, "unknown")
                logger.warning(
                    f"🛡️ DELIVERY GUARD (user_input_transcribed): Discarding transcript — "
                    f"delivery_state={_current_ds} for Q#{moderator.current_question_num}/{_expected} "
                    f"(need 'delivered'): '{transcript[:60]}'"
                )
                # #region agent log
                import json as _json
                with open("/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log", "a") as _f:
                    _f.write(_json.dumps({"location": "moderator_agent.py:user_input_transcribed:delivery_guard", "message": "Delivery guard blocked transcript", "data": {"delivery_state": _current_ds, "expected": _expected, "question_num": moderator.current_question_num, "transcript": transcript[:100]}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "H_EVENT_GUARD"}) + "\n")
                # #endregion
                return
        # ─────────────────────────────────────────────────────────────

        # CRITICAL FIX: STT sends BOTH cumulative updates AND new fragments
        # - Cumulative: "Hello" → "Hello world" (extends previous)
        # - New fragment: "Hello world" → "Goodbye" (different text after pause)
        # We need to APPEND new fragments but REPLACE cumulative updates

        # Store in pending STT transcript for immediate capture function
        moderator.pending_stt_transcript = transcript

        question_context = f"Q#{moderator.current_question_num} ({moderator.current_question_object.id if moderator.current_question_object else 'N/A'})"

        # Track the last individual fragment (not the accumulated response)
        prev_fragment = moderator.last_stt_fragment.strip()
        new_fragment = transcript.strip()

        # SMARTER APPROACH: Check if new fragment actually extends previous
        # - If new STARTS WITH previous → cumulative update (REPLACE)
        # - If previous is CONTAINED in new → cumulative update (REPLACE)
        # - If significant word overlap (>60%) → take the longer one (REPLACE)
        # - Otherwise → new fragment after pause (APPEND)
        if prev_fragment:
            # Check if this is truly a cumulative update
            is_cumulative = (
                new_fragment.startswith(prev_fragment) or  # Direct extension
                prev_fragment in new_fragment  # Previous contained in new
            )

            # If not obvious cumulative, check for significant word overlap
            if not is_cumulative and len(prev_fragment) > 10:
                # Calculate word overlap
                prev_words = set(prev_fragment.lower().split())
                new_words = set(new_fragment.lower().split())
                if prev_words and new_words:
                    overlap = len(prev_words & new_words)
                    smaller_set_size = min(len(prev_words), len(new_words))
                    overlap_ratio = overlap / smaller_set_size if smaller_set_size > 0 else 0

                    # If >60% word overlap, treat as cumulative and keep the longer one
                    if overlap_ratio > 0.6:
                        is_cumulative = True
                        logger.debug(f"Detected {overlap_ratio:.0%} word overlap - treating as cumulative")

            if is_cumulative:
                # Cumulative update - replace the previous fragment with the new one
                # BUT: Keep the LONGER version to avoid losing content (like "Yes.")
                if moderator.latest_user_response:
                    acc_stripped = moderator.latest_user_response.rstrip()

                    # Try to find and replace the previous fragment at the end
                    if acc_stripped.endswith(prev_fragment):
                        # Clean replacement
                        prefix = acc_stripped[:-len(prev_fragment)].rstrip()
                        if prefix:
                            # Keep prefix + new fragment
                            moderator.latest_user_response = prefix + " " + new_fragment
                        else:
                            # No prefix - use whichever is longer
                            moderator.latest_user_response = new_fragment if len(new_fragment) >= len(prev_fragment) else prev_fragment
                    else:
                        # Previous fragment not at the end - try to find it anywhere
                        # This handles cases where STT drops beginning words
                        if prev_fragment in acc_stripped:
                            # Replace only the last occurrence
                            parts = acc_stripped.rsplit(prev_fragment, 1)
                            moderator.latest_user_response = parts[0].rstrip() + " " + new_fragment if parts[0].strip() else new_fragment
                        else:
                            # Can't find prev in accumulated - check if accumulated has unique beginning
                            # (This handles "Yes. It is..." vs "It is..." - we want to keep "Yes.")

                            # Check if accumulated starts with something new doesn't have
                            acc_start_words = acc_stripped.split()[:3]  # First 3 words
                            new_start_words = new_fragment.split()[:3]

                            has_unique_prefix = False
                            if acc_start_words and new_start_words:
                                # Check if accumulated has unique words at the beginning
                                acc_start_set = set(w.lower() for w in acc_start_words)
                                new_start_set = set(w.lower() for w in new_start_words)
                                unique_start_words = acc_start_set - new_start_set
                                has_unique_prefix = len(unique_start_words) > 0

                            if has_unique_prefix:
                                # Keep accumulated - it has important beginning content (like "Yes")
                                logger.warning(f"⚠️  Keeping accumulated - has unique prefix: {' '.join(acc_start_words)}")
                                # Don't change response
                            elif len(new_fragment) > len(acc_stripped):
                                # New is longer and no unique prefix - use new
                                moderator.latest_user_response = new_fragment
                                logger.warning(f"⚠️  Using new fragment (longer: {len(new_fragment)} vs {len(acc_stripped)} chars)")
                            else:
                                # Keep accumulated (longer or same)
                                logger.warning(f"⚠️  Keeping accumulated (longer: {len(acc_stripped)} vs {len(new_fragment)} chars)")
                else:
                    moderator.latest_user_response = new_fragment

                moderator.last_stt_fragment = new_fragment
                logger.critical(f"💾 [{question_context}] REPLACED cumulative ({len(moderator.latest_user_response)} chars): '{moderator.latest_user_response[:120]}'...")
            else:
                # New fragment - pause occurred, append
                if moderator.latest_user_response:
                    moderator.latest_user_response = moderator.latest_user_response.strip() + " " + new_fragment
                else:
                    moderator.latest_user_response = new_fragment

                moderator.last_stt_fragment = new_fragment
                logger.critical(f"💾 [{question_context}] APPENDED new fragment ({len(moderator.latest_user_response)} chars): '{moderator.latest_user_response[:120]}'...")
        else:
            # First fragment for this question
            moderator.latest_user_response = new_fragment
            moderator.last_stt_fragment = new_fragment
            moderator._first_fragment_time = datetime.now()
            logger.critical(f"💾 [{question_context}] FIRST FRAGMENT ({len(new_fragment)} chars): '{new_fragment[:120]}'...")

        # Update timestamp for silence detection
        moderator.last_fragment_time = datetime.now()

    # BACKUP EVENTS: Try these too in case SDK version is different
    @session.on("user_speech_committed")
    def on_user_speech_committed(message):
        """Backup event handler - may not fire"""
        logger.critical(f"🔥 EVENT FIRED: user_speech_committed (backup)")

        transcript = ""
        if hasattr(message, 'alternatives') and len(message.alternatives) > 0:
            transcript = message.alternatives[0].text
        elif hasattr(message, 'text'):
            transcript = message.text
        else:
            transcript = str(message)

        if transcript:
            _expected = moderator.expected_respondent
            if _expected and moderator.current_question_num and not moderator._is_delivery_confirmed(moderator.current_question_num, _expected):
                logger.warning(f"🛡️ DELIVERY GUARD (user_speech_committed): Discarding — not delivered yet")
                return
            logger.critical(f"📝 USER TRANSCRIPT CAPTURED via user_speech_committed: {transcript[:100]}")
            moderator.pending_stt_transcript = transcript
            moderator.latest_user_response = transcript

    @session.on("transcript_received")
    def on_transcript_received(event):
        """Backup event handler - may not fire"""
        logger.critical(f"🔥 EVENT FIRED: transcript_received (backup)")

        transcript = ""
        if hasattr(event, 'alternatives') and len(event.alternatives) > 0:
            transcript = event.alternatives[0].text
        elif hasattr(event, 'text'):
            transcript = event.text
        elif hasattr(event, 'transcript'):
            transcript = event.transcript
        else:
            transcript = str(event)

        if transcript:
            _expected = moderator.expected_respondent
            if _expected and moderator.current_question_num and not moderator._is_delivery_confirmed(moderator.current_question_num, _expected):
                logger.warning(f"🛡️ DELIVERY GUARD (transcript_received): Discarding — not delivered yet")
                return
            logger.critical(f"📝 USER TRANSCRIPT CAPTURED via transcript_received: {transcript[:100]}")
            moderator.pending_stt_transcript = transcript
            moderator.latest_user_response = transcript

    @session.on("user_state_changed")
    def on_user_state_changed(event):
        """Called when user state changes (speaking/listening/away)"""
        logger.critical(f"🔥 EVENT FIRED: user_state_changed - {event.old_state} -> {event.new_state}")
        print(f"🔥 EVENT FIRED: user_state_changed - {event.old_state} -> {event.new_state}")

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
            with open("/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log", "a") as _f:
                _f.write(_json.dumps({"location": "moderator_agent.py:conversation_item_added:unconfirmed_delivery", "message": "Skipped mark_answered due to unconfirmed delivery", "data": {"participant_id": participant_id, "question_num": moderator.current_question_num, "expected_respondent": moderator.expected_respondent}, "timestamp": int(datetime.now().timestamp() * 1000), "hypothesisId": "H_ANSWERED_GATE"}) + "\n")
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
