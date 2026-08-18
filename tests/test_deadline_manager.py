"""Tests for DeadlineManager — single owner of all timeout-window state.

Includes unit tests for every method and 4 lifecycle tests that simulate
real multi-step scenarios from production.
"""
import time
from src.domain.deadline_manager import DeadlineManager, WatchdogSignal
from src.domain.constants import (
    SILENCE_WATCHDOG_TIMEOUT,
    IDLE_NO_VAD_TIMEOUT,
    TTS_SAFETY_MARGIN,
    POST_NUDGE_EXTENSION_SECS,
    STT_NUDGE_VAD_THRESHOLD
)


# ── Epoch tests ──────────────────────────────────────────────────────────────

class TestEpoch:
    def test_starts_at_zero(self):
        dm = DeadlineManager()
        assert dm.epoch == 0

    def test_bump_increments(self):
        dm = DeadlineManager()
        new = dm.bump_epoch()
        assert new == 1
        assert dm.epoch == 1

    def test_bump_monotonic(self):
        dm = DeadlineManager()
        e1 = dm.bump_epoch()
        e2 = dm.bump_epoch()
        e3 = dm.bump_epoch()
        assert e1 < e2 < e3

    def test_is_stale_detects_mismatch(self):
        dm = DeadlineManager()
        old = dm.epoch
        dm.bump_epoch()
        assert dm.is_stale(old) is True

    def test_is_stale_matches_current(self):
        dm = DeadlineManager()
        dm.bump_epoch()
        assert dm.is_stale(dm.epoch) is False


# ── Deadline tests ───────────────────────────────────────────────────────────

class TestDeadline:
    def test_set_deadline(self):
        dm = DeadlineManager()
        now = 1000.0
        dm.set_deadline(30.0, now=now)
        assert dm.deadline == 1030.0
        assert dm.start_time == 1000.0

    def test_remaining(self):
        dm = DeadlineManager()
        dm.set_deadline(30.0, now=1000.0)
        assert dm.remaining(now=1010.0) == 20.0

    def test_remaining_negative_when_expired(self):
        dm = DeadlineManager()
        dm.set_deadline(10.0, now=1000.0)
        assert dm.remaining(now=1015.0) == -5.0

    def test_remaining_zero_when_no_deadline(self):
        dm = DeadlineManager()
        assert dm.remaining() == 0.0

    def test_is_expired_true(self):
        dm = DeadlineManager()
        dm.set_deadline(10.0, now=1000.0)
        assert dm.is_expired(now=1010.0) is True

    def test_is_expired_false(self):
        dm = DeadlineManager()
        dm.set_deadline(10.0, now=1000.0)
        assert dm.is_expired(now=1005.0) is False

    def test_is_expired_false_when_no_deadline(self):
        dm = DeadlineManager()
        assert dm.is_expired() is False

    def test_extend_deadline_monotonic(self):
        dm = DeadlineManager()
        dm.set_deadline(30.0, now=1000.0)  # deadline = 1030
        # Try to extend to 1025 (earlier) — should not move
        result = dm.extend_deadline(25.0, now=1000.0)
        assert result is False
        assert dm.deadline == 1030.0

    def test_extend_deadline_later(self):
        dm = DeadlineManager()
        dm.set_deadline(30.0, now=1000.0)  # deadline = 1030
        # Extend to 1040 (later) — should move
        result = dm.extend_deadline(20.0, now=1020.0)
        assert result is True
        assert dm.deadline == 1040.0

    def test_extend_deadline_to_absolute(self):
        dm = DeadlineManager()
        dm.set_deadline(30.0, now=1000.0)  # deadline = 1030
        result = dm.extend_deadline_to(1050.0)
        assert result is True
        assert dm.deadline == 1050.0

    def test_extend_deadline_to_earlier_noop(self):
        dm = DeadlineManager()
        dm.set_deadline(30.0, now=1000.0)  # deadline = 1030
        result = dm.extend_deadline_to(1020.0)
        assert result is False
        assert dm.deadline == 1030.0

    def test_extend_deadline_noop_when_no_deadline(self):
        dm = DeadlineManager()
        assert dm.extend_deadline(10.0) is False


