"""
Regression test for the 2026-05-12 Christopher demo failure (Failure A):
the analyzer was handed a stale early fragment ("Well, I I I think") while
richer STT fragments ("...advertising, marketing, public affairs campaign...")
had already arrived in the live buffer.

Per CLAUDE.md, tests duplicate the relevant logic from `moderator_agent.py`
rather than importing it (LiveKit deps are heavy). This file mirrors:
  - the STT accumulation behaviour in on_user_input_transcribed
    (src/moderator_agent.py:4858–4866)
  - the `_richest_response_for_analysis` helper
    (src/moderator_agent.py:~532)

If either logic drifts, update this test by hand.
"""

import pytest


# ── Duplicated buffer logic (mirrors on_user_input_transcribed) ──────────────

class FakeModerator:
    """Minimal stand-in mirroring the buffer attributes mutated by
    on_user_input_transcribed in moderator_agent.py."""

    def __init__(self):
        self.latest_user_response = None
        self.last_stt_fragment = ""
        self._turn_accumulated_text = ""
        self._turn_epoch = 0

    def on_stt_fragment(self, transcript: str) -> None:
        """Mirror of moderator_agent.py:4843-4866."""
        new_fragment = transcript.strip()
        if not new_fragment:
            return

        _prev = self.last_stt_fragment or ""
        if _prev and len(new_fragment) < len(_prev) * 0.6 and len(_prev) > 5:
            if self._turn_accumulated_text:
                self._turn_accumulated_text += " " + _prev
            else:
                self._turn_accumulated_text = _prev

        if self._turn_accumulated_text:
            self.latest_user_response = (
                self._turn_accumulated_text + " " + new_fragment
            )
        else:
            self.latest_user_response = new_fragment

        self.last_stt_fragment = new_fragment


# ── Duplicated Fix A helper (mirrors _richest_response_for_analysis) ────────

def richest_response_for_analysis(m: FakeModerator, captured_text: str) -> str:
    captured = (captured_text or "").strip()
    latest = (m.latest_user_response or "").strip()
    acc = (m._turn_accumulated_text or "").strip()
    last = (m.last_stt_fragment or "").strip()
    joined = (acc + " " + last).strip() if acc and last and last not in acc else (acc or last)

    candidates = [("captured", captured), ("latest", latest), ("joined_acc", joined)]
    non_empty = [(label, txt) for label, txt in candidates if txt]
    if not non_empty:
        return captured

    chosen_label, chosen_text = max(
        non_empty,
        key=lambda lt: (len(lt[1]), 0 if lt[0] == "captured" else -1),
    )
    return chosen_text


# ── The actual regression: replay Christopher's STT fragment sequence ───────

# Exact sequence from logs/agent.log on 2026-05-12 (Q#4, T3, 01:55:08–01:55:23).
CHRISTOPHER_FRAGMENTS = [
    "Well, I I ",
    "Well, I I I ",
    "Well, I I I think",
    # Disfluency-extension fires here in production; captured_text snapshot
    # is taken below as "Well, I I I think".
    "advertising,",
    "advertising, marketing, public affairs",
    "advertising, marketing, public affairs campaign.",
    "advertising, marketing, public affairs campaign that was out there.",
    "advertising, marketing, public affairs campaign that without them, we understand",
    "Public",
    "Affairs.",
]


class TestStaleFragmentAnalysis:
    def test_richest_picks_substantive_over_disfluent_starter(self):
        """The bug: disfluency-extension captured 'Well, I I I think';
        analyzer should receive the richer accumulated buffer instead."""
        m = FakeModerator()

        # Replay fragments up to the disfluency snapshot
        for f in CHRISTOPHER_FRAGMENTS[:3]:
            m.on_stt_fragment(f)
        # In production this is what the disfluency loop binds to:
        captured_text_at_extension = "Well, I I I think"

        # ...then richer fragments keep arriving while the polling deadline
        # extends. They land in latest_user_response / _turn_accumulated_text.
        for f in CHRISTOPHER_FRAGMENTS[3:]:
            m.on_stt_fragment(f)

        chosen = richest_response_for_analysis(m, captured_text_at_extension)

        assert chosen != "Well, I I I think", (
            "Regression: analyzer would have received the stale disfluent "
            "starter while richer text was in the live buffer."
        )
        assert "advertising" in chosen.lower(), (
            f"Expected substantive content in analyzed text; got: {chosen!r}"
        )
        assert len(chosen) > 30, (
            f"Expected length > 30 chars; got {len(chosen)} chars: {chosen!r}"
        )

    def test_returns_captured_when_buffer_is_empty(self):
        """If no live buffer exists (edge case), respect caller's value."""
        m = FakeModerator()  # buffer empty
        assert richest_response_for_analysis(m, "yes") == "yes"

    def test_returns_captured_when_it_is_already_richest(self):
        """Don't second-guess when captured_text is already the longest."""
        m = FakeModerator()
        m.on_stt_fragment("ok")
        assert richest_response_for_analysis(
            m, "this is the full final answer text"
        ) == "this is the full final answer text"

    def test_short_final_fragment_does_not_clobber_accumulation(self):
        """STT 'Public' / 'Affairs.' as a tail must not override the longer
        in-turn accumulation that preceded them."""
        m = FakeModerator()
        for f in CHRISTOPHER_FRAGMENTS:
            m.on_stt_fragment(f)
        chosen = richest_response_for_analysis(m, "Well, I I I think")
        assert "advertising" in chosen.lower()
        assert chosen != "Affairs."
        assert chosen != "Public"

    def test_epoch_starts_zero(self):
        m = FakeModerator()
        assert m._turn_epoch == 0
