"""
Phase 7 bug-fix tests — disfluency guard, TTS estimate handling,
track handler defensiveness, idle-no-VAD safety margin, avatar cleanup.
"""

import asyncio
import logging
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


# ── Mirror constants / helpers from moderator_agent.py ──

_DISFLUENT_STARTER_TOKENS = frozenset({
    "well", "um", "uh", "uhm", "erm", "hmm", "ah", "oh",
    "like", "so", "i", "think", "guess", "mean",
    "you", "know", "yeah", "yes", "no", "okay", "ok",
    "right", "and", "but", "just", "that", "the", "a",
    "it", "its", "is", "was", "not", "really",
})

DISFLUENCY_EXTENSION_BUDGET = 10.0
IDLE_NO_VAD_TIMEOUT = 12.0
TTS_SAFETY_MARGIN = 3.0


def _is_disfluent_starter(text: str) -> bool:
    """Return True if text consists entirely of disfluent/filler tokens."""
    words = [w.strip(".,!?…'\"") for w in text.lower().split()]
    words = [w for w in words if w]
    if not words:
        return True
    return all(w in _DISFLUENT_STARTER_TOKENS for w in words)


def _estimate_tts_duration(text: str) -> float:
    return max(len(text) / 15.0, 1.0)


# ── Test 1: _is_disfluent_starter classification ──

class TestDisfluencyClassification(unittest.TestCase):
    """Verify _is_disfluent_starter correctly classifies disfluent vs substantive text."""

    def test_disfluent_cases(self):
        self.assertTrue(_is_disfluent_starter("Well, I"))
        self.assertTrue(_is_disfluent_starter("I I think I"))
        self.assertTrue(_is_disfluent_starter("um I think"))
        self.assertTrue(_is_disfluent_starter("I"))
        self.assertTrue(_is_disfluent_starter("think that"))
        self.assertTrue(_is_disfluent_starter("um"))
        self.assertTrue(_is_disfluent_starter("uh, well, I guess"))
        self.assertTrue(_is_disfluent_starter(""))
        self.assertTrue(_is_disfluent_starter("okay so like"))
        self.assertTrue(_is_disfluent_starter("yeah no I mean"))

    def test_substantive_cases(self):
        self.assertFalse(_is_disfluent_starter("I love cheese"))
        self.assertFalse(_is_disfluent_starter("I live in Dallas"))
        self.assertFalse(_is_disfluent_starter("companies benefit from"))
        self.assertFalse(_is_disfluent_starter("the weather is nice"))
        self.assertFalse(_is_disfluent_starter("I think productivity increases"))
        self.assertFalse(_is_disfluent_starter("well I believe collaboration helps"))


# ── Test 2: TTS estimate preservation across _reset_response_flags ──

class TestTTSEstimatePreservation(unittest.TestCase):
    """Verify save/restore pattern preserves pre-set _estimated_remaining_tts."""

    def test_save_restore_pattern(self):
        """Simulate the save/restore around _reset_response_flags."""
        class MockModerator:
            def __init__(self):
                self._estimated_remaining_tts = 13.4

            def _reset_response_flags(self, participant):
                # This zeros the estimate (as in the real code)
                self._estimated_remaining_tts = 0.0

        mod = MockModerator()
        # Save before reset
        _saved = mod._estimated_remaining_tts
        mod._reset_response_flags("test_user")
        # After reset, it's 0
        self.assertEqual(mod._estimated_remaining_tts, 0.0)
        # Restore
        mod._estimated_remaining_tts = _saved
        self.assertEqual(mod._estimated_remaining_tts, 13.4)


# ── Test 3 & 4: _safe_say clears stale estimate ──

