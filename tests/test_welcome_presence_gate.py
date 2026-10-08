"""Defect A — the welcome must never play to an empty room.

Observed RM_Aq5EeHDjAozN: welcome sent to TTS at t=30.8, first human joined at
t=89.0, agent_speaking span 89.3->89.9 = 0.6s audible out of 59.3s. Control
RM_Txc3zKUpnAWe (human present at t=13.2): one 58.9s span, whole welcome heard.

The behaviour tests import the real implementation from
src/domain/presence_gate.py. It has no LiveKit dependency, and src/__init__.py
is empty of imports, so a plain import works. No mirror — the tests and the
shipped code are the same function.

The structural tests read agent.py as source, because agent.py cannot be
imported without LiveKit. They exist to catch the three edits that would
silently restore Defect A: dropping the presence leg, reordering it behind the
avatar leg, or turning either shutdown branch back into fall-through.
"""

import asyncio
import pathlib
import re

import pytest

from src.domain import constants as _constants
from src.domain.presence_gate import wait_for_presence

_ROOT = pathlib.Path(__file__).parent.parent


# ── Constants ────────────────────────────────────────────────────────────────

class TestPresenceConstants:
    def test_bound_clears_the_slowest_observed_join(self):
        """RM_Aq5EeHDjAozN had an 89.0s dispatch->join gap. Any bound in the
        tens of seconds would fire during a normal-but-slow join."""
        assert _constants.PARTICIPANT_WAIT_TIMEOUT >= 300.0
        assert _constants.PARTICIPANT_WAIT_TIMEOUT > 89.0

    def test_bound_is_ten_minutes(self):
        assert _constants.PARTICIPANT_WAIT_TIMEOUT == 600.0

    def test_poll_interval_is_cheap_but_responsive(self):
        assert 0.1 <= _constants.PARTICIPANT_WAIT_POLL_INTERVAL <= 2.0


# ── Behaviour ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_already_present_returns_immediately_without_sleeping():
    """The common case: dispatch lags the join. Must cost nothing."""
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    assert await wait_for_presence(lambda: True, timeout=600.0) is True
    assert loop.time() - t0 < 0.05


@pytest.mark.asyncio
async def test_presence_arriving_mid_wait_is_detected():
    """The failure session: the human joins partway into the wait."""
    calls = []

    def is_present():
        calls.append(1)
        return len(calls) >= 3

    assert await wait_for_presence(
        is_present, timeout=5.0, poll_interval=0.02) is True
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_nobody_joins_returns_false_and_does_not_hang():
    """The bound. False is the signal to stay silent, never to speak anyway."""
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    result = await wait_for_presence(
        lambda: False, timeout=0.20, poll_interval=0.02)
    elapsed = loop.time() - t0

    assert result is False
    assert 0.18 <= elapsed < 1.0, f"bound not respected ({elapsed:.2f}s)"


@pytest.mark.asyncio
async def test_long_poll_interval_cannot_overshoot_the_bound():
    """A 10s poll against a 0.2s bound must still return at ~0.2s."""
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    assert await wait_for_presence(
        lambda: False, timeout=0.20, poll_interval=10.0) is False
    assert loop.time() - t0 < 1.0


@pytest.mark.asyncio
async def test_zero_timeout_still_checks_once():
    """timeout=0 is a probe, not a guaranteed False."""
    assert await wait_for_presence(lambda: True, timeout=0.0) is True
    assert await wait_for_presence(lambda: False, timeout=0.0) is False


@pytest.mark.asyncio
async def test_predicate_errors_propagate_rather_than_reading_as_absent():
    """A broken predicate must not silently look like 'nobody here' — that
    would trade a visible crash for an invisible ten-minute stall."""
    def boom():
        raise RuntimeError("room handle gone")

    with pytest.raises(RuntimeError):
        await wait_for_presence(boom, timeout=1.0, poll_interval=0.01)


# ── Structural guards ────────────────────────────────────────────────────────

def _agent_src() -> str:
    return (_ROOT / "agent.py").read_text(encoding="utf-8")


def _at(src: str, needle: str) -> int:
    """Index of `needle`, with a readable failure instead of ValueError."""
    i = src.find(needle)
    assert i != -1, f"agent.py no longer contains: {needle!r}"
    return i


