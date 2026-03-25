"""
Tests for the per-room locking and duplicate dispatch prevention in
web/backend/server.py.

Mirrors the ensure_room_and_agent() logic rather than importing it
directly (server depends on LiveKit SDK, FastAPI, and env vars).

Covers:
  1. Two concurrent calls for the same room → exactly one dispatch
  2. Agent already present in room → zero new dispatches
  3. Lock is per-room — different rooms dispatch independently
  4. Failed dispatch allows retry (not cached as dispatched)

Source of truth: web/backend/server.py — ensure_room_and_agent(),
_room_locks, _dispatched_rooms.
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── Mirrored logic from web/backend/server.py ────────────────────────────────

_room_locks: defaultdict = defaultdict(asyncio.Lock)
_dispatched_rooms: set = set()


async def ensure_room_and_agent(
    room_name: str,
    *,
    create_room_fn: AsyncMock,
    list_participants_fn: AsyncMock,
    dispatch_fn: AsyncMock,
) -> bool:
    """Mirrored version of ensure_room_and_agent() for testing.

    Instead of calling LiveKit API directly, accepts injectable mock
    functions for room creation, participant listing, and dispatch.
    """
    lock = _room_locks[room_name]

    async with lock:
        if room_name in _dispatched_rooms:
            return True

        # Step 1: Create room (idempotent)
        try:
            await create_room_fn(room_name)
        except Exception:
            return False

        # Step 2: Check for existing agents
        try:
            participants = await list_participants_fn(room_name)
            agent_count = sum(
                1 for p in participants
                if getattr(p, "kind", None) == 4
                or getattr(p, "identity", "").startswith("agent")
            )
            if agent_count > 0:
                _dispatched_rooms.add(room_name)
                return True
        except Exception:
            pass  # Proceed to dispatch

        # Step 3: Dispatch
        try:
            await dispatch_fn(room_name)
            _dispatched_rooms.add(room_name)
            return True
        except Exception:
            return False


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_global_state():
    """Reset module-level state between tests."""
    _dispatched_rooms.clear()
    _room_locks.clear()
    yield
    _dispatched_rooms.clear()
    _room_locks.clear()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestConcurrentDispatchRace:
    """Two concurrent ensure_room_and_agent() calls for the same room
    must result in exactly one dispatch."""

    @pytest.mark.asyncio
    async def test_concurrent_same_room_dispatches_once(self):
        """Simulate two coroutines racing for the same room."""
        dispatch_count = 0

        async def mock_create_room(room_name):
            await asyncio.sleep(0.01)  # Simulate network latency

        async def mock_list_participants(room_name):
            await asyncio.sleep(0.01)
            return []  # No agents yet

        async def mock_dispatch(room_name):
            nonlocal dispatch_count
            await asyncio.sleep(0.01)
            dispatch_count += 1

        create_fn = AsyncMock(side_effect=mock_create_room)
        list_fn = AsyncMock(side_effect=mock_list_participants)
        dispatch_fn = AsyncMock(side_effect=mock_dispatch)

        # Launch two concurrent calls for the same room
        results = await asyncio.gather(
            ensure_room_and_agent(
                "room-A",
                create_room_fn=create_fn,
                list_participants_fn=list_fn,
                dispatch_fn=dispatch_fn,
            ),
            ensure_room_and_agent(
                "room-A",
                create_room_fn=create_fn,
                list_participants_fn=list_fn,
                dispatch_fn=dispatch_fn,
            ),
        )

        assert all(results), "Both calls should succeed"
        assert dispatch_count == 1, f"Expected 1 dispatch, got {dispatch_count}"
        assert "room-A" in _dispatched_rooms

    @pytest.mark.asyncio
    async def test_three_concurrent_same_room(self):
        """Even with 3 concurrent calls, dispatch happens exactly once."""
        dispatch_count = 0

        async def mock_create_room(room_name):
            await asyncio.sleep(0.01)

        async def mock_list_participants(room_name):
            await asyncio.sleep(0.01)
            return []

        async def mock_dispatch(room_name):
            nonlocal dispatch_count
            await asyncio.sleep(0.01)
            dispatch_count += 1

        results = await asyncio.gather(
            *[
                ensure_room_and_agent(
                    "room-A",
                    create_room_fn=AsyncMock(side_effect=mock_create_room),
                    list_participants_fn=AsyncMock(side_effect=mock_list_participants),
                    dispatch_fn=AsyncMock(side_effect=mock_dispatch),
                )
                for _ in range(3)
            ]
        )

        assert all(results)
        assert dispatch_count == 1


class TestAgentAlreadyPresent:
    """If an agent is already in the room, no new dispatch should happen."""

    @pytest.mark.asyncio
    async def test_agent_present_skips_dispatch(self):
        """Agent detected via kind=4 → zero dispatches."""
        agent_participant = MagicMock()
        agent_participant.kind = 4
        agent_participant.identity = "survey-moderator-abc123"

        dispatch_fn = AsyncMock()

        result = await ensure_room_and_agent(
            "room-B",
            create_room_fn=AsyncMock(),
            list_participants_fn=AsyncMock(return_value=[agent_participant]),
            dispatch_fn=dispatch_fn,
        )

        assert result is True
        dispatch_fn.assert_not_called()
        assert "room-B" in _dispatched_rooms

    @pytest.mark.asyncio
    async def test_agent_present_by_identity_prefix(self):
        """Agent detected via identity prefix → zero dispatches."""
        agent_participant = MagicMock()
        agent_participant.kind = 1  # Not AGENT kind, but identity matches
        agent_participant.identity = "agent-survey-moderator"

        dispatch_fn = AsyncMock()

        result = await ensure_room_and_agent(
            "room-C",
            create_room_fn=AsyncMock(),
            list_participants_fn=AsyncMock(return_value=[agent_participant]),
            dispatch_fn=dispatch_fn,
        )

        assert result is True
        dispatch_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_human_participants_only_triggers_dispatch(self):
        """Only human participants in room → dispatch happens."""
        human = MagicMock()
        human.kind = 1  # STANDARD participant
        human.identity = "john_doe"

        dispatch_fn = AsyncMock()

        result = await ensure_room_and_agent(
            "room-D",
            create_room_fn=AsyncMock(),
            list_participants_fn=AsyncMock(return_value=[human]),
            dispatch_fn=dispatch_fn,
        )

        assert result is True
        dispatch_fn.assert_called_once_with("room-D")


class TestDifferentRoomsIndependent:
    """Different rooms should dispatch independently (no cross-room blocking)."""

    @pytest.mark.asyncio
    async def test_different_rooms_both_dispatch(self):
        dispatch_rooms = []

        async def mock_dispatch(room_name):
            await asyncio.sleep(0.01)
            dispatch_rooms.append(room_name)

        async def mock_create(room_name):
            await asyncio.sleep(0.01)

        results = await asyncio.gather(
            ensure_room_and_agent(
                "room-X",
                create_room_fn=AsyncMock(side_effect=mock_create),
                list_participants_fn=AsyncMock(return_value=[]),
                dispatch_fn=AsyncMock(side_effect=mock_dispatch),
            ),
            ensure_room_and_agent(
                "room-Y",
                create_room_fn=AsyncMock(side_effect=mock_create),
                list_participants_fn=AsyncMock(return_value=[]),
                dispatch_fn=AsyncMock(side_effect=mock_dispatch),
            ),
        )

        assert all(results)
        assert len(dispatch_rooms) == 2
        assert set(dispatch_rooms) == {"room-X", "room-Y"}


class TestDispatchFailureRetry:
    """Failed dispatch should not be cached, allowing retry."""

    @pytest.mark.asyncio
    async def test_failed_dispatch_not_cached(self):
        dispatch_fn = AsyncMock(side_effect=Exception("network error"))

        result = await ensure_room_and_agent(
            "room-E",
            create_room_fn=AsyncMock(),
            list_participants_fn=AsyncMock(return_value=[]),
            dispatch_fn=dispatch_fn,
        )

        assert result is False
        assert "room-E" not in _dispatched_rooms

    @pytest.mark.asyncio
    async def test_retry_after_failure_succeeds(self):
        call_count = 0

        async def flaky_dispatch(room_name):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("transient error")
            # Second call succeeds

        dispatch_fn = AsyncMock(side_effect=flaky_dispatch)

        # First call fails
        r1 = await ensure_room_and_agent(
            "room-F",
            create_room_fn=AsyncMock(),
            list_participants_fn=AsyncMock(return_value=[]),
            dispatch_fn=dispatch_fn,
        )
        assert r1 is False
        assert "room-F" not in _dispatched_rooms

        # Retry succeeds
        r2 = await ensure_room_and_agent(
            "room-F",
            create_room_fn=AsyncMock(),
            list_participants_fn=AsyncMock(return_value=[]),
            dispatch_fn=dispatch_fn,
        )
        assert r2 is True
        assert "room-F" in _dispatched_rooms


class TestRoomCreationFailure:
    """Room creation failure should abort before dispatch."""

    @pytest.mark.asyncio
    async def test_room_creation_failure_aborts(self):
        dispatch_fn = AsyncMock()

        result = await ensure_room_and_agent(
            "room-G",
            create_room_fn=AsyncMock(side_effect=Exception("room create failed")),
            list_participants_fn=AsyncMock(return_value=[]),
            dispatch_fn=dispatch_fn,
        )

        assert result is False
        dispatch_fn.assert_not_called()
        assert "room-G" not in _dispatched_rooms