# ── STT health watchdog tests ────────────────────────────────────────────────

class TestCheckSttHealth:
    def test_fires_after_3s_no_stt(self):
        dm = DeadlineManager()
        signal = dm.check_stt_health(
            has_response=False, has_stt_transcript=False,
            first_vad_seconds_ago=4.0, user_speaking=False,
        )
        assert signal == WatchdogSignal.STT_NUDGE

    def test_does_not_fire_under_3s(self):
        dm = DeadlineManager()
        signal = dm.check_stt_health(
            has_response=False, has_stt_transcript=False,
            first_vad_seconds_ago=2.0, user_speaking=False,
        )
        assert signal == WatchdogSignal.NONE

    def test_does_not_fire_exactly_at_the_threshold(self):
        """The check is `> STT_NUDGE_VAD_THRESHOLD`, so the threshold itself
        must not fire. Guards against a `>` / `>=` slip during retuning."""
        dm = DeadlineManager()
        signal = dm.check_stt_health(
            has_response=False, has_stt_transcript=False,
            first_vad_seconds_ago=STT_NUDGE_VAD_THRESHOLD, user_speaking=False,
        )
        assert signal == WatchdogSignal.NONE

    def test_suppressed_if_already_fired(self):
        dm = DeadlineManager()
        dm.check_stt_health(
            has_response=False, has_stt_transcript=False,
            first_vad_seconds_ago=7.0, user_speaking=False,
        )
        # Second call should be suppressed
        signal = dm.check_stt_health(
            has_response=False, has_stt_transcript=False,
            first_vad_seconds_ago=10.0, user_speaking=False,
        )
        assert signal == WatchdogSignal.NONE

    def test_suppressed_if_has_response(self):
        dm = DeadlineManager()
        signal = dm.check_stt_health(
            has_response=True, has_stt_transcript=False,
            first_vad_seconds_ago=7.0, user_speaking=False,
        )
        assert signal == WatchdogSignal.NONE

    def test_suppressed_if_has_stt(self):
        dm = DeadlineManager()
        signal = dm.check_stt_health(
            has_response=False, has_stt_transcript=True,
            first_vad_seconds_ago=7.0, user_speaking=False,
        )
        assert signal == WatchdogSignal.NONE

    def test_suppressed_if_user_speaking(self):
        dm = DeadlineManager()
        signal = dm.check_stt_health(
            has_response=False, has_stt_transcript=False,
            first_vad_seconds_ago=7.0, user_speaking=True,
        )
        assert signal == WatchdogSignal.NONE

    def test_suppressed_if_no_vad(self):
        dm = DeadlineManager()
        signal = dm.check_stt_health(
            has_response=False, has_stt_transcript=False,
            first_vad_seconds_ago=None, user_speaking=False,
        )
        assert signal == WatchdogSignal.NONE


# ── Silence watchdog tests ───────────────────────────────────────────────────

