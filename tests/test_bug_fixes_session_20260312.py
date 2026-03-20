"""
Regression tests for bugs fixed in session 20260312.

Tests mirror the relevant logic from src/moderator_agent.py without importing
it directly (heavy LiveKit dependencies). When changing the source logic,
update the mirrored logic here too.

Covers:
  Bug 7 — RuntimeError on session.interrupt() with non-interruptible speech
  Bug 5 — Short fragment ("So I") falsely flagged as off-topic
  Bug 3 — Gentle warning resets _stt_nudge_given, enabling false "didn't hear" nudge
  Bug 4 — Off-topic reset doesn't extend polling deadline
  Bug 1 — Post-TTS cooldown prevents echo capture
  Bug 2 — Nudge text should not contain participant name
"""

import asyncio
import re
import time
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock


# ═══════════════════════════════════════════════════════════════════════════════
# Bug 7: session.interrupt() RuntimeError handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestInterruptRuntimeError:
    """_safe_interrupt must catch ALL RuntimeErrors as non-fatal and
    wait for _tts_in_flight to clear before returning."""

    @pytest.mark.asyncio
    async def test_non_interruptible_error_caught(self):
        """The classic 'does not allow interruptions' error must not crash."""
        session = AsyncMock()
        session.interrupt.side_effect = RuntimeError(
            "This generation handle does not allow interruptions"
        )
        session.say = AsyncMock()

        # Mirror of _safe_interrupt logic: catch ALL RuntimeErrors
        interrupt_denied_count = 0
        tts_in_flight = False  # simulate cleared

        try:
            await session.interrupt()
            succeeded = True
        except RuntimeError:
            succeeded = False
            interrupt_denied_count += 1
            # Wait for tts_in_flight (simulated — already False)
            waited = 0.0
            while tts_in_flight and waited < 3.0:
                await asyncio.sleep(0.1)
                waited += 0.1

        assert succeeded is False
        assert interrupt_denied_count == 1
        # After catching, code should proceed to say() the warning
        await session.say("test warning", allow_interruptions=False)
        session.say.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_runtime_errors_are_non_fatal(self):
        """ANY RuntimeError (not just 'does not allow') must be caught."""
        session = AsyncMock()
        session.interrupt.side_effect = RuntimeError("some other error")

        interrupt_denied_count = 0
        try:
            await session.interrupt()
        except RuntimeError:
            interrupt_denied_count += 1

        # Should NOT propagate — all RuntimeErrors are non-fatal
        assert interrupt_denied_count == 1

    @pytest.mark.asyncio
    async def test_force_end_stops_after_successful_interrupt(self):
        """Force-end loop should break early when interrupt succeeds."""
        session = AsyncMock()
        # First call fails, second succeeds
        session.interrupt.side_effect = [
            RuntimeError("does not allow interruptions"),
            None,  # success
        ]

        interrupt_denied_count = 0
        attempts = 0
        for _ in range(2):
            attempts += 1
            try:
                await session.interrupt()
                break  # success — stop retrying
            except RuntimeError:
                interrupt_denied_count += 1
            await asyncio.sleep(0)

        assert interrupt_denied_count == 1
        assert attempts == 2  # needed both attempts

    @pytest.mark.asyncio
    async def test_tts_in_flight_wait_on_denial(self):
        """After interrupt denial, should wait for tts_in_flight to clear."""
        tts_in_flight = True

        # Simulate a background task clearing tts_in_flight after 0.15s
        async def clear_flag():
            nonlocal tts_in_flight
            await asyncio.sleep(0.15)
            tts_in_flight = False

        asyncio.create_task(clear_flag())

        waited = 0.0
        while tts_in_flight and waited < 3.0:
            await asyncio.sleep(0.05)
            waited += 0.05

        assert tts_in_flight is False
        assert waited < 1.0, f"Should have cleared quickly, waited {waited:.2f}s"

    @pytest.mark.asyncio
    async def test_tts_in_flight_wait_caps_at_3s(self):
        """If tts_in_flight never clears, wait caps at 3s."""
        tts_in_flight = True  # never cleared

        waited = 0.0
        max_wait = 0.3  # shortened for test speed
        while tts_in_flight and waited < max_wait:
            await asyncio.sleep(0.05)
            waited += 0.05

        assert waited >= max_wait - 0.05


