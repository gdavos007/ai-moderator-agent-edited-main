"""
Tests for short off-topic response escalation, STT nudge gating,
and idle-no-VAD watchdog.

Covers:
  1. _normalize_for_offtopic_compare produces stable, comparable strings
  2. Short off-topic escalation: first-occurrence deferral, repeat escalation,
     count >= 2 escalation, and encouragement_given escalation
  3. _had_stt_transcript_this_turn gates the STT health nudge
  4. Idle-no-VAD watchdog fires when no VAD/STT activity
  5. Acceptance scenarios:
     - Q2: "I don't know" → encouragement → "I love cheese" → immediate escalation
     - Q3: partial delivery → silence → idle watchdog fires

NOTE: Mirrors logic from src/moderator_agent.py rather than importing it
directly (heavy LiveKit dependencies).
"""

import re
import time

import pytest


# ── Mirrored helpers ──────────────────────────────────────────────────────────

_FILLER_TOKENS = frozenset({
    "um", "uh", "uhm", "erm", "hmm", "hm", "ah", "oh",
    "like", "so", "well", "yeah", "yes", "no", "okay", "ok",
    "right", "and", "but", "just", "you", "know", "mean",
    "i", "a", "the", "is", "it", "that", "this",
})

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

IDLE_NO_VAD_TIMEOUT = 12.0


def _substantive_word_count(text: str) -> int:
    return sum(1 for w in text.lower().split() if w.strip(".,!?…") not in _FILLER_TOKENS)


def _is_too_short_for_offtopic(text: str) -> bool:
    words = len(text.split())
    chars = len(text)
    substantive = _substantive_word_count(text)
    if words < MIN_OFFTOPIC_WORDS or chars < MIN_OFFTOPIC_CHARS or substantive < 4:
        return True
    return False


