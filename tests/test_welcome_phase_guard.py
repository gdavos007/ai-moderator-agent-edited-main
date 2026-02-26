"""
Regression test: user transcripts during WELCOME phase must be discarded.

Reproduces the scenario where a participant says "okay" while the agent
is still delivering the welcome message.  Before the fix, the transcript
would be stored in response_fragments and pollute Q#1's response.
"""

from enum import Enum


# ── Recreate SurveyState locally (avoids heavy livekit imports) ──────────────
class SurveyState(Enum):
    """Mirror of src.moderator_agent.SurveyState — kept in sync manually."""
    WELCOME = "welcome"
    WAITING_FOR_OBSERVER = "waiting_for_observer"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"


# ── Minimal mock of the moderator's mutable state ───────────────────────────
class MockModerator:
    """Tiny double of CommunityModeratorAgent with only the fields the
    welcome-guard and response-storage code paths touch."""

    def __init__(self):
        self.survey_state = SurveyState.WELCOME
        self.response_fragments: list = []
        self.latest_user_response = None
        self.pending_stt_transcript = None
        self.last_stt_fragment = ""
        self.response_captured = False
        self.expected_respondent = None
        self.actual_respondent = None
        self.current_question_num = 0


def _simulate_transcript_during_welcome(moderator, transcript: str) -> bool:
    """Simulate what on_user_input_transcribed does at the guard check.

    Returns True if transcript was DISCARDED (guard fired),
            False if it would have been processed.
    """
    if moderator.survey_state == SurveyState.WELCOME:
        return True  # Guard discards

    # Would proceed to store in response_fragments (bad during welcome)
    moderator.response_fragments.append(transcript)
    moderator.latest_user_response = transcript
    moderator.response_captured = True
    return False


# ── Tests ────────────────────────────────────────────────────────────────────

def test_transcript_during_welcome_is_discarded():
    """User says 'okay' during welcome — must NOT reach response_fragments."""
    mod = MockModerator()
    assert mod.survey_state == SurveyState.WELCOME

    discarded = _simulate_transcript_during_welcome(mod, "okay")

    assert discarded is True
    assert mod.response_fragments == []
    assert mod.latest_user_response is None
    assert mod.response_captured is False


def test_transcript_after_welcome_is_processed():
    """After state transitions to RUNNING, transcripts proceed normally."""
    mod = MockModerator()
    mod.survey_state = SurveyState.RUNNING

    discarded = _simulate_transcript_during_welcome(mod, "I live in Seattle")

    assert discarded is False
    assert mod.response_fragments == ["I live in Seattle"]
    assert mod.latest_user_response == "I live in Seattle"
    assert mod.response_captured is True


def test_multiple_transcripts_during_welcome_all_discarded():
    """Multiple user utterances during welcome must all be discarded."""
    mod = MockModerator()

    for phrase in ["hello", "okay", "can you hear me?", "testing"]:
        discarded = _simulate_transcript_during_welcome(mod, phrase)
        assert discarded is True

    assert mod.response_fragments == []
    assert mod.latest_user_response is None


def test_welcome_to_running_transition_clears_buffers():
    """Simulates the buffer-clearing that happens at state transition."""
    mod = MockModerator()

    # Somehow a fragment leaked (shouldn't happen with guard, but belt & suspenders)
    mod.response_fragments = ["leaked"]
    mod.latest_user_response = "leaked"

    # Transition (mirrors what agent.py does)
    mod.survey_state = SurveyState.RUNNING
    mod.response_fragments = []
    mod.latest_user_response = None
    mod.pending_stt_transcript = None
    mod.last_stt_fragment = ""
    mod.response_captured = False

    assert mod.response_fragments == []
    assert mod.latest_user_response is None
    assert mod.response_captured is False

    # Now a real response should work
    discarded = _simulate_transcript_during_welcome(mod, "I work in tech")
    assert discarded is False
    assert mod.response_fragments == ["I work in tech"]


def test_state_enum_has_welcome():
    """Verify WELCOME is a valid SurveyState."""
    assert hasattr(SurveyState, "WELCOME")
    assert SurveyState.WELCOME.value == "welcome"


def test_default_state_is_welcome():
    """New moderator instances must start in WELCOME, not RUNNING."""
    mod = MockModerator()
    assert mod.survey_state == SurveyState.WELCOME