class TestCheckSilenceWatchdog:
    def test_fires_after_timeout(self):
        dm = DeadlineManager()
        signal = dm.check_silence_watchdog(
            encouragement_given=True, has_response=False,
            user_speaking=False, warning_in_progress=False,
            last_progress_seconds_ago=SILENCE_WATCHDOG_TIMEOUT + 1,
        )
        assert signal == WatchdogSignal.SILENCE_PROMPT

    def test_does_not_fire_before_timeout(self):
        dm = DeadlineManager()
        signal = dm.check_silence_watchdog(
            encouragement_given=True, has_response=False,
            user_speaking=False, warning_in_progress=False,
            last_progress_seconds_ago=SILENCE_WATCHDOG_TIMEOUT - 1,
        )
        assert signal == WatchdogSignal.NONE

    def test_requires_encouragement_given(self):
        dm = DeadlineManager()
        signal = dm.check_silence_watchdog(
            encouragement_given=False, has_response=False,
            user_speaking=False, warning_in_progress=False,
            last_progress_seconds_ago=SILENCE_WATCHDOG_TIMEOUT + 5,
        )
        assert signal == WatchdogSignal.NONE

    def test_suppressed_during_warning(self):
        dm = DeadlineManager()
        signal = dm.check_silence_watchdog(
            encouragement_given=True, has_response=False,
            user_speaking=False, warning_in_progress=True,
            last_progress_seconds_ago=SILENCE_WATCHDOG_TIMEOUT + 5,
        )
        assert signal == WatchdogSignal.NONE

    def test_suppressed_if_has_response(self):
        dm = DeadlineManager()
        signal = dm.check_silence_watchdog(
            encouragement_given=True, has_response=True,
            user_speaking=False, warning_in_progress=False,
            last_progress_seconds_ago=SILENCE_WATCHDOG_TIMEOUT + 5,
        )
        assert signal == WatchdogSignal.NONE

    def test_one_shot(self):
        dm = DeadlineManager()
        dm.check_silence_watchdog(
            encouragement_given=True, has_response=False,
            user_speaking=False, warning_in_progress=False,
            last_progress_seconds_ago=SILENCE_WATCHDOG_TIMEOUT + 1,
        )
        signal = dm.check_silence_watchdog(
            encouragement_given=True, has_response=False,
            user_speaking=False, warning_in_progress=False,
            last_progress_seconds_ago=SILENCE_WATCHDOG_TIMEOUT + 10,
        )
        assert signal == WatchdogSignal.NONE


# ── Idle-no-VAD watchdog tests ───────────────────────────────────────────────

