"""
Tests for the TURN_ENGINE flag and the native slim-waiter's core invariant.

Two layers:
1. Config plumbing (real): AgentConfig.from_env() reads TURN_ENGINE, defaults
   to "legacy", lowercases. config/agent_config.py is standalone (no src import),
   so this runs even without the livekit plugins installed.
2. Waiter decision (mirrored): per CLAUDE.md, the native waiter logic is
   duplicated here rather than importing moderator_agent (heavy LiveKit deps).
   The invariant under test: in NATIVE mode the waiter returns the moment
   _response_ready is set + captured_response present (EOU already decided the
   turn is done) — with NO pause-cooldown / stabilization gate; in LEGACY mode
   those gates still apply.
"""

import os
import pytest

from config.agent_config import AgentConfig


# ── Layer 1: config plumbing (real) ─────────────────────────────────────────

class TestTurnEngineConfig:
    def test_defaults_to_legacy_when_unset(self, monkeypatch):
        monkeypatch.delenv("TURN_ENGINE", raising=False)
        assert AgentConfig.from_env().turn_engine == "legacy"

    def test_native_when_set(self, monkeypatch):
        monkeypatch.setenv("TURN_ENGINE", "native")
        assert AgentConfig.from_env().turn_engine == "native"

    def test_lowercased_and_stripped(self, monkeypatch):
        monkeypatch.setenv("TURN_ENGINE", "  NATIVE  ")
        assert AgentConfig.from_env().turn_engine == "native"

    def test_dataclass_field_default_is_legacy(self):
        # The field default must be "legacy" so the known-good path is the
        # fallback everywhere (AgentConfig has other required fields, so we
        # inspect the field default rather than instantiating bare).
        import dataclasses
        default = AgentConfig.__dataclass_fields__["turn_engine"].default
        assert default == "legacy"


# ── Layer 2: waiter decision invariant (mirrored logic) ─────────────────────

# Mirror of the gate decision inside _await_response / _await_response_native.
# Legacy: must satisfy stabilization + pause-cooldown before returning.
# Native: returns as soon as the response is ready + delivery confirmed.

def should_return(*, turn_engine, response_ready, captured_present,
                  delivery_confirmed, stabilization_pending, cooldown_satisfied,
                  user_speaking):
    if not (response_ready and captured_present):
        return False
    if not delivery_confirmed:
        return False
    if turn_engine == "native":
        # EOU owns end-of-turn: no cooldown / stabilization / speaking gate.
        return True
    # legacy gates:
    if user_speaking:
        return False
    if stabilization_pending:
        return False
    if not cooldown_satisfied:
        return False
    return True


class TestNativeWaiterInvariant:
    def test_native_returns_immediately_ignoring_gates(self):
        # Even with stabilization pending and cooldown NOT satisfied, native returns.
        assert should_return(
            turn_engine="native", response_ready=True, captured_present=True,
            delivery_confirmed=True, stabilization_pending=True,
            cooldown_satisfied=False, user_speaking=False,
        ) is True

    def test_legacy_waits_for_cooldown(self):
        assert should_return(
            turn_engine="legacy", response_ready=True, captured_present=True,
            delivery_confirmed=True, stabilization_pending=False,
            cooldown_satisfied=False, user_speaking=False,
        ) is False

    def test_legacy_waits_for_stabilization(self):
        assert should_return(
            turn_engine="legacy", response_ready=True, captured_present=True,
            delivery_confirmed=True, stabilization_pending=True,
            cooldown_satisfied=True, user_speaking=False,
        ) is False

    def test_legacy_returns_when_all_gates_pass(self):
        assert should_return(
            turn_engine="legacy", response_ready=True, captured_present=True,
            delivery_confirmed=True, stabilization_pending=False,
            cooldown_satisfied=True, user_speaking=False,
        ) is True

    def test_delivery_guard_blocks_both_engines(self):
        for eng in ("native", "legacy"):
            assert should_return(
                turn_engine=eng, response_ready=True, captured_present=True,
                delivery_confirmed=False, stabilization_pending=False,
                cooldown_satisfied=True, user_speaking=False,
            ) is False

    def test_no_response_returns_false_both_engines(self):
        for eng in ("native", "legacy"):
            assert should_return(
                turn_engine=eng, response_ready=False, captured_present=False,
                delivery_confirmed=True, stabilization_pending=False,
                cooldown_satisfied=True, user_speaking=False,
            ) is False
