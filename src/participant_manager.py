"""
Participant Manager
Manages participant selection for round-robin questioning
"""
import logging
import json
import random
from typing import List, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# #region agent log
_DEBUG_LOG_PATH = "/Users/ganeshkrishnan/Documents/Lever_AI_FINAL/ai-moderator-agent-edited-main/.cursor/debug.log"
def _dlog(location, message, data=None, hypothesis_id=""):
    try:
        entry = {"timestamp": int(datetime.now().timestamp() * 1000), "location": location, "message": message, "data": data or {}, "hypothesisId": hypothesis_id}
        with open(_DEBUG_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass
# #endregion


class ParticipantManager:
    """Manages participant selection and questioning order"""

    def __init__(self, disconnect_grace_period: float = 10.0):
        """Initialize participant manager.

        Args:
            disconnect_grace_period: Seconds to wait before removing disconnected participant (default: 10s)
        """
        self.participants: List[str] = []  # List of participant identities (enrollment roster — never shrinks)
        self.unavailable_participants: set = set()  # Participants who disconnected past grace period (soft removal)
        self.participant_display_names: Dict[str, str] = {}  # identity -> display name from LiveKit
        self.asked_participants: Dict[int, List[str]] = {}  # question_num -> [participants]
        self.current_question_num = 0
        self.participant_stats: Dict[str, Dict] = {}
        self.disconnect_grace_period = disconnect_grace_period
        self.pending_disconnects: Dict[str, datetime] = {}  # participant -> disconnect_time

        # Observer tracking
        self.observer_identity: Optional[str] = None  # Observer's identity (if present)

    def is_observer(self, participant_identity: str) -> bool:
        """
        Check if a participant identity is an observer.

        Args:
            participant_identity: Participant's identity

        Returns:
            True if the identity starts with 'observer_'
        """
        return participant_identity.startswith("observer_")

    def has_observer(self) -> bool:
        """Check if an observer is connected."""
        return self.observer_identity is not None

    def get_observer_identity(self) -> Optional[str]:
        """Get the observer's identity."""
        return self.observer_identity

    def add_participant(self, participant_identity: str, display_name: Optional[str] = None):
        """
        Add a participant to the pool.

        Args:
            participant_identity: Participant's identity (unique ID)
            display_name: Participant's display name from LiveKit token
        """
        # Check if this is an observer
        if self.is_observer(participant_identity):
            if self.observer_identity is not None:
                logger.warning(f"⚠️  Second observer attempted to join: {participant_identity}. Only one observer allowed.")
                return
            self.observer_identity = participant_identity
            logger.info(f"👁️ Observer joined: {participant_identity} (display: {display_name})")
            logger.info(f"   Observer will NOT be asked questions - controls survey via voice commands")
            return  # Don't add to regular participant list

        # If they were pending disconnect, cancel it (reconnected!)
        if participant_identity in self.pending_disconnects:
            elapsed = (datetime.now() - self.pending_disconnects[participant_identity]).total_seconds()
            logger.info(f"✅ {participant_identity} RECONNECTED after {elapsed:.1f}s (within grace period)")
            del self.pending_disconnects[participant_identity]
            return  # Don't re-add, they're still in the list

        # If they were marked unavailable (disconnected past grace), restore them
        if participant_identity in self.unavailable_participants:
            self.unavailable_participants.discard(participant_identity)
            logger.info(f"✅ {participant_identity} RECONNECTED (was unavailable) — restored to active pool")
            # #region agent log
            _dlog("participant_manager.py:add_participant:restored", "Participant restored from unavailable", {"identity": participant_identity, "unavailable_remaining": list(self.unavailable_participants)}, "FIX")
            # #endregion
            return  # Already in participants list, just needed to clear unavailable flag

        if participant_identity not in self.participants:
            self.participants.append(participant_identity)
            # Store display name from LiveKit token (defaults to identity if not provided)
            self.participant_display_names[participant_identity] = display_name or participant_identity
            self.participant_stats[participant_identity] = {
                "first_seen": datetime.now().isoformat(),
                "questions_answered": 0,
                "total_speaking_time": 0.0,
            }
            display_info = f" ('{display_name}')" if display_name and display_name != participant_identity else ""
            logger.info(f"✅ Added participant: {participant_identity}{display_info} (total: {len(self.participants)})")

    def mark_participant_disconnected(self, participant_identity: str):
        """
        Mark a participant as disconnected (with grace period before removal).

        Args:
            participant_identity: Participant's identity
        """
        if participant_identity in self.participants:
            self.pending_disconnects[participant_identity] = datetime.now()
            logger.warning(f"⚠️  Participant {participant_identity} disconnected - grace period of {self.disconnect_grace_period}s before removal")
            logger.warning(f"   If they don't reconnect, they will be removed from the survey")

    def remove_participant(self, participant_identity: str, immediate: bool = False):
        """
        Soft-remove a participant: mark them as unavailable rather than deleting
        from the enrollment roster. This prevents denominator shrinkage in
        all_participants_answered() which was causing other participants to be skipped.

        If they reconnect later, add_participant() will restore them automatically.

        Args:
            participant_identity: Participant's identity
            immediate: If True, skip grace period check. If False, check grace period.
        """
        if participant_identity not in self.participants:
            return  # Not enrolled, nothing to do

        if participant_identity in self.unavailable_participants:
            return  # Already marked unavailable, idempotent

        # Check if they're still in grace period
        if not immediate and participant_identity in self.pending_disconnects:
            disconnect_time = self.pending_disconnects[participant_identity]
            elapsed = (datetime.now() - disconnect_time).total_seconds()

            if elapsed < self.disconnect_grace_period:
                logger.info(f"Participant {participant_identity} still in grace period ({elapsed:.1f}s/{self.disconnect_grace_period}s)")
                return

        # #region agent log
        _dlog("participant_manager.py:remove_participant", "SOFT-REMOVING: marking unavailable (NOT deleting from roster)", {"identity": participant_identity, "immediate": immediate, "participants": list(self.participants), "asked_participants": {str(k): v for k, v in self.asked_participants.items()}}, "FIX")
        # #endregion

        # Soft removal: mark as unavailable instead of deleting from roster
        self.unavailable_participants.add(participant_identity)
        if participant_identity in self.pending_disconnects:
            del self.pending_disconnects[participant_identity]

        available_count = len(self.participants) - len(self.unavailable_participants)
        logger.warning(f"⚠️  PARTICIPANT UNAVAILABLE: {participant_identity} (available: {available_count}/{len(self.participants)})")
        logger.warning(f"   They will be skipped for current question but roster stays intact")
        logger.warning(f"   Unavailable: {list(self.unavailable_participants)}")

    def select_next_participant(self, question_num: int) -> Optional[str]:
        """
        Select the next participant in random order.
        Ensures participants are asked in different random orders for each question.

        NOTE: This ONLY selects - it does NOT mark as answered.
        Use mark_participant_answered() after they actually respond.

        Args:
            question_num: Current question number

        Returns:
            Selected participant identity or None if no participants available
        """
        if not self.participants:
            logger.warning("No participants available")
            return None

        # Initialize tracking for this question if needed
        if question_num not in self.asked_participants:
            self.asked_participants[question_num] = []

        # Get participants who haven't answered this question yet AND are currently available
        answered_this_question = self.asked_participants[question_num]
        available = [
            p for p in self.participants
            if p not in answered_this_question and p not in self.unavailable_participants
        ]

        if not available:
            logger.info(f"All participants have answered question {question_num}")
            return None

        # Select random participant from available (do NOT add to asked list yet)
        selected = random.choice(available)

        # #region agent log
        _dlog("participant_manager.py:select_next_participant", "Selected participant", {"selected": selected, "question_num": question_num, "available": available, "answered": answered_this_question, "all_participants": list(self.participants), "pending_disconnects": list(self.pending_disconnects.keys())}, "H3")
        # #endregion

        logger.info(
            f"Selected participant: {selected} for question {question_num} "
            f"({len(answered_this_question)}/{len(self.participants)} have answered so far)"
        )

        return selected

    def get_unanswered_participants(self, question_num: int) -> List[str]:
        """
        Get list of available participants who haven't answered a specific question.
        Excludes unavailable (disconnected) participants.

        Args:
            question_num: Question number

        Returns:
            List of participant identities
        """
        if question_num not in self.asked_participants:
            return [p for p in self.participants if p not in self.unavailable_participants]

        asked_this_question = self.asked_participants[question_num]
        return [
            p for p in self.participants
            if p not in asked_this_question and p not in self.unavailable_participants
        ]

    def mark_participant_answered(self, participant_identity: str, question_num: int):
        """
        Mark that a participant has answered a question.
        This should be called AFTER they actually provide a response.

        Args:
            participant_identity: Participant's identity
            question_num: Question number
        """
        if question_num not in self.asked_participants:
            self.asked_participants[question_num] = []

        if participant_identity not in self.asked_participants[question_num]:
            self.asked_participants[question_num].append(participant_identity)

            # Update stats
            if participant_identity in self.participant_stats:
                self.participant_stats[participant_identity]["questions_answered"] += 1

            logger.info(
                f"Marked {participant_identity} as answered for question {question_num} "
                f"({len(self.asked_participants[question_num])}/{len(self.participants)} have answered)"
            )

    def update_speaking_time(self, participant_identity: str, duration: float):
        """
        Update participant's total speaking time.

        Args:
            participant_identity: Participant's identity
            duration: Speaking duration in seconds
        """
        if participant_identity in self.participant_stats:
            self.participant_stats[participant_identity]["total_speaking_time"] += duration

    def get_participant_stats(self, participant_identity: str) -> Dict:
        """
        Get statistics for a participant.

        Args:
            participant_identity: Participant's identity

        Returns:
            Dictionary with participant stats
        """
        return self.participant_stats.get(participant_identity, {})

    def get_display_name(self, participant_identity: str) -> str:
        """
        Get participant's display name.

        Args:
            participant_identity: Participant's identity

        Returns:
            Display name from LiveKit token, or identity if not found
        """
        return self.participant_display_names.get(participant_identity, participant_identity)

    def get_all_stats(self) -> Dict:
        """
        Get statistics for all participants.

        Returns:
            Dictionary with all participant stats
        """
        return {
            "total_participants": len(self.participants),
            "active_participants": [p for p in self.participants if p not in self.unavailable_participants],
            "unavailable_participants": list(self.unavailable_participants),
            "participant_stats": self.participant_stats.copy(),
            "questions_asked": len(self.asked_participants),
        }

    def reset(self):
        """Reset all participant tracking."""
        self.asked_participants = {}
        self.current_question_num = 0
        logger.info("Participant tracking reset")

    def all_participants_answered(self, question_num: int) -> bool:
        """
        Check if all participants have answered (or are excused) for a question.

        Unavailable participants are treated as "excused" — they don't block
        the question from completing, but they also don't shrink the roster
        (preventing the denominator-shrinkage skip bug).

        Args:
            question_num: Question number

        Returns:
            True if all available participants have answered and all unavailable
            participants are accounted for
        """
        if not self.participants:
            # #region agent log
            _dlog("participant_manager.py:all_participants_answered", "No participants → True", {"question_num": question_num}, "FIX")
            # #endregion
            return True

        if question_num not in self.asked_participants:
            return False

        answered = set(self.asked_participants[question_num])
        all_enrolled = set(self.participants)
        unavailable = self.unavailable_participants

        # A participant is "accounted for" if they answered OR are unavailable
        accounted_for = answered | unavailable
        result = all_enrolled.issubset(accounted_for)

        # #region agent log
        if result:
            _dlog("participant_manager.py:all_participants_answered", "ALL ACCOUNTED FOR (answered + excused)", {"question_num": question_num, "answered": list(answered), "unavailable_excused": list(unavailable - answered), "all_enrolled": list(all_enrolled), "total": len(all_enrolled)}, "FIX")
        # #endregion

        return result