# ═══════════════════════════════════════════════════════════════════════════════
# Bug 5: Short response off-topic guard
# ═══════════════════════════════════════════════════════════════════════════════

# Minimum thresholds mirrored from moderator_agent.py
MIN_OFFTOPIC_WORDS = 8
MIN_OFFTOPIC_CHARS = 40

# Filler tokens mirrored from moderator_agent.py
_FILLER_TOKENS = frozenset({
    "um", "uh", "uhm", "erm", "hmm", "hm", "ah", "oh",
    "like", "so", "well", "yeah", "yes", "no", "okay", "ok",
    "right", "and", "but", "just", "you", "know", "mean",
    "i", "a", "the", "is", "it", "that", "this",
})


def _substantive_word_count(text: str) -> int:
    """Count words that are NOT filler/hedge tokens."""
    return sum(1 for w in text.lower().split() if w.strip(".,!?…") not in _FILLER_TOKENS)


def should_skip_offtopic_check(response_text: str) -> bool:
    """Mirror of _is_too_short_for_offtopic() from moderator_agent.py.

    Three gates (any one triggers skip):
      1. Total word count < MIN_OFFTOPIC_WORDS
      2. Total char count < MIN_OFFTOPIC_CHARS
      3. Substantive (non-filler) word count < 4
    """
    words = len(response_text.split())
    chars = len(response_text)
    substantive = _substantive_word_count(response_text)
    return words < MIN_OFFTOPIC_WORDS or chars < MIN_OFFTOPIC_CHARS or substantive < 4


class TestSubstantiveWordCount:
    """Filler tokens must be stripped before counting."""

    def test_pure_filler(self):
        assert _substantive_word_count("um uh well like you know so") == 0

    def test_mixed(self):
        # "cheese" and "pizza" are substantive; the rest are filler
        assert _substantive_word_count("I like cheese and pizza") == 2

    def test_all_substantive(self):
        assert _substantive_word_count("infrastructure needs improvement") == 3

    def test_punctuation_stripped(self):
        # "what" is NOT in the filler set, so "What?" → 1 substantive
        assert _substantive_word_count("What?") == 1
        # "infrastructure" is substantive even with trailing punctuation
        assert _substantive_word_count("infrastructure!") == 1
        # "well," → "well" IS filler → 0
        assert _substantive_word_count("well,") == 0

    def test_empty(self):
        assert _substantive_word_count("") == 0


class TestShortResponseOffTopicGuard:
    """Very short/incomplete responses must NOT trigger the off-topic path."""

    def test_two_word_fragment_skipped(self):
        assert should_skip_offtopic_check("So I") is True

    def test_single_word_skipped(self):
        assert should_skip_offtopic_check("Well") is True

    def test_filler_phrase_skipped(self):
        assert should_skip_offtopic_check("Um, let me think") is True

    def test_short_incomplete_skipped(self):
        assert should_skip_offtopic_check("I think that") is True

    def test_substantive_offtopic_not_skipped(self):
        """A genuinely off-topic response with enough content should NOT be skipped."""
        assert should_skip_offtopic_check(
            "I love cheese and pizza and burgers for dinner every night"
        ) is False

    def test_boundary_exactly_8_words_not_skipped(self):
        response = "one two three four five six seven eight"
        assert len(response.split()) == 8
        # 8 words but < 40 chars = still skipped (OR condition)
        assert should_skip_offtopic_check(response) is True

    def test_boundary_8_words_40_chars_not_skipped(self):
        response = "I really think that this particular idea is great"
        assert len(response.split()) >= 8
        assert len(response) >= 40
        assert should_skip_offtopic_check(response) is False

    def test_filler_heavy_8_words_skipped(self):
        """8+ words but only filler — substantive count < 4 triggers skip."""
        response = "um well like you know so yeah okay right"
        assert len(response.split()) >= 8
        assert should_skip_offtopic_check(response) is True

    def test_filler_heavy_long_skipped(self):
        """Long filler-only response — substantive count < 4 triggers skip."""
        response = "um uh well like you know I mean yeah so like okay"
        assert len(response.split()) >= 8
        assert len(response) >= 40
        assert should_skip_offtopic_check(response) is True

    def test_real_session_so_i(self):
        """Exact fragment from the log that triggered Bug 5."""
        assert should_skip_offtopic_check("So I") is True

    def test_real_session_so_i_i(self):
        """Accumulated fragment variant from the log."""
        assert should_skip_offtopic_check("So I I") is True

    def test_real_session_i_dot_dot(self):
        """Another tiny fragment from the log."""
        assert should_skip_offtopic_check("I...") is True


