"""Bound the relevance classifier so a slow call cannot hang a turn.

Session RM_Txc3zKUpnAWe, t=167.9: one classifier call took 29,515ms. Anshita
answered, heard "One moment...", answered again — and the moderator stayed
silent for ~30s before responding to her first answer. Two turns were captured
and discarded in the gap.

A wait_for WAS present, but it wrapped asyncio.shield(), which prevents
cancellation. It was a filler trigger, not a deadline. The line that actually
hung was a bare `await analysis_task` with no bound.

Two stages: stage 1 decides whether to speak a filler (shielded — must not kill
a merely-slow call); stage 2 is the real deadline and does cancel. On expiry we
fail OPEN, because the deterministic guards that matter most for UX —
is_uncertain_response and is_repeat_request — already ran before this call.
Blocking the turn would reproduce the dead air this exists to remove.
"""

import asyncio
import contextlib

from .response_analysis import ResponseAnalysis


async def analyze_with_deadline(
    analysis_coro,
    *,
    filler_threshold: float,
    hard_deadline: float,
    on_filler=None,
):
    """Run the classifier under a hard deadline. Returns (result, timed_out)."""

    task = asyncio.create_task(analysis_coro)

    # ── Stage 1: decide whether to speak a filler ──
    # TODO 1: await the shielded task with timeout=filler_threshold.
    #         On success, return (result, False).
    #         Comment WHY the shield is here.
    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=filler_threshold)
        return result, False
    except asyncio.TimeoutError:
        pass

    # TODO 2: call on_filler if it was provided.
    #         Decide: does it need awaiting?
    if on_filler is not None:
        maybe = on_filler()
        if asyncio.iscoroutine(maybe):
            await maybe

    # ── Stage 2: the real deadline ──
    remaining = max(hard_deadline - filler_threshold, 0.5)
    try:
        # TODO 3: await the task (NOT shielded) with timeout=remaining.
        #         On success, return (result, False).
        result = await asyncio.wait_for(task, timeout=remaining)
        return result, False
    except asyncio.TimeoutError:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        # TODO 4: build the fail-open ResponseAnalysis with all six fields
        #         explicit, and return it with True.
        return ResponseAnalysis(
            is_relevant=True,
            is_already_answered_claim=False,
            is_repeat_request=False,
            partial_repeat_status="NO_REPEAT",
            partial_answer="",
            unanswered_questions="",
        ), True