"""STT-stream-authoritative response reconstruction (Defect F).

Deepgram marks utterance boundaries explicitly with ``is_final``. The previous
accumulator instead inferred boundaries from relative string length
(``len(new) < len(prev) * 0.6``) while ``is_final`` sat unread in the same event.

That desynchronises permanently when an interim over-runs and is then retracted.
Observed in session RM_Aq5EeHDjAozN, turn T23:

    t=481.2  is_final=False  frag(49)='experience as being the future. It probably needs'
    t=481.5  is_final=True   frag(30)='experience as being the future'      <-- retraction
    t=481.5  is_final=False  frag(22)='it probably needs some'

The 30-char final was measured against the 49-char run-ahead: ``30 < 49*0.6``
is ``30 < 29.4`` — false by 0.6 of a character. No accumulation. The next
fragment (22) was then measured against 30: ``22 < 18`` — false again. The final
was orphaned twice, the accumulator froze at 27 chars for the remainder of the
turn, and every later event rebuilt the live buffer from that frozen prefix.

This module reads the boundary marker instead of guessing it. Verified against
an independent oracle (``scripts/otlp_tools.py ... truth``) over 94 real events
spanning 7 turns in two sessions: concatenating ``is_final=True`` fragments
reproduces the oracle text exactly in 7 of 7 cases, including both turns where
every legacy derived buffer was corrupted.

Two grades of text come out of a turn, and they must stay distinguishable:

* **finals**   — validated by Deepgram. Safe to persist as the client record.
* **trailing** — an interim captured when the turn was cut before Deepgram could
  finalise it. Real speech, but unvalidated: the retraction above shows Deepgram
  revising an interim. Never silently merge it into a client deliverable.
"""
from dataclasses import dataclass
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconstructedResponse:
    """The two grades of text produced by one turn, kept separate by design."""

    finals: str    # validated by Deepgram
    trailing: str  # UNVALIDATED interim captured at turn-cut

    @property
    def text(self) -> str:
        """finals + trailing. For consumers that want maximum context and can
        tolerate unvalidated text (e.g. the relevance classifier). Client-facing
        renderers should use ``finals``/``trailing`` separately instead."""
        if self.finals and self.trailing:
            return f"{self.finals} {self.trailing}"
        return self.finals or self.trailing

    @property
    def is_provisional(self) -> bool:
        """True when ``text`` contains unvalidated trailing speech."""
        return bool(self.trailing)


class SttTurnAccumulator:
    """Accumulates one turn's STT fragments. One instance per turn epoch.

    Turn ownership is **declared, never inferred** — the owner is seeded by the
    caller from the participant the moderator called on. Inferring the owner
    from the first arriving fragment would let a spillover fragment that wins a
    race claim the turn, after which every genuine fragment is rejected as
    cross-speaker: strictly worse than the bug this fixes.

    Hot path: ``add()`` runs ~2562x per 690s session. It does one strip, one
    comparison and one list append — no string building. Joining is deferred to
    ``result()``, called once per turn.
    """

    __slots__ = ("_participant", "_finals", "_trailing", "_warned_no_owner")

    def __init__(self, participant: Optional[str] = None):
        self._participant = participant
        self._finals: List[str] = []
        self._trailing: str = ""
        self._warned_no_owner: bool = False

    def add(self, text: str, *, is_final: bool, participant: Optional[str] = None) -> bool:
        """Accumulate one STT fragment. Returns True if accepted.

        Returns False (rather than raising) for the expected-but-rare rejection
        cases, so the hot-path caller stays a straight line. A rejection is
        never silent — cross-speaker rejections log CRITICAL here.
        """
        text = (text or "").strip()
        if not text:
            return False

        if self._participant is None:
            # Owner was never seeded — a call-site bug. Accept the fragment
            # (dropping audio is worse) but say so once per turn.
            if not self._warned_no_owner:
                self._warned_no_owner = True
                logger.warning(
                    "SttTurnAccumulator has no declared owner; cross-speaker "
                    "protection is disabled for this turn. Seed it in reset()."
                )
        elif participant is not None and participant != self._participant:
            logger.critical(
                "🚨 CROSS-SPEAKER FRAGMENT: turn owned by %r but fragment tagged "
                "%r — dropping %d chars. Finals-based accumulation assumes one "
                "speaker per turn epoch.",
                self._participant, participant, len(text),
            )
            return False

        if is_final:
            self._finals.append(text)
            self._trailing = ""  # superseded by the validated final
        else:
            # Latest wins, not longest: interims retract (see module docstring),
            # and a longest-wins rule can resurrect text Deepgram withdrew.
            self._trailing = text
        return True

    def result(self) -> ReconstructedResponse:
        return ReconstructedResponse(" ".join(self._finals), self._trailing)

    def reset(self, participant: Optional[str] = None) -> None:
        """Start a new turn. Pass the participant the moderator just called on."""
        self._participant = participant
        self._finals.clear()
        self._trailing = ""
        self._warned_no_owner = False
