"""Defect B — the relevance classifier must not be able to hang the turn.

Observed: RM_Txc3zKUpnAWe t=167.9 a single classifier call took 29,515ms and
produced ~27s of dead air after one "One moment..." filler, during which two
further participant turns were captured and discarded.

Root cause was NOT a missing asyncio.wait_for. There was one — but it wrapped
asyncio.shield(), which explicitly prevents cancellation, so it functioned as a
filler trigger rather than a deadline. The line that actually hung was a bare
`result = await analysis_task` with no bound. Compounding it, the OpenAI client
was constructed with no timeout and no max_retries (SDK defaults: 600s, 2
retries -> ~30 minute worst case).

Per CLAUDE.md these tests mirror the control flow rather than importing
moderator_agent (heavy LiveKit deps). The mirrored shape below is the same
two-stage structure as the source; if that changes, update this file.
"""

import asyncio
import importlib.util
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).parent.parent


def _constants():
    spec = importlib.util.spec_from_file_location(
        "constants_only", _ROOT / "src" / "domain" / "constants.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Mirror of the two-stage control flow in _analyze_with_filler ─────────────

class _FailOpen:
    """Stand-in for the fail-open ResponseAnalysis."""
    is_relevant = True
    is_repeat_request = False
    is_already_answered_claim = False
    partial_repeat_status = "NO_REPEAT"


async def analyze_with_deadline(analysis_coro, *, filler_threshold, hard_deadline,
                                on_filler=None):
    """Mirrors src/moderator_agent.py::_analyze_with_filler.

    Returns (result, timed_out). Stage 1 is shielded ON PURPOSE — it decides
    whether to speak, and must not cancel the analysis. Stage 2 is the real
    deadline and DOES cancel.
    """
    task = asyncio.create_task(analysis_coro)
    filler_said = False
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=filler_threshold), False
    except asyncio.TimeoutError:
        if not filler_said:
            filler_said = True
            if on_filler:
                on_filler()
        remaining = max(hard_deadline - filler_threshold, 0.5)
        try:
            return await asyncio.wait_for(task, timeout=remaining), False
        except asyncio.TimeoutError:
            task.cancel()
            return _FailOpen(), True


# ── Constants ────────────────────────────────────────────────────────────────

class TestDeadlineConstants:
    def test_hard_deadline_caps_dead_air_well_below_the_observed_hang(self):
        c = _constants()
        assert c.ANALYSIS_HARD_DEADLINE <= 10.0
        assert c.ANALYSIS_HARD_DEADLINE < 29.5, "must beat the observed 29.5s hang"

    def test_hard_deadline_clears_the_slowest_legitimate_call(self):
        """Observed max legitimate analysis was 3844ms; leave real margin."""
        c = _constants()
        assert c.ANALYSIS_HARD_DEADLINE >= 5.0

    def test_client_timeout_sits_above_our_deadline(self):
        """Ours must fire first so we control the fallback, not the SDK."""
        c = _constants()
        assert c.ANALYSIS_CLIENT_TIMEOUT > c.ANALYSIS_HARD_DEADLINE
        assert c.ANALYSIS_CLIENT_RETRIES <= 1, "retries multiply the worst case"


# ── Behaviour ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fast_analysis_returns_normally_and_never_fills():
    fired = []

    async def quick():
        await asyncio.sleep(0.01)
        return "REAL"

    result, timed_out = await analyze_with_deadline(
        quick(), filler_threshold=0.05, hard_deadline=0.30,
        on_filler=lambda: fired.append(1))
    assert result == "REAL" and timed_out is False
    assert not fired, "filler must not fire on a fast call"


@pytest.mark.asyncio
async def test_slow_but_within_deadline_fills_then_returns_the_real_verdict():
    """The filler exists for this case — speak, then still use the real answer."""
    fired = []

    async def slow():
        await asyncio.sleep(0.12)
        return "REAL"

    result, timed_out = await analyze_with_deadline(
        slow(), filler_threshold=0.05, hard_deadline=0.60,
        on_filler=lambda: fired.append(1))
    assert result == "REAL" and timed_out is False
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_hang_is_bounded_and_fails_open():
    """The 29.5s defect: an unbounded await must now expire and fail OPEN."""
    async def hang():
        await asyncio.sleep(30)
        return "NEVER"

    loop = asyncio.get_event_loop()
    t0 = loop.time()
    result, timed_out = await analyze_with_deadline(
        hang(), filler_threshold=0.05, hard_deadline=0.30)
    elapsed = loop.time() - t0

    assert timed_out is True
    assert elapsed < 1.0, f"deadline did not bound the hang ({elapsed:.2f}s)"
    # Fail OPEN: accept and proceed rather than block the turn.
    assert result.is_relevant is True
    assert result.is_repeat_request is False
    assert result.partial_repeat_status == "NO_REPEAT"


@pytest.mark.asyncio
async def test_expired_analysis_is_cancelled_not_left_running():
    """A leaked task can land a stale verdict on a LATER turn."""
    started = asyncio.Event()
    cancelled = []

    async def hang():
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(1)
            raise

    await analyze_with_deadline(hang(), filler_threshold=0.05, hard_deadline=0.30)
    await asyncio.sleep(0.05)  # let cancellation propagate
    assert started.is_set()
    assert cancelled, "the timed-out analysis task must be cancelled"


@pytest.mark.asyncio
async def test_stage_one_shield_does_not_kill_a_merely_slow_call():
    """Regression guard on the deliberate shield: the filler wait must never
    cancel analysis, or every slow-but-valid call would be discarded."""
    async def slow():
        await asyncio.sleep(0.15)
        return "REAL"

    result, timed_out = await analyze_with_deadline(
        slow(), filler_threshold=0.02, hard_deadline=1.0)
    assert result == "REAL" and timed_out is False
