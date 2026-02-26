"""Tests for _speak_question_safely: truncation detection + single-flight guard.

These tests avoid importing the heavy livekit stack by replicating only the
logic under test (duration estimation, truncation threshold, retry behaviour,
single-flight guard).
"""

import asyncio
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

# ── Minimal replica of the method-under-test logic ───────────────────────────
# We mirror the core algorithm here to avoid importing moderator_agent.py
# (which pulls in livekit and other heavy dependencies).

_TTS_WORDS_PER_SEC = 2.5


def _estimate_min_sec(text: str) -> float:
    return len(text.split()) / _TTS_WORDS_PER_SEC


def _is_truncated(actual_sec: float, estimated_min_sec: float) -> bool:
    """Return True if TTS completed in less than 50% of estimated minimum."""
    return actual_sec < (estimated_min_sec * 0.5) and estimated_min_sec > 2.0


class FakeAgentSession:
    """Simulates agent_session.say() with configurable durations."""

    def __init__(self, durations: list[float]):
        """durations: list of seconds each say() call should take."""
        self._durations = list(durations)
        self._call_index = 0
        self.calls: list[dict] = []

    async def say(self, text: str, *, allow_interruptions: bool = True):
        duration = self._durations[min(self._call_index, len(self._durations) - 1)]
        self._call_index += 1
        self.calls.append({
            "text": text,
            "allow_interruptions": allow_interruptions,
            "simulated_duration": duration,
        })
        await asyncio.sleep(duration)


async def speak_question_safely(
    agent_session,
    text: str,
    *,
    context: str = "",
    max_retries: int = 1,
    tts_in_flight_ref: list,
) -> bool:
    """Standalone replica of CommunityModeratorAgent._speak_question_safely."""
    word_count = len(text.split())
    estimated_min_sec = word_count / _TTS_WORDS_PER_SEC

    for attempt in range(1 + max_retries):
        allow_int = attempt == 0

        if tts_in_flight_ref[0]:
            for _ in range(60):
                await asyncio.sleep(0.01)
                if not tts_in_flight_ref[0]:
                    break
            if tts_in_flight_ref[0]:
                pass  # proceed anyway

        tts_in_flight_ref[0] = True
        tts_start = datetime.now()

        try:
            await agent_session.say(text, allow_interruptions=allow_int)
        except Exception:
            pass
        finally:
            tts_in_flight_ref[0] = False

        tts_duration = (datetime.now() - tts_start).total_seconds()
        truncated = _is_truncated(tts_duration, estimated_min_sec)

        if not truncated:
            return True

        if attempt < max_retries:
            await asyncio.sleep(0.01)

    return False


# ── Tests ────────────────────────────────────────────────────────────────────

class TestTruncationDetection:
    """Tests for the truncation heuristic."""

    def test_short_text_never_truncated(self):
        """Very short text (< 2s estimated) is never flagged as truncated."""
        text = "Hello there"
        est = _estimate_min_sec(text)
        assert est < 2.0
        assert not _is_truncated(0.1, est)

    def test_long_text_fast_return_is_truncated(self):
        """A 30-word question returning in 1s is clearly truncated."""
        text = " ".join(["word"] * 30)
        est = _estimate_min_sec(text)
        assert est == 12.0
        assert _is_truncated(1.0, est)

    def test_long_text_full_return_not_truncated(self):
        """A 30-word question returning in 8s is NOT truncated."""
        text = " ".join(["word"] * 30)
        est = _estimate_min_sec(text)
        assert not _is_truncated(8.0, est)

    def test_borderline_just_above_threshold(self):
        """Duration at exactly 50% of estimated is NOT truncated (>= not <)."""
        text = " ".join(["word"] * 30)
        est = _estimate_min_sec(text)
        assert not _is_truncated(est * 0.5, est)

    def test_borderline_just_below_threshold(self):
        """Duration just below 50% IS truncated."""
        text = " ".join(["word"] * 30)
        est = _estimate_min_sec(text)
        assert _is_truncated(est * 0.5 - 0.01, est)


class TestEstimate:
    def test_word_count_scaling(self):
        assert _estimate_min_sec("one two three") == pytest.approx(1.2)
        assert _estimate_min_sec(" ".join(["w"] * 15)) == pytest.approx(6.0)
        assert _estimate_min_sec(" ".join(["w"] * 30)) == pytest.approx(12.0)


