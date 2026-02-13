"""
Utility to convert survey questions from Excel to structured JSON format.
This helps the agent understand question structure better and avoid hallucination.
"""
import json
import sys
from openpyxl import load_workbook
from pathlib import Path


def convert_survey_to_json(xlsx_path: str, output_path: str = None):
    """
    Convert survey questions from Excel to structured JSON.

    Expected Excel format (actual structure from the file):
    - Row 1: Introduction text
    - Row 3: Headers (Question Category | Question Category Comments | Question | Response Options)
    - Row 4+: Category | Question | Option (repeated rows for same question with different options)

    Args:
        xlsx_path: Path to Excel file
        output_path: Optional output path for JSON file

    Returns:
        List of structured question dictionaries
    """
    wb = load_workbook(xlsx_path)
    ws = wb.active

    questions_dict = {}  # Track questions by text to group options
    categories_dict = {}  # Track categories
    introduction_text = None

    # Read first row for introduction (column 2, index 2)
    first_row = list(ws.iter_rows(min_row=1, max_row=1, values_only=True))[0]
    if first_row and len(first_row) > 2 and first_row[2]:
        introduction_text = str(first_row[2]).strip()
        print(f"Found introduction: {introduction_text[:100]}...")
    else:
        print("No introduction found in row 1, column 2")

    question_counter = 0

    # Start from row 4 (after headers in row 3)
    for row_idx, row in enumerate(ws.iter_rows(min_row=4, values_only=True), start=4):
        if not row or not any(row):  # Skip empty rows
            continue

        # Parse columns: Category (0), Category Comments (1), Question (2), Response Option (3)
        category = str(row[0]).strip() if row[0] else ""
        category_comments = str(row[1]).strip() if row[1] and '?' not in str(row[1]) else ""

        # Question can have '?' OR just be a statement with options
        question_text = str(row[2]).strip() if row[2] else None
        option = str(row[3]).strip() if len(row) > 3 and row[3] else None

        # Skip if no question text OR if question text is too short
        if not question_text or len(question_text) < 5:
            continue

        # Skip header-like text
        if question_text in ['Question', 'question', 'Question Category', 'Response Options']:
            continue

        # Normalize question text
        question_text = question_text.strip()

        # Determine question type
        if 'choose three' in question_text.lower() or 'choose 3' in question_text.lower():
            question_type = "multiple-choice"
            max_selections = 3
        elif option:
            # Has options = single choice (most common for rating scales)
            question_type = "single-choice"
            max_selections = 1
        elif '?' in question_text and ('select' in question_text.lower() or 'which' in question_text.lower()):
            question_type = "single-choice"
            max_selections = 1
        elif 'rate' in question_text.lower() or 'rating' in question_text.lower():
            question_type = "rating"
            max_selections = 1
        else:
            question_type = "open-ended"
            max_selections = None

        # Initialize category if new
        if category and category not in categories_dict:
            categories_dict[category] = {
                "name": category,
                "comments": category_comments if category_comments else "",
                "questions": []
            }
            print(f"\nCategory: {category}")
        # Update category comments if we find a non-empty one
        elif category and category in categories_dict and category_comments:
            if not categories_dict[category]["comments"]:
                categories_dict[category]["comments"] = category_comments

        # Group questions by text (same question may have multiple option rows)
        if question_text not in questions_dict:
            question_counter += 1
            questions_dict[question_text] = {
                "id": f"Q{question_counter}",
                "question": question_text,
                "type": question_type,
                "options": [],
                "max_selections": max_selections
            }
            # Add question to its category
            if category in categories_dict:
                categories_dict[category]["questions"].append(questions_dict[question_text])
            print(f"  Row {row_idx}: Q{question_counter} - {question_type}")

        # Add option if present
        if option and option not in questions_dict[question_text]["options"]:
            questions_dict[question_text]["options"].append(option)
            print(f"    Row {row_idx}: Added option: {option}")

    # Convert categories dict to list (preserving order)
    categories = list(categories_dict.values())

    # Clean up options - remove empty ones, set to None if no options
    for category in categories:
        for q in category["questions"]:
            if q["options"]:
                q["options"] = [opt for opt in q["options"] if opt]
                if not q["options"]:
                    q["options"] = None
            else:
                q["options"] = None

    # Create output structure matching the expected format
    # Format: {
    #   "agent introduction comments": "...",
    #   "question category": [
    #     {
    #       "category": "General issues",
    #       "category comments": "...",
    #       "question list": [...]
    #     }
    #   ]
    # }
    output_data = {
        "agent introduction comments": introduction_text if introduction_text else "",
        "question category": []
    }

    # Add each category with its questions
    for category in categories:
        category_obj = {
            "category": category["name"],
            "category comments": category.get("comments", ""),  # Store category-specific comments
            "question list": []
        }

        # Format questions for this category
        for q in category["questions"]:
            question_obj = {
                "id": q["id"],
                "question": q["question"],
                "response options": q["options"] if q["options"] else [],
                "max_selections": q["max_selections"]
            }
            category_obj["question list"].append(question_obj)

        output_data["question category"].append(category_obj)

    # Save to JSON
    if output_path is None:
        output_path = xlsx_path.replace('.xlsx', '.json')

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    total_questions = sum(len(cat["questions"]) for cat in categories)
    print(f"\n{'='*60}")
    print(f"Successfully converted {total_questions} questions across {len(categories)} categories")
    print(f"Output saved to: {output_path}")

    return output_data


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python convert_xlsx_to_json.py <path_to_xlsx_file> [output_json_path]")
        sys.exit(1)

    xlsx_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    if not Path(xlsx_path).exists():
        print(f"Error: File not found: {xlsx_path}")
        sys.exit(1)

    try:
        output_data = convert_survey_to_json(xlsx_path, output_path)

        # Print detailed summary
        print("\n" + "="*60)
        print("DETAILED SUMMARY:")
        print("="*60)

        # Print categories
        for cat_obj in output_data.get("question category", []):
            cat_name = cat_obj.get("category", "Unknown")
            questions = cat_obj.get("question list", [])
            comments = cat_obj.get("category comments", "")
            print(f"\n{cat_name}:")
            print(f"  Questions: {len(questions)}")
            if comments:
                print(f"  Comments: {comments[:80]}...")

    except Exception as e:
        print(f"Error converting file: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
