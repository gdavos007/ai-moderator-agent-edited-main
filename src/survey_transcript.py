"""
Survey Transcript Logger
Captures the complete survey conversation: greetings, questions, responses, closing
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class SurveyTranscript:
    """Records the complete survey conversation as it happens"""

    def __init__(self, output_dir: str = "output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)

        self.session_start = datetime.now()
        self.session_id = self.session_start.strftime("%Y%m%d_%H%M%S")

        self.transcript = {
            "session_id": self.session_id,
            "session_start": self.session_start.isoformat(),
            "session_end": None,
            "participants": [],
            "conversation": []
        }

        logger.info(f"📝 Survey transcript started: {self.session_id}")

    def add_greeting(self, greeting_text: str):
        """Record the initial agent greeting"""
        self.transcript["conversation"].append({
            "timestamp": datetime.now().isoformat(),
            "type": "greeting",
            "speaker": "agent",
            "text": greeting_text
        })
        logger.info(f"📝 Recorded greeting")
        self._save()

    def add_question(self, question_number: int, question_id: str,
                    participant: str, question_text: str,
                    response_options: List[str] = None):
        """Record a question asked by the agent"""
        self.transcript["conversation"].append({
            "timestamp": datetime.now().isoformat(),
            "type": "question",
            "question_number": question_number,
            "question_id": question_id,
            "speaker": "agent",
            "directed_to": participant,
            "text": question_text,
            "response_options": response_options or []
        })
        logger.info(f"📝 Recorded question #{question_number} to {participant}")
        self._save()

    def add_response(self, question_number: int, participant: str, response_text: str,
                     finals_text: str = "", trailing_text: str = "",
                     is_provisional: bool = False):
        """Record a participant's response (from STT).

        Defect F: validated and unvalidated speech are recorded as SEPARATE
        fields, never merged inline. `finals_text` was finalised by Deepgram;
        `trailing_text` is an interim captured when the turn was cut short and
        may be retracted. An inline marker would pollute both the classifier
        input and any quote pulled for a client report, so consumers choose:
        render `finals_text` alone for a conservative transcript, or
        `finals_text + trailing_text` when completeness matters more.

        `response_text` remains the combined text for existing consumers.
        """
        self.transcript["conversation"].append({
            "timestamp": datetime.now().isoformat(),
            "type": "response",
            "question_number": question_number,
            "speaker": participant,
            "text": response_text,
            "finals_text": finals_text,
            "trailing_text": trailing_text,
            "is_provisional": is_provisional,
        })

        # Track participants
        if participant not in self.transcript["participants"]:
            self.transcript["participants"].append(participant)

        logger.info(f"📝 Recorded response from {participant} for Q#{question_number}")
        self._save()

    def add_acknowledgment(self, ack_text: str = "Thank you."):
        """Record agent acknowledgment after response"""
        self.transcript["conversation"].append({
            "timestamp": datetime.now().isoformat(),
            "type": "acknowledgment",
            "speaker": "agent",
            "text": ack_text
        })
        logger.debug(f"📝 Recorded acknowledgment")
        self._save()

    def add_category_announcement(self, category: str, announcement_text: str):
        """Record category transition announcement"""
        self.transcript["conversation"].append({
            "timestamp": datetime.now().isoformat(),
            "type": "category_announcement",
            "speaker": "agent",
            "category": category,
            "text": announcement_text
        })
        logger.info(f"📝 Recorded category announcement: {category}")
        self._save()

    def add_closing(self, closing_text: str):
        """Record the final closing statement"""
        self.transcript["conversation"].append({
            "timestamp": datetime.now().isoformat(),
            "type": "closing",
            "speaker": "agent",
            "text": closing_text
        })
        logger.info(f"📝 Recorded closing")
        self._save()

    def end_session(self):
        """Mark the session as ended and save final transcript"""
        self.transcript["session_end"] = datetime.now().isoformat()
        self._save()
        logger.info(f"📝 Survey transcript completed: {self.session_id}")

    def _save(self):
        """Save transcript to JSON file"""
        output_file = self.output_dir / f"survey_transcript_{self.session_id}.json"

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(self.transcript, f, indent=2, ensure_ascii=False)

        logger.debug(f"💾 Transcript saved: {output_file}")

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of the survey"""
        questions_asked = [c for c in self.transcript["conversation"] if c["type"] == "question"]
        responses_received = [c for c in self.transcript["conversation"] if c["type"] == "response"]

        return {
            "session_id": self.session_id,
            "total_participants": len(self.transcript["participants"]),
            "questions_asked": len(questions_asked),
            "responses_received": len(responses_received),
            "conversation_entries": len(self.transcript["conversation"]),
            "participants": self.transcript["participants"]
        }
