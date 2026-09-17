"""Defect A — the welcome must never play to an empty room.

Observed RM_Aq5EeHDjAozN: welcome sent to TTS at t=30.8, first human joined at
t=89.0, agent_speaking span 89.3->89.9 = 0.6s audible out of 59.3s. Control
RM_Txc3zKUpnAWe (human present at t=13.2): one 58.9s span, whole welcome heard.

The behaviour tests import the real implementation from
src/domain/presence_gate.py. It has no LiveKit dependency, so it loads without
executing src/__init__.py. No mirror — the tests and the shipped code are the
same function.

The structural tests read agent.py as source, because agent.py cannot be
imported without LiveKit. They exist to catch the three edits that would
silently restore Defect A: dropping the presence leg, reordering it behind the
avatar leg, or turning either shutdown branch back into fall-through.
"""

import asyncio
import importlib.util
import pathlib
import re
import sys
import types

import pytest

_ROOT = pathlib.Path(__file__).parent.parent


def _load(module_name: str, relpath: str):
    for name in ("src", "src.domain"):
        if name not in sys.modules:
            m = types.ModuleType(name)
            m.__path__ = [str(_ROOT / name.replace(".", "/"))]
            sys.modules[name] = m
    spec = importlib.util.spec_from_file_location(module_name, _ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


wait_for_presence = _load(
    "src.domain.presence_gate", "src/domain/presence_gate.py").wait_for_presence
_constants = _load("src.domain.constants", "src/domain/constants.py")


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


def test_moderator_exposes_the_human_predicate():
    """agent.py fails closed without this attribute, so losing it takes the
    whole app down. Catch it here instead."""
    src = (_ROOT / "src" / "moderator_agent.py").read_text(encoding="utf-8")
    assert "moderator._has_human_participant_now = _has_human_participant_now" in src


def test_agent_imports_the_bound_rather_than_hardcoding_it():
    """The bound is a tuned constant and lives in src/domain/constants.py with
    the others. A literal in agent.py would drift from the test above."""
    src = _agent_src()
    assert "PARTICIPANT_WAIT_TIMEOUT" in src
    assert re.search(
        r"from src\.domain\.constants import \(\s*PARTICIPANT_WAIT_TIMEOUT", src), \
        "agent.py must import the bound from src.domain.constants"
    assert not re.search(r"PARTICIPANT_WAIT_TIMEOUT\s*=\s*[0-9]", src), \
        "agent.py must not redefine the bound"


def test_presence_gate_runs_before_the_avatar_wait():
    """Ordering is load-bearing: the avatar is lazy-started by the first human,
    so an avatar wait placed first can never be satisfied."""
    src = _agent_src()
    presence = src.index("PRESENCE_GATE waiting for first human")
    avatar = src.index("AVATAR_WAIT_TIMEOUT")
    assert presence < avatar, "presence leg must precede the avatar leg"


def test_expired_presence_ends_the_job_instead_of_speaking():
    """The whole point: on expiry we shut down, we do not fall through to the
    welcome. Guards against a future edit restoring fail-open."""
    src = _agent_src()
    expiry = src.index('ctx.shutdown(reason="no_participant_joined")')
    welcome = src.index("wait_seconds = await handle_welcome_section")
    assert expiry < welcome
    assert "return" in src[expiry:welcome], \
        "the expiry branch must return before reaching the welcome"


def test_missing_predicate_also_ends_the_job_instead_of_speaking():
    """Fail closed. A guard whose failure mode is the defect it guards is not
    a guard."""
    src = _agent_src()
    missing = src.index('ctx.shutdown(reason="presence_predicate_missing")')
    welcome = src.index("wait_seconds = await handle_welcome_section")
    assert missing < welcome
    assert "return" in src[missing:welcome], \
        "the missing-predicate branch must return before reaching the welcome"


def test_both_shutdown_branches_suppress_the_ghost_upload():
    """Neither early exit should POST a transcript for a session nobody
    attended."""
    src = _agent_src()
    for reason in ("presence_predicate_missing", "no_participant_joined"):
        idx = src.index(f'ctx.shutdown(reason="{reason}")')
        window = src[max(0, idx - 300):idx]
        assert "_ended_before_survey_started = True" in window, \
            f"the {reason} branch must flag the session before shutting down"

    upload = src.index("async def _upload_transcript_on_shutdown")
    body = src[upload:upload + 1200]
    assert '_ended_before_survey_started' in body, \
        "the upload callback must honour the flag"


def test_welcome_is_delivered_exactly_once_in_normal_mode():
    """No catch-up re-delivery: per the domain rule, anyone arriving after the
    intro is not admitted, so a second delivery would be wrong."""
    assert _agent_src().count("await handle_welcome_section(") == 1
