"""STT correction and multi-option response parsing.

No SDK imports — only stdlib ``re``, ``difflib``, and ``logging``.
"""
import logging
import re
from difflib import get_close_matches
from typing import List

logger = logging.getLogger(__name__)


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
        logger.info(f"Multi-option parsing ({max_selections} expected): '{text[:50]}...' -> {len(final_options)} matched: {result[:100]}")
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
        '4': 'good',
        'four': 'good',
        'for': 'good',
        'fore': 'good',
        'beri': 'very poor',
        'berry': 'very poor',
        'very': 'very poor',
        'we report': 'very poor',
        'report': 'very poor',
        'po': 'poor',
        'poo': 'poor',
        'pour': 'poor',
        'blink': 'excellent',
        'great': 'very likely',
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
                logger.info(f"STT Correction (common mapping): '{text}' -> '{option}'")
                return option

    # STEP 3: Check if any expected option is CONTAINED in the text
    for option in expected_options:
        option_words = option.lower().split()
        # Check if key words from option appear in text
        matches = sum(1 for word in option_words if word in text_clean)
        if matches >= len(option_words) * 0.5:  # At least 50% of words match
            logger.info(f"STT Correction (partial match): '{text}' -> '{option}'")
            return option

    # STEP 4: Check if text is contained in any option
    for option in expected_options:
        if text_clean in option.lower() or any(word in option.lower() for word in text_clean.split() if len(word) > 2):
            logger.info(f"STT Correction (substring match): '{text}' -> '{option}'")
            return option

    # STEP 5: Fuzzy match with LOWER threshold (40% instead of 60%)
    matches = get_close_matches(
        text_clean,
        [re.sub(r'[^\w\s]', '', opt.lower()) for opt in expected_options],
        n=1,
        cutoff=0.4
    )

    if matches:
        # Find original case version
        for option in expected_options:
            if re.sub(r'[^\w\s]', '', option.lower()) == matches[0]:
                logger.info(f"STT Correction (fuzzy match): '{text}' -> '{option}'")
                return option

    # STEP 6: Try matching just the first word (for truncated responses)
    first_word = text_clean.split()[0] if text_clean.split() else ""
    if len(first_word) >= 3:
        for option in expected_options:
            if option.lower().startswith(first_word) or first_word in option.lower():
                logger.info(f"STT Correction (first word match): '{text}' -> '{option}'")
                return option

    # Return original if no good match found
    logger.warning(f"STT: No correction found for '{text}' in options: {expected_options}")
    return text
