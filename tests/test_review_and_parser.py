"""
Test Suite for Mohra Enhanced DocxParser, Review Service, and Dry-Run Approval Pipeline.
"""

import sys
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from modules.docx_parser import DocxParser
from modules.review_service import prepare_story_review
from config import load_config


def test_docx_parser_all_cache_files():
    print("Testing DocxParser across all cached docx files...")
    cache_docx = list(Path("cache/docx").glob("*.docx"))
    assert len(cache_docx) > 0, "No cached docx files found"

    for docx_path in cache_docx:
        questions = DocxParser.parse_comprehension_questions(docx_path)
        assert len(questions) in (10, 20), f"{docx_path.name} unexpected question count: {len(questions)}"

        val = DocxParser.validate_questions(questions)
        assert val["is_valid"] is True, f"{docx_path.name} validation issues: {val['issues']}"
        assert val["total_questions"] == len(questions)

        # Verify each question structure
        for q in questions:
            assert "num" in q
            assert "question" in q
            assert "raw_question" in q
            assert "choices" in q
            assert len(q["choices"]) == 4
            assert q["answer"] in ("A", "B", "C", "D")

    print(f"  ✓ Successfully verified {len(cache_docx)} docx files (100% parsed with all questions and choices).")


def test_prepare_story_review():
    print("Testing prepare_story_review...")
    cfg = load_config()

    # Use existing cached file
    sample_docx = next(Path("cache/docx").glob("*.docx"))
    story = {
        "story_name": "Test Story Review",
        "sheet_name": "Grade 2",
        "row_index": 42,
        "drive_url": "https://drive.google.com/test",
        "docx_path": str(sample_docx)
    }

    res = prepare_story_review(story, cfg, download_if_missing=False)
    assert res["success"] is True
    assert len(res["questions"]) in (10, 20)
    assert res["validation"]["is_valid"] is True
    assert "config_preview" in res
    assert res["config_preview"]["content_type"] == "None"
    print("  ✓ prepare_story_review verified successfully.")


def test_missing_drive_url_handling():
    print("Testing missing drive URL handling in review...")
    cfg = load_config()
    story = {
        "story_name": "No URL Story",
        "sheet_name": "Grade 1",
        "row_index": 1,
        "drive_url": ""
    }

    res = prepare_story_review(story, cfg, download_if_missing=False)
    assert res["success"] is False
    assert "No Google Drive URL" in res["error"]
    print("  ✓ Missing drive URL handled gracefully.")


if __name__ == "__main__":
    print("==================================================")
    print("  Running Mohra Review & Parser Test Suite")
    print("==================================================")
    test_docx_parser_all_cache_files()
    test_prepare_story_review()
    test_missing_drive_url_handling()
    print("==================================================")
    print("  All Review & Parser tests passed!")
    print("==================================================")
