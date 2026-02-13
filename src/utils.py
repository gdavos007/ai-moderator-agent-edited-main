"""
Utility functions for the moderator agent
"""
import logging
from typing import Dict, List, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class ParticipantTracker:
    """Track participant activity and statistics"""

    def __init__(self):
        self.participants: Dict[str, Dict[str, Any]] = {}

    def add_participant(self, identity: str, name: str):
        """Add a new participant to tracking"""
        if identity not in self.participants:
            self.participants[identity] = {
                "identity": identity,
                "name": name,
                "joined_at": datetime.now(),
                "message_count": 0,
                "warnings": 0,
                "last_active": datetime.now(),
            }
            logger.info(f"Tracking new participant: {name} ({identity})")

    def update_activity(self, identity: str):
        """Update participant's last activity time"""
        if identity in self.participants:
            self.participants[identity]["last_active"] = datetime.now()
            self.participants[identity]["message_count"] += 1

    def add_warning(self, identity: str):
        """Add a warning to a participant"""
        if identity in self.participants:
            self.participants[identity]["warnings"] += 1
            logger.warning(
                f"Warning added to {identity}. "
                f"Total warnings: {self.participants[identity]['warnings']}"
            )

    def get_inactive_participants(self, minutes: int = 10) -> List[str]:
        """Get list of participants inactive for specified minutes"""
        now = datetime.now()
        inactive = []

        for identity, data in self.participants.items():
            inactive_duration = (now - data["last_active"]).total_seconds() / 60
            if inactive_duration >= minutes:
                inactive.append(data["name"])

        return inactive

    def get_statistics(self) -> Dict[str, Any]:
        """Get overall statistics"""
        return {
            "total_participants": len(self.participants),
            "active_participants": sum(
                1 for p in self.participants.values()
                if p["message_count"] > 0
            ),
            "total_messages": sum(
                p["message_count"] for p in self.participants.values()
            ),
            "total_warnings": sum(
                p["warnings"] for p in self.participants.values()
            ),
        }


class ModerationLogger:
    """Log moderation events for analysis"""

    def __init__(self, log_file: str = "logs/moderation.log"):
        self.log_file = log_file
        self.events: List[Dict[str, Any]] = []

        # Setup file logger
        self.file_logger = logging.getLogger("moderation")
        handler = logging.FileHandler(log_file)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(levelname)s - %(message)s"
            )
        )
        self.file_logger.addHandler(handler)
        self.file_logger.setLevel(logging.INFO)

    def log_event(
        self,
        event_type: str,
        details: str,
        severity: str = "info",
        participant: str = None,
    ):
        """Log a moderation event"""
        event = {
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            "details": details,
            "severity": severity,
            "participant": participant,
        }
        self.events.append(event)

        log_message = f"[{event_type}] {details}"
        if participant:
            log_message += f" (participant: {participant})"

        if severity == "warning":
            self.file_logger.warning(log_message)
        elif severity == "error":
            self.file_logger.error(log_message)
        else:
            self.file_logger.info(log_message)

    def get_events_by_type(self, event_type: str) -> List[Dict[str, Any]]:
        """Get all events of a specific type"""
        return [e for e in self.events if e["type"] == event_type]

    def get_events_by_participant(self, participant: str) -> List[Dict[str, Any]]:
        """Get all events for a specific participant"""
        return [e for e in self.events if e.get("participant") == participant]

    def export_report(self) -> Dict[str, Any]:
        """Export a summary report"""
        return {
            "total_events": len(self.events),
            "events_by_type": {
                event_type: len(self.get_events_by_type(event_type))
                for event_type in set(e["type"] for e in self.events)
            },
            "high_severity_events": [
                e for e in self.events
                if e.get("severity") in ["warning", "error"]
            ],
            "events": self.events,
        }


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string"""
    hours, remainder = divmod(int(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if seconds > 0 or not parts:
        parts.append(f"{seconds}s")

    return " ".join(parts)


def truncate_text(text: str, max_length: int = 100) -> str:
    """Truncate text to maximum length"""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."
