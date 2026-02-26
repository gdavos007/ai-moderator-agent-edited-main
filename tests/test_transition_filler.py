"""
Tests for the transition filler mechanism.

The _analyze_with_filler method speaks a brief "One moment..." if the LLM
analysis takes longer than a configurable threshold (default 2.0s).
This test suite verifies:
  1. Filler fires when analysis exceeds the threshold
  2. Filler does NOT fire when analysis completes fast
  3. Filler fires only once per turn (guard flag)
  4. Analysis result is returned correctly regardless of filler
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from dataclasses import dataclass


@dataclass
class FakeResponseAnalysis:
    is_relevant: bool = True
    is_already_answered_claim: bool = False
    is_repeat_request: bool = False
    partial_repeat_status: str = "NO_REPEAT"
    partial_answer: str = ""
    unanswered_questions: str = ""


class FakeModeratorForFiller:
    """Minimal stand-in that mirrors CommunityModeratorAgent's filler logic."""

    def __init__(self):
        self._transition_filler_said = False
        self._analysis_start_time = None
        self.current_question_num = 1
        self.agent_session = MagicMock()
        self.agent_session.say = AsyncMock()

    async def _analyze_with_filler(
        self, question_text, response_text, survey_desc, filler_threshold=2.0,
        _analyze_fn=None
    ):
        """Mirrors the real method but accepts a pluggable analysis function."""
        self._analysis_start_time = datetime.now()
        analysis_task = asyncio.create_task(
            _analyze_fn(question_text, response_text, survey_desc)
        )

        try:
            result = await asyncio.wait_for(
                asyncio.shield(analysis_task), timeout=filler_threshold
            )
        except asyncio.TimeoutError:
            if not self._transition_filler_said and self.agent_session:
                self._transition_filler_said = True
                await self.agent_session.say("One moment...", allow_interruptions=False)
            result = await analysis_task

        analysis_duration = (datetime.now() - self._analysis_start_time).total_seconds()
        return result, analysis_duration


async def _fast_analysis(q, r, s):
    """Simulates an LLM analysis that completes in ~50ms."""
    await asyncio.sleep(0.05)
    return FakeResponseAnalysis(is_relevant=True)


async def _slow_analysis(q, r, s):
    """Simulates an LLM analysis that takes ~3s."""
    await asyncio.sleep(3.0)
    return FakeResponseAnalysis(is_relevant=True)


async def _medium_analysis(q, r, s):
    """Simulates an LLM analysis that takes ~1.5s (under default threshold)."""
    await asyncio.sleep(1.5)
    return FakeResponseAnalysis(is_relevant=False, is_repeat_request=True)


# ── Tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_filler_fires_on_slow_analysis():
    mod = FakeModeratorForFiller()
    result, duration = await mod._analyze_with_filler(
        "Q?", "Answer", "", filler_threshold=0.5, _analyze_fn=_slow_analysis
    )
    assert mod._transition_filler_said is True
    mod.agent_session.say.assert_called_once_with("One moment...", allow_interruptions=False)
    assert result.is_relevant is True
    assert duration >= 0.5


@pytest.mark.asyncio
async def test_filler_does_not_fire_on_fast_analysis():
    mod = FakeModeratorForFiller()
    result, duration = await mod._analyze_with_filler(
        "Q?", "Answer", "", filler_threshold=2.0, _analyze_fn=_fast_analysis
    )
    assert mod._transition_filler_said is False
    mod.agent_session.say.assert_not_called()
    assert result.is_relevant is True
    assert duration < 1.0


@pytest.mark.asyncio
async def test_filler_fires_only_once_per_turn():
    mod = FakeModeratorForFiller()

    async def _slow(q, r, s):
        await asyncio.sleep(1.0)
        return FakeResponseAnalysis()

    # First call: filler should fire
    await mod._analyze_with_filler("Q?", "A1", "", filler_threshold=0.3, _analyze_fn=_slow)
    assert mod._transition_filler_said is True
    assert mod.agent_session.say.call_count == 1

    # Second call (same turn, flag not reset): filler should NOT fire again
    await mod._analyze_with_filler("Q?", "A2", "", filler_threshold=0.3, _analyze_fn=_slow)
    assert mod.agent_session.say.call_count == 1  # still 1


@pytest.mark.asyncio
async def test_filler_resets_between_turns():
    mod = FakeModeratorForFiller()

    async def _slow(q, r, s):
        await asyncio.sleep(1.0)
        return FakeResponseAnalysis()

    # First turn: filler fires
    await mod._analyze_with_filler("Q?", "A1", "", filler_threshold=0.3, _analyze_fn=_slow)
    assert mod.agent_session.say.call_count == 1

    # Simulate turn reset (as done in ask_next_question / move_to_next_participant)
    mod._transition_filler_said = False

    # Second turn: filler should fire again
    await mod._analyze_with_filler("Q?", "A2", "", filler_threshold=0.3, _analyze_fn=_slow)
    assert mod.agent_session.say.call_count == 2


@pytest.mark.asyncio
async def test_analysis_result_returned_correctly_with_filler():
    mod = FakeModeratorForFiller()
    result, _ = await mod._analyze_with_filler(
        "Q?", "repeat", "", filler_threshold=0.3, _analyze_fn=_medium_analysis
    )
    assert result.is_relevant is False
    assert result.is_repeat_request is True


@pytest.mark.asyncio
async def test_no_filler_when_session_is_none():
    mod = FakeModeratorForFiller()
    mod.agent_session = None

    async def _slow(q, r, s):
        await asyncio.sleep(1.0)
        return FakeResponseAnalysis()

    result, _ = await mod._analyze_with_filler(
        "Q?", "A", "", filler_threshold=0.3, _analyze_fn=_slow
    )
    assert mod._transition_filler_said is False
    assert result.is_relevant is True
