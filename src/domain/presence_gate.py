"""Wait for a human participant before the moderator speaks.

Defect A. The welcome ran on a timer, not on presence. Session
RM_Aq5EeHDjAozN spoke a 59.3s welcome starting at t=30.8 into an empty room;
the first human joined at t=89.0 and 0.6s of it was audible
(agent_speaking 89.3 -> 89.9). The matched control RM_Txc3zKUpnAWe, which had
a human in the room at t=13.2, shows a single 58.9s agent_speaking span
covering the same welcome end to end.

The cause was one timer doing two jobs. The 30s wait in agent.py existed for
avatar lip-sync, but because the avatar is lazy-started by the first human it
also behaved like a presence gate -- and on expiry it failed OPEN into the
welcome. This module is the presence half, with its own bound and its own
reason; the avatar wait keeps its own.

No LiveKit dependency: the caller supplies the predicate.
"""

import asyncio
from typing import Callable


async def wait_for_presence(
    is_present: Callable[[], bool],
    *,
    timeout: float,
    poll_interval: float = 0.5,
) -> bool:
    """Block until ``is_present()`` returns True.

    Returns True as soon as the predicate is satisfied, False if ``timeout``
    seconds elapse first. The predicate is evaluated BEFORE the first sleep,
    so an already-present participant costs nothing.

    The caller decides what False means. For the welcome that is "stay silent
    and end the job" -- never "speak anyway", which is the defect.

    Exceptions from ``is_present`` propagate. A broken predicate must not read
    as "nobody here": that would trade a visible crash for an invisible wait.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    while True:
        if is_present():
            return True
        remaining = deadline - loop.time()
        if remaining <= 0:
            return False
        # Clamp so a long poll interval cannot overshoot the bound.
        await asyncio.sleep(min(poll_interval, remaining))
