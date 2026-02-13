"""
Survey Configuration Manager
Loads and manages survey configurations from YAML file.
"""
import os
import logging
import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class SurveyConfig:
    """Single survey configuration"""
    survey_id: str  # The key from YAML
    name: str
    questions_file: Path  # Absolute path
    welcome_message: Optional[str] = None
    response_output_dir: Path = Path("output")
    enabled: bool = True

    def validate(self, project_root: Path) -> bool:
        """
        Validate this survey configuration.

        Args:
            project_root: Root directory of the project

        Returns:
            True if valid, raises ValueError if invalid
        """
        # Check name is non-empty
        if not self.name or len(self.name.strip()) == 0:
            raise ValueError(f"Survey '{self.survey_id}' has empty name")

        if len(self.name) > 200:
            raise ValueError(f"Survey '{self.survey_id}' name too long (max 200 chars)")

        # Check questions_file exists
        absolute_questions_path = project_root / self.questions_file
        if not absolute_questions_path.exists():
            raise FileNotFoundError(
                f"Questions file not found for survey '{self.survey_id}'\n"
                f"Expected: {self.questions_file}\n"
                f"Absolute path: {absolute_questions_path}\n\n"
                f"Check 'questions_file' path in surveys_config.yaml"
            )

        if not absolute_questions_path.is_file():
            raise ValueError(f"Questions file path is not a file: {absolute_questions_path}")

        # Check response_output_dir is valid path (will be created if doesn't exist)
        if not isinstance(self.response_output_dir, Path):
            self.response_output_dir = Path(self.response_output_dir)

        return True

    def get_absolute_questions_path(self, project_root: Path) -> Path:
        """Resolve questions_file to absolute path"""
        return project_root / self.questions_file


