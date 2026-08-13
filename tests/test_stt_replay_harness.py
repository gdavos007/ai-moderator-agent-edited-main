"""Integration replay harness for Defect F.

`tests/test_stt_reconstruction.py` proves the accumulator MODULE is correct.
This file proves the WIRING is correct: it replays the same 94 real Deepgram
events through the agent's actual ingest method and the actual persistence
method, then asserts on what lands in the real transcript/CSV export objects.

Coverage boundary — read this before trusting a green run:
  COVERED     _ingest_stt_fragment (the hot path body), _canonical_response,
              _record_response_to_exports, and the real SurveyTranscript /
              SurveyDataExport writers including the new finals_text /
              trailing_text / is_provisional fields.
  NOT COVERED the `@session.on("user_input_transcribed")` registration and the
              event-unpacking above the delegate call. That adapter is now
              ~4 lines; everything below it is exercised here.

`src.moderator_agent` imports the LiveKit plugin surface, which is not
installed locally (CLAUDE.md). We stub the imports rather than skip: skipping
would leave the integration untested, which is the exact gap this file exists
to close.
"""

import json
import pathlib
import sys
import types
from unittest.mock import MagicMock

import pytest

_HERE = pathlib.Path(__file__).parent
_ROOT = _HERE.parent
_FIXTURES = json.loads(
    (_HERE / "fixtures" / "stt_reconstruction_fixtures.json").read_text(encoding="utf-8")
)
_CASES = _FIXTURES["fixtures"]


# ── LiveKit stubs ────────────────────────────────────────────────────────────
# Only `Agent` is subclassed, so it must be a real class; everything else can be
# a MagicMock. Installed into sys.modules before importing moderator_agent.

def _install_livekit_stubs():
    if "livekit.agents" in sys.modules:
        return

    class _Agent:  # subclassed by CommunityModeratorAgent
        def __init__(self, *a, **kw):
            pass

        async def on_user_turn_completed(self, turn_ctx, new_message):
            return None

        def llm_node(self, chat_ctx, tools, model_settings):
            return None

    def _mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        m.__getattr__ = lambda _n: MagicMock()  # type: ignore[attr-defined]
        return m

    livekit = _mod("livekit")
    agents = _mod("livekit.agents", Agent=_Agent, AgentSession=MagicMock(),
                  RoomInputOptions=MagicMock(), StopResponse=type("StopResponse", (Exception,), {}),
                  LanguageCode=MagicMock(), JobContext=MagicMock(), WorkerOptions=MagicMock(),
                  cli=MagicMock(), JobExecutorType=MagicMock(), AutoSubscribe=MagicMock())
    plugins = _mod("livekit.plugins")
    sys.modules.update({
        "livekit": livekit, "livekit.agents": agents, "livekit.rtc": _mod("livekit.rtc"),
        "livekit.plugins": plugins,
        "livekit.agents.llm": _mod("livekit.agents.llm"),
        "livekit.agents.voice": _mod("livekit.agents.voice"),
        "livekit.plugins.turn_detector": _mod("livekit.plugins.turn_detector"),
        "livekit.plugins.turn_detector.multilingual": _mod(
            "livekit.plugins.turn_detector.multilingual", MultilingualModel=MagicMock()),
    })
    for name in ("openai", "google", "silero", "noise_cancellation", "deepgram",
                 "elevenlabs", "anam"):
        sys.modules[f"livekit.plugins.{name}"] = _mod(f"livekit.plugins.{name}")
        setattr(plugins, name, sys.modules[f"livekit.plugins.{name}"])


@pytest.fixture(scope="module")
def agent_cls():
    _install_livekit_stubs()
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    try:
        from src.moderator_agent import CommunityModeratorAgent
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"could not import the wired agent even with stubs: {exc!r}")
    return CommunityModeratorAgent


def _make_agent(agent_cls, participant):
    """A real agent instance with only the collaborators this path touches."""
    from src.survey_transcript import SurveyTranscript
    from src.survey_data_export import SurveyDataExport

    a = agent_cls.__new__(agent_cls)          # bypass __init__ (needs a session)
    from src.domain.stt_reconstruction import SttTurnAccumulator
    a._stt_turn = SttTurnAccumulator(participant=participant)
    a.latest_user_response = None
    a._turn_accumulated_text = ""
    a.last_stt_fragment = ""
    a._turn_epoch = 1
    a.actual_respondent = participant
    a._current_stt_participant = participant
    a.accumulated_partial_answer = ""
    a.current_question_num = 5
    a.current_question_object = None          # no response_options -> no STT correction
    a.survey_transcript = SurveyTranscript(output_dir="/tmp/harness_out")
    a.survey_data_export = SurveyDataExport(output_dir="/tmp/harness_out")
    a.stt_debug_logger = MagicMock()
    a.participant_manager = MagicMock()
    a.question_delivery_state = {}
    a.waiting_for_response = True
    a.response_timeout_task = None
    a.captured_response = None
    a._response_ready = MagicMock()
    a.agent_session = MagicMock()
    a._tts_dedupe_spoken = set()
    a._shutting_down = False
    a._estimated_remaining_tts = 0.0
    a._set_delivery_state = MagicMock()
    a._record_turn_result = MagicMock()
    a._is_delivery_confirmed = MagicMock(return_value=True)
    a._register_missing_participant_for_retry = MagicMock()
    return a


