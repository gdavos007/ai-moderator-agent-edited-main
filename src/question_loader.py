"""
Question Loader Utility
Loads survey questions from Word documents or JSON files
"""
import logging
import os
import random
import json
from pathlib import Path
from typing import List, Dict, Optional
from docx import Document
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SurveyMeta:
    """Survey metadata"""
    survey_id: str = ""
    title: str = ""
    description: str = ""
    duration_minutes: int = 30
    version: str = "1.0"
    created_date: str = ""
    participant_profile: str = ""
    agent_name: str = ""  # Name of the AI moderator for this survey


@dataclass
class AudioCheck:
    """Audio check configuration"""
    enabled: bool = True
    message: str = ""
    wait_seconds: int = 15


@dataclass
class WelcomeSection:
    """Welcome section configuration"""
    enabled: bool = False
    greeting: str = ""
    instructions: str = ""
    audio_check: Optional[AudioCheck] = None

    def __post_init__(self):
        if self.audio_check is None:
            self.audio_check = AudioCheck()


@dataclass
class ClosingSection:
    """Closing section configuration"""
    enabled: bool = False
    message: str = ""


@dataclass
class SurveySection:
    """Represents a section containing multiple questions"""
    id: str
    title: str
    description: str = ""
    introduction: Optional[str] = None
    required: bool = True
    skip_allowed: bool = False
    questions: List['Question'] = None

    def __post_init__(self):
        if self.questions is None:
            self.questions = []


@dataclass
class Question:
    """Represents a single survey question with all metadata"""
    id: str
    question: str
    question_type: str = "quantitative"  # "quantitative", "qualitative", "info", "rollcall"
    response_options: Optional[List[str]] = None
    max_selections: Optional[int] = None
    category: str = ""
    category_comments: str = ""
    required: bool = True
    expected_response_type: str = "text"  # "text", "numeric", etc.
    time_allocation_seconds: Optional[int] = None
    # Legacy field for backward compatibility
    text: Optional[str] = None  # Used for "info" type questions

    def __post_init__(self):
        """Post-initialization validation and defaults"""
        # If response_options is None, initialize to empty list
        if self.response_options is None:
            self.response_options = []

        # For info questions, use 'text' field if 'question' is empty
        if self.question_type == "info" and self.text and not self.question:
            self.question = self.text

    def has_options(self) -> bool:
        """Check if question has response options"""
        return bool(self.response_options)

    def is_quantitative(self) -> bool:
        """Check if this is a quantitative question (with predefined options)"""
        # Quantitative if explicitly marked, OR has response options
        return self.question_type == "quantitative" and self.has_options()

    def is_qualitative(self) -> bool:
        """Check if this is a qualitative question (open-ended)"""
        # Qualitative if explicitly marked as qualitative,
        # OR if expected_response_type is "text" and no response options (free-form)
        if self.question_type == "qualitative":
            return True
        # Infer qualitative: text response with no predefined options
        return self.expected_response_type == "text" and not self.has_options()

    def is_info(self) -> bool:
        """Check if this is an informational statement (not a question)"""
        return self.question_type == "info"

    def is_rollcall(self) -> bool:
        """Check if this requires rollcall-style individual responses"""
        return self.question_type == "rollcall"

    def get_recommended_vad_silence(self) -> float:
        """
        Get recommended VAD min_silence_duration based on question type.

        Returns:
            float: Recommended silence duration in seconds
                - 0.8s for qualitative (open-ended, expect continuous speech)
                - 1.2-1.5s for quantitative (multi-option, pauses between options)
                - 1.0s for other types
        """
        if self.is_qualitative():
            # Qualitative question - expect longer, continuous responses
            # Use custom time allocation if specified
            if self.time_allocation_seconds and self.time_allocation_seconds > 60:
                return 1.2  # Longer responses need more pause tolerance
            return 0.8  # Shorter qualitative responses

        if self.is_quantitative() and self.has_options():
            # Multi-option question - people pause between reading options
            num_options = len(self.response_options)
            if num_options >= 4:
                # Many options - people need time to think and pause
                return 1.5
            else:
                # Few options - moderate pause time
                return 1.2

        # Default for other types
        return 1.0

    def format_for_speech(self) -> str:
        """
        Format question and options for natural speech output.
        Returns the complete text the agent should speak, including all options.
        """
        # Info type - just read the text/question
        if self.is_info():
            return self.question or self.text or ""

        # Qualitative or no options - just read the question
        if self.is_qualitative() or not self.has_options():
            return self.question

        # Quantitative with options - read question + all options
        formatted_text = self.question

        # For questions with options, naturally transition and read ALL options
        formatted_text += " The options are: "

        # Read each option clearly
        for i, option in enumerate(self.response_options):
            if i == len(self.response_options) - 1:
                # Last option
                formatted_text += f"or {option}."
            else:
                # All other options
                formatted_text += f"{option}, "

        return formatted_text


