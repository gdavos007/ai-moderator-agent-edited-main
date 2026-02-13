"""
Test keyword extraction for Deepgram Nova-3 keyterm prompting
"""
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.question_loader import QuestionLoader
from src.keyword_extractor import KeywordExtractor
from src.survey_config import SurveyConfigManager


def test_keyword_extraction(survey_id: str = "amtrak_passenger_rail"):
    """Test keyword extraction on a survey"""
    print(f"\n{'='*80}")
    print(f"Testing Keyword Extraction for Survey: {survey_id}")
    print(f"{'='*80}\n")

    # Load survey via SurveyConfigManager
    try:
        config_manager = SurveyConfigManager()
        config_manager.load_config()  # Must load config first
        survey_config = config_manager.get_survey(survey_id)

        # Create QuestionLoader from survey config
        question_loader = QuestionLoader(questions_file=survey_config.questions_file)
        if not question_loader.load_questions():
            print(f"❌ Failed to load questions from {survey_config.questions_file}")
            return

        # Get actual question count based on format
        if question_loader.use_unified_format:
            question_count = len(question_loader.structured_questions)
        else:
            question_count = len(question_loader.questions)

        print(f"✅ Loaded survey: {survey_config.name}")
        print(f"   Questions file: {survey_config.questions_file}")
        print(f"   Total questions: {question_count}")
        print(f"   Format: {'Unified' if question_loader.use_unified_format else 'Legacy'}\n")
    except Exception as e:
        print(f"❌ Failed to load survey: {e}")
        import traceback
        traceback.print_exc()
        return

    # Extract keywords
    try:
        extractor = KeywordExtractor(question_loader)
        keywords = extractor.extract_keywords()  # Now returns 75 by default
        print(f"✅ Extracted {len(keywords)} keywords (Limited to avoid URL length issues)\n")
    except Exception as e:
        print(f"❌ Failed to extract keywords: {e}")
        import traceback
        traceback.print_exc()
        return

    # Display keywords
    print(f"{'='*80}")
    print("EXTRACTED KEYWORDS FOR DEEPGRAM NOVA-3")
    print(f"{'='*80}\n")

    # Group keywords by type
    proper_nouns = [k for k in keywords if k[0].isupper()]
    common_words = [k for k in keywords if k[0].islower()]

    print(f"Proper Nouns/Brand Names ({len(proper_nouns)}):")
    print("-" * 80)
    for i, keyword in enumerate(proper_nouns, 1):
        print(f"  {i:2d}. {keyword}")

    print(f"\nCommon Terms ({len(common_words)}):")
    print("-" * 80)
    for i, keyword in enumerate(common_words, 1):
        print(f"  {i:2d}. {keyword}")

    # Display per-question keywords
    print(f"\n{'='*80}")
    print("QUESTION-SPECIFIC KEYWORDS (First 3 Questions)")
    print(f"{'='*80}\n")

    for q_num in range(1, min(4, len(question_loader.questions) + 1)):
        question = question_loader.questions[q_num - 1]
        q_keywords = extractor.get_keyterms_for_question(q_num)

        print(f"Question {q_num} ({question.question_id}): {question.question[:60]}...")
        print(f"Keywords ({len(q_keywords)}): {', '.join(q_keywords[:10])}")
        if len(q_keywords) > 10:
            print(f"              ... and {len(q_keywords) - 10} more")
        print()

    # Summary
    print(f"{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    print(f"Total Keywords: {len(keywords)}")
    print(f"Proper Nouns: {len(proper_nouns)}")
    print(f"Common Terms: {len(common_words)}")
    print(f"\nNova-3 Keyterm Limit: 500 tokens (~150-200 words)")
    print(f"URL Length Constraint: Limited to 75 keywords to avoid HTTP 400 errors")
    print(f"Estimated Token Usage: ~{len(keywords) * 1.5:.0f} tokens")
    print(f"\nPriority Breakdown:")
    print(f"  Priority 1 (Survey Topic/Title): First ~10-20 keywords")
    print(f"  Priority 2 (Question Terms): Next ~50-80 keywords")
    print(f"  Priority 3 (Response Options): Next ~20-30 keywords")
    print(f"  Priority 4 (General Terms): Remaining slots")
    print(f"\n✅ Keywords ready to pass to Deepgram Nova-3 via 'keyterms' parameter")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    # Test with Amtrak survey by default
    survey_id = sys.argv[1] if len(sys.argv) > 1 else "amtrak_passenger_rail"
    test_keyword_extraction(survey_id)
