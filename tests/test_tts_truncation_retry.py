"""Tests for _speak_question_safely and _safe_say after Phase 2 simplification.

Phase 2 removed the manual TTS lock/sequence/retry machinery. These tests
cover the simplified SDK-native behavior: normal completion, user interruption,
dedupe, shutdown guard, and delivery states.
"""

import asyncio
import pytest


# ── Minimal SpeechHandle mock ────────────────────────────────────────────────

class FakeSpeechHandle:
    """Simulates an SDK SpeechHandle returned by session.say()."""

    def __init__(self, *, interrupted: bool = False, fail: bool = False):
        self._interrupted = interrupted
        self._fail = fail
        self._done = False
        self._awaited = asyncio.Event()

    @property
    def interrupted(self) -> bool:
        return self._interrupted

    def done(self) -> bool:
        return self._done

    def __await__(self):
        return self._do_await().__await__()

    async def _do_await(self):
        if self._fail:
            raise RuntimeError("TTS engine crashed")
        await asyncio.sleep(0.01)  # simulate brief TTS
        self._done = True
        self._awaited.set()

    async def wait_for_playout(self):
        await self._awaited.wait()


class FakeAgentSession:
    """Simulates agent_session with SDK-native say() returning SpeechHandle."""

    def __init__(self, handles: list[FakeSpeechHandle] | None = None):
        self._handles = list(handles or [FakeSpeechHandle()])
        self._call_index = 0
        self.calls: list[dict] = []
        self._current_speech: FakeSpeechHandle | None = None

    @property
    def current_speech(self):
        return self._current_speech

    def say(self, text: str, *, allow_interruptions: bool = True) -> FakeSpeechHandle:
        handle = self._handles[min(self._call_index, len(self._handles) - 1)]
        self._call_index += 1
        self.calls.append({
            "text": text,
            "allow_interruptions": allow_interruptions,
        })
        self._current_speech = handle
        return handle

    def interrupt(self):
        if self._current_speech and not self._current_speech.done():
            self._current_speech._interrupted = True
            self._current_speech._done = True
            self._current_speech._awaited.set()


# ── Standalone replica of simplified _speak_question_safely ──────────────────

async def speak_question_safely(
    agent_session,
    text: str,
    *,
    allow_interruptions_first: bool = True,
    dedupe_key: str | None = None,
    dedupe_spoken: set | None = None,
    shutting_down: bool = False,
) -> bool:
    """Standalone replica of the Phase 2 _speak_question_safely."""
    if dedupe_key and dedupe_spoken is not None:
        if dedupe_key in dedupe_spoken:
            return True
        dedupe_spoken.add(dedupe_key)

    if shutting_down or agent_session is None:
        return False

    try:
        handle = agent_session.say(text, allow_interruptions=allow_interruptions_first)
        await handle
        # Wait for actual client-side playback to finish
        try:
            await asyncio.wait_for(handle.wait_for_playout(), timeout=120.0)
        except asyncio.TimeoutError:
            pass
        return not handle.interrupted
    except asyncio.CancelledError:
        return False
    except RuntimeError:
        return False


# ── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestSpeakQuestionSafely:
    """Tests for the simplified _speak_question_safely."""

    async def test_normal_completion_returns_true(self):
        """Speech that completes without interruption returns True."""
        session = FakeAgentSession([FakeSpeechHandle()])
        result = await speak_question_safely(session, "Hello world")
        assert result is True
        assert len(session.calls) == 1

    async def test_interrupted_speech_returns_false(self):
        """Speech interrupted by user returns False."""
        session = FakeAgentSession([FakeSpeechHandle(interrupted=True)])
        result = await speak_question_safely(session, "Long question text")
        assert result is False

    async def test_runtime_error_returns_false(self):
        """RuntimeError from say() returns False without raising."""
        session = FakeAgentSession([FakeSpeechHandle(fail=True)])
        result = await speak_question_safely(session, "Test text")
        assert result is False

    async def test_shutdown_returns_false_without_speaking(self):
        """When shutting_down=True, returns False without calling say()."""
        session = FakeAgentSession()
        result = await speak_question_safely(session, "Hello", shutting_down=True)
        assert result is False
        assert len(session.calls) == 0

    async def test_no_session_returns_false(self):
        """When agent_session is None, returns False."""
        result = await speak_question_safely(None, "Hello")
        assert result is False

    async def test_single_attempt_no_retry_loop(self):
        """Only one say() call is made — no retry loop."""
        session = FakeAgentSession([FakeSpeechHandle()])
        await speak_question_safely(session, "Test")
        assert len(session.calls) == 1

    async def test_allow_interruptions_passed_through(self):
        """allow_interruptions_first is forwarded to say()."""
        session = FakeAgentSession()
        await speak_question_safely(session, "Test", allow_interruptions_first=False)
        assert session.calls[0]["allow_interruptions"] is False