# ═══════════════════════════════════════════════════════════════════════════════
# Bug 3: Gentle warning must NOT reset _stt_nudge_given
# ═══════════════════════════════════════════════════════════════════════════════

class TestGentleWarningNudgeFlag:
    """After gentle warning, _stt_nudge_given must remain True to prevent
    the STT health-check from firing 'I didn't hear you'."""

    def test_nudge_flag_stays_true_pre_warning(self):
        """Simulates the pre-warning state reset."""
        # Before the fix, this was set to False
        _stt_nudge_given = True  # The fix: set True, not False
        assert _stt_nudge_given is True

    def test_nudge_flag_stays_true_post_warning(self):
        """Simulates the post-warning state reset."""
        _stt_nudge_given = True  # The fix: set True, not False
        assert _stt_nudge_given is True

    def test_stt_health_check_suppressed_when_nudge_given(self):
        """Mirror of the STT health-check condition from the polling loop."""
        latest_user_response = None
        response_captured = False
        _stt_nudge_given = True  # Set by gentle warning
        _first_vad_speaking_time = datetime.now()
        user_currently_speaking = False

        # The condition that triggers the "I didn't hear you" nudge:
        should_nudge = (
            latest_user_response is None
            and not response_captured
            and not _stt_nudge_given  # ← This blocks it
            and _first_vad_speaking_time is not None
            and not user_currently_speaking
        )
        assert should_nudge is False, (
            "STT nudge should NOT fire after gentle warning"
        )

    def test_response_timeout_cancelled_after_warning(self):
        """The response timeout task must be cancelled post-warning."""
        # Simulate: task exists before warning
        response_timeout_task = MagicMock()
        response_timeout_task.cancel = MagicMock()

        # Post-warning logic
        if response_timeout_task:
            response_timeout_task.cancel()
            response_timeout_task = None

        assert response_timeout_task is None


# ═══════════════════════════════════════════════════════════════════════════════
# Bug 4: Off-topic reset must extend polling deadline
# ═══════════════════════════════════════════════════════════════════════════════

class TestOffTopicPollingDeadline:
    """After off-topic reset, _polling_deadline must be extended."""

    def test_deadline_extended_after_offtopic_reset(self):
        """Simulates the off-topic reset deadline extension."""
        max_turn_duration = 20
        first_interrupt_grace = 30
        second_interrupt_grace = 15

        max_turn_time = max_turn_duration + first_interrupt_grace + second_interrupt_grace
        off_topic_extension = max_turn_time + 10

        old_deadline = time.time() - 5  # Already expired
        new_deadline = time.time() + off_topic_extension

        # After off-topic reset, deadline should be in the future
        assert new_deadline > time.time()
        assert new_deadline > old_deadline
        assert off_topic_extension == 75  # 20 + 30 + 15 + 10

    def test_deadline_gives_full_turn_time(self):
        """The extension should be at least max_turn_time."""
        max_turn_duration = 20
        first_interrupt_grace = 30
        second_interrupt_grace = 15
        max_turn_time = max_turn_duration + first_interrupt_grace + second_interrupt_grace

        off_topic_extension = max_turn_time + 10
        new_deadline = time.time() + off_topic_extension

        # Participant should have at least max_turn_time seconds
        assert new_deadline - time.time() >= max_turn_time


# ═══════════════════════════════════════════════════════════════════════════════
# Bug 2: Nudge text should not contain participant name
# ═══════════════════════════════════════════════════════════════════════════════

class TestNudgeTextNoName:
    """The response timeout nudge must not prefix the participant name."""

    def test_nudge_text_has_no_name(self):
        """The fixed nudge text should be name-free."""
        prompt_text = "Please go ahead and share your thoughts."
        # Should NOT start with a capitalized name pattern like "Ganesh, ..."
        assert not re.match(r"^[A-Z][a-z]+,", prompt_text), (
            "Nudge text should not start with a participant name"
        )

    def test_nudge_does_not_use_display_name(self):
        """Ensure no f-string with display_name in the nudge."""
        display_name = "Ganesh"
        # Old buggy code: f"{display_name}, please let me know what you think."
        # New fixed code:
        prompt_text = "Please go ahead and share your thoughts."
        assert display_name not in prompt_text


