"""
Red-Green target for Defect F — STT-stream-authoritative response reconstruction.

Replays REAL Deepgram fragment streams (94 events across 7 turns, extracted from
the OTLP session dumps) through the accumulator and asserts against the oracle
produced by `scripts/otlp_tools.py ... truth`.

The oracle is independent of all three legacy derived buffers
(`latest_user_response`, `_turn_accumulated_text`, `last_stt_fragment`), which is
what makes it a valid assertion target: those buffers are each corrupted in a
different way, so string-comparing them cannot settle what was actually said.

Assertion policy (per review 2026-08-13):
  * FINALS   — exact match. Deepgram has finalized these; they are validated.
  * TRAILING — containment only. These are interims captured at turn-cut and are
               NOT validated; Deepgram demonstrably retracts them (s1_t23: a
               49-char run-ahead at t=481.2 was retracted to a 30-char final at
               t=481.5). Trailing text must stay flagged provisional in the data
               model — the transcript is a client deliverable and unvalidated
               text must remain distinguishable from validated text.

Tiers are encoded in fixture names so unwinnable cases stay visible:
  finals_only          — recovered by the accumulator fix alone
  trailing_recoverable — recovered in Defect F (interim was in the buffer)
  postack_lost         — speech after the early ack produced ZERO STT events;
                         no artifact exists. NOT winnable here; needs Defect D.

Module is loaded by file path on purpose: importing `src.*` pulls in
`moderator_agent` -> `livekit.plugins.google`, which is not installed locally
(see CLAUDE.md). Loading the pure-logic module directly keeps this runnable.
"""

import importlib.util
import json
import pathlib

import pytest

_HERE = pathlib.Path(__file__).parent
_FIXTURES = json.loads(
    (_HERE / "fixtures" / "stt_reconstruction_fixtures.json").read_text(encoding="utf-8")
)
_CASES = _FIXTURES["fixtures"]
_MODULE_PATH = _HERE.parent / "src" / "domain" / "stt_reconstruction.py"


def _load_module():
    """Load src/domain/stt_reconstruction.py without importing the src package."""
    if not _MODULE_PATH.exists():
        pytest.fail(
            f"RED: {_MODULE_PATH.relative_to(_HERE.parent)} does not exist yet. "
            "This is the expected failure until the Defect F diff lands."
        )
    spec = importlib.util.spec_from_file_location("stt_reconstruction", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _replay(case):
    """Feed a fixture's real fragment stream through the accumulator."""
    mod = _load_module()
    acc = mod.SttTurnAccumulator(participant=case["participant"])
    for ev in case["stream"]:
        acc.add(ev["text"], is_final=ev["is_final"], participant=case["participant"])
    return acc.result()


def _ids(cases):
    return [c["name"] for c in cases]


# ── Fixture integrity (guards against a corrupted/mis-transcribed fixture) ────

@pytest.mark.parametrize("case", _CASES, ids=_ids(_CASES))
def test_fixture_declared_lengths_match_strings(case):
    exp = case["expected"]
    assert len(exp["finals"]) == exp["finals_len"]
    assert len(exp["trailing"]) == exp["trailing_len"]
    assert exp["is_provisional"] == bool(exp["trailing"])


@pytest.mark.parametrize("case", _CASES, ids=_ids(_CASES))
def test_fixture_stream_reproduces_oracle_finals(case):
    """Concatenating is_final=True fragments must equal the oracle finals.

    This is the core claim of the fix: the STT stream alone is sufficient.
    """
    finals = " ".join(ev["text"] for ev in case["stream"] if ev["is_final"])
    assert finals == case["expected"]["finals"]


# ── The actual Red-Green target ──────────────────────────────────────────────

@pytest.mark.parametrize("case", _CASES, ids=_ids(_CASES))
def test_finals_are_exact(case):
    """Validated text must match the oracle exactly."""
    assert _replay(case).finals == case["expected"]["finals"]


@pytest.mark.parametrize(
    "case", [c for c in _CASES if c["expected"]["trailing"]],
    ids=_ids([c for c in _CASES if c["expected"]["trailing"]]),
)
def test_trailing_is_contained_not_equal(case):
    """Trailing is an unvalidated interim — assert containment, never equality."""
    result = _replay(case)
    assert result.trailing, "expected provisional trailing text"
    assert result.trailing in case["expected"]["trailing"] or \
           case["expected"]["trailing"] in result.trailing


@pytest.mark.parametrize("case", _CASES, ids=_ids(_CASES))
def test_provisional_flag_matches_trailing_presence(case):
    """Unvalidated text must be distinguishable in the data model."""
    result = _replay(case)
    assert result.is_provisional == case["expected"]["is_provisional"]


@pytest.mark.parametrize("case", _CASES, ids=_ids(_CASES))
def test_full_text_recovers_oracle_length(case):
    """The reconstructed text must be at least as complete as today's output."""
    result = _replay(case)
    assert len(result.text) >= case["current_buggy"]["persisted_len"]


def test_t22_does_not_reintroduce_duplication():
    """Regression guard: joined_acc produced 220 chars containing
    'quantitative certainty. quantitative certainty'. The persisted 196-char
    version was correct. Finals-based reconstruction must not duplicate."""
    case = next(c for c in _CASES if "dedup_guard" in c["name"])
    text = _replay(case).text
    assert text.count("quantitative certainty") == 1, f"duplication reintroduced: {text!r}"
    assert len(text) == case["expected"]["finals_len"]


# ── Safety rails called out in review ────────────────────────────────────────

def test_cross_speaker_fragment_is_rejected_loudly():
    """Finals-based accumulation is only safe because each turn epoch is tagged
    to exactly one participant (verified across 46 epochs / 2 sessions, <=3
    participants). That sample is small, so a mismatch must fail loudly rather
    than silently contaminate a transcript."""
    mod = _load_module()
    acc = mod.SttTurnAccumulator(participant="gary_")
    acc.add("I think that I can see this", is_final=True, participant="gary_")
    with pytest.raises(mod.CrossSpeakerFragment):
        acc.add("something else entirely", is_final=True, participant="christopher")


def test_add_is_cheap_on_the_hot_path():
    """on_user_input_transcribed fires ~2562x per 690s session — the hottest
    path in the agent. add() must not rebuild the joined string per event;
    joining is deferred to result()."""
    mod = _load_module()
    acc = mod.SttTurnAccumulator(participant="p")
    for i in range(500):
        acc.add(f"fragment {i}", is_final=(i % 5 == 0), participant="p")
    assert isinstance(acc._finals, list), "finals must accumulate as a list, joined lazily"
    assert len(acc._finals) == 100