def _replay_into_agent(agent, case):
    for ev in case["stream"]:
        agent._ingest_stt_fragment(
            ev["text"], is_final=ev["is_final"], participant=case["participant"]
        )


def _ids(cases):
    return [c["name"] for c in cases]


# ── The wired path, end to end ───────────────────────────────────────────────

@pytest.mark.parametrize("case", _CASES, ids=_ids(_CASES))
def test_wired_ingest_produces_oracle_canonical(agent_cls, case):
    """Real _ingest_stt_fragment -> real _canonical_response == oracle."""
    a = _make_agent(agent_cls, case["participant"])
    _replay_into_agent(a, case)
    canon = a._canonical_response("")
    assert canon.finals == case["expected"]["finals"]
    assert canon.is_provisional == case["expected"]["is_provisional"]


@pytest.mark.parametrize("case", _CASES, ids=_ids(_CASES))
def test_wired_persistence_writes_separated_fields(agent_cls, case):
    """The Defect F fix itself: what reaches the transcript must be the same
    reconstruction the analyzer sees, with validated/unvalidated split."""
    a = _make_agent(agent_cls, case["participant"])
    _replay_into_agent(a, case)
    a._record_response_to_exports(case["participant"], "", "Q5")

    entry = a.survey_transcript.transcript["conversation"][-1]
    exp = case["expected"]
    assert entry["finals_text"] == exp["finals"]
    assert entry["is_provisional"] == exp["is_provisional"]
    if exp["trailing"]:
        assert entry["trailing_text"] in exp["trailing"] or \
               exp["trailing"] in entry["trailing_text"]
    else:
        assert entry["trailing_text"] == ""


@pytest.mark.parametrize("case", _CASES, ids=_ids(_CASES))
def test_wired_persistence_is_not_lossier_than_before(agent_cls, case):
    """Regression bound: the persisted text must never be shorter than what the
    buggy build persisted for the same turn."""
    a = _make_agent(agent_cls, case["participant"])
    _replay_into_agent(a, case)
    a._record_response_to_exports(case["participant"], "", "Q5")
    entry = a.survey_transcript.transcript["conversation"][-1]
    assert len(entry["text"]) >= case["current_buggy"]["persisted_len"]


@pytest.mark.parametrize("case", _CASES, ids=_ids(_CASES))
def test_wired_csv_row_carries_the_new_columns(agent_cls, case):
    a = _make_agent(agent_cls, case["participant"])
    _replay_into_agent(a, case)
    a._record_response_to_exports(case["participant"], "", "Q5")
    row = a.survey_data_export.responses_df.iloc[-1]
    for col in ("finals_text", "trailing_text", "is_provisional"):
        assert col in a.survey_data_export.responses_df.columns, f"missing column {col}"
    assert row["finals_text"] == case["expected"]["finals"]
    assert bool(row["is_provisional"]) == case["expected"]["is_provisional"]


def test_wired_analyzed_and_persisted_are_the_same_string(agent_cls):
    """Defect F in one assertion, on the turn that exposed it (s1 T23).

    Before the fix the analyzer saw 111 chars while 58 were persisted, and the
    111 was missing 'experience as being the future' entirely.
    """
    case = next(c for c in _CASES if c["turn_epoch"] == 23 and c["session"].endswith("Aq5EeHDjAozN"))
    a = _make_agent(agent_cls, case["participant"])
    _replay_into_agent(a, case)

    analyzed = a._canonical_response("").text          # what the analyzer receives
    a._record_response_to_exports(case["participant"], "", "Q5")
    persisted = a.survey_transcript.transcript["conversation"][-1]["text"]

    assert analyzed == persisted
    assert "experience as being the future" in persisted, "the content Defect F dropped"
    assert "it probably needs some tuning" in persisted, "the trailing content"


def test_wired_cross_speaker_fragment_never_reaches_the_transcript(agent_cls):
    """Spillover protection at the wired layer, not just in the module."""
    a = _make_agent(agent_cls, "gary_")
    a._ingest_stt_fragment("legitimate answer", is_final=True, participant="gary_")
    assert a._ingest_stt_fragment("spillover", is_final=True, participant="christopher") is False
    a._record_response_to_exports("gary_", "", "Q5")
    assert "spillover" not in a.survey_transcript.transcript["conversation"][-1]["text"]
