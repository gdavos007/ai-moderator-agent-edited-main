"""
Survey Data Export - CSV/DataFrame Output for Analysis
Maintains DataFrame during session and exports to CSV at end
"""
import pandas as pd
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger(__name__)


class SurveyDataExport:
    """Tracks survey responses in DataFrame and exports to CSV for analysis"""

    def __init__(self, output_dir: str = "output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)

        self.session_start = datetime.now()
        self.session_id = self.session_start.strftime("%Y%m%d_%H%M%S")

        # Session metadata
        self.greeting_text = ""
        self.closing_text = ""
        self.participants = set()

        # DataFrame for responses
        self.responses_df = pd.DataFrame(columns=[
            'session_id',
            'participant',
            'question_number',
            'question_id',
            'question_text',
            'response_options',
            'response_text',
            'finals_text',      # Defect F: validated by Deepgram
            'trailing_text',    # Defect F: UNVALIDATED interim, may be retracted
            'is_provisional',   # Defect F: True when response_text includes trailing
            'timestamp'
        ])

        logger.info(f"📊 Survey data export initialized: {self.session_id}")

    def set_greeting(self, greeting_text: str):
        """Store the greeting text"""
        self.greeting_text = greeting_text
        logger.debug("Greeting text stored")

    def set_closing(self, closing_text: str):
        """Store the closing text"""
        self.closing_text = closing_text
        logger.debug("Closing text stored")

    def add_response(self, participant: str, question_number: int, question_id: str,
                     question_text: str, response_options: List[str],
                     response_text: str, finals_text: str = "",
                     trailing_text: str = "", is_provisional: bool = False):
        """Add a participant response to the DataFrame.

        Defect F: `finals_text` (validated by Deepgram) and `trailing_text`
        (unvalidated interim, captured when the turn was cut short) are separate
        columns. `is_provisional` flags rows whose combined text contains
        unvalidated speech, so a client-facing export can filter or annotate
        them without string-parsing an inline marker.
        """

        # Track unique participants
        self.participants.add(participant)

        # Join response options with semicolon
        options_str = ";".join(response_options) if response_options else ""

        # Format response_text: If multiple answers detected, format as numbered list
        formatted_response = self._format_multi_answer(response_text)

        # Create new row
        new_row = pd.DataFrame([{
            'session_id': self.session_id,
            'participant': participant,
            'question_number': question_number,
            'question_id': question_id,
            'question_text': question_text,
            'response_options': options_str,
            'response_text': formatted_response,
            'finals_text': finals_text,
            'trailing_text': trailing_text,
            'is_provisional': is_provisional,
            'timestamp': datetime.now().isoformat()
        }])

        # Append to DataFrame
        self.responses_df = pd.concat([self.responses_df, new_row], ignore_index=True)

        logger.info(f"📊 Added response to DataFrame: Q{question_number} from {participant}")

    def _format_multi_answer(self, response_text: str) -> str:
        """
        Format multi-answer responses as numbered list.

        Detects multiple answers separated by common delimiters and formats as:
        1. First answer
        2. Second answer
        3. Third answer
        """
        if not response_text:
            return response_text or ""

        # Common separators for multiple answers
        separators = [';', ',', ' and ', '&', '\n']

        # Try to split by common separators
        parts = [response_text]  # Start with original
        for sep in separators:
            if sep in response_text:
                parts = [p.strip() for p in response_text.split(sep) if p.strip()]
                break

        # If we found multiple parts, format as numbered list
        if len(parts) > 1:
            return '\n'.join([f"{i+1}. {part}" for i, part in enumerate(parts)])

        # Single answer - return as-is
        return response_text

    def export_to_csv(self):
        """Export DataFrame and metadata to CSV files"""

        # Export responses
        responses_file = self.output_dir / f"AI_Survey_Agent_Output_Responses_{self.session_id}.csv"
        self.responses_df.to_csv(responses_file, index=False, encoding='utf-8')
        logger.info(f"✅ Exported responses to: {responses_file}")

        # Create metadata DataFrame
        session_end = datetime.now()
        metadata_df = pd.DataFrame([{
            'session_id': self.session_id,
            'session_start': self.session_start.isoformat(),
            'session_end': session_end.isoformat(),
            'greeting_text': self.greeting_text,
            'closing_text': self.closing_text,
            'total_participants': len(self.participants),
            'total_questions': self.responses_df['question_number'].nunique() if not self.responses_df.empty else 0,
            'total_responses': len(self.responses_df),
            'participants': ";".join(sorted(self.participants))
        }])

        # Export metadata
        metadata_file = self.output_dir / f"AI_Survey_Agent_Output_Metadata_{self.session_id}.csv"
        metadata_df.to_csv(metadata_file, index=False, encoding='utf-8')
        logger.info(f"✅ Exported metadata to: {metadata_file}")

        return responses_file, metadata_file

    def get_summary(self):
        """Get a summary of collected data"""
        return {
            'session_id': self.session_id,
            'total_participants': len(self.participants),
            'total_responses': len(self.responses_df),
            'unique_questions': self.responses_df['question_number'].nunique() if not self.responses_df.empty else 0
        }
