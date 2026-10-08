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

These tests import the real implementation from src/domain/analysis_deadline.py.
It has no LiveKit dependency, so it loads without executing src/__init__.py
(which eagerly imports moderator_agent). No mirror — the tests and the shipped
code are the same function.
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

# ── Load the real implementation ─────────────────────────────────────────────


def _load_analysis_deadline():
    import sys, types
    for name in ("src", "src.domain"):
        if name not in sys.modules:
            m = types.ModuleType(name)
            m.__path__ = [str(_ROOT / name.replace(".", "/"))]
            sys.modules[name] = m
    spec = importlib.util.spec_from_file_location(
        "src.domain.analysis_deadline",
        _ROOT / "src" / "domain" / "analysis_deadline.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["src.domain.analysis_deadline"] = mod
    spec.loader.exec_module(mod)
    return mod


analyze_with_deadline = _load_analysis_deadline().analyze_with_deadline


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

@pytest.mark.asyncio
async def test_timeout_does_not_leak_into_the_next_analysis():
    """Regression guard for the retry leak.

    The agent assigns `self._analysis_timed_out = timed_out` on every call. That
    is only safe if a SUCCESSFUL analysis reports False — including immediately
    after one that timed out. An earlier version SET the flag True on timeout and
    relied on four scattered turn-level resets to clear it; the retry path in
    _process_captured_response crossed none of them, so a validated response was
    persisted as analysis_timed_out=True.
    """
    async def hang():
        await asyncio.sleep(30)

    async def quick():
        await asyncio.sleep(0.01)
        return "REAL"

    _, first = await analyze_with_deadline(
        hang(), filler_threshold=0.05, hard_deadline=0.30)
    assert first is True, "the hang must time out"

    result, second = await analyze_with_deadline(
        quick(), filler_threshold=0.05, hard_deadline=0.30)
    assert second is False, "a successful analysis must report False, not inherit True"
    assert result == "REAL"