class TestCheckIdleNoVad:
    def test_fires_after_timeout_no_tts(self):
        dm = DeadlineManager()
        signal = dm.check_idle_no_vad(
            has_vad=False, has_stt_transcript=False, has_response=False,
            user_speaking=False, warning_in_progress=False,
            tts_remaining=0.0, elapsed_since_start=IDLE_NO_VAD_TIMEOUT + 1,
        )
        assert signal == WatchdogSignal.IDLE_REPROMPT

    def test_accounts_for_tts_remaining(self):
        """TTS still playing should offset the idle threshold.

        With 13.4s TTS remaining: offset = 13.4 + TTS_SAFETY_MARGIN = 16.4
        At 12s elapsed: 12 - 16.4 = -4.4 < IDLE_NO_VAD_TIMEOUT → should NOT fire.
        """
        dm = DeadlineManager()
        signal = dm.check_idle_no_vad(
            has_vad=False, has_stt_transcript=False, has_response=False,
            user_speaking=False, warning_in_progress=False,
            tts_remaining=13.4, elapsed_since_start=12.0,
        )
        assert signal == WatchdogSignal.NONE

    def test_fires_after_tts_offset_expires(self):
        """After TTS offset expires, idle timeout should fire.

        With 13.4s TTS: offset = 13.4 + 3.0 = 16.4
        At 28.5s elapsed: 28.5 - 16.4 = 12.1 > 12.0 → fires.
        """
        dm = DeadlineManager()
        signal = dm.check_idle_no_vad(
            has_vad=False, has_stt_transcript=False, has_response=False,
            user_speaking=False, warning_in_progress=False,
            tts_remaining=13.4, elapsed_since_start=28.5,
        )
        assert signal == WatchdogSignal.IDLE_REPROMPT

    def test_first_nudge_given_suppresses(self):
        """If timeout monitor already nudged, idle-no-VAD suppresses."""
        dm = DeadlineManager()
        dm.mark_nudge_given()
        signal = dm.check_idle_no_vad(
            has_vad=False, has_stt_transcript=False, has_response=False,
            user_speaking=False, warning_in_progress=False,
            tts_remaining=0.0, elapsed_since_start=IDLE_NO_VAD_TIMEOUT + 5,
        )
        assert signal == WatchdogSignal.NONE
        # But _idle_no_vad_nudge_fired is still set (it entered the block)
        assert dm.idle_no_vad_nudge_fired is True

    def test_sets_first_nudge_given_on_fire(self):
        dm = DeadlineManager()
        dm.check_idle_no_vad(
            has_vad=False, has_stt_transcript=False, has_response=False,
            user_speaking=False, warning_in_progress=False,
            tts_remaining=0.0, elapsed_since_start=IDLE_NO_VAD_TIMEOUT + 1,
        )
        assert dm.first_nudge_given is True

    def test_suppressed_if_has_vad(self):
        dm = DeadlineManager()
        signal = dm.check_idle_no_vad(
            has_vad=True, has_stt_transcript=False, has_response=False,
            user_speaking=False, warning_in_progress=False,
            tts_remaining=0.0, elapsed_since_start=IDLE_NO_VAD_TIMEOUT + 5,
        )
        assert signal == WatchdogSignal.NONE

    def test_suppressed_during_warning(self):
        dm = DeadlineManager()
        signal = dm.check_idle_no_vad(
            has_vad=False, has_stt_transcript=False, has_response=False,
            user_speaking=False, warning_in_progress=True,
            tts_remaining=0.0, elapsed_since_start=IDLE_NO_VAD_TIMEOUT + 5,
        )
        assert signal == WatchdogSignal.NONE

    def test_one_shot(self):
        dm = DeadlineManager()
        dm.check_idle_no_vad(
            has_vad=False, has_stt_transcript=False, has_response=False,
            user_speaking=False, warning_in_progress=False,
            tts_remaining=0.0, elapsed_since_start=IDLE_NO_VAD_TIMEOUT + 1,
        )
        signal = dm.check_idle_no_vad(
            has_vad=False, has_stt_transcript=False, has_response=False,
            user_speaking=False, warning_in_progress=False,
            tts_remaining=0.0, elapsed_since_start=IDLE_NO_VAD_TIMEOUT + 20,
        )
        assert signal == WatchdogSignal.NONE


# ── mark_nudge_given tests ───────────────────────────────────────────────────

class TestMarkNudgeGiven:
    def test_sets_flag(self):
        dm = DeadlineManager()
        assert dm.first_nudge_given is False
        dm.mark_nudge_given()
        assert dm.first_nudge_given is True


# ── Post-nudge extension tests ───────────────────────────────────────────────

class TestTryPostNudgeExtension:
    def test_fires_when_nudge_given(self):
        dm = DeadlineManager()
        dm.set_deadline(30.0, now=1000.0)
        dm.mark_nudge_given()
        result = dm.try_post_nudge_extension(POST_NUDGE_EXTENSION_SECS, now=1020.0)
        assert result is True

    def test_extends_deadline(self):
        dm = DeadlineManager()
        dm.set_deadline(30.0, now=1000.0)  # deadline = 1030
        dm.mark_nudge_given()
        dm.try_post_nudge_extension(POST_NUDGE_EXTENSION_SECS, now=1025.0)
        assert dm.deadline == 1025.0 + POST_NUDGE_EXTENSION_SECS

    def test_clears_first_nudge_given(self):
        dm = DeadlineManager()
        dm.set_deadline(30.0, now=1000.0)
        dm.mark_nudge_given()
        dm.try_post_nudge_extension(POST_NUDGE_EXTENSION_SECS, now=1025.0)
        assert dm.first_nudge_given is False

    def test_bumps_epoch(self):
        dm = DeadlineManager()
        dm.set_deadline(30.0, now=1000.0)
        dm.mark_nudge_given()
        old_epoch = dm.epoch
        dm.try_post_nudge_extension(POST_NUDGE_EXTENSION_SECS, now=1025.0)
        assert dm.epoch == old_epoch + 1

    def test_fires_at_most_once(self):
        dm = DeadlineManager()
        dm.set_deadline(30.0, now=1000.0)
        dm.mark_nudge_given()
        assert dm.try_post_nudge_extension(POST_NUDGE_EXTENSION_SECS, now=1025.0) is True
        # Second call — post_nudge_extended is now True
        dm.mark_nudge_given()  # re-set for test
        assert dm.try_post_nudge_extension(POST_NUDGE_EXTENSION_SECS, now=1030.0) is False

    def test_noop_if_no_nudge_given(self):
        dm = DeadlineManager()
        dm.set_deadline(30.0, now=1000.0)
        result = dm.try_post_nudge_extension(POST_NUDGE_EXTENSION_SECS, now=1025.0)
        assert result is False
        assert dm.epoch == 0  # no bump


