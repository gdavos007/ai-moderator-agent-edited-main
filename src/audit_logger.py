"""
Audit Logger for AI Moderator Agent
Tracks the complete flow: JSON -> LLM -> TTS -> STT
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class AuditLogger:
    """Logs the complete question flow for debugging"""

    def __init__(self, output_dir: str = "logs"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.session_start = datetime.now()
        self.session_id = self.session_start.strftime("%Y%m%d_%H%M%S")
        self.audit_data = []
        self.current_question_audit = None

    def start_question(self, question_num: int, question_id: str, json_question: Dict[str, Any]):
        """Start tracking a new question"""
        self.current_question_audit = {
            "question_number": question_num,
            "question_id": question_id,
            "timestamp": datetime.now().isoformat(),
            "json_question": json_question,
            "system_prompt": None,
            "user_prompt": None,
            "conversation_history": None,  # Full context LLM sees
            "llm_full_context": None,  # Combined: system + history + user prompt
            "llm_response": None,
            "tts_text": None,
            "participant_stt_response": None,  # Participant's answer (STT captured)
            "participant": None
        }
        logger.info(f"📋 Started audit for question #{question_num} ({question_id})")

    def log_system_prompt(self, prompt: str):
        """Log the system prompt sent to LLM"""
        if self.current_question_audit:
            self.current_question_audit["system_prompt"] = prompt
            logger.critical(f"📤 SYSTEM PROMPT LOGGED ({len(prompt)} chars)")

    def log_user_prompt(self, prompt: str, conversation_history: list = None):
        """Log the user prompt (instructions) sent to LLM and full conversation context"""
        if self.current_question_audit:
            self.current_question_audit["user_prompt"] = prompt

            if conversation_history:
                # Serialize conversation history
                history_serialized = []
                for item in conversation_history:
                    if hasattr(item, '__dict__'):
                        history_serialized.append({
                            'role': getattr(item, 'role', 'unknown'),
                            'content': str(getattr(item, 'content', ''))
                        })
                    else:
                        history_serialized.append(str(item))

                self.current_question_audit["conversation_history"] = history_serialized

                # Create full context view
                system = self.current_question_audit.get("system_prompt", "")
                full_context = f"SYSTEM PROMPT:\n{system}\n\n"
                full_context += "CONVERSATION HISTORY:\n"
                for msg in history_serialized:
                    if isinstance(msg, dict):
                        full_context += f"[{msg['role']}]: {msg['content']}\n"
                    else:
                        full_context += f"{msg}\n"
                full_context += f"\nCURRENT USER PROMPT:\n{prompt}"

                self.current_question_audit["llm_full_context"] = full_context

            logger.critical(f"📤 USER PROMPT LOGGED ({len(prompt)} chars)")
            logger.critical("=" * 80)
            logger.critical("📤 FULL USER PROMPT SENT TO LLM:")
            logger.critical(prompt)
            if conversation_history:
                logger.critical(f"📜 CONVERSATION HISTORY: {len(conversation_history)} messages")
            logger.critical("=" * 80)

    def log_llm_response(self, response: str):
        """Log what the LLM returned"""
        if self.current_question_audit:
            self.current_question_audit["llm_response"] = response
            logger.critical(f"📥 LLM RESPONSE LOGGED ({len(response)} chars)")
            logger.critical("=" * 80)
            logger.critical("📥 LLM RESPONSE RECEIVED:")
            logger.critical(response)
            logger.critical("=" * 80)

    def log_tts_text(self, text: str):
        """Log the text that went to TTS (text-to-speech)"""
        if self.current_question_audit:
            self.current_question_audit["tts_text"] = text
            logger.critical(f"🔊 TTS TEXT LOGGED ({len(text)} chars)")
            logger.critical("=" * 80)
            logger.critical("🔊 TEXT SENT TO TTS:")
            logger.critical(text)
            logger.critical("=" * 80)

    def log_stt_text(self, text: str):
        """Log what STT (speech-to-text) captured from participant's answer"""
        if self.current_question_audit:
            self.current_question_audit["participant_stt_response"] = text
            logger.critical(f"🎤 PARTICIPANT STT RESPONSE LOGGED ({len(text)} chars)")
            logger.critical(f"🎤 Participant said: {text}")

    def log_participant(self, participant: str):
        """Log the participant who was asked"""
        if self.current_question_audit:
            self.current_question_audit["participant"] = participant

    def end_question(self):
        """Finish tracking current question and save to audit log"""
        if self.current_question_audit:
            self.audit_data.append(self.current_question_audit)

            # Save to file after each question
            self._save_audit_file()

            logger.info(f"✅ Completed audit for question #{self.current_question_audit['question_number']}")
            self.current_question_audit = None

    def _save_audit_file(self):
        """Save audit data to JSON file"""
        output_file = self.output_dir / f"audit_{self.session_id}.json"

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump({
                "session_id": self.session_id,
                "session_start": self.session_start.isoformat(),
                "total_questions": len(self.audit_data),
                "questions": self.audit_data
            }, f, indent=2, ensure_ascii=False)

        logger.info(f"💾 Audit file saved: {output_file}")

    def get_comparison_report(self):
        """Generate a comparison report showing where paraphrasing occurred"""
        report = {
            "summary": {
                "total_questions": len(self.audit_data),
                "questions_with_discrepancies": 0
            },
            "discrepancies": []
        }

        for q in self.audit_data:
            json_q_text = q['json_question'].get('question', '')
            user_prompt = q.get('user_prompt', '')
            llm_resp = q.get('llm_response', '')
            tts_text = q.get('tts_text', '')

            issues = []

            # Check if LLM changed the question text
            if llm_resp and json_q_text and json_q_text not in llm_resp:
                issues.append({
                    "issue": "LLM paraphrased or changed the question",
                    "expected": json_q_text,
                    "got": llm_resp
                })

            # Check if TTS is different from LLM (shouldn't happen)
            if llm_resp and tts_text and llm_resp != tts_text:
                issues.append({
                    "issue": "TTS text differs from LLM response",
                    "llm_said": llm_resp,
                    "tts_got": tts_text
                })

            if issues:
                report["summary"]["questions_with_discrepancies"] += 1
                report["discrepancies"].append({
                    "question_number": q['question_number'],
                    "question_id": q['question_id'],
                    "participant": q.get('participant', ''),
                    "issues": issues,
                    "json_question_full": json_q_text,
                    "user_prompt_sent": user_prompt,
                    "llm_response_received": llm_resp,
                    "tts_text": tts_text
                })

        return report