def test_moderator_exposes_the_human_predicate():
    """agent.py fails closed without this attribute, so losing it takes the
    whole app down. Catch it here instead."""
    src = (_ROOT / "src" / "moderator_agent.py").read_text(encoding="utf-8")
    assert "moderator._has_human_participant_now = _has_human_participant_now" in src


def test_agent_imports_the_bound_rather_than_hardcoding_it():
    """The bound is a tuned constant and lives in src/domain/constants.py with
    the others. A literal in agent.py would drift from the test above."""
    src = _agent_src()
    # Order-independent: isort sorts the names alphabetically, which would put
    # POLL_INTERVAL first. Pinning the order fails CI on correct code.
    imports = re.search(
        r"from src\.domain\.constants import \(([^)]*)\)", src)
    assert imports, "agent.py must import from src.domain.constants"
    assert "PARTICIPANT_WAIT_TIMEOUT" in imports.group(1), \
        "agent.py must import the bound from src.domain.constants"
    assert not re.search(r"PARTICIPANT_WAIT_TIMEOUT\s*=\s*[0-9]", src), \
        "agent.py must not redefine the bound"


def test_presence_gate_runs_before_the_avatar_wait():
    """Ordering is load-bearing: the avatar is lazy-started by the first human,
    so an avatar wait placed first can never be satisfied."""
    src = _agent_src()
    assert _at(src, "PRESENCE_GATE waiting for first human") < \
        _at(src, "AVATAR_WAIT_TIMEOUT"), "presence leg must precede the avatar leg"


@pytest.mark.parametrize("reason", [
    "no_participant_joined",            # nobody ever arrived
    "presence_predicate_missing",       # fail closed on lost wiring
    "participant_left_before_welcome",  # left during the avatar warm-up
])
def test_every_presence_exit_ends_the_job_instead_of_speaking(reason):
    """Each exit shuts down and returns; none falls through to the welcome.
    Guards against a future edit restoring fail-open."""
    src = _agent_src()
    exit_at = _at(src, f'ctx.shutdown(reason="{reason}")')
    welcome = _at(src, "wait_seconds = await handle_welcome_section")
    assert exit_at < welcome, f"{reason} must be reachable before the welcome"
    assert "return" in src[exit_at:welcome], \
        f"the {reason} branch must return before reaching the welcome"


def test_presence_is_rechecked_at_the_point_of_use():
    """The avatar leg can run ~32s between the presence check and the welcome.
    Checking only before it reopens Defect A through a narrower window."""
    src = _agent_src()
    avatar = _at(src, "AVATAR_WAIT_TIMEOUT")
    welcome = _at(src, "wait_seconds = await handle_welcome_section")
    assert "if not _has_human():" in src[avatar:welcome], \
        "presence must be re-checked after the avatar leg, immediately before speaking"


def test_ghost_upload_guard_is_armed_before_the_wait_not_per_branch():
    """Fail-safe, not opt-in. Per-branch arming leaves the whole silent wait
    unguarded: a cancellation in that window runs no branch and posts an empty
    transcript. Armed before the wait, cleared only once the welcome is said."""
    src = _agent_src()
    armed = _at(src, "moderator._ended_before_survey_started = True")
    gate = _at(src, "PRESENCE_GATE waiting for first human")
    welcome = _at(src, "wait_seconds = await handle_welcome_section")
    cleared = _at(src, "moderator._ended_before_survey_started = False")

    assert armed < gate, "the flag must be armed BEFORE the wait begins"
    assert welcome < cleared, "the flag must only clear AFTER the welcome is spoken"
    assert src.count("_ended_before_survey_started = True") == 1, \
        "arm once; per-branch arming is the bug this replaced"

    upload = _at(src, "async def _upload_transcript_on_shutdown")
    assert "_ended_before_survey_started" in src[upload:upload + 1200], \
        "the upload callback must honour the flag"


def test_welcome_is_delivered_exactly_once_in_normal_mode():
    """No catch-up re-delivery: per the domain rule, anyone arriving after the
    intro is not admitted, so a second delivery would be wrong."""
    assert _agent_src().count("await handle_welcome_section(") == 1