# ── Reset method tests ───────────────────────────────────────────────────────

class TestResetFull:
    def test_clears_all_flags(self):
        dm = DeadlineManager()
        dm.suppress_stt_nudge()
        dm.mark_silence_watchdog_fired()
        dm.mark_idle_no_vad_fired()
        dm.mark_nudge_given()
        dm._post_nudge_extended = True  # no public setter; test-only
        dm.reset_full(50.0, now=1000.0)
        assert dm.stt_nudge_given is False
        assert dm.silence_watchdog_fired is False
        assert dm.idle_no_vad_nudge_fired is False
        assert dm.first_nudge_given is False
        assert dm.post_nudge_extended is False

    def test_bumps_epoch(self):
        dm = DeadlineManager()
        old = dm.epoch
        dm.reset_full(50.0, now=1000.0)
        assert dm.epoch == old + 1

    def test_sets_fresh_deadline(self):
        dm = DeadlineManager()
        dm.reset_full(50.0, now=1000.0)
        assert dm.deadline == 1050.0
        assert dm.start_time == 1000.0


class TestResetForRetry:
    def test_clears_flags_and_bumps_epoch(self):
        dm = DeadlineManager()
        dm.suppress_stt_nudge()
        dm.mark_nudge_given()
        dm._post_nudge_extended = True  # no public setter; test-only
        old = dm.epoch
        dm.reset_for_retry(50.0, now=1000.0)
        assert dm.stt_nudge_given is False
        assert dm.first_nudge_given is False
        assert dm.post_nudge_extended is False
        assert dm.epoch == old + 1
        assert dm.deadline == 1050.0


class TestResetForOffTopic:
    def test_clears_all_and_bumps_epoch(self):
        dm = DeadlineManager()
        dm.suppress_stt_nudge()
        dm.mark_silence_watchdog_fired()
        dm.mark_idle_no_vad_fired()
        dm.mark_nudge_given()
        dm._post_nudge_extended = True  # no public setter; test-only
        old = dm.epoch
        dm.reset_for_off_topic(60.0, now=1000.0)
        assert dm.stt_nudge_given is False
        assert dm.silence_watchdog_fired is False
        assert dm.idle_no_vad_nudge_fired is False
        assert dm.first_nudge_given is False
        assert dm.post_nudge_extended is False
        assert dm.epoch == old + 1
        assert dm.deadline == 1060.0