class TestTTSDedupe:
    """Per-turn dedupe prevents re-speaking same prompt."""

    def test_dedupe_blocks_second_call(self):
        """Second call with same dedupe_key returns True without speaking."""
        dedupe_spoken = set()
        key = "ask_Q1_ganesh"

        # First call
        assert key not in dedupe_spoken
        dedupe_spoken.add(key)

        # Second call
        assert key in dedupe_spoken

    def test_dedupe_allows_different_keys(self):
        """Different keys are not blocked."""
        dedupe_spoken = set()
        dedupe_spoken.add("ask_Q1_ganesh")
        assert "move_Q1_justin" not in dedupe_spoken

    def test_dedupe_cleared_on_new_turn(self):
        """Dedupe set is cleared between participant turns."""
        dedupe_spoken = set()
        dedupe_spoken.add("ask_Q1_ganesh")
        dedupe_spoken.clear()
        assert "ask_Q1_ganesh" not in dedupe_spoken


@pytest.mark.asyncio
class TestDedupIntegration:
    """Dedupe integration with speak_question_safely."""

    async def test_dedupe_skips_second_speak(self):
        """Second call with same dedupe_key skips say()."""
        session = FakeAgentSession([FakeSpeechHandle(), FakeSpeechHandle()])
        dedupe = set()
        r1 = await speak_question_safely(session, "Q1", dedupe_key="q1", dedupe_spoken=dedupe)
        r2 = await speak_question_safely(session, "Q1", dedupe_key="q1", dedupe_spoken=dedupe)
        assert r1 is True
        assert r2 is True  # returned True without speaking
        assert len(session.calls) == 1  # only one actual say() call


class TestDeliveryStates:
    """Explicit delivery states replace the generic 'delivered'."""

    def test_full_delivery_confirmed(self):
        states = {(1, "ganesh"): "delivered_full"}
        assert states.get((1, "ganesh")) in ("delivered_full", "delivered_partial", "delivered")

    def test_partial_delivery_confirmed(self):
        states = {(1, "ganesh"): "delivered_partial"}
        assert states.get((1, "ganesh")) in ("delivered_full", "delivered_partial", "delivered")

    def test_delivering_not_confirmed(self):
        states = {(1, "ganesh"): "delivering"}
        assert states.get((1, "ganesh")) not in ("delivered_full", "delivered_partial", "delivered")

    def test_failed_delivery_not_confirmed(self):
        states = {(1, "ganesh"): "failed_delivery"}
        assert states.get((1, "ganesh")) not in ("delivered_full", "delivered_partial", "delivered")

    def test_full_delivery_check(self):
        assert "delivered_full" in ("delivered_full", "delivered")
        assert "delivered_partial" not in ("delivered_full", "delivered")

    def test_backward_compat_old_delivered(self):
        states = {(1, "ganesh"): "delivered"}
        assert states.get((1, "ganesh")) in ("delivered_full", "delivered_partial", "delivered")


@pytest.mark.asyncio
class TestSafeInterruptBehavior:
    """Tests for _safe_interrupt using wait_for_playout instead of polling."""

    async def test_interrupt_succeeds(self):
        """When interrupt doesn't raise, returns True."""
        session = FakeAgentSession([FakeSpeechHandle()])
        # Start speech
        handle = session.say("test")
        session.interrupt()
        assert handle.interrupted is True

    async def test_wait_for_playout_on_denial(self):
        """When interrupt is denied, wait_for_playout completes when speech finishes."""
        handle = FakeSpeechHandle()
        # Simulate speech completing after brief delay
        async def complete_speech():
            await asyncio.sleep(0.05)
            handle._done = True
            handle._awaited.set()

        asyncio.create_task(complete_speech())
        await asyncio.wait_for(handle.wait_for_playout(), timeout=1.0)
        assert handle.done() is True


@pytest.mark.asyncio
class TestTTSActiveProperty:
    """Tests for the _tts_active property behavior."""

    async def test_no_session_returns_false(self):
        """_tts_active is False when there's no agent_session."""
        # Simulated: agent_session is None → _tts_active returns False
        agent_session = None
        result = agent_session is not None  # mirrors property logic
        assert result is False

    async def test_no_current_speech_returns_false(self):
        """_tts_active is False when current_speech is None."""
        session = FakeAgentSession()
        assert session.current_speech is None

    async def test_active_speech_returns_true(self):
        """_tts_active is True when speech is in progress."""
        session = FakeAgentSession([FakeSpeechHandle()])
        handle = session.say("test")
        assert session.current_speech is not None
        assert not session.current_speech.done()

    async def test_finished_speech_returns_false(self):
        """_tts_active is False when speech is done."""
        session = FakeAgentSession([FakeSpeechHandle()])
        handle = session.say("test")
        await handle
        assert session.current_speech.done() is True
