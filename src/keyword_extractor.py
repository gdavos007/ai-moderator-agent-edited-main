"""
Keyword Extractor for Deepgram STT Keyterm Prompting
Extracts domain-specific keywords from survey configuration to improve transcription accuracy
"""
import re
from typing import List, Set
from src.question_loader import QuestionLoader


class KeywordExtractor:
    """Extract keywords from survey configuration for STT keyterm prompting"""

    # NOTE: All keywords are now dynamically extracted from the survey JSON file
    # This ensures keywords are specific and relevant to each survey's domain
    # No hardcoded general keywords - everything comes from the survey content

    # Stop words to exclude (too common, no value for STT)
    STOP_WORDS = {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "as", "is", "are", "was", "were", "be",
        "have", "has", "had", "do", "does", "did", "will", "would", "should",
        "could", "may", "might", "must", "can", "this", "that", "these", "those",
        "it", "its", "you", "your", "we", "our", "they", "their", "what", "which",
        "who", "when", "where", "why", "how"
    }

    def __init__(self, question_loader: QuestionLoader):
        """
        Initialize keyword extractor with survey questions

        Args:
            question_loader: QuestionLoader instance with loaded survey
        """
        self.question_loader = question_loader
        self.keywords: Set[str] = set()

        # Determine which format to use (unified vs legacy)
        # Unified format uses structured_questions, legacy uses questions
        if question_loader.use_unified_format:
            self._questions = question_loader.structured_questions
        else:
            self._questions = question_loader.questions

    def extract_keywords(self, max_keywords: int = 100) -> List[str]:
        """
        Extract all keywords DYNAMICALLY from survey configuration.

        All keywords are extracted from the survey JSON file content:
        - Survey metadata (title, description, participant profile)
        - Section titles, descriptions, and introductions
        - Question text (domain vocabulary)
        - Response options (expected answer vocabulary)

        NO hardcoded keywords - everything is survey-specific for maximum relevance.

        Deepgram Nova-3/Flux supports keyterm prompting via URL parameters.
        We limit to 100 keywords to balance accuracy vs URL length constraints.

        Priority order:
        1. Survey title/topic (highest priority - critical domain context)
        2. Section titles and introductions
        3. Question-specific terms (domain vocabulary from questions)
        4. Response options (expected answer vocabulary)

        Args:
            max_keywords: Maximum number of keywords to return (default: 100)

        Returns:
            List of keywords for Deepgram keyterm prompting
        """
        # Track keywords by priority level
        priority_1_keywords = set()  # Survey metadata (title, topic, description)
        priority_2_keywords = set()  # Section titles and introductions
        priority_3_keywords = set()  # Questions and categories
        priority_4_keywords = set()  # Response options

        # PRIORITY 1: Survey metadata - Most important context
        self.keywords = set()
        self._extract_from_metadata()
        priority_1_keywords = self.keywords.copy()

        # PRIORITY 2: Section titles, descriptions, and introductions
        self.keywords = set()
        self._extract_from_sections()
        self._extract_from_categories()
        priority_2_keywords = self.keywords.copy()

        # PRIORITY 3: Question-specific domain terms
        self.keywords = set()
        self._extract_from_questions()
        priority_3_keywords = self.keywords.copy()

        # PRIORITY 4: Response options
        self.keywords = set()
        self._extract_from_response_options()
        priority_4_keywords = self.keywords.copy()

        # Combine in priority order, respecting max_keywords limit
        final_keywords = []

        # Add Priority 1 (always include all - survey context is critical)
        final_keywords.extend(list(priority_1_keywords))

        # Add Priority 2 (section context)
        remaining = max_keywords - len(final_keywords)
        if remaining > 0:
            final_keywords.extend(list(priority_2_keywords)[:remaining])

        # Add Priority 3 (question vocabulary)
        remaining = max_keywords - len(final_keywords)
        if remaining > 0:
            final_keywords.extend(list(priority_3_keywords)[:remaining])

        # Add Priority 4 (response options)
        remaining = max_keywords - len(final_keywords)
        if remaining > 0:
            final_keywords.extend(list(priority_4_keywords)[:remaining])

        return final_keywords[:max_keywords]

    def _extract_from_metadata(self):
        """Extract keywords from survey metadata (title, topic, description)"""
        # Extract from unified format survey_meta
        if hasattr(self.question_loader, 'survey_meta') and self.question_loader.survey_meta:
            meta = self.question_loader.survey_meta

            # Survey title - CRITICAL for domain context
            if hasattr(meta, 'title') and meta.title:
                self._extract_from_text(meta.title)
                # Also add title words individually as high-priority keywords
                title_words = meta.title.split()
                for word in title_words:
                    if len(word) > 3 and word.lower() not in self.STOP_WORDS:
                        self._add_keyword(word)

            # Survey description - Important context
            if hasattr(meta, 'description') and meta.description:
                self._extract_from_text(meta.description)

            # Participant profile (may contain domain-specific terminology)
            if hasattr(meta, 'participant_profile') and meta.participant_profile:
                self._extract_from_text(meta.participant_profile)

        # Legacy survey_config support
        if hasattr(self.question_loader, 'survey_config') and self.question_loader.survey_config:
            config = self.question_loader.survey_config

            # Survey name/title
            if hasattr(config, 'name') and config.name:
                self._extract_from_text(config.name)

            # Survey description
            if hasattr(config, 'description') and config.description:
                self._extract_from_text(config.description)

    def _extract_from_questions(self):
        """Extract keywords from all question texts"""
        for question in self._questions:
            # Question text
            if hasattr(question, 'question') and question.question:
                self._extract_from_text(question.question)

            # Question ID as potential keyword (e.g., "Q1", "T1")
            # Skip generic IDs but keep descriptive ones
            question_id = getattr(question, 'question_id', None) or getattr(question, 'id', None)
            if question_id and len(question_id) > 2:
                self._add_keyword(question_id)

    def _extract_from_response_options(self):
        """Extract keywords from response options"""
        for question in self._questions:
            response_options = getattr(question, 'response_options', None) or getattr(question, 'options', None)
            if response_options:
                for option in response_options:
                    self._extract_from_text(option)

    def _extract_from_categories(self):
        """Extract keywords from category names"""
        for question in self._questions:
            if hasattr(question, 'category') and question.category:
                self._extract_from_text(question.category)

    def _extract_from_sections(self):
        """Extract keywords from section titles, descriptions, and introductions (unified format only)"""
        if hasattr(self.question_loader, 'sections') and self.question_loader.sections:
            for section in self.question_loader.sections:
                # Section title
                if hasattr(section, 'title') and section.title:
                    self._extract_from_text(section.title)
                # Section description
                if hasattr(section, 'description') and section.description:
                    self._extract_from_text(section.description)
                # Section introduction
                if hasattr(section, 'introduction') and section.introduction:
                    self._extract_from_text(section.introduction)

    def _extract_from_text(self, text: str):
        """
        Extract meaningful keywords from a text string

        Args:
            text: Text to extract keywords from
        """
        if not text:
            return

        # Extract multi-word phrases (2-4 words) that are likely important
        # These are often compound terms like "passenger rail", "federal investment"
        self._extract_phrases(text)

        # Extract individual significant words
        words = re.findall(r'\b[A-Za-z][A-Za-z\-\']+\b', text)
        for word in words:
            word_lower = word.lower()

            # Skip stop words and very short words
            if word_lower not in self.STOP_WORDS and len(word) > 2:
                self._add_keyword(word)
                # Also extract verb forms for common verbs found in questions
                self._extract_verb_forms(word_lower)

    def _extract_verb_forms(self, word: str):
        """
        Extract common verb forms ONLY for known verbs to boost recognition of related responses.

        Only adds verb conjugations for a curated list of common survey response verbs.
        This prevents creating invalid words like "seniored" or "membering".

        Args:
            word: Word to extract verb forms from (lowercase)
        """
        # Curated list of common verbs used in survey responses
        # Only these verbs will have their forms expanded
        verb_conjugations = {
            'change': ['change', 'changes', 'changed', 'changing'],
            'help': ['help', 'helps', 'helped', 'helping'],
            'impact': ['impact', 'impacts', 'impacted', 'impacting'],
            'improve': ['improve', 'improves', 'improved', 'improving'],
            'reduce': ['reduce', 'reduces', 'reduced', 'reducing'],
            'value': ['value', 'values', 'valued', 'valuing'],
            'feel': ['feel', 'feels', 'felt', 'feeling'],
            'describe': ['describe', 'describes', 'described', 'describing'],
            'enroll': ['enroll', 'enrolls', 'enrolled', 'enrolling'],
            'join': ['join', 'joins', 'joined', 'joining'],
            'care': ['care', 'cares', 'cared', 'caring'],
            'support': ['support', 'supports', 'supported', 'supporting'],
            'coordinate': ['coordinate', 'coordinates', 'coordinated', 'coordinating'],
            'transform': ['transform', 'transforms', 'transformed', 'transforming'],
            'empower': ['empower', 'empowers', 'empowered', 'empowering'],
            'live': ['live', 'lives', 'lived', 'living'],
            'work': ['work', 'works', 'worked', 'working'],
            'provide': ['provide', 'provides', 'provided', 'providing'],
            'receive': ['receive', 'receives', 'received', 'receiving'],
            'manage': ['manage', 'manages', 'managed', 'managing'],
            'recommend': ['recommend', 'recommends', 'recommended', 'recommending'],
            'appreciate': ['appreciate', 'appreciates', 'appreciated', 'appreciating'],
            'stress': ['stress', 'stresses', 'stressed', 'stressing'],
        }

        # Check if word matches any known verb or its forms
        for base, forms in verb_conjugations.items():
            if word in forms or word == base:
                # Add all forms of this verb
                for form in forms:
                    self.keywords.add(form)
                break

    def _extract_phrases(self, text: str):
        """
        Extract 2-4 word phrases that are likely important compound terms

        Args:
            text: Text to extract phrases from
        """
        # Split into sentences first to avoid cross-sentence phrases
        sentences = re.split(r'[.!?;]', text)

        for sentence in sentences:
            words = re.findall(r'\b[A-Za-z][A-Za-z\-\']+\b', sentence)

            # Extract 2-word phrases
            for i in range(len(words) - 1):
                phrase = f"{words[i]} {words[i+1]}"
                if self._is_significant_phrase(phrase):
                    self._add_keyword(phrase)

            # Extract 3-word phrases
            for i in range(len(words) - 2):
                phrase = f"{words[i]} {words[i+1]} {words[i+2]}"
                if self._is_significant_phrase(phrase):
                    self._add_keyword(phrase)

    def _is_significant_phrase(self, phrase: str) -> bool:
        """
        Check if a phrase is significant enough to be a keyword

        Args:
            phrase: Phrase to check

        Returns:
            True if phrase should be added as keyword
        """
        words = phrase.lower().split()

        # Must have at least one non-stop word
        has_content = any(word not in self.STOP_WORDS for word in words)

        # Must not be entirely stop words
        all_stop = all(word in self.STOP_WORDS for word in words)

        return has_content and not all_stop

    def _add_keyword(self, keyword: str):
        """
        Add a keyword with proper formatting

        Args:
            keyword: Keyword to add
        """
        # Proper nouns should be capitalized (brand names, places, etc.)
        # For now, preserve original capitalization from source
        # Common nouns should be lowercase

        # If keyword is all caps or title case, it's likely a proper noun
        if keyword.isupper() or (keyword[0].isupper() and len(keyword) > 3):
            # Keep capitalization for proper nouns
            self.keywords.add(keyword)
        else:
            # Lowercase for common terms
            self.keywords.add(keyword.lower())

    def get_keyterms_for_question(self, question_number: int) -> List[str]:
        """
        Get question-specific keyterms for a particular question
        This can be used to provide more focused keywords per question

        Args:
            question_number: 1-indexed question number

        Returns:
            List of keywords specific to this question
        """
        if question_number < 1 or question_number > len(self._questions):
            return []

        question = self._questions[question_number - 1]
        question_keywords = set()

        # Extract from this question's text
        if question.question:
            words = re.findall(r'\b[A-Za-z][A-Za-z\-\']+\b', question.question)
            for word in words:
                if word.lower() not in self.STOP_WORDS and len(word) > 2:
                    question_keywords.add(word)

        # Add response options
        if question.response_options:
            for option in question.response_options:
                words = re.findall(r'\b[A-Za-z][A-Za-z\-\']+\b', option)
                for word in words:
                    if word.lower() not in self.STOP_WORDS and len(word) > 2:
                        question_keywords.add(word.lower())

        return list(question_keywords)[:20]  # Limit to 20 per question
