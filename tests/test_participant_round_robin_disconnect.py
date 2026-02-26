import importlib.util
from pathlib import Path

_PM_PATH = Path(__file__).resolve().parent.parent / "src" / "participant_manager.py"
_PM_SPEC = importlib.util.spec_from_file_location("participant_manager_module", _PM_PATH)
_PM_MODULE = importlib.util.module_from_spec(_PM_SPEC)
assert _PM_SPEC and _PM_SPEC.loader
_PM_SPEC.loader.exec_module(_PM_MODULE)
ParticipantManager = _PM_MODULE.ParticipantManager


class DeliveryStateController:
    """Minimal model of delivery gating policy for regression testing."""

    def __init__(self, pm: ParticipantManager, question_num: int, max_retries: int = 3):
        self.pm = pm
        self.question_num = question_num
        self.max_retries = max_retries
        self.delivery_state = {}
        self.retries = {}

    def set_state(self, participant: str, state: str):
        self.delivery_state[(self.question_num, participant)] = state

    def is_delivered(self, participant: str) -> bool:
        return self.delivery_state.get((self.question_num, participant)) == "delivered"

    def mark_timeout_if_delivered(self, participant: str):
        if self.is_delivered(participant):
            self.pm.mark_participant_answered(participant, self.question_num)
            self.set_state(participant, "timeout")

    def register_missing(self, participant: str):
        key = (self.question_num, participant)
        self.retries[key] = self.retries.get(key, 0) + 1
        if self.retries[key] >= self.max_retries:
            self.pm.remove_participant(participant, immediate=True)
            self.set_state(participant, "unavailable")
        else:
            self.set_state(participant, "pending_retry")


def test_brief_disconnect_reconnect_not_removed():
    pm = ParticipantManager(disconnect_grace_period=10.0)
    pm.add_participant("alice")

    pm.mark_participant_disconnected("alice")
    # Reconnect within grace period should clear pending disconnect.
    pm.add_participant("alice")

    # Delayed remover should now be a no-op.
    pm.remove_participant("alice", immediate=False)

    assert "alice" in pm.participants
    assert "alice" not in pm.unavailable_participants
    assert "alice" not in pm.pending_disconnects


def test_missing_during_delivery_requeued_not_marked_answered():
    pm = ParticipantManager(disconnect_grace_period=10.0)
    pm.add_participant("alice")
    controller = DeliveryStateController(pm, question_num=1, max_retries=3)

    # Simulate one failed delivery attempt (missing in room).
    controller.register_missing("alice")
    controller.mark_timeout_if_delivered("alice")

    assert controller.delivery_state[(1, "alice")] == "pending_retry"
    assert controller.retries[(1, "alice")] == 1
    assert pm.asked_participants.get(1, []) == []
    assert "alice" not in pm.unavailable_participants


def test_advance_only_after_delivery_or_explicit_unavailable():
    pm = ParticipantManager(disconnect_grace_period=10.0)
    pm.add_participant("alice")
    pm.add_participant("bob")

    controller = DeliveryStateController(pm, question_num=1, max_retries=3)

    # Simulate delivered -> answered path.
    controller.set_state("alice", "delivered")
    controller.mark_timeout_if_delivered("alice")

    # Simulate repeated missing participant until explicitly unavailable.
    controller.register_missing("bob")
    controller.register_missing("bob")
    controller.register_missing("bob")

    assert "alice" in pm.asked_participants[1]
    assert "bob" not in pm.asked_participants[1]
    assert "bob" in pm.unavailable_participants
    assert pm.all_participants_answered(1) is True