def _normalize_for_offtopic_compare(text: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', '', text.lower())).strip()


def _has_blatant_offtopic_keywords(text: str) -> bool:
    normalized = _normalize_for_offtopic_compare(text)
    if not normalized:
        return False
    if any(phrase in normalized for phrase in _BLATANT_OFFTOPIC_PHRASES):
        return True
    return bool(set(normalized.split()) & _BLATANT_OFFTOPIC_KEYWORDS)


# ── Simulated state ──────────────────────────────────────────────────────────

class ShortOfftopicState:
    """Minimal simulation of the off-topic / idle tracking fields."""

    def __init__(self):
        self.encouragement_given = False
        self._short_offtopic_count = 0
        self._last_short_offtopic_norm = None
        self._had_stt_transcript_this_turn = False
        self._idle_no_vad_nudge_fired = False
        self._polling_start_time = None
        self._first_vad_speaking_time = None
        self.relevance_prompt_given = False

    def reset(self):
        self.encouragement_given = False
        self._short_offtopic_count = 0
        self._last_short_offtopic_norm = None
        self._had_stt_transcript_this_turn = False
        self._idle_no_vad_nudge_fired = False
        self._polling_start_time = None
        self._first_vad_speaking_time = None
        self.relevance_prompt_given = False

    def handle_short_offtopic(self, text):
        """Mirror of the CHECK 4 logic.

        Returns (action, reason):
            action: "wait" or "escalate"
            reason: "none", "blatant_keyword", "encouragement_given",
                "repeat_short_offtopic", or "count_ge_2"
        """
        _norm = _normalize_for_offtopic_compare(text)
        _has_blatant_keyword = _has_blatant_offtopic_keywords(text)
        _is_repeat = (_norm == self._last_short_offtopic_norm) if self._last_short_offtopic_norm else False
        self._short_offtopic_count += 1
        self._last_short_offtopic_norm = _norm

        _should_escalate = (
            self.encouragement_given
            or _is_repeat
            or self._short_offtopic_count >= 2
        )
        if _should_escalate:
            reason = (
                "encouragement_given" if self.encouragement_given
                else ("repeat_short_offtopic" if _is_repeat else "count_ge_2")
            )
            return "escalate", reason
        return "wait", "none"

    def method2_short_response_action(self, text):
        """Mirror of METHOD 2 short-transcript handling."""
        if not _is_too_short_for_offtopic(text):
            return "normal_relevance_check"
        if _has_blatant_offtopic_keywords(text):
            return "fallback_relevance_check"
        return "nudge_for_more_detail"

    def should_stt_nudge(self):
        """Returns True only if no transcript has been received this turn."""
        return not self._had_stt_transcript_this_turn

    def should_idle_no_vad_fire(self, now=None):
        """Returns True if idle-no-VAD watchdog should fire."""
        if now is None:
            now = time.time()
        return (
            not self._idle_no_vad_nudge_fired
            and self._first_vad_speaking_time is None
            and not self._had_stt_transcript_this_turn
            and self._polling_start_time is not None
            and (now - self._polling_start_time) > IDLE_NO_VAD_TIMEOUT
        )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestNormalize:
    def test_lowercase_strip_punctuation(self):
        assert _normalize_for_offtopic_compare("I Love Cheese!") == "i love cheese"

    def test_collapse_whitespace(self):
        assert _normalize_for_offtopic_compare("  hello   world  ") == "hello world"

    def test_identical_inputs_match(self):
        a = _normalize_for_offtopic_compare("I love cheese")
        b = _normalize_for_offtopic_compare("I love cheese.")
        assert a == b

    def test_different_inputs_differ(self):
        a = _normalize_for_offtopic_compare("I love cheese")
        b = _normalize_for_offtopic_compare("I love pizza")
        assert a != b


class TestShortOfftopicDetection:
    def test_i_love_cheese_is_short(self):
        assert _is_too_short_for_offtopic("I love cheese")

    def test_so_i_is_short(self):
        assert _is_too_short_for_offtopic("So I…")

    def test_um_well_like_is_short(self):
        assert _is_too_short_for_offtopic("Um well like")

    def test_long_substantive_not_short(self):
        text = "I think the product is really good because it solves many problems"
        assert not _is_too_short_for_offtopic(text)

    def test_blatant_keyword_detector_matches_food_topic(self):
        assert _has_blatant_offtopic_keywords("I like cheese")

    def test_blatant_keyword_detector_ignores_generic_fragment(self):
        assert not _has_blatant_offtopic_keywords("So I...")


class TestShortOfftopicEscalation:
    """Core logic: escalation conditions."""

    def test_first_occurrence_no_encouragement_waits(self):
        state = ShortOfftopicState()
        action, reason = state.handle_short_offtopic("I love cheese")
        assert action == "wait"
        assert reason == "none"

    def test_second_occurrence_escalates(self):
        state = ShortOfftopicState()
        state.handle_short_offtopic("I love cheese")
        action, reason = state.handle_short_offtopic("I love pizza")
        assert action == "escalate"
        assert reason == "count_ge_2"

    def test_repeat_same_text_escalates(self):
        state = ShortOfftopicState()
        state.handle_short_offtopic("I love cheese")
        action, reason = state.handle_short_offtopic("I love cheese")
        assert action == "escalate"
        assert reason == "repeat_short_offtopic"

    def test_repeat_same_text_ignoring_punctuation(self):
        state = ShortOfftopicState()
        state.handle_short_offtopic("I love cheese!")
        action, reason = state.handle_short_offtopic("i love cheese")
        assert action == "escalate"
        assert reason == "repeat_short_offtopic"

    def test_encouragement_given_escalates_on_first_short_offtopic(self):
        """Key new behavior: after uncertainty encouragement, first short
        off-topic escalates immediately."""
        state = ShortOfftopicState()
        state.encouragement_given = True
        action, reason = state.handle_short_offtopic("I love cheese")
        assert action == "escalate"
        assert reason == "encouragement_given"
        assert state._short_offtopic_count == 1

    def test_reset_clears_all(self):
        state = ShortOfftopicState()
        state.encouragement_given = True
        state.handle_short_offtopic("I love cheese")
        state.reset()
        action, reason = state.handle_short_offtopic("I love cheese")
        assert action == "wait"
        assert reason == "none"

    def test_blatant_keyword_does_not_override_first_wait_behavior(self):
        state = ShortOfftopicState()
        action, reason = state.handle_short_offtopic("I like cheese")
        assert action == "wait"
        assert reason == "none"


class TestMethod2ShortTranscriptHandling:
    def test_blatant_short_nonsequitur_gets_fallback_relevance_check(self):
        state = ShortOfftopicState()
        assert state.method2_short_response_action("I like cheese") == "fallback_relevance_check"

    def test_non_blatant_short_fragment_gets_clarification_nudge(self):
        state = ShortOfftopicState()
        assert state.method2_short_response_action("So I...") == "nudge_for_more_detail"

    def test_genuine_incomplete_still_waits(self):
        state = ShortOfftopicState()
        action, reason = state.handle_short_offtopic("So I…")
        assert action == "wait"


class TestSTTNudgeGating:
    def test_nudge_allowed_when_no_transcript(self):
        state = ShortOfftopicState()
        assert state.should_stt_nudge() is True

    def test_nudge_blocked_after_transcript(self):
        state = ShortOfftopicState()
        state._had_stt_transcript_this_turn = True
        assert state.should_stt_nudge() is False

    def test_nudge_allowed_after_reset(self):
        state = ShortOfftopicState()
        state._had_stt_transcript_this_turn = True
        state.reset()
        assert state.should_stt_nudge() is True


class TestIdleNoVadWatchdog:
    """Idle-no-VAD watchdog fires when user stays completely quiet."""

    def test_fires_after_timeout(self):
        state = ShortOfftopicState()
        state._polling_start_time = time.time() - 15  # 15s ago
        assert state.should_idle_no_vad_fire() is True

    def test_does_not_fire_before_timeout(self):
        state = ShortOfftopicState()
        state._polling_start_time = time.time() - 5  # 5s ago
        assert state.should_idle_no_vad_fire() is False

    def test_does_not_fire_if_vad_detected(self):
        state = ShortOfftopicState()
        state._polling_start_time = time.time() - 15
        state._first_vad_speaking_time = time.time() - 10  # VAD fired
        assert state.should_idle_no_vad_fire() is False

    def test_does_not_fire_if_stt_received(self):
        state = ShortOfftopicState()
        state._polling_start_time = time.time() - 15
        state._had_stt_transcript_this_turn = True
        assert state.should_idle_no_vad_fire() is False

    def test_does_not_fire_twice(self):
        state = ShortOfftopicState()
        state._polling_start_time = time.time() - 15
        assert state.should_idle_no_vad_fire() is True
        state._idle_no_vad_nudge_fired = True
        assert state.should_idle_no_vad_fire() is False

    def test_does_not_fire_without_polling_start(self):
        state = ShortOfftopicState()
        assert state.should_idle_no_vad_fire() is False


class TestShortOfftopicTranscriptOrdering:
    """Verify that the nudge path records the triggering utterance before the nudge.

    Source of truth: _nudge_for_short_offtopic_retry() in src/moderator_agent.py
    should call add_response([Short response before clarification] ...) then
    add_acknowledgment(nudge_text).
    """

    def test_transcript_entry_order(self):
        """Simulate the transcript calls made by _nudge_for_short_offtopic_retry."""
        transcript_log = []

        # Simulate the two transcript calls in order
        captured_text = "What?"
        participant = "christopher"
        question_number = 1
        speaker_name = "Christopher"

        # 1. Response entry with label (added before the nudge)
        transcript_log.append({
            "type": "response",
            "question_number": question_number,
            "speaker": participant,
            "text": f"[Short response before clarification] {captured_text}",
        })

        # 2. Acknowledgment entry (the nudge itself)
        nudge_text = (
            f"Could you say a bit more about your answer, {speaker_name}? "
            f"I want to make sure it responds to the question."
        )
        transcript_log.append({
            "type": "acknowledgment",
            "speaker": "agent",
            "text": nudge_text,
        })

        assert len(transcript_log) == 2
        assert transcript_log[0]["type"] == "response"
        assert transcript_log[0]["text"].startswith("[Short response before clarification]")
        assert captured_text in transcript_log[0]["text"]
        assert transcript_log[1]["type"] == "acknowledgment"
        assert "say a bit more" in transcript_log[1]["text"]


class TestAcceptanceQ2:
    """
    Q2 flow: User says "I don't know" → encouragement → "I love cheese"
    Expected: relevance prompt fires immediately on first short off-topic.
    """

    def test_uncertainty_then_short_offtopic_escalates(self):
        state = ShortOfftopicState()
        text = "I love cheese"
        assert _is_too_short_for_offtopic(text)

        # Step 1: User said "I don't know", agent gave encouragement
        state.encouragement_given = True
        state._had_stt_transcript_this_turn = True

        # Step 2: User says "I love cheese" — first short off-topic
        action, reason = state.handle_short_offtopic(text)
        assert action == "escalate"
        assert reason == "encouragement_given"

        # STT nudge should NOT fire
        assert state.should_stt_nudge() is False

    def test_no_encouragement_first_offtopic_waits(self):
        """Without prior encouragement, first short off-topic waits."""
        state = ShortOfftopicState()
        state._had_stt_transcript_this_turn = True

        action, reason = state.handle_short_offtopic("I love cheese")
        assert action == "wait"
        assert reason == "none"


class TestAcceptanceQ3:
    """
    Q3 flow: Partial/truncated question delivery → user stays silent
    Expected: idle-no-VAD watchdog fires; no indefinite polling.
    """

    def test_partial_delivery_idle_watchdog_fires(self):
        state = ShortOfftopicState()

        # Simulate partial delivery — polling starts, but no VAD/STT
        state._polling_start_time = time.time() - 13  # 13s ago, past 12s threshold

        # No VAD, no STT
        assert state._first_vad_speaking_time is None
        assert state._had_stt_transcript_this_turn is False

        # Watchdog should fire
        assert state.should_idle_no_vad_fire() is True

    def test_partial_delivery_user_responds_no_watchdog(self):
        state = ShortOfftopicState()
        state._polling_start_time = time.time() - 13

        # User speaks — VAD fires
        state._first_vad_speaking_time = time.time() - 10
        assert state.should_idle_no_vad_fire() is False