class SurveyConfigManager:
    """Manages all survey configurations from YAML file"""

    def __init__(self, config_file: str = "surveys_config.yaml"):
        """
        Initialize survey configuration manager.

        Args:
            config_file: Path to YAML configuration file (relative to project root)
        """
        self.project_root = Path.cwd()
        self.config_file = self.project_root / config_file
        self.default_survey_id: Optional[str] = None
        self.surveys: Dict[str, SurveyConfig] = {}

    def load_config(self) -> bool:
        """
        Load and validate YAML configuration.

        Returns:
            True if successful, raises exception on error
        """
        # Check if config file exists
        if not self.config_file.exists():
            raise FileNotFoundError(
                f"Survey configuration file not found: {self.config_file.name}\n"
                f"Expected location: {self.config_file}\n\n"
                f"Create this file with at least one survey configuration.\n"
                f"See documentation for example format."
            )

        # Load YAML file
        try:
            with open(self.config_file, 'r') as f:
                config_data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(
                f"Failed to parse {self.config_file.name}\n"
                f"YAML syntax error: {str(e)}\n\n"
                f"Validate your YAML syntax at https://www.yamllint.com/"
            )

        if not config_data:
            raise ValueError(f"Empty configuration file: {self.config_file.name}")

        # Validate required top-level fields
        if 'surveys' not in config_data:
            raise ValueError(
                f"Missing required field 'surveys' in {self.config_file.name}\n"
                f"Configuration must include a 'surveys' section"
            )

        if 'default_survey' not in config_data:
            raise ValueError(
                f"Missing required field 'default_survey' in {self.config_file.name}\n"
                f"Specify which survey to use by default"
            )

        # Parse survey configurations
        surveys_data = config_data['surveys']
        if not surveys_data:
            raise ValueError("No surveys defined in configuration")

        # Support both list format (new) and dict format (legacy)
        if isinstance(surveys_data, list):
            # New list format: surveys is a list of dictionaries with survey_id field
            for survey_item in surveys_data:
                # Validate required fields
                if 'survey_id' not in survey_item:
                    raise ValueError(
                        f"Survey item missing required field: 'survey_id'\n\n"
                        f"Required fields: survey_id, name, questions_file\n"
                        f"Optional fields: description, welcome_message, response_output_dir, enabled"
                    )

                survey_id = survey_item['survey_id']

                if 'name' not in survey_item:
                    raise ValueError(
                        f"Survey '{survey_id}' is missing required field: 'name'\n\n"
                        f"Required fields: survey_id, name, questions_file\n"
                        f"Optional fields: description, welcome_message, response_output_dir, enabled"
                    )

                if 'questions_file' not in survey_item:
                    raise ValueError(
                        f"Survey '{survey_id}' is missing required field: 'questions_file'\n\n"
                        f"Required fields: survey_id, name, questions_file\n"
                        f"Optional fields: description, welcome_message, response_output_dir, enabled"
                    )

                # Create SurveyConfig object
                survey_config = SurveyConfig(
                    survey_id=survey_id,
                    name=survey_item['name'],
                    questions_file=Path(survey_item['questions_file']),
                    welcome_message=survey_item.get('welcome_message'),
                    response_output_dir=Path(survey_item.get('response_output_dir', 'output')),
                    enabled=survey_item.get('enabled', True)
                )

                # Validate survey config
                try:
                    survey_config.validate(self.project_root)
                except (ValueError, FileNotFoundError) as e:
                    # Re-raise with survey context
                    raise type(e)(str(e))

                self.surveys[survey_id] = survey_config

        elif isinstance(surveys_data, dict):
            # Legacy dict format: surveys is a dictionary with survey_id as keys
            for survey_id, survey_data in surveys_data.items():
                # Validate required fields
                if 'name' not in survey_data:
                    raise ValueError(
                        f"Survey '{survey_id}' is missing required field: 'name'\n\n"
                        f"Required fields: name, questions_file\n"
                        f"Optional fields: welcome_message, response_output_dir, enabled"
                    )

                if 'questions_file' not in survey_data:
                    raise ValueError(
                        f"Survey '{survey_id}' is missing required field: 'questions_file'\n\n"
                        f"Required fields: name, questions_file\n"
                        f"Optional fields: welcome_message, response_output_dir, enabled"
                    )

                # Create SurveyConfig object
                survey_config = SurveyConfig(
                    survey_id=survey_id,
                    name=survey_data['name'],
                    questions_file=Path(survey_data['questions_file']),
                    welcome_message=survey_data.get('welcome_message'),
                    response_output_dir=Path(survey_data.get('response_output_dir', 'output')),
                    enabled=survey_data.get('enabled', True)
                )

                # Validate survey config
                try:
                    survey_config.validate(self.project_root)
                except (ValueError, FileNotFoundError) as e:
                    # Re-raise with survey context
                    raise type(e)(str(e))

                self.surveys[survey_id] = survey_config
        else:
            raise ValueError(
                f"Invalid format for 'surveys' in {self.config_file.name}\n"
                f"Expected either a list or dictionary, got {type(surveys_data).__name__}"
            )

        # Check that at least one survey is enabled
        enabled_surveys = [s for s in self.surveys.values() if s.enabled]
        if not enabled_surveys:
            raise ValueError(
                "No enabled surveys found in configuration.\n"
                "Set 'enabled: true' for at least one survey."
            )

        # Validate default_survey
        self.default_survey_id = config_data['default_survey']
        if self.default_survey_id not in self.surveys:
            available = list(self.surveys.keys())
            raise ValueError(
                f"Default survey '{self.default_survey_id}' not found in configuration.\n"
                f"Available surveys: {', '.join(available)}\n\n"
                f"Update 'default_survey' in {self.config_file.name}"
            )

        default_survey = self.surveys[self.default_survey_id]
        if not default_survey.enabled:
            raise ValueError(
                f"Default survey '{self.default_survey_id}' is disabled.\n"
                f"Either enable it or choose a different default survey."
            )

        logger.info(f"Loaded {len(self.surveys)} survey(s) from {self.config_file.name}")
        logger.info(f"Default survey: {self.default_survey_id}")

        return True

    def get_survey(self, survey_id: Optional[str] = None) -> SurveyConfig:
        """
        Get survey by ID with environment variable override.

        Priority:
        1. Explicit survey_id parameter
        2. SURVEY_ID environment variable
        3. Default survey from config

        Args:
            survey_id: Optional survey ID to retrieve

        Returns:
            SurveyConfig object
        """
        # Priority 1: Explicit parameter
        if survey_id:
            target_id = survey_id
        # Priority 2: Environment variable
        elif os.getenv("SURVEY_ID"):
            target_id = os.getenv("SURVEY_ID")
            logger.info(f"Using survey from SURVEY_ID env var: {target_id}")
        # Priority 3: Default
        else:
            target_id = self.default_survey_id
            logger.info(f"Using default survey: {target_id}")

        if target_id not in self.surveys:
            available = list(self.surveys.keys())
            raise ValueError(
                f"Survey '{target_id}' not found.\n"
                f"Available surveys: {', '.join(available)}"
            )

        survey = self.surveys[target_id]
        if not survey.enabled:
            raise ValueError(f"Survey '{target_id}' is disabled")

        # Return survey with absolute questions file path
        survey.questions_file = survey.get_absolute_questions_path(self.project_root)

        return survey

    def get_default_survey(self) -> SurveyConfig:
        """Get the default survey configuration"""
        return self.get_survey()

    def list_enabled_surveys(self) -> List[SurveyConfig]:
        """Get all enabled surveys"""
        return [s for s in self.surveys.values() if s.enabled]