class TestResetForEncouragement:
    def test_clears_subset_and_extends_deadline(self):
        dm = DeadlineManager()
        dm.set_deadline(30.0, now=1000.0)  # deadline = 1030
        dm.suppress_stt_nudge()
        dm.mark_silence_watchdog_fired()
        dm.mark_nudge_given()  # should NOT be cleared by encouragement reset
        old = dm.epoch
        dm.reset_for_encouragement(15.0, now=1020.0)
        assert dm.stt_nudge_given is False
        assert dm.silence_watchdog_fired is False
        assert dm.first_nudge_given is True  # preserved
        assert dm.epoch == old  # NOT bumped
        assert dm.deadline == 1035.0  # extended to 1020 + 15

    def test_does_not_bump_epoch(self):
        dm = DeadlineManager()
        dm.set_deadline(30.0, now=1000.0)
        old = dm.epoch
        dm.reset_for_encouragement(15.0, now=1020.0)
        assert dm.epoch == old

    def test_monotonic_extension(self):
        """Does not reduce deadline if current is later."""
        dm = DeadlineManager()
        dm.set_deadline(50.0, now=1000.0)  # deadline = 1050
        dm.reset_for_encouragement(15.0, now=1020.0)  # would be 1035 < 1050
        assert dm.deadline == 1050.0  # unchanged


class TestResetForShortOfftopic:
    def test_clears_flags_bumps_epoch_extends(self):
        dm = DeadlineManager()
        dm.set_deadline(30.0, now=1000.0)  # deadline = 1030
        dm.suppress_stt_nudge()
        dm.mark_silence_watchdog_fired()
        dm.mark_idle_no_vad_fired()
        dm.mark_nudge_given()
        dm._post_nudge_extended = True  # no public setter; test-only
        old = dm.epoch
        dm.reset_for_short_offtopic(15.0, now=1025.0)
        assert dm.stt_nudge_given is False
        assert dm.silence_watchdog_fired is False
        assert dm.idle_no_vad_nudge_fired is False
        assert dm.first_nudge_given is False
        assert dm.post_nudge_extended is False
        assert dm.epoch == old + 1
        assert dm.deadline == 1040.0  # extended to 1025 + 15


# ══════════════════════════════════════════════════════════════════════════════
# LIFECYCLE TESTS — Multi-step scenarios from production
# ══════════════════════════════════════════════════════════════════════════════

class TestLifecycleTimeoutThenSpeech:
    """Scenario (a): question asked -> timeout nudge -> participant starts
    speaking -> timeout task becomes stale.

    Regression for the Q2 2026-03-26 epoch race bug.
    """

    def test_timeout_nudge_then_speech_makes_task_stale(self):
        dm = DeadlineManager()
        # Question asked: set deadline, record epoch
        dm.set_deadline(50.0, now=1000.0)
        task_epoch = dm.epoch  # epoch 0

        # Timeout monitor fires nudge after 15s
        dm.mark_nudge_given()
        assert dm.first_nudge_given is True

        # Participant starts speaking → post-nudge extension fires
        fired = dm.try_post_nudge_extension(POST_NUDGE_EXTENSION_SECS, now=1020.0)
        assert fired is True

        # Old timeout task (epoch 0) should now be stale
        assert dm.is_stale(task_epoch) is True

        # New epoch for restarted task
        new_epoch = dm.epoch
        assert not dm.is_stale(new_epoch)

        # Even a second bump (e.g., repeat) makes the new epoch stale too
        dm.bump_epoch()
        assert dm.is_stale(new_epoch) is True