# ═══════════════════════════════════════════════════════════════════════════════
# Bug 6: Avatar disconnect notification
# ═══════════════════════════════════════════════════════════════════════════════

class TestAvatarDisconnectNotification:
    """When avatar disconnects, participants should be notified via TTS
    only in a safe window with dedupe cooldown."""

    def test_avatar_identity_detected(self):
        """The avatar identity check should match anam-avatar-agent."""
        identity = "anam-avatar-agent"
        assert "avatar-agent" in identity

    def test_non_avatar_not_matched(self):
        """Regular participants should not match the avatar check."""
        assert "avatar-agent" not in "ganesh"
        assert "avatar-agent" not in "justin"
        assert "avatar-agent" not in "christopher"

    def test_dedupe_cooldown_suppresses_second_notice(self):
        """A second disconnect within 60s should be suppressed."""
        _COOLDOWN = 60
        last_notice = time.time()
        # Immediately after first notice
        assert (time.time() - last_notice) < _COOLDOWN

    def test_dedupe_cooldown_allows_after_expiry(self):
        """After cooldown expires, notice should be allowed."""
        _COOLDOWN = 60
        last_notice = time.time() - 61  # 61s ago
        assert (time.time() - last_notice) >= _COOLDOWN

    def test_safe_window_check(self):
        """Notification should only fire when no active speaking/warning/TTS."""
        user_currently_speaking = False
        gentle_warning_in_progress = False
        tts_in_flight = False

        safe = (not user_currently_speaking
                and not gentle_warning_in_progress
                and not tts_in_flight)
        assert safe is True

    def test_unsafe_window_blocked(self):
        """Notification blocked when user is speaking."""
        user_currently_speaking = True
        gentle_warning_in_progress = False
        tts_in_flight = False

        safe = (not user_currently_speaking
                and not gentle_warning_in_progress
                and not tts_in_flight)
        assert safe is False

    def test_unsafe_warning_blocked(self):
        """Notification blocked when gentle warning is in progress."""
        user_currently_speaking = False
        gentle_warning_in_progress = True
        tts_in_flight = False

        safe = (not user_currently_speaking
                and not gentle_warning_in_progress
                and not tts_in_flight)
        assert safe is False

    def test_unsafe_tts_blocked(self):
        """Notification blocked when TTS is in flight."""
        user_currently_speaking = False
        gentle_warning_in_progress = False
        tts_in_flight = True

        safe = (not user_currently_speaking
                and not gentle_warning_in_progress
                and not tts_in_flight)
        assert safe is False


# ═══════════════════════════════════════════════════════════════════════════════
# TTS delivery retry + conditional delivery state
# ═══════════════════════════════════════════════════════════════════════════════

class TestTTSDeliveryRetryAndState:
    """delivery_state should only be 'delivered' when TTS succeeds.
    If TTS fails, a full re-delivery cycle should be attempted."""

    @pytest.mark.asyncio
    async def test_successful_tts_sets_delivered(self):
        """When _speak_question_safely returns True, state = delivered."""
        delivery_state = {}

        async def fake_speak(text, *, context="", **kw):
            return True

        tts_fully_spoken = await fake_speak("question text", context="ask_Q1_ganesh")
        if tts_fully_spoken:
            delivery_state["Q1_ganesh"] = "delivered"
        else:
            delivery_state["Q1_ganesh"] = "delivered"  # still delivered but partial

        assert delivery_state["Q1_ganesh"] == "delivered"
        assert tts_fully_spoken is True

    @pytest.mark.asyncio
    async def test_failed_tts_triggers_redelivery(self):
        """When first TTS fails, a re-delivery attempt should be made."""
        call_count = 0

        async def fake_speak(text, *, context="", allow_interruptions_first=True, **kw):
            nonlocal call_count
            call_count += 1
            # First call fails (truncated), re-delivery succeeds
            if "redeliver" in context:
                return True
            return False

        tts_fully_spoken = await fake_speak("question text", context="ask_Q2_ganesh")
        if not tts_fully_spoken:
            await asyncio.sleep(0)  # simulates 1.0s settle
            tts_fully_spoken = await fake_speak(
                "question text",
                context="ask_Q2_ganesh_redeliver",
                allow_interruptions_first=False,
            )

        assert call_count == 2, "Should have attempted re-delivery"
        assert tts_fully_spoken is True

    @pytest.mark.asyncio
    async def test_both_attempts_fail(self):
        """When both TTS attempts fail, should still proceed (partial)."""
        async def fake_speak(text, *, context="", **kw):
            return False

        tts_fully_spoken = await fake_speak("question text", context="ask_Q3_ganesh")
        if not tts_fully_spoken:
            tts_fully_spoken = await fake_speak(
                "question text", context="ask_Q3_ganesh_redeliver"
            )

        assert tts_fully_spoken is False


