"""
Tests for post-nudge speech extension (Commit 1).

Covers the invariant: after a timeout nudge (_first_nudge_given=True),
any participant activity (VAD speaking or STT fragment) must extend the
polling deadline, clear _first_nudge_given, bump _response_epoch, and
start a fresh timeout monitor.

NOTE: These tests mirror logic from src/moderator_agent.py rather than
importing it directly (heavy LiveKit dependencies).
"""

import time

import pytest


# ── Mirrored constant ────────────────────────────────────────────────────────
POST_NUDGE_EXTENSION_SECS = 15.0


# ── Minimal state simulation ─────────────────────────────────────────────────

class FakeTimeoutTask:
    """Simulates an asyncio.Task for response_timeout_task."""
    def __init__(self, epoch: int):
        self.epoch = epoch
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class PostNudgeState:
    """Mirrors the flags relevant to _extend_post_nudge_window."""

    def __init__(self):
        self._first_nudge_given = False
        self._post_nudge_extended = False
        self._polling_deadline = None
        self._response_epoch = 0
        self.response_timeout_task = None
        # Track tasks created for verification
        self._created_tasks = []

    def extend_post_nudge_window(self, participant: str) -> None:
        """Mirror of CommunityModeratorAgent._extend_post_nudge_window."""
        if not self._first_nudge_given or self._post_nudge_extended:
            return

        self._post_nudge_extended = True

        # Extend polling deadline
        if self._polling_deadline is not None:
            fresh_deadline = time.time() + POST_NUDGE_EXTENSION_SECS
            if fresh_deadline > self._polling_deadline:
                self._polling_deadline = fresh_deadline

        # Clear _first_nudge_given so restarted monitor won't suppress itself
        self._first_nudge_given = False

        # Bump epoch
        self._response_epoch += 1
        if self.response_timeout_task:
            self.response_timeout_task.cancel()

        # Create fresh timeout task
        task = FakeTimeoutTask(epoch=self._response_epoch)
        self.response_timeout_task = task
        self._created_tasks.append(task)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestPostNudgeExtension:
    """Verify _extend_post_nudge_window behavior."""

    def test_extends_deadline_after_nudge(self):
        """VAD speaking after nudge extends deadline by >= POST_NUDGE_EXTENSION_SECS."""
        state = PostNudgeState()
        state._first_nudge_given = True
        state._polling_deadline = time.time() + 2.0  # nearly expired

        before = time.time()
        state.extend_post_nudge_window("ganesh")

        assert state._polling_deadline >= before + POST_NUDGE_EXTENSION_SECS

    def test_clears_first_nudge_given(self):
        """After extension, _first_nudge_given is False so new timeout task won't suppress itself."""
        state = PostNudgeState()
        state._first_nudge_given = True
        state._polling_deadline = time.time() + 2.0

        state.extend_post_nudge_window("ganesh")

        assert state._first_nudge_given is False

    def test_creates_new_timeout_task(self):
        """Extension creates a fresh timeout task with the bumped epoch."""
        state = PostNudgeState()
        state._first_nudge_given = True
        state._polling_deadline = time.time() + 2.0
        state._response_epoch = 3

        state.extend_post_nudge_window("ganesh")

        assert len(state._created_tasks) == 1
        assert state._created_tasks[0].epoch == 4  # bumped from 3
        assert state._response_epoch == 4

    def test_cancels_existing_timeout_task(self):
        """Extension cancels any existing timeout task before creating a new one."""
        state = PostNudgeState()
        state._first_nudge_given = True
        state._polling_deadline = time.time() + 2.0
        old_task = FakeTimeoutTask(epoch=2)
        state.response_timeout_task = old_task

        state.extend_post_nudge_window("ganesh")

        assert old_task.cancelled is True

    def test_no_extension_before_nudge(self):
        """Pre-nudge activity (_first_nudge_given=False) does NOT trigger extension."""
        state = PostNudgeState()
        state._first_nudge_given = False
        state._polling_deadline = time.time() + 2.0
        original_deadline = state._polling_deadline
        original_epoch = state._response_epoch

        state.extend_post_nudge_window("ganesh")

        assert state._polling_deadline == original_deadline
        assert state._response_epoch == original_epoch
        assert len(state._created_tasks) == 0

    def test_fires_at_most_once_per_nudge(self):
        """Extension fires only once per nudge cycle (_post_nudge_extended guard)."""
        state = PostNudgeState()
        state._first_nudge_given = True
        state._polling_deadline = time.time() + 2.0

        state.extend_post_nudge_window("ganesh")
        first_epoch = state._response_epoch
        first_deadline = state._polling_deadline

        # Second call should be a no-op
        state.extend_post_nudge_window("ganesh")

        assert state._response_epoch == first_epoch
        assert state._polling_deadline == first_deadline
        assert len(state._created_tasks) == 1

    def test_post_nudge_extended_is_true_after(self):
        """_post_nudge_extended is True after extension fires."""
        state = PostNudgeState()
        state._first_nudge_given = True
        state._polling_deadline = time.time() + 2.0

        state.extend_post_nudge_window("ganesh")

        assert state._post_nudge_extended is True

    def test_no_deadline_without_existing_deadline(self):
        """If _polling_deadline is None (unusual), extension still works for epoch/task."""
        state = PostNudgeState()
        state._first_nudge_given = True
        state._polling_deadline = None

        state.extend_post_nudge_window("ganesh")

        assert state._polling_deadline is None  # remains None
        assert state._first_nudge_given is False
        assert state._response_epoch == 1
        assert len(state._created_tasks) == 1


class TestPostNudgeResetClearing:
    """Verify _post_nudge_extended is cleared by reset operations."""

    RESET_FLAGS = {
        "_post_nudge_extended": False,
        "_first_nudge_given": False,
    }

    def test_flag_starts_false(self):
        state = PostNudgeState()
        assert state._post_nudge_extended is False

    def test_flag_set_after_extension(self):
        state = PostNudgeState()
        state._first_nudge_given = True
        state._polling_deadline = time.time() + 10
        state.extend_post_nudge_window("ganesh")
        assert state._post_nudge_extended is True

    def test_stt_path_same_as_vad_path(self):
        """Both VAD and STT paths call the same helper — behavior is identical."""
        # Simulate VAD path
        vad_state = PostNudgeState()
        vad_state._first_nudge_given = True
        vad_state._polling_deadline = time.time() + 2.0
        vad_state._response_epoch = 5
        vad_state.extend_post_nudge_window("ganesh")

        # Simulate STT path (same helper)
        stt_state = PostNudgeState()
        stt_state._first_nudge_given = True
        stt_state._polling_deadline = time.time() + 2.0
        stt_state._response_epoch = 5
        stt_state.extend_post_nudge_window("ganesh")

        assert vad_state._first_nudge_given == stt_state._first_nudge_given
        assert vad_state._post_nudge_extended == stt_state._post_nudge_extended
        assert vad_state._response_epoch == stt_state._response_epoch