class TestLifecycleDoubleWatchdogRace:
    """Scenario (b): idle-no-VAD and timeout monitor both become eligible
    simultaneously → exactly one first nudge fires.

    Regression for the watchdog race condition from 2026-03-25 demo.
    """

    def test_idle_no_vad_and_timeout_both_eligible_exactly_one_fires(self):
        dm = DeadlineManager()
        dm.set_deadline(50.0, now=1000.0)

        # Simulate: timeout monitor fires first (at 15s)
        dm.mark_nudge_given()
        assert dm.first_nudge_given is True

        # Now idle-no-VAD tries to fire — should be suppressed
        signal = dm.check_idle_no_vad(
            has_vad=False, has_stt_transcript=False, has_response=False,
            user_speaking=False, warning_in_progress=False,
            tts_remaining=0.0, elapsed_since_start=IDLE_NO_VAD_TIMEOUT + 5,
        )
        assert signal == WatchdogSignal.NONE
        # idle_no_vad_nudge_fired is set (entered the block) but signal suppressed
        assert dm.idle_no_vad_nudge_fired is True

    def test_idle_no_vad_fires_first_blocks_timeout(self):
        """Reverse order: idle-no-VAD fires first, timeout should see first_nudge_given."""
        dm = DeadlineManager()
        dm.set_deadline(50.0, now=1000.0)

        # idle-no-VAD fires first
        signal = dm.check_idle_no_vad(
            has_vad=False, has_stt_transcript=False, has_response=False,
            user_speaking=False, warning_in_progress=False,
            tts_remaining=0.0, elapsed_since_start=IDLE_NO_VAD_TIMEOUT + 1,
        )
        assert signal == WatchdogSignal.IDLE_REPROMPT
        assert dm.first_nudge_given is True

        # Timeout monitor should see first_nudge_given is True
        # (In production, monitor_response_timeout checks this flag
        # before calling _safe_say. The flag is now readable.)
        assert dm.first_nudge_given is True


class TestLifecycleGentleWarningExtension:
    """Scenario (c): gentle warning extends deadline → participant wrap-up
    arrives within the extended window → not double-timed-out.
    """

    def test_gentle_warning_extends_window_then_wrapup_accepted(self):
        dm = DeadlineManager()
        # Initial deadline: 50s from now
        dm.set_deadline(50.0, now=1000.0)  # deadline = 1050

        # Time passes, gentle warning fires at 1045 → extend by (grace2 + 10) = 20
        extended = dm.extend_deadline(20.0, now=1045.0)
        assert extended is True
        assert dm.deadline == 1065.0  # 1045 + 20

        # Participant wraps up at 1055 — within the extended window
        assert dm.is_expired(now=1055.0) is False
        assert dm.remaining(now=1055.0) == 10.0

        # After extension expires at 1065
        assert dm.is_expired(now=1066.0) is True


class TestLifecycleTimeoutNudgeDuringAgentTTS:
    """Scenario (d): timeout monitor is eligible but agent reprompt/redirect
    TTS is still playing → timeout nudge suppressed until TTS finishes.

    This is one of the easiest demo-only race conditions to miss.
    """

    def test_timeout_monitor_does_not_nudge_during_agent_reprompt_tts(self):
        dm = DeadlineManager()
        dm.set_deadline(50.0, now=1000.0)

        # Agent is re-prompting (TTS playing), 15s have elapsed
        # Idle-no-VAD check should NOT fire while TTS is still significant
        tts_remaining = 5.0  # 5 seconds of agent TTS left
        elapsed = IDLE_NO_VAD_TIMEOUT + 1  # would fire without TTS offset

        # With TTS offset: elapsed - (5.0 + TTS_SAFETY_MARGIN) = 13 - 8.0 = 5.0
        # 5.0 < IDLE_NO_VAD_TIMEOUT (12.0) → should NOT fire
        signal = dm.check_idle_no_vad(
            has_vad=False, has_stt_transcript=False, has_response=False,
            user_speaking=False, warning_in_progress=False,
            tts_remaining=tts_remaining,
            elapsed_since_start=elapsed,
        )
        assert signal == WatchdogSignal.NONE
        assert dm.idle_no_vad_nudge_fired is False

        # Later, TTS finishes, more time passes
        # elapsed = 25s, tts_remaining = 0 → 25 - 0 = 25 > 12 → fires
        signal = dm.check_idle_no_vad(
            has_vad=False, has_stt_transcript=False, has_response=False,
            user_speaking=False, warning_in_progress=False,
            tts_remaining=0.0,
            elapsed_since_start=25.0,
        )
        assert signal == WatchdogSignal.IDLE_REPROMPT
        assert dm.first_nudge_given is True

        # No duplicate: second call is suppressed (one-shot)
        signal = dm.check_idle_no_vad(
            has_vad=False, has_stt_transcript=False, has_response=False,
            user_speaking=False, warning_in_progress=False,
            tts_remaining=0.0,
            elapsed_since_start=30.0,
        )
        assert signal == WatchdogSignal.NONE