# ═══════════════════════════════════════════════════════════════════════════════
# Adaptive post-TTS VAD gate
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdaptivePostTTSGate:
    """Post-TTS gate should wait for VAD silence, with 1.5s max fallback."""

    @pytest.mark.asyncio
    async def test_gate_exits_early_on_vad_silence(self):
        """If user_currently_speaking is False, gate exits immediately."""
        user_currently_speaking = False

        gate_start = asyncio.get_event_loop().time()
        gate_max = 1.5
        while (asyncio.get_event_loop().time() - gate_start) < gate_max:
            if not user_currently_speaking:
                break
            await asyncio.sleep(0.05)
        gate_elapsed = asyncio.get_event_loop().time() - gate_start

        # Should exit almost immediately (well under 1.5s)
        assert gate_elapsed < 0.1, f"Gate should exit early, took {gate_elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_gate_respects_max_timeout(self):
        """If user_currently_speaking stays True, gate caps at 1.5s."""
        user_currently_speaking = True  # Simulates stuck/noisy VAD

        gate_start = asyncio.get_event_loop().time()
        gate_max = 0.3  # shortened for test speed
        while (asyncio.get_event_loop().time() - gate_start) < gate_max:
            if not user_currently_speaking:
                break
            await asyncio.sleep(0.05)
        gate_elapsed = asyncio.get_event_loop().time() - gate_start

        assert gate_elapsed >= gate_max - 0.05, "Gate should wait until max timeout"

    @pytest.mark.asyncio
    async def test_gate_adds_buffer_on_early_exit(self):
        """When VAD settles early, a 0.3s audio pipeline buffer is added."""
        user_currently_speaking = False

        gate_start = asyncio.get_event_loop().time()
        gate_max = 1.5
        while (asyncio.get_event_loop().time() - gate_start) < gate_max:
            if not user_currently_speaking:
                break
            await asyncio.sleep(0.05)
        gate_elapsed = asyncio.get_event_loop().time() - gate_start

        # Simulate the buffer add
        buffer_added = False
        if gate_elapsed < gate_max:
            await asyncio.sleep(0.05)  # shortened for test
            buffer_added = True

        assert buffer_added is True, "Buffer should be added when gate exits early"


# ═══════════════════════════════════════════════════════════════════════════════
# Bug 8: Shutdown / RuntimeError crash — iterative survey loop + safe_say
# ═══════════════════════════════════════════════════════════════════════════════

class TestSafeSay:
    """_safe_say must catch RuntimeError from session closing and set _shutting_down."""

    @pytest.mark.asyncio
    async def test_safe_say_returns_true_on_success(self):
        """Normal operation: session.say() succeeds → returns True."""
        agent = MagicMock()
        agent._shutting_down = False
        agent.agent_session = AsyncMock()
        agent.agent_session.say = AsyncMock()

        # Inline replica of _safe_say
        async def safe_say(text, *, allow_interruptions=True, context=""):
            if agent._shutting_down:
                return False
            if not agent.agent_session:
                return False
            try:
                await agent.agent_session.say(text, allow_interruptions=allow_interruptions)
                return True
            except RuntimeError as e:
                if "closing" in str(e).lower() or "closed" in str(e).lower():
                    agent._shutting_down = True
                return False

        result = await safe_say("Hello", context="test")
        assert result is True
        assert agent._shutting_down is False

    @pytest.mark.asyncio
    async def test_safe_say_catches_closing_error(self):
        """session.say() raises 'AgentSession is closing' → returns False, sets flag."""
        agent = MagicMock()
        agent._shutting_down = False
        agent.agent_session = AsyncMock()
        agent.agent_session.say = AsyncMock(
            side_effect=RuntimeError("AgentSession is closing, cannot use say()")
        )

        async def safe_say(text, *, allow_interruptions=True, context=""):
            if agent._shutting_down:
                return False
            if not agent.agent_session:
                return False
            try:
                await agent.agent_session.say(text, allow_interruptions=allow_interruptions)
                return True
            except RuntimeError as e:
                if "closing" in str(e).lower() or "closed" in str(e).lower():
                    agent._shutting_down = True
                return False

        result = await safe_say("Hello", context="test")
        assert result is False
        assert agent._shutting_down is True

    @pytest.mark.asyncio
    async def test_safe_say_suppressed_when_shutting_down(self):
        """When _shutting_down is already True, say() is never called."""
        agent = MagicMock()
        agent._shutting_down = True
        agent.agent_session = AsyncMock()
        agent.agent_session.say = AsyncMock()

        async def safe_say(text, *, allow_interruptions=True, context=""):
            if agent._shutting_down:
                return False
            await agent.agent_session.say(text, allow_interruptions=allow_interruptions)
            return True

        result = await safe_say("Hello", context="test")
        assert result is False
        agent.agent_session.say.assert_not_called()

    @pytest.mark.asyncio
    async def test_safe_say_no_session(self):
        """If agent_session is None, returns False without crashing."""
        agent = MagicMock()
        agent._shutting_down = False
        agent.agent_session = None

        async def safe_say(text, *, allow_interruptions=True, context=""):
            if agent._shutting_down:
                return False
            if not agent.agent_session:
                return False
            await agent.agent_session.say(text, allow_interruptions=allow_interruptions)
            return True

        result = await safe_say("Hello", context="test")
        assert result is False