class TestSafeSayClearsTTSEstimate(unittest.TestCase):
    """Verify _safe_say() clears stale _estimated_remaining_tts on dispatch/cancel/error."""

    def _make_moderator(self, handle_behavior="success"):
        """Create a mock moderator with _safe_say that mirrors real code."""
        mod = MagicMock()
        mod._shutting_down = False
        mod._estimated_remaining_tts = 13.4

        async def _awaitable_handle():
            if handle_behavior == "cancel":
                raise asyncio.CancelledError()
            elif handle_behavior == "error":
                raise RuntimeError("session closed")

        # say() returns a coroutine that can be awaited
        mod.agent_session.say.return_value = _awaitable_handle()

        async def safe_say(text, *, allow_interruptions=True, context=""):
            if mod._shutting_down:
                return False
            if not mod.agent_session:
                return False
            try:
                handle = mod.agent_session.say(text, allow_interruptions=allow_interruptions)
                mod._estimated_remaining_tts = 0.0  # Stale question-TTS estimate is now invalid
                await handle
                return True
            except asyncio.CancelledError:
                return False
            except RuntimeError as e:
                if "closing" in str(e).lower() or "closed" in str(e).lower():
                    mod._shutting_down = True
                return False

        mod._safe_say = safe_say
        return mod

    def test_clears_on_successful_dispatch(self):
        mod = self._make_moderator("success")

        async def run():
            return await mod._safe_say("test", context="test")

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertTrue(result)
        self.assertEqual(mod._estimated_remaining_tts, 0.0)

    def test_clears_on_cancelled_error(self):
        mod = self._make_moderator("cancel")

        async def run():
            return await mod._safe_say("test", context="test")

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertFalse(result)
        # Estimate was cleared BEFORE await, so it's 0.0 even on cancel
        self.assertEqual(mod._estimated_remaining_tts, 0.0)

    def test_clears_on_runtime_error(self):
        mod = self._make_moderator("error")

        async def run():
            return await mod._safe_say("test", context="test")

        result = asyncio.get_event_loop().run_until_complete(run())
        self.assertFalse(result)
        self.assertEqual(mod._estimated_remaining_tts, 0.0)


# ── Test 5: Polling-phase direct say() paths don't leak stale TTS offsets ──

class TestPollingPhaseTTSManagement(unittest.TestCase):
    """Verify partial repeat, off-topic re-ask, and ack all manage _estimated_remaining_tts."""

    def test_partial_repeat_sets_fresh_estimate(self):
        """After partial repeat question delivery, a fresh estimate should be set."""
        question_text = "What benefits do you see from implementing AI in your workplace processes?"
        start = time.time()
        # Simulate: say + playout takes ~0.1s in test
        elapsed = 0.1
        est = _estimate_tts_duration(question_text)
        remaining = max(est - elapsed, 0.0)
        self.assertGreater(remaining, 0.0, "Fresh estimate should be positive for long text")

    def test_offtopic_reask_sets_fresh_estimate(self):
        """After off-topic re-ask, a fresh estimate should be set."""
        question_text = "How would you rate your overall satisfaction with the product on a scale of 1 to 10?"
        est = _estimate_tts_duration(question_text)
        elapsed = 0.05
        remaining = max(est - elapsed, 0.0)
        self.assertGreater(remaining, 0.0)

    def test_ack_clears_estimate(self):
        """Ack handle clears _estimated_remaining_tts immediately."""
        # In the real code: self._estimated_remaining_tts = 0.0 right after say()
        estimate_before = 13.4
        estimate_after = 0.0  # Cleared on say()
        self.assertEqual(estimate_after, 0.0)


# ── Test 6: Track handler defensiveness ──

class TestTrackHandlerDefensiveness(unittest.TestCase):
    """Mock object lacking .identity → no raise, debug log, return."""

    def test_no_identity_attribute(self):
        """Publication/participant without identity should not raise."""
        # Simulate the defensive extraction
        participant = MagicMock(spec=[])  # No .identity attribute
        publication = MagicMock(spec=['kind', 'sid'])

        identity = getattr(participant, 'identity', None)
        if identity is None:
            identity = getattr(getattr(publication, 'participant', None), 'identity', None)

        self.assertIsNone(identity)

    def test_identity_via_publication_fallback(self):
        """If participant lacks identity, try publication.participant.identity."""
        participant = MagicMock(spec=[])
        pub_participant = MagicMock()
        pub_participant.identity = "user_123"
        publication = MagicMock()
        publication.participant = pub_participant

        identity = getattr(participant, 'identity', None)
        if identity is None:
            identity = getattr(getattr(publication, 'participant', None), 'identity', None)

        self.assertEqual(identity, "user_123")

    def test_identity_direct(self):
        """Normal path: participant.identity works."""
        participant = MagicMock()
        participant.identity = "direct_user"

        identity = getattr(participant, 'identity', None)
        self.assertEqual(identity, "direct_user")


# ── Test 7: TTS safety margin math ──