class QuestionLoader:
    """Loads and manages survey questions from Word documents or JSON files"""

    def __init__(self, questions_file: Optional[str] = None,
                 questions_dir: Optional[str] = None,
                 json_only: bool = True):
        """
        Initialize question loader.

        Args:
            questions_file: Absolute path to specific JSON file (PREFERRED - new approach)
            questions_dir: Directory for auto-detection (LEGACY - backward compatible)
            json_only: If True, only load JSON files (ignore DOCX). Default: True
        """
        if questions_file:
            # New approach: direct file path
            self.questions_file = Path(questions_file)
            self.questions_dir = self.questions_file.parent
        elif questions_dir:
            # Legacy approach: auto-detect from directory
            self.questions_dir = Path(questions_dir)
            self.questions_file = None
        else:
            # Ultimate fallback for backward compatibility
            self.questions_dir = Path("topic_questions")
            self.questions_file = None

        self.json_only = json_only  # Only load JSON files if True
        self.questions: List[str] = []  # Legacy string-based questions
        self.structured_questions: List[Question] = []  # New structured questions from JSON
        self.current_question_index = 0
        self.asked_questions: List[int] = []
        self.current_category: Optional[str] = None
        self.current_section: Optional[str] = None
        self.introduction: str = ""
        self.use_structured_format: bool = False  # Flag to determine which format to use

        # New unified survey schema fields
        self.survey_meta: Optional[SurveyMeta] = None
        self.welcome_section: Optional[WelcomeSection] = None
        self.closing_section: Optional[ClosingSection] = None
        self.sections: List[SurveySection] = []
        self.use_unified_format: bool = False  # Flag for new unified format

    def load_questions_from_docx(self, filename: str) -> List[str]:
        """
        Load questions from a Word document.
        Captures questions AND their options if they exist.

        Args:
            filename: Name of the Word file

        Returns:
            List of questions (with options appended if present)
        """
        file_path = self.questions_dir / filename

        if not file_path.exists():
            logger.error(f"Question file not found: {file_path}")
            return []

        try:
            doc = Document(file_path)
            questions = []
            current_question = None
            options = []

            collecting_options = False  # Track when we're in options mode

            for para in doc.paragraphs:
                text = para.text.strip()

                # Skip empty lines - they don't end question collection
                if not text:
                    continue

                # Identify questions (lines containing ?)
                if '?' in text:
                    # Save previous question if exists
                    if current_question:
                        if options:
                            full_question = current_question + " Options: " + ", ".join(options)
                            questions.append(full_question)
                            logger.debug(f"Saved question with {len(options)} options")
                        else:
                            questions.append(current_question)
                            logger.debug(f"Saved question without options")

                    # Start new question (skip if too short)
                    if len(text) > 20:
                        current_question = text
                        options = []
                        collecting_options = True  # Start collecting options
                    else:
                        current_question = None
                        options = []
                        collecting_options = False

                # Capture option lines (lines without ? that follow a question)
                elif collecting_options and current_question:
                    # Skip instruction markers
                    skip_patterns = ['[ROTATE]', '[READ]', 'General issues', 'Thank you for participating']

                    if not any(pattern in text for pattern in skip_patterns):
                        # Check if this looks like an option (not too long, not another question)
                        if len(text) > 3 and len(text) < 150 and '?' not in text:
                            options.append(text)
                            logger.debug(f"  Added option: {text}")
                        elif len(text) > 150:
                            # Long text probably means we're done with options
                            collecting_options = False

            # Save last question if exists
            if current_question and options:
                full_question = current_question + " " + ", ".join(options)
                questions.append(full_question)
            elif current_question:
                questions.append(current_question)

            logger.info(f"Loaded {len(questions)} questions from {filename}")
            return questions

        except Exception as e:
            logger.error(f"Error loading questions from {filename}: {e}", exc_info=True)
            return []

    def load_unified_format(self, data: Dict) -> bool:
        """
        Load questions from unified survey schema format.

        Args:
            data: Parsed JSON data in unified format

        Returns:
            True if loaded successfully
        """
        try:
            # Load metadata
            meta_data = data.get("meta", {})
            self.survey_meta = SurveyMeta(
                survey_id=meta_data.get("survey_id", ""),
                title=meta_data.get("title", ""),
                description=meta_data.get("description", ""),
                duration_minutes=meta_data.get("duration_minutes", 30),
                version=meta_data.get("version", "1.0"),
                created_date=meta_data.get("created_date", ""),
                participant_profile=meta_data.get("participant_profile", ""),
                agent_name=meta_data.get("agent_name", "")
            )

            # Load welcome section
            welcome_data = data.get("welcome", {})
            audio_check_data = welcome_data.get("audio_check", {})
            self.welcome_section = WelcomeSection(
                enabled=welcome_data.get("enabled", False),
                greeting=welcome_data.get("greeting", ""),
                instructions=welcome_data.get("instructions", ""),
                audio_check=AudioCheck(
                    enabled=audio_check_data.get("enabled", True),
                    message=audio_check_data.get("message", ""),
                    wait_seconds=audio_check_data.get("wait_seconds", 15)
                )
            )

            # Load closing section
            closing_data = data.get("closing", {})
            self.closing_section = ClosingSection(
                enabled=closing_data.get("enabled", False),
                message=closing_data.get("message", "")
            )

            # Load sections and questions
            sections_data = data.get("sections", [])
            for section_data in sections_data:
                section = SurveySection(
                    id=section_data.get("id", ""),
                    title=section_data.get("title", ""),
                    description=section_data.get("description", ""),
                    introduction=section_data.get("introduction"),
                    required=section_data.get("required", True),
                    skip_allowed=section_data.get("skip_allowed", False),
                    questions=[]
                )

                # Load questions for this section
                for q_data in section_data.get("questions", []):
                    question = Question(
                        id=q_data.get("id", ""),
                        question=q_data.get("question", q_data.get("text", "")),
                        question_type=q_data.get("type", "quantitative"),
                        response_options=q_data.get("response_options"),
                        max_selections=q_data.get("max_selections"),
                        category=section.title,  # Use section title as category
                        category_comments=section.introduction or "",
                        required=q_data.get("required", True),
                        expected_response_type=q_data.get("expected_response_type", "text"),
                        time_allocation_seconds=q_data.get("time_allocation_seconds"),
                        text=q_data.get("text")  # For info type
                    )
                    section.questions.append(question)
                    self.structured_questions.append(question)

                self.sections.append(section)

            logger.info(f"✅ Loaded unified format survey: {self.survey_meta.title}")
            logger.info(f"   Sections: {len(self.sections)}")
            logger.info(f"   Total questions: {len(self.structured_questions)}")
            logger.info(f"   Welcome enabled: {self.welcome_section.enabled}")
            logger.info(f"   Closing enabled: {self.closing_section.enabled}")

            self.use_unified_format = True
            self.use_structured_format = True
            return len(self.structured_questions) > 0

        except Exception as e:
            logger.error(f"Error loading unified format: {e}", exc_info=True)
            return False

    def load_legacy_quantitative_format(self, data: Dict) -> bool:
        """
        Load questions from legacy quantitative format (backward compatibility).

        Args:
            data: Parsed JSON data in legacy format

        Returns:
            True if loaded successfully
        """
        try:
            self.introduction = data.get("agent introduction comments", "")
            categories = data.get("question category", [])

            logger.info(f"Introduction: {self.introduction[:100] if self.introduction else 'None'}...")

            # Flatten questions from all categories into a single ordered list
            for category_obj in categories:
                category_name = category_obj.get("category", "")
                category_comments = category_obj.get("category comments", "")

                for q_data in category_obj.get("question list", []):
                    question = Question(
                        id=q_data["id"],
                        question=q_data["question"],
                        question_type="quantitative",  # Legacy format is always quantitative
                        response_options=q_data.get("response options", []),
                        max_selections=q_data.get("max_selections"),
                        category=category_name,
                        category_comments=category_comments
                    )
                    self.structured_questions.append(question)

            logger.info(f"📚 Loaded legacy quantitative format: {len(self.structured_questions)} questions from {len(categories)} categories")
            for category_obj in categories:
                q_count = len(category_obj.get("question list", []))
                logger.info(f"  - {category_obj['category']}: {q_count} questions")

            # VERIFICATION: Log the exact question order loaded
            logger.critical("=" * 80)
            logger.critical("📋 QUESTION ORDER VERIFICATION (Loaded from JSON)")
            for idx, q in enumerate(self.structured_questions, 1):
                logger.critical(f"  Position {idx}: {q.id} - {q.question[:60]}...")
            logger.critical("=" * 80)

            self.use_structured_format = True
            self.use_unified_format = False
            return len(self.structured_questions) > 0

        except Exception as e:
            logger.error(f"Error loading legacy format: {e}", exc_info=True)
            return False

    def load_questions_from_json(self, filename: str) -> bool:
        """
        Load questions from a JSON file (auto-detects format).

        Args:
            filename: Name of the JSON file

        Returns:
            True if questions loaded successfully
        """
        file_path = self.questions_dir / filename

        if not file_path.exists():
            logger.error(f"Question file not found: {file_path}")
            return False

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Detect format based on top-level keys
            if "meta" in data and "sections" in data:
                # New unified format
                logger.info(f"📊 Detected unified survey format in {filename}")
                return self.load_unified_format(data)
            elif "question category" in data:
                # Legacy quantitative format
                logger.info(f"📊 Detected legacy quantitative format in {filename}")
                return self.load_legacy_quantitative_format(data)
            else:
                logger.error(f"Unknown survey format in {filename}")
                logger.error(f"Top-level keys: {list(data.keys())}")
                return False

        except Exception as e:
            logger.error(f"Error loading questions from {filename}: {e}", exc_info=True)
            return False

    def load_questions(self, filename: Optional[str] = None) -> bool:
        """
        Load questions from file (auto-detects JSON or DOCX).

        Args:
            filename: Optional specific filename, otherwise auto-selects first JSON or .docx file

        Returns:
            True if questions loaded successfully
        """
        if filename is None:
            # Check if we have a direct file path (new approach)
            if self.questions_file and self.questions_file.exists():
                logger.info(f"Loading from configured path: {self.questions_file}")
                return self.load_questions_from_json(str(self.questions_file.name))

            # Legacy auto-detection (backward compatible)
            # Try JSON first (preferred format)
            json_files = list(self.questions_dir.glob("*.json"))
            if json_files:
                filename = json_files[0].name
                logger.info(f"Auto-selected JSON question file: {filename}")
                return self.load_questions_from_json(filename)

            # If json_only mode, don't fall back to DOCX
            if self.json_only:
                logger.error(f"No .json files found in {self.questions_dir} (json_only mode enabled)")
                return False

            # Fall back to DOCX
            docx_files = list(self.questions_dir.glob("*.docx"))
            # Filter out temporary Word files (starting with ~$)
            docx_files = [f for f in docx_files if not f.name.startswith("~$")]

            if not docx_files:
                logger.error(f"No .json or .docx files found in {self.questions_dir}")
                return False

            filename = docx_files[0].name
            logger.info(f"Auto-selected DOCX question file: {filename}")

        # Check file extension
        if filename.endswith('.json'):
            return self.load_questions_from_json(filename)
        else:
            # If json_only mode, reject DOCX files
            if self.json_only:
                logger.error(f"Cannot load DOCX file '{filename}' - json_only mode enabled")
                return False

            # Legacy DOCX loading
            self.questions = self.load_questions_from_docx(filename)
            self.current_question_index = 0
            self.asked_questions = []
            self.use_structured_format = False
            return len(self.questions) > 0

    def get_next_question(self):
        """
        Get the next question in sequence.

        Returns:
            Next question (Question object if structured, string if legacy) or None if no more questions
        """
        if self.use_structured_format:
            # New structured format - return Question object
            if not self.structured_questions:
                logger.warning("No questions loaded")
                return None

            if self.current_question_index >= len(self.structured_questions):
                logger.info("All questions have been asked")
                return None

            question = self.structured_questions[self.current_question_index]
            self.asked_questions.append(self.current_question_index)
            self.current_question_index += 1

            logger.info(f"Getting question {self.current_question_index}/{len(self.structured_questions)}: {question.id}")
            return question
        else:
            # Legacy format - return string
            if not self.questions:
                logger.warning("No questions loaded")
                return None

            if self.current_question_index >= len(self.questions):
                logger.info("All questions have been asked")
                return None

            question = self.questions[self.current_question_index]
            self.asked_questions.append(self.current_question_index)
            self.current_question_index += 1

            logger.info(f"Getting question {self.current_question_index}/{len(self.questions)}")
            return question

    def should_announce_category(self, question: Question) -> bool:
        """
        Determine if we should announce the category before this question.
        Returns True if this is the first question in a new category.

        Args:
            question: The question to check

        Returns:
            True if category should be announced
        """
        if not self.use_structured_format:
            return False

        if question.category != self.current_category:
            self.current_category = question.category
            return True
        return False

    def get_random_question(self) -> Optional[str]:
        """
        Get a random unasked question.

        Returns:
            Random question or None if all questions asked
        """
        if not self.questions:
            logger.warning("No questions loaded")
            return None

        # Get indices of unasked questions
        unasked_indices = [i for i in range(len(self.questions)) if i not in self.asked_questions]

        if not unasked_indices:
            logger.info("All questions have been asked")
            return None

        # Select random question
        question_index = random.choice(unasked_indices)
        question = self.questions[question_index]
        self.asked_questions.append(question_index)

        logger.info(f"Selected random question {question_index + 1}/{len(self.questions)}")
        return question

    def reset(self):
        """Reset the question tracker."""
        self.current_question_index = 0
        self.asked_questions = []
        self.current_category = None
        logger.info("Question tracker reset")

    def get_progress(self) -> Dict[str, int]:
        """
        Get progress statistics.

        Returns:
            Dictionary with progress info
        """
        total_q = len(self.structured_questions) if self.use_structured_format else len(self.questions)
        return {
            "total_questions": total_q,
            "asked_questions": len(self.asked_questions),
            "remaining_questions": total_q - len(self.asked_questions),
        }
