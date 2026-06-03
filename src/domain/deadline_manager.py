"""Deadline and timeout management for the AI moderator agent.

Single owner of all timeout-window state: epoch, polling deadline,
and watchdog guard flags. Pure domain logic — no asyncio, no I/O, no SDK.

The DeadlineManager EMITS decisions (via return values and signal enums).
The LiveKit-coupled moderator class ACTS on them (TTS, task creation, etc.).
"""
import logging
import time as _time_mod
from enum import Enum
from typing import Optional

from .constants import (
    SILENCE_WATCHDOG_TIMEOUT,
    IDLE_NO_VAD_TIMEOUT,
    TTS_SAFETY_MARGIN,
)

logger = logging.getLogger(__name__)


class WatchdogSignal(Enum):
    """Signal returned by watchdog check methods."""
    NONE = "none"                  # No action needed
    STT_NUDGE = "stt_nudge"       # VAD saw speech but no STT arrived
    SILENCE_PROMPT = "silence"     # No progress after encouragement
    IDLE_REPROMPT = "idle"         # No VAD/STT at all since question


class DeadlineManager:
    """Single owner of all timeout-window state.

    Fields managed (absorbed from CommunityModeratorAgent):
        epoch             — monotonic counter; stale tasks check this and exit
        deadline          — absolute time.time() deadline for polling
        start_time        — when polling started (for idle-no-VAD offset)
        stt_nudge_given   — one-shot: STT health nudge fired
        silence_watchdog_fired — one-shot: silence watchdog fired
        idle_no_vad_nudge_fired — one-shot: idle-no-VAD nudge fired
        first_nudge_given — coordinates timeout-monitor vs idle-no-VAD
        post_nudge_extended — one-shot: post-nudge extension fired

    All deadline/epoch writes MUST go through this class.
    monitor_turn_duration() and other agent methods must call these APIs,
    not mutate the fields directly.
    """

    def __init__(self) -> None:
        self._epoch: int = 0
        self._deadline: Optional[float] = None
        self._start_time: Optional[float] = None

        # Watchdog one-shot guards
        self._stt_nudge_given: bool = False
        self._silence_watchdog_fired: bool = False
        self._idle_no_vad_nudge_fired: bool = False
        self._first_nudge_given: bool = False
        self._post_nudge_extended: bool = False

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def epoch(self) -> int:
        """Current epoch value. Stale tasks compare against this."""
        return self._epoch

    @property
    def deadline(self) -> Optional[float]:
        """Absolute time.time() deadline, or None if not set."""
        return self._deadline

    @property
    def start_time(self) -> Optional[float]:
        """When polling started (time.time()), or None."""
        return self._start_time

    @property
    def stt_nudge_given(self) -> bool:
        return self._stt_nudge_given

    @property
    def silence_watchdog_fired(self) -> bool:
        return self._silence_watchdog_fired

    @property
    def idle_no_vad_nudge_fired(self) -> bool:
        return self._idle_no_vad_nudge_fired

    @property
    def first_nudge_given(self) -> bool:
        return self._first_nudge_given

    @property
    def post_nudge_extended(self) -> bool:
        return self._post_nudge_extended

    # ── Epoch management ─────────────────────────────────────────────────

    def bump_epoch(self) -> int:
        """Increment the epoch. Returns the new value.

        Callers must cancel/restart their timeout tasks after bumping.
        """
        self._epoch += 1
        return self._epoch

    def is_stale(self, captured_epoch: int) -> bool:
        """Return True if captured_epoch does not match the current epoch.

        Used by timeout monitors to detect that the turn/window changed
        since they were launched.
        """
        return captured_epoch != self._epoch

    # ── Deadline management ──────────────────────────────────────────────

    def set_deadline(self, timeout_secs: float, *, now: Optional[float] = None) -> None:
        """Set deadline to now + timeout_secs (absolute replacement)."""
        t = now if now is not None else _time_mod.time()
        self._deadline = t + timeout_secs
        self._start_time = t

    def extend_deadline(self, seconds: float, *, now: Optional[float] = None) -> bool:
        """Extend deadline by seconds, but only if the new deadline is later.

        Returns True if the deadline was actually extended (monotonic guarantee).
        """
        if self._deadline is None:
            return False
        t = now if now is not None else _time_mod.time()
        new_deadline = t + seconds
        if new_deadline > self._deadline:
            self._deadline = new_deadline
            return True
        return False

    def extend_deadline_to(self, absolute_time: float) -> bool:
        """Extend deadline to an absolute time, only if later than current.

        Returns True if deadline was actually extended.
        """
        if self._deadline is None:
            return False
        if absolute_time > self._deadline:
            self._deadline = absolute_time
            return True
        return False

    def remaining(self, *, now: Optional[float] = None) -> float:
        """Seconds until deadline. Negative if expired. 0 if no deadline set."""
        if self._deadline is None:
            return 0.0
        t = now if now is not None else _time_mod.time()
        return self._deadline - t

    def is_expired(self, *, now: Optional[float] = None) -> bool:
        """True if deadline has passed."""
        if self._deadline is None:
            return False
        t = now if now is not None else _time_mod.time()
        return t >= self._deadline

    # ── Watchdog decisions ───────────────────────────────────────────────

    def check_stt_health(
        self,
        *,
        has_response: bool,
        has_stt_transcript: bool,
        first_vad_seconds_ago: Optional[float],
        user_speaking: bool,
    ) -> WatchdogSignal:
        """Check if STT health nudge should fire.

        Fires when VAD detected speech but no STT transcripts arrived
        within 6 seconds, and user has stopped speaking.

        Args:
            has_response: True if captured_response or latest_user_response exists
            has_stt_transcript: True if any STT transcript received this turn
            first_vad_seconds_ago: Seconds since first VAD speaking event, or None
            user_speaking: True if user is currently speaking (VAD)
        """
        if self._stt_nudge_given:
            return WatchdogSignal.NONE
        if has_response:
            return WatchdogSignal.NONE
        if has_stt_transcript:
            return WatchdogSignal.NONE
        if first_vad_seconds_ago is None:
            return WatchdogSignal.NONE
        if user_speaking:
            return WatchdogSignal.NONE
        if first_vad_seconds_ago > 3.0:
            self._stt_nudge_given = True
            return WatchdogSignal.STT_NUDGE
        return WatchdogSignal.NONE

    def check_silence_watchdog(
        self,
        *,
        encouragement_given: bool,
        has_response: bool,
        user_speaking: bool,
        warning_in_progress: bool,
        last_progress_seconds_ago: Optional[float],
    ) -> WatchdogSignal:
        """Check if silence watchdog should fire.

        Fires when no transcript progress after encouragement for
        SILENCE_WATCHDOG_TIMEOUT seconds.

        Args:
            encouragement_given: True if uncertain-response encouragement was given
            has_response: True if captured_response or latest_user_response exists
            user_speaking: True if user is currently speaking
            warning_in_progress: True if gentle warning TTS is playing
            last_progress_seconds_ago: Seconds since last transcript progress, or None
        """
        if self._silence_watchdog_fired:
            return WatchdogSignal.NONE
        if user_speaking:
            return WatchdogSignal.NONE
        if warning_in_progress:
            return WatchdogSignal.NONE
        if not encouragement_given:
            return WatchdogSignal.NONE
        if has_response:
            return WatchdogSignal.NONE
        if last_progress_seconds_ago is None:
            return WatchdogSignal.NONE
        if last_progress_seconds_ago > SILENCE_WATCHDOG_TIMEOUT:
            self._silence_watchdog_fired = True
            return WatchdogSignal.SILENCE_PROMPT
        return WatchdogSignal.NONE

    def check_idle_no_vad(
        self,
        *,
        has_vad: bool,
        has_stt_transcript: bool,
        has_response: bool,
        user_speaking: bool,
        warning_in_progress: bool,
        tts_remaining: float,
        elapsed_since_start: Optional[float],
    ) -> WatchdogSignal:
        """Check if idle-no-VAD watchdog should fire.

        Fires when no VAD/STT activity at all since polling started,
        accounting for TTS still playing.

        Args:
            has_vad: True if any VAD speaking event detected this turn
            has_stt_transcript: True if any STT transcript received this turn
            has_response: True if captured_response or latest_user_response exists
            user_speaking: True if user is currently speaking
            warning_in_progress: True if gentle warning TTS is playing
            tts_remaining: Estimated seconds of agent TTS still playing
            elapsed_since_start: Seconds since polling started, or None
        """
        if self._idle_no_vad_nudge_fired:
            return WatchdogSignal.NONE
        if user_speaking:
            return WatchdogSignal.NONE
        if warning_in_progress:
            return WatchdogSignal.NONE
        if has_vad:
            return WatchdogSignal.NONE
        if has_stt_transcript:
            return WatchdogSignal.NONE
        if has_response:
            return WatchdogSignal.NONE
        if elapsed_since_start is None:
            return WatchdogSignal.NONE

        tts_offset = (tts_remaining + TTS_SAFETY_MARGIN) if tts_remaining > 0 else 0.0
        if elapsed_since_start - tts_offset > IDLE_NO_VAD_TIMEOUT:
            self._idle_no_vad_nudge_fired = True
            # Single-owner guard: only one first nudge per turn
            if self._first_nudge_given:
                logger.info(
                    "idle-no-VAD: suppressing nudge — "
                    "first_nudge_given already set by timeout monitor"
                )
                return WatchdogSignal.NONE
            self._first_nudge_given = True
            return WatchdogSignal.IDLE_REPROMPT
        return WatchdogSignal.NONE

    def mark_nudge_given(self) -> None:
        """Mark that the timeout monitor delivered a nudge.

        Sets first_nudge_given = True so that idle-no-VAD will not
        fire a duplicate nudge.
        """
        self._first_nudge_given = True

    def suppress_stt_nudge(self) -> None:
        """Suppress future STT health nudges for the remainder of this turn.

        Called by monitor_turn_duration during gentle warning to prevent
        echo-triggered "I didn't hear you" nudges after the warning TTS
        bleeds into the mic.
        """
        self._stt_nudge_given = True

    def mark_silence_watchdog_fired(self) -> None:
        """Mark silence watchdog as fired (one-shot)."""
        self._silence_watchdog_fired = True

    def mark_idle_no_vad_fired(self) -> None:
        """Mark idle-no-VAD watchdog as fired (one-shot)."""
        self._idle_no_vad_nudge_fired = True

    # ── Post-nudge extension ─────────────────────────────────────────────

    def try_post_nudge_extension(
        self,
        extension_secs: float,
        *,
        now: Optional[float] = None,
    ) -> bool:
        """Attempt to extend the response window after a timeout nudge.

        Guards: only fires if first_nudge_given=True AND post_nudge_extended=False.
        On success: extends deadline, clears first_nudge_given (so restarted
        timeout task runs its full cycle), bumps epoch.

        Returns True if extension fired (caller must cancel/restart timeout task).
        Returns False if guards prevent firing (caller does nothing).
        """
        if not self._first_nudge_given or self._post_nudge_extended:
            return False

        self._post_nudge_extended = True

        # Extend deadline
        t = now if now is not None else _time_mod.time()
        if self._deadline is not None:
            new_deadline = t + extension_secs
            if new_deadline > self._deadline:
                self._deadline = new_deadline

        # Clear first_nudge_given so restarted timeout task runs full cycle
        self._first_nudge_given = False

        # Bump epoch to invalidate stale timeout task
        self._epoch += 1

        return True

    # ── Reset methods (5 variants for 5 entrypoints) ─────────────────────

    def reset_full(self, timeout_secs: float, *, now: Optional[float] = None) -> None:
        """Full reset for _reset_response_flags().

        Clears ALL 8 watchdog flags, bumps epoch, sets fresh deadline.
        """
        t = now if now is not None else _time_mod.time()
        self._stt_nudge_given = False
        self._silence_watchdog_fired = False
        self._idle_no_vad_nudge_fired = False
        self._first_nudge_given = False
        self._post_nudge_extended = False
        self._epoch += 1
        self._deadline = t + timeout_secs
        self._start_time = t

    def reset_for_retry(self, timeout_secs: float, *, now: Optional[float] = None) -> None:
        """Reset for _reset_for_repeat().

        Clears 6 flags (preserves silence_watchdog_fired, idle_no_vad_nudge_fired
        are cleared; stt_nudge_given cleared), bumps epoch, sets full deadline.
        """
        t = now if now is not None else _time_mod.time()
        self._stt_nudge_given = False
        self._silence_watchdog_fired = False
        self._idle_no_vad_nudge_fired = False
        self._first_nudge_given = False
        self._post_nudge_extended = False
        self._epoch += 1
        self._deadline = t + timeout_secs
        self._start_time = t

    def reset_for_off_topic(self, timeout_secs: float, *, now: Optional[float] = None) -> None:
        """Reset for _reset_for_off_topic().

        Clears 7 flags (all except: none — clears all watchdog flags),
        bumps epoch, sets full deadline.
        """
        t = now if now is not None else _time_mod.time()
        self._stt_nudge_given = False
        self._silence_watchdog_fired = False
        self._idle_no_vad_nudge_fired = False
        self._first_nudge_given = False
        self._post_nudge_extended = False
        self._epoch += 1
        self._deadline = t + timeout_secs
        self._start_time = t

    def reset_for_encouragement(
        self,
        extension_secs: float = 15.0,
        *,
        now: Optional[float] = None,
    ) -> None:
        """Reset for _handle_uncertain_response() encouragement path.

        Clears 3 flags (stt_nudge, silence_watchdog, first_vad-related).
        Extends deadline (does NOT set fresh). Does NOT bump epoch.
        """
        t = now if now is not None else _time_mod.time()
        self._stt_nudge_given = False
        self._silence_watchdog_fired = False
        # Note: _first_vad_speaking_time is managed by the agent (VAD state),
        # not by DeadlineManager. The agent clears it separately.
        # Extend deadline (monotonic)
        if self._deadline is not None:
            new_deadline = t + extension_secs
            if new_deadline > self._deadline:
                self._deadline = new_deadline

    def reset_for_short_offtopic(
        self,
        extension_secs: float = 15.0,
        *,
        now: Optional[float] = None,
    ) -> None:
        """Reset for _nudge_for_short_offtopic_retry().

        Clears 6 flags (stt_nudge, silence_watchdog, idle_no_vad, first_nudge,
        post_nudge_extended, and has_stt resets). Bumps epoch.
        Extends deadline (does NOT set fresh).
        """
        t = now if now is not None else _time_mod.time()
        self._stt_nudge_given = False
        self._silence_watchdog_fired = False
        self._idle_no_vad_nudge_fired = False
        self._first_nudge_given = False
        self._post_nudge_extended = False
        self._epoch += 1
        # Extend deadline (monotonic)
        if self._deadline is not None:
            new_deadline = t + extension_secs
            if new_deadline > self._deadline:
                self._deadline = new_deadline
        self._start_time = t

    def reset_for_disfluency_retry(self) -> None:
        """Reset watchdog flags for the disfluency/greeting retry path in
        _process_captured_response().

        Clears 6 watchdog flags (stt_nudge, silence_watchdog, idle_no_vad,
        first_nudge, post_nudge_extended) WITHOUT bumping epoch and WITHOUT
        touching deadlines.

        This is a distinct 6th reset variant. Unlike the other resets:
        - Does NOT bump epoch (no task invalidation — polling loop continues)
        - Does NOT touch deadline (caller manages disfluency budget separately)
        - Only clears watchdog guards so they can re-fire if needed
        """
        self._stt_nudge_given = False
        self._silence_watchdog_fired = False
        self._idle_no_vad_nudge_fired = False
        self._first_nudge_given = False
        self._post_nudge_extended = False