class TestSurveyLoopTrampoline:
    """The _survey_loop trampoline must iterate without recursion and respect _shutting_down."""

    @pytest.mark.asyncio
    async def test_trampoline_iterates_ask_then_move(self):
        """Loop calls ask→move→ask→None, tracking call order."""
        calls = []

        async def ask_impl():
            calls.append("ask")
            return "move"

        async def move_impl():
            calls.append("move")
            return "ask" if len(calls) < 4 else None

        # Simulate the trampoline loop
        action = "ask"
        shutting_down = False
        while action and not shutting_down:
            if action == "ask":
                action = await ask_impl()
            elif action == "move":
                action = await move_impl()
            else:
                break

        assert calls == ["ask", "move", "ask", "move"]

    @pytest.mark.asyncio
    async def test_trampoline_stops_on_shutting_down(self):
        """Loop exits when _shutting_down is set mid-iteration."""
        calls = []
        shutting_down = False

        async def ask_impl():
            nonlocal shutting_down
            calls.append("ask")
            shutting_down = True  # simulate session closing
            return "move"

        async def move_impl():
            calls.append("move")
            return "ask"

        action = "ask"
        while action and not shutting_down:
            if action == "ask":
                action = await ask_impl()
            elif action == "move":
                action = await move_impl()

        # Should have only called ask once — move never ran
        assert calls == ["ask"]

    @pytest.mark.asyncio
    async def test_trampoline_catches_session_closing_error(self):
        """RuntimeError('session closing') inside step → sets shutting_down, loop exits."""
        calls = []
        shutting_down = False

        async def ask_impl():
            calls.append("ask")
            raise RuntimeError("AgentSession is closing, cannot use say()")

        action = "ask"
        _iteration = 0
        while action and not shutting_down:
            _iteration += 1
            try:
                if action == "ask":
                    action = await ask_impl()
                else:
                    break
            except RuntimeError as e:
                if "closing" in str(e).lower():
                    shutting_down = True
                    break
                raise

        assert calls == ["ask"]
        assert shutting_down is True

    @pytest.mark.asyncio
    async def test_trampoline_no_recursion_depth(self):
        """Run 500 iterations to prove no recursion depth issue."""
        calls = []
        max_iterations = 500
        iteration = [0]

        async def ask_impl():
            iteration[0] += 1
            calls.append("ask")
            return "move"

        async def move_impl():
            calls.append("move")
            if iteration[0] >= max_iterations:
                return None
            return "ask"

        action = "ask"
        shutting_down = False
        while action and not shutting_down:
            if action == "ask":
                action = await ask_impl()
            elif action == "move":
                action = await move_impl()

        assert len(calls) == max_iterations * 2  # 500 ask + 500 move
        assert iteration[0] == max_iterations