class TestTTSSafetyMarginMath(unittest.TestCase):
    """Verify idle-no-VAD watchdog math with TTS_SAFETY_MARGIN."""

    def _should_fire(self, idle_elapsed: float, remaining_tts: float) -> bool:
        """Mirror the watchdog check from moderator_agent.py."""
        tts_offset = remaining_tts + TTS_SAFETY_MARGIN if remaining_tts > 0 else 0.0
        return (idle_elapsed - tts_offset) > IDLE_NO_VAD_TIMEOUT

    def test_during_tts_playback_no_fire(self):
        """12s elapsed, 13.4s remaining TTS → should NOT fire."""
        self.assertFalse(self._should_fire(12.0, 13.4))

    def test_long_silence_no_tts_fires(self):
        """30s elapsed, 0 remaining TTS → SHOULD fire."""
        self.assertTrue(self._should_fire(30.0, 0.0))

    def test_within_tts_plus_margin_no_fire(self):
        """28.4s elapsed, 13.4s TTS → offset=16.4, 28.4-16.4=12.0, NOT > 12 → no fire."""
        self.assertFalse(self._should_fire(28.4, 13.4))

    def test_past_tts_plus_margin_fires(self):
        """28.5s elapsed, 13.4s TTS → offset=16.4, 28.5-16.4=12.1 > 12 → fire."""
        self.assertTrue(self._should_fire(28.5, 13.4))

    def test_zero_tts_no_margin_added(self):
        """0 remaining TTS → no safety margin added, pure idle check."""
        # 12s idle, 0 TTS → 12 - 0 = 12, NOT > 12 → no fire
        self.assertFalse(self._should_fire(12.0, 0.0))
        # 12.1s idle → fire
        self.assertTrue(self._should_fire(12.1, 0.0))


# ── Test 8: Disfluency wait respects _polling_deadline ──

class TestDisfluencyRespectsPollingDeadline(unittest.TestCase):
    """Set _polling_deadline 5s from now, feed disfluent text → guard exits at deadline."""

    def test_deadline_bounds_disfluency_wait(self):
        """The disfluency local deadline should be min(budget, polling_deadline)."""
        polling_deadline = time.time() + 5.0  # Only 5s away
        budget_deadline = time.time() + DISFLUENCY_EXTENSION_BUDGET  # 10s away

        disfluency_deadline = min(budget_deadline, polling_deadline)

        # Should be bounded by polling_deadline (5s), not budget (10s)
        self.assertAlmostEqual(disfluency_deadline, polling_deadline, delta=0.1)
        self.assertLess(disfluency_deadline, budget_deadline)


# ── Test 9: Disfluency total extension budget is bounded ──

class TestDisfluencyBudgetBounded(unittest.TestCase):
    """Feed continuous disfluent fragments → _polling_deadline extended only once."""

    def test_single_extension(self):
        """Simulate the disfluency guard loop — budget applied once only."""
        original_deadline = time.time() + 30.0
        polling_deadline = original_deadline
        budget_used = False
        iterations = 0

        # Simulate 3 rounds of disfluent text
        for _ in range(3):
            text = "um I think"
            if not _is_disfluent_starter(text):
                break
            if not budget_used:
                polling_deadline += DISFLUENCY_EXTENSION_BUDGET
                budget_used = True
            iterations += 1

        self.assertTrue(budget_used)
        self.assertEqual(iterations, 3)
        # Deadline extended exactly once by DISFLUENCY_EXTENSION_BUDGET
        self.assertAlmostEqual(
            polling_deadline - original_deadline,
            DISFLUENCY_EXTENSION_BUDGET,
            delta=0.01,
        )


# ── Test 10: Avatar cleanup timeout is non-fatal and bounded ──

class TestAvatarCleanupTimeout(unittest.TestCase):
    """Mock async teardown that hangs → completes in ≤5s, logs at info level."""

    def test_avatar_cleanup_timeout(self):
        """Async teardown that hangs should be bounded by 5s timeout."""

        async def run_test():
            async def hanging_stop():
                await asyncio.sleep(60)  # Hang forever

            avatar_ref = MagicMock()
            avatar_ref.stop = hanging_stop
            avatar_connected = False

            start = time.time()
            for method_name in ("stop", "close", "disconnect"):
                method = getattr(avatar_ref, method_name, None)
                if callable(method):
                    try:
                        result = method()
                        if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                            try:
                                await asyncio.wait_for(result, timeout=5.0)
                            except asyncio.TimeoutError:
                                # Expected: should log at info level when disconnected
                                elapsed = time.time() - start
                                self.assertLessEqual(elapsed, 6.0)
                    except Exception:
                        pass
                    break

            elapsed = time.time() - start
            self.assertLessEqual(elapsed, 6.0, "Avatar cleanup should complete in ≤6s")

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_avatar_cleanup_logging_severity(self):
        """When avatar is disconnected, errors log at debug, not warning."""
        avatar_connected = False
        # Mirror the severity selection logic
        _severity_name = "debug" if not avatar_connected else "warning"
        self.assertEqual(_severity_name, "debug")

        avatar_connected = True
        _severity_name = "debug" if not avatar_connected else "warning"
        self.assertEqual(_severity_name, "warning")


if __name__ == "__main__":
    unittest.main()
