"""Deploy blocker for Defect F: provisional text must never reach the report LLM.

Direction-of-change argument (review 2026-08-13): before F, a client quote was
short but every word was confirmed. After F, a quote is longer and its tail may
be text Deepgram was about to retract — RM_Aq5EeHDjAozN t=481.2 emitted a
49-char interim that became a 30-char final, so '. It probably needs' was words
the participant had not yet said. Attributing those to a named participant in a
client deliverable is worse than the truncation F fixes.

Policy under test (interim, pending Justin/Christopher sign-off):
reports quote confirmed words only. `trailing_text` stays in the stored
transcript but is stripped from the report payload — enforced in code, not by
an instruction in report_system.md that the model may ignore.

Loaded by file path: web/backend imports FastAPI/openai which aren't needed here.
"""

import importlib.util
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).parent.parent
_MODULE_PATH = _ROOT / "web" / "backend" / "report_generator.py"


def _strip():
    spec = importlib.util.spec_from_file_location("report_generator_under_test", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"report_generator deps unavailable: {exc!r}")
    return mod.strip_provisional


# Real shape produced by survey_transcript.add_response after Defect F.
def _entry(text, finals, trailing, type_="response", speaker="gary_"):
    return {"type": type_, "speaker": speaker, "timestamp": "t", "text": text,
            "finals_text": finals, "trailing_text": trailing,
            "is_provisional": bool(trailing)}


def test_trailing_never_survives_into_the_payload():
    """The core guarantee: no unvalidated substring reaches the report LLM."""
    strip = _strip()
    convo = [_entry(
        "I think that I can see this experience as being the future "
        "it probably needs some tuning",
        "I think that I can see this experience as being the future",
        "it probably needs some tuning",
    )]
    out, trimmed, chars, dropped = strip(convo)
    assert trimmed == 1 and dropped == 0
    assert chars == len("it probably needs some tuning")
    assert out[0]["text"] == "I think that I can see this experience as being the future"
    assert "it probably needs some tuning" not in out[0]["text"]
    # The provisional fields must not leak either — the model shouldn't see them.
    for key in ("trailing_text", "finals_text", "is_provisional"):
        assert key not in out[0]


def test_entry_with_no_confirmed_words_is_dropped_not_emptied():
    """A turn cut before Deepgram confirmed anything has nothing quotable.
    Emitting an empty entry would invite the model to invent a quote."""
    strip = _strip()
    out, trimmed, chars, dropped = strip([_entry("um", "", "um")])
    assert out == [] and dropped == 1 and trimmed == 0


def test_confirmed_only_entries_pass_through_unchanged_in_text():
    strip = _strip()
    convo = [_entry("A complete confirmed answer.", "A complete confirmed answer.", "")]
    out, trimmed, chars, dropped = strip(convo)
    assert (trimmed, chars, dropped) == (0, 0, 0)
    assert out[0]["text"] == "A complete confirmed answer."


def test_pre_defect_f_transcripts_pass_through_untouched():
    """Old transcripts have no trailing_text key — must not be mangled."""
    strip = _strip()
    legacy = [{"type": "response", "speaker": "gary_", "timestamp": "t",
               "text": "legacy entry with no new fields"}]
    out, trimmed, chars, dropped = strip(legacy)
    assert out == legacy and (trimmed, chars, dropped) == (0, 0, 0)


def test_non_response_turns_are_untouched():
    strip = _strip()
    convo = [{"type": "question", "text": "What do you think?"},
             {"type": "acknowledgment", "text": "Thank you, Gary."}]
    out, *_ = strip(convo)
    assert out == convo


def test_counter_totals_across_a_mixed_session():
    """The counter is the input to the loosening decision — it must be right."""
    strip = _strip()
    convo = [
        _entry("a b", "a", "b"),                       # trimmed, 1 char
        _entry("confirmed", "confirmed", ""),          # untouched
        _entry("xy", "", "xy"),                        # dropped, 2 chars
        {"type": "question", "text": "Q?"},            # untouched
    ]
    out, trimmed, chars, dropped = strip(convo)
    assert (trimmed, dropped, chars) == (1, 1, 3)
    assert len(out) == 3  # the dropped response is gone