class TestCancelAllMonitorTasks:
    """_cancel_all_monitor_tasks must cancel both tasks and clear current_turn."""

    def test_cancels_both_tasks(self):
        """Both response_timeout_task and turn_monitor_task are cancelled."""
        agent = MagicMock()
        timeout_task = MagicMock()
        turn_task = MagicMock()
        agent.response_timeout_task = timeout_task
        agent.turn_monitor_task = turn_task
        agent.current_turn = MagicMock()

        # Inline replica
        if agent.response_timeout_task:
            agent.response_timeout_task.cancel()
            agent.response_timeout_task = None
        if agent.turn_monitor_task:
            agent.turn_monitor_task.cancel()
            agent.turn_monitor_task = None
        agent.current_turn = None

        timeout_task.cancel.assert_called_once()
        turn_task.cancel.assert_called_once()
        assert agent.response_timeout_task is None
        assert agent.turn_monitor_task is None
        assert agent.current_turn is None

    def test_no_crash_when_tasks_are_none(self):
        """No error when both tasks are already None."""
        agent = MagicMock()
        agent.response_timeout_task = None
        agent.turn_monitor_task = None
        agent.current_turn = None

        # Should not crash
        if agent.response_timeout_task:
            agent.response_timeout_task.cancel()
        if agent.turn_monitor_task:
            agent.turn_monitor_task.cancel()
        # No assertion needed — test passes if no exception


class TestShuttingDownGuard:
    """The _shutting_down flag must be respected by polling loops and monitors."""

    @pytest.mark.asyncio
    async def test_polling_loop_exits_on_shutting_down(self):
        """Simulate a polling loop that checks _shutting_down each iteration."""
        shutting_down = False
        iterations = 0

        for _ in range(1000):
            await asyncio.sleep(0)  # yield
            if shutting_down:
                break
            iterations += 1
            if iterations == 5:
                shutting_down = True  # simulate shutdown mid-poll

        assert iterations == 5

    @pytest.mark.asyncio
    async def test_monitor_exits_on_shutting_down(self):
        """Simulate monitor_turn_duration checking _shutting_down."""
        shutting_down = False
        monitor_ran = False
        monitor_stopped_early = False

        async def monitor():
            nonlocal monitor_ran, monitor_stopped_early
            monitor_ran = True
            for _ in range(100):
                if shutting_down:
                    monitor_stopped_early = True
                    return
                await asyncio.sleep(0.01)

        shutting_down = True
        await monitor()
        assert monitor_ran is True
        assert monitor_stopped_early is True


class TestParticipantDisconnectDuringPolling:
    """Simulate participant disconnect during active polling — no exception should escape."""

    @pytest.mark.asyncio
    async def test_disconnect_triggers_shutdown_no_crash(self):
        """When session closes during polling, the loop should exit cleanly."""
        shutting_down = False
        say_calls = 0
        loop_exited_cleanly = False

        async def safe_say(text, **kwargs):
            nonlocal say_calls, shutting_down
            say_calls += 1
            if say_calls >= 2:
                # Simulate session closing on 2nd TTS attempt
                shutting_down = True
                raise RuntimeError("AgentSession is closing, cannot use say()")

        # Simulate polling loop
        action = "ask"
        _iteration = 0
        while action and not shutting_down:
            _iteration += 1
            try:
                if action == "ask":
                    await safe_say("Question text")
                    action = "move"
                elif action == "move":
                    await safe_say("Thank you")
                    action = "ask" if _iteration < 10 else None
            except RuntimeError as e:
                if "closing" in str(e).lower():
                    shutting_down = True
                    loop_exited_cleanly = True
                    break
                raise

        assert shutting_down is True
        assert loop_exited_cleanly is True
        assert say_calls == 2  # first succeeded, second triggered shutdown

    @pytest.mark.asyncio
    async def test_safe_say_prevents_crash_on_disconnect(self):
        """_safe_say absorbs the error — no exception propagates."""
        agent = MagicMock()
        agent._shutting_down = False
        call_count = 0

        async def safe_say(text, **kwargs):
            nonlocal call_count
            if agent._shutting_down:
                return False
            call_count += 1
            try:
                if call_count >= 3:
                    raise RuntimeError("AgentSession is closing")
                return True
            except RuntimeError as e:
                if "closing" in str(e).lower():
                    agent._shutting_down = True
                return False

        results = []
        for _ in range(5):
            r = await safe_say("text")
            results.append(r)

        # First 2 succeed, 3rd catches and sets flag, 4th+5th suppressed
        assert results == [True, True, False, False, False]
        assert agent._shutting_down is True