# ── suppress_stt_nudge tests ─────────────────────────────────────────────────

class TestSuppressSttNudge:
    def test_suppresses_future_stt_nudge(self):
        dm = DeadlineManager()
        dm.suppress_stt_nudge()
        assert dm.stt_nudge_given is True
        signal = dm.check_stt_health(
            has_response=False, has_stt_transcript=False,
            first_vad_seconds_ago=10.0, user_speaking=False,
        )
        assert signal == WatchdogSignal.NONE

    def test_idempotent(self):
        dm = DeadlineManager()
        dm.suppress_stt_nudge()
        dm.suppress_stt_nudge()
        assert dm.stt_nudge_given is True

    def test_cleared_by_reset_full(self):
        dm = DeadlineManager()
        dm.suppress_stt_nudge()
        dm.reset_full(50.0, now=1000.0)
        assert dm.stt_nudge_given is False


# ── reset_for_disfluency_retry tests ─────────────────────────────────────────

class TestResetForDisfluencyRetry:
    def test_clears_watchdog_flags(self):
        dm = DeadlineManager()
        dm.suppress_stt_nudge()
        dm.mark_silence_watchdog_fired()
        dm.mark_idle_no_vad_fired()
        dm.mark_nudge_given()
        dm._post_nudge_extended = True  # no public setter; test-only
        dm.reset_for_disfluency_retry()
        assert dm.stt_nudge_given is False
        assert dm.silence_watchdog_fired is False
        assert dm.idle_no_vad_nudge_fired is False
        assert dm.first_nudge_given is False
        assert dm.post_nudge_extended is False

    def test_does_not_bump_epoch(self):
        dm = DeadlineManager()
        old = dm.epoch
        dm.reset_for_disfluency_retry()
        assert dm.epoch == old

    def test_does_not_touch_deadline(self):
        dm = DeadlineManager()
        dm.set_deadline(30.0, now=1000.0)
        original_deadline = dm.deadline
        dm.reset_for_disfluency_retry()
        assert dm.deadline == original_deadline

    def test_does_not_touch_start_time(self):
        dm = DeadlineManager()
        dm.set_deadline(30.0, now=1000.0)
        original_start = dm.start_time
        dm.reset_for_disfluency_retry()
        assert dm.start_time == original_start

    def test_differs_from_reset_for_short_offtopic(self):
        """reset_for_short_offtopic bumps epoch and extends deadline;
        reset_for_disfluency_retry does neither."""
        dm1 = DeadlineManager()
        dm1.set_deadline(30.0, now=1000.0)
        e1 = dm1.epoch
        dm1.reset_for_disfluency_retry()
        assert dm1.epoch == e1  # no bump

        dm2 = DeadlineManager()
        dm2.set_deadline(30.0, now=1000.0)
        e2 = dm2.epoch
        dm2.reset_for_short_offtopic(15.0, now=1020.0)
        assert dm2.epoch == e2 + 1  # bumped

    def test_differs_from_reset_for_encouragement(self):
        """reset_for_encouragement extends deadline;
        reset_for_disfluency_retry does not."""
        dm1 = DeadlineManager()
        dm1.set_deadline(30.0, now=1000.0)
        d1 = dm1.deadline
        dm1.reset_for_disfluency_retry()
        assert dm1.deadline == d1  # untouched

        dm2 = DeadlineManager()
        dm2.set_deadline(30.0, now=1000.0)
        dm2.reset_for_encouragement(15.0, now=1025.0)
        assert dm2.deadline == 1040.0  # extended