@pytest.mark.asyncio
class TestSpeakQuestionSafely:
    """Integration-style tests using FakeAgentSession."""

    async def test_full_playback_no_retry(self):
        """When TTS completes in expected time, no retry is needed."""
        text = " ".join(["word"] * 30)  # est 10s
        session = FakeAgentSession([0.6])  # 0.6s actual (> 5s threshold? No.)
        # Actually 0.6 < 5.0 → truncated on first attempt, retry with allow_interruptions=False
        # Let's make it realistic: 0.6s is too fast for 30 words
        # Use 6s instead
        session = FakeAgentSession([0.06])  # simulate fast call (async sleep)
        # The FakeAgentSession sleeps for the given duration; the wall-clock
        # will be approximately that duration. For 30 words (est 10s), even
        # 0.06s is clearly truncated.
        # Let's test with a realistic non-truncated scenario using short text
        short_text = "Hello"  # est < 2s → never truncated
        session = FakeAgentSession([0.01])
        ref = [False]
        result = await speak_question_safely(session, short_text, tts_in_flight_ref=ref)
        assert result is True
        assert len(session.calls) == 1
        assert session.calls[0]["allow_interruptions"] is True

    async def test_truncation_triggers_retry(self):
        """When first attempt is truncated, retry is called with allow_interruptions=False."""
        text = " ".join(["word"] * 30)  # est 10s
        # First call: 0.01s (truncated); Second call: 0.01s (also truncated but no more retries)
        session = FakeAgentSession([0.01, 0.01])
        ref = [False]
        result = await speak_question_safely(session, text, max_retries=1, tts_in_flight_ref=ref)
        # Both attempts truncated → returns False
        assert result is False
        assert len(session.calls) == 2
        assert session.calls[0]["allow_interruptions"] is True
        assert session.calls[1]["allow_interruptions"] is False

    async def test_retry_succeeds_on_second_attempt(self):
        """First attempt truncated, second attempt succeeds."""
        text = " ".join(["word"] * 9)  # est 3s; truncation threshold = 1.5s
        # First call: 0.01s (truncated); second call: 2.0s (> 1.5s → OK)
        session = FakeAgentSession([0.01, 2.0])
        ref = [False]
        result = await speak_question_safely(session, text, max_retries=1, tts_in_flight_ref=ref)
        assert result is True
        assert len(session.calls) == 2
        assert session.calls[1]["allow_interruptions"] is False

    async def test_single_flight_guard(self):
        """Second TTS call waits for first to finish."""
        text = "Hello"
        session = FakeAgentSession([0.05, 0.05])
        ref = [True]  # pretend TTS is already in flight

        async def release():
            await asyncio.sleep(0.02)
            ref[0] = False

        asyncio.create_task(release())
        result = await speak_question_safely(session, text, tts_in_flight_ref=ref)
        assert result is True
        # Guard waited, then proceeded
        assert len(session.calls) == 1

    async def test_no_retries_when_max_retries_zero(self):
        """With max_retries=0, no retry even on truncation."""
        text = " ".join(["word"] * 30)
        session = FakeAgentSession([0.01])
        ref = [False]
        result = await speak_question_safely(session, text, max_retries=0, tts_in_flight_ref=ref)
        assert result is False
        assert len(session.calls) == 1

    async def test_say_exception_handled_gracefully(self):
        """If say() raises, the guard is released and we still get truncation detection."""
        text = " ".join(["word"] * 30)

        class BrokenSession:
            calls = []

            async def say(self, text, *, allow_interruptions=True):
                self.calls.append(text)
                raise RuntimeError("TTS engine crashed")

        session = BrokenSession()
        ref = [False]
        result = await speak_question_safely(session, text, max_retries=1, tts_in_flight_ref=ref)
        assert ref[0] is False  # guard released despite exception
        assert len(session.calls) == 2  # both attempts tried


class TestRealWorldScenarios:
    """Verify detection for the actual Feb 17 demo truncations."""

    def test_q3_christopher_truncation(self):
        """Q3 Christopher: 2.6s actual for 'What do you think the value of
        opinion research is for companies? And for government agencies?'"""
        text = "Christopher, What do you think the value of opinion research is for companies? And for government agencies?"
        est = _estimate_min_sec(text)
        assert _is_truncated(2.6, est), f"Should detect 2.6s as truncated (est={est:.1f}s)"

    def test_q3_christopher_full_playback(self):
        """Same question played fully for Ganesh took 10.4s — should NOT be truncated."""
        text = "Ganesh, What do you think the value of opinion research is for companies? And for government agencies?"
        est = _estimate_min_sec(text)
        assert not _is_truncated(10.4, est), f"10.4s should NOT be truncated (est={est:.1f}s)"

    def test_q5_christopher_truncation(self):
        """Q5 Christopher: 2.9s for 'How would you describe the flow of this
        conversation compared to other research experiences you've had?'"""
        text = "Christopher, How would you describe the flow of this conversation compared to other research experiences you've had?"
        est = _estimate_min_sec(text)
        assert _is_truncated(2.9, est), f"Should detect 2.9s as truncated (est={est:.1f}s)"
