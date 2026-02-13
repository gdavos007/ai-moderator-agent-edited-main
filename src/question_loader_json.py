"""
JSON-based Question Loader
Loads survey questions from structured JSON files to prevent hallucination
"""
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Question:
    """Represents a single survey question with all metadata"""
    id: str
    question: str
    response_options: List[str]
    max_selections: Optional[int]
    category: str
    category_comments: str

    def has_options(self) -> bool:
        """Check if question has response options"""
        return bool(self.response_options)

    def format_for_speech(self) -> str:
        """
        Format question and options for natural speech output.
        Returns the complete text the agent should speak, including all options.
        """
        if not self.response_options:
            # No options to read - open-ended question
            return self.question

        # Start with the question text
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


class JSONQuestionLoader:
    """Loads and manages survey questions from JSON files"""

    def __init__(self, questions_file: str):
        """
        Initialize question loader with JSON file path.

        Args:
            questions_file: Path to JSON file containing questions
        """
        self.questions_file = Path(questions_file)
        self.introduction: str = ""
        self.global_category_comments: str = ""
        self.categories: List[Dict] = []
        self.all_questions: List[Question] = []
        self.current_question_index = 0
        self.asked_question_ids: List[str] = []
        self.current_category: Optional[str] = None

    def load_questions(self) -> bool:
        """
        Load questions from JSON file.

        Returns:
            True if questions loaded successfully
        """
        if not self.questions_file.exists():
            logger.error(f"Question file not found: {self.questions_file}")
            return False

        try:
            with open(self.questions_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self.introduction = data.get("agent introduction comments", "")
            self.global_category_comments = data.get("question category comments", "")
            self.categories = data.get("question category", [])

            # Flatten questions from all categories into a single ordered list
            for category_obj in self.categories:
                category_name = category_obj.get("category", "")
                category_comments = category_obj.get("category comments", "")

                for q_data in category_obj.get("question list", []):
                    question = Question(
                        id=q_data["id"],
                        question=q_data["question"],
                        response_options=q_data.get("response options", []),
                        max_selections=q_data.get("max_selections"),
                        category=category_name,
                        category_comments=category_comments
                    )
                    self.all_questions.append(question)

            logger.info(f"Loaded {len(self.all_questions)} questions from {len(self.categories)} categories")
            for category_obj in self.categories:
                q_count = len(category_obj.get("question list", []))
                logger.info(f"  - {category_obj['category']}: {q_count} questions")

            return len(self.all_questions) > 0

        except Exception as e:
            logger.error(f"Error loading questions from {self.questions_file}: {e}", exc_info=True)
            return False

    def get_next_question(self) -> Optional[Question]:
        """
        Get the next question in sequence.

        Returns:
            Next Question object or None if no more questions
        """
        if not self.all_questions:
            logger.warning("No questions loaded")
            return None

        if self.current_question_index >= len(self.all_questions):
            logger.info("All questions have been asked")
            return None

        question = self.all_questions[self.current_question_index]
        self.asked_question_ids.append(question.id)
        self.current_question_index += 1

        logger.info(f"Getting question {self.current_question_index}/{len(self.all_questions)}: {question.id}")
        logger.info(f"  Category: {question.category}")
        logger.info(f"  Question: {question.question[:80]}...")
        logger.info(f"  Options: {len(question.response_options)} options")

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
        if question.category != self.current_category:
            self.current_category = question.category
            return True
        return False

    def get_introduction(self) -> Optional[str]:
        """Get the agent introduction text"""
        return self.introduction if self.introduction else None

    def reset(self):
        """Reset the question tracker."""
        self.current_question_index = 0
        self.asked_question_ids = []
        self.current_category = None
        logger.info("Question tracker reset")

    def get_progress(self) -> Dict[str, int]:
        """
        Get progress statistics.

        Returns:
            Dictionary with progress info
        """
        return {
            "total_questions": len(self.all_questions),
            "asked_questions": len(self.asked_question_ids),
            "remaining_questions": len(self.all_questions) - len(self.asked_question_ids),
        }
