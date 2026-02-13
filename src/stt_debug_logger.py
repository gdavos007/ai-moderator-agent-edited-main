"""
STT Debug Logger - Captures RAW STT transcripts vs corrected responses
Helps debug issues with speech-to-text accuracy and response capture
"""
import logging
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


class STTDebugLogger:
    """
    Logs raw STT transcripts and corrected responses for debugging.
    Creates a detailed comparison report to help identify STT issues.
    """

    def __init__(self, output_dir: str = "output"):
        """
        Initialize STT debug logger.

        Args:
            output_dir: Directory to save debug logs
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Store all STT events
        self.stt_events: List[Dict] = []
        self.question_responses: Dict[int, Dict] = {}

        # Create session-specific log file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.stt_log_file = self.output_dir / f"stt_debug_{timestamp}.jsonl"
        self.comparison_file = self.output_dir / f"stt_comparison_{timestamp}.txt"

        logger.info(f"STT Debug Logger initialized:")
        logger.info(f"  - JSONL log: {self.stt_log_file}")
        logger.info(f"  - Comparison report: {self.comparison_file}")

    def log_stt_event(
        self,
        event_type: str,
        raw_transcript: str,
        participant: str,
        question_num: Optional[int] = None,
        question_id: Optional[str] = None,
        corrected_response: Optional[str] = None,
        response_options: Optional[List[str]] = None,
        metadata: Optional[Dict] = None
    ):
        """
        Log a single STT event with all context.

        Args:
            event_type: Type of event (e.g., "user_speech", "confirmation")
            raw_transcript: RAW STT output (what the STT heard)
            participant: Participant identity
            question_num: Question number (if applicable)
            question_id: Question ID (if applicable)
            corrected_response: Corrected/interpreted response (after matching to options)
            response_options: Available response options (if applicable)
            metadata: Additional metadata
        """
        timestamp = datetime.now().isoformat()

        event = {
            "timestamp": timestamp,
            "event_type": event_type,
            "participant": participant,
            "question_num": question_num,
            "question_id": question_id,
            "raw_stt_transcript": raw_transcript,
            "corrected_response": corrected_response,
            "response_options": response_options,
            "stt_was_corrected": raw_transcript != corrected_response if corrected_response else False,
            "metadata": metadata or {}
        }

        self.stt_events.append(event)

        # Write to JSONL file immediately (for real-time debugging)
        with open(self.stt_log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')

        # Log to console for immediate visibility
        if corrected_response and raw_transcript != corrected_response:
            logger.warning(f"⚠️  STT CORRECTION APPLIED:")
            logger.warning(f"   Question: Q{question_num} ({question_id})")
            logger.warning(f"   Participant: {participant}")
            logger.warning(f"   RAW STT: '{raw_transcript}'")
            logger.warning(f"   Corrected: '{corrected_response}'")
        else:
            logger.info(f"✅ STT MATCHED:")
            logger.info(f"   Question: Q{question_num} ({question_id})")
            logger.info(f"   Participant: {participant}")
            logger.info(f"   Transcript: '{raw_transcript}'")

    def log_question_response(
        self,
        question_num: int,
        question_id: str,
        question_text: str,
        participant: str,
        raw_transcript: str,
        corrected_response: str,
        response_options: Optional[List[str]] = None,
        expected_respondent: Optional[str] = None
    ):
        """
        Log a complete question-response pair with all context.

        Args:
            question_num: Question number
            question_id: Question ID
            question_text: Full question text
            participant: Actual participant who responded
            raw_transcript: RAW STT output
            corrected_response: Corrected/matched response
            response_options: Available options (if applicable)
            expected_respondent: Who was expected to respond
        """
        if question_num not in self.question_responses:
            self.question_responses[question_num] = {
                "question_id": question_id,
                "question_text": question_text,
                "response_options": response_options,
                "responses": []
            }

        response_data = {
            "participant": participant,
            "expected_respondent": expected_respondent,
            "respondent_mismatch": participant != expected_respondent if expected_respondent else False,
            "raw_stt_transcript": raw_transcript,
            "corrected_response": corrected_response,
            "stt_was_corrected": raw_transcript != corrected_response,
            "timestamp": datetime.now().isoformat()
        }

        self.question_responses[question_num]["responses"].append(response_data)

        # Log the event
        self.log_stt_event(
            event_type="question_response",
            raw_transcript=raw_transcript,
            participant=participant,
            question_num=question_num,
            question_id=question_id,
            corrected_response=corrected_response,
            response_options=response_options,
            metadata={
                "expected_respondent": expected_respondent,
                "respondent_mismatch": response_data["respondent_mismatch"]
            }
        )

    def generate_comparison_report(self):
        """
        Generate a human-readable comparison report showing:
        - Questions asked
        - RAW STT transcripts
        - Corrected responses
        - Differences highlighted
        """
        try:
            with open(self.comparison_file, 'w', encoding='utf-8') as f:
                f.write("=" * 100 + "\n")
                f.write("STT TRANSCRIPT vs CORRECTED RESPONSE COMPARISON REPORT\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 100 + "\n\n")

                # Summary statistics
                total_events = len(self.stt_events)
                corrected_count = sum(1 for e in self.stt_events if e.get("stt_was_corrected"))
                f.write(f"SUMMARY:\n")
                f.write(f"  Total STT Events: {total_events}\n")
                f.write(f"  Corrections Applied: {corrected_count} ({corrected_count/total_events*100:.1f}%)\n")
                f.write(f"  Exact Matches: {total_events - corrected_count}\n\n")

                # Question-by-question breakdown
                for question_num in sorted(self.question_responses.keys()):
                    q_data = self.question_responses[question_num]

                    f.write("=" * 100 + "\n")
                    f.write(f"QUESTION #{question_num}: {q_data['question_id']}\n")
                    f.write("-" * 100 + "\n")
                    f.write(f"Question Text: {q_data['question_text']}\n")

                    if q_data['response_options']:
                        f.write(f"\nResponse Options:\n")
                        for i, option in enumerate(q_data['response_options'], 1):
                            f.write(f"  {i}. {option}\n")

                    f.write("\nRESPONSES:\n")
                    f.write("-" * 100 + "\n")

                    for resp in q_data['responses']:
                        f.write(f"\nParticipant: {resp['participant']}")
                        if resp['respondent_mismatch']:
                            f.write(f" ⚠️  (Expected: {resp['expected_respondent']})")
                        f.write(f"\nTimestamp: {resp['timestamp']}\n")

                        f.write(f"\n  RAW STT TRANSCRIPT:\n")
                        f.write(f"    \"{resp['raw_stt_transcript']}\"\n")

                        if resp['stt_was_corrected']:
                            f.write(f"\n  ⚠️  CORRECTED TO:\n")
                            f.write(f"    \"{resp['corrected_response']}\"\n")
                            f.write(f"\n  REASON: STT transcript did not exactly match expected options\n")
                        else:
                            f.write(f"\n  ✅ EXACT MATCH (No correction needed)\n")
                            f.write(f"    \"{resp['corrected_response']}\"\n")

                        f.write("\n" + "-" * 100 + "\n")

                    f.write("\n")

                # All STT corrections summary
                corrections = [e for e in self.stt_events if e.get('stt_was_corrected')]
                if corrections:
                    f.write("=" * 100 + "\n")
                    f.write("ALL STT CORRECTIONS (RAW → CORRECTED)\n")
                    f.write("=" * 100 + "\n\n")

                    for event in corrections:
                        q_info = f"Q{event['question_num']} ({event['question_id']})" if event['question_num'] else "N/A"
                        f.write(f"{q_info} - {event['participant']}:\n")
                        f.write(f"  \"{event['raw_stt_transcript']}\" → \"{event['corrected_response']}\"\n\n")

            logger.info(f"✅ Comparison report generated: {self.comparison_file}")
            return str(self.comparison_file)

        except Exception as e:
            logger.error(f"Failed to generate comparison report: {e}", exc_info=True)
            return None

    def get_summary(self) -> Dict:
        """
        Get summary statistics.

        Returns:
            Dictionary with summary stats
        """
        total_events = len(self.stt_events)
        corrected_count = sum(1 for e in self.stt_events if e.get("stt_was_corrected"))

        return {
            "total_stt_events": total_events,
            "corrections_applied": corrected_count,
            "exact_matches": total_events - corrected_count,
            "correction_rate": f"{corrected_count/total_events*100:.1f}%" if total_events > 0 else "0%",
            "questions_logged": len(self.question_responses),
            "stt_log_file": str(self.stt_log_file),
            "comparison_report": str(self.comparison_file)
        }
