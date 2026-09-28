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
from modules.drive_downloader import DriveDownloader
from modules.ai_groq_parser import GroqAnswerResolver
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
    sample_docx = next(p for p in Path("cache/docx").glob("*.docx") if DriveDownloader.is_first_language_docx(p))
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


def test_first_language_docx_selection(tmp_path, monkeypatch):
    downloader = DriveDownloader(cache_dir=str(tmp_path))
    files = {
        "Second Language.docx": ("second-id", "docx"),
        "New.docx": ("new-id", "docx"),
        "First language.docx": ("first-id", "docx"),
    }
    monkeypatch.setattr(downloader, "get_folder_items", lambda _folder: files)
    downloaded = []

    def fake_download(file_id, destination):
        downloaded.append(file_id)
        destination.write_bytes(b"PK\x03\x04" + b"x" * 1100)
        return True

    monkeypatch.setattr(downloader, "download_file_by_id", fake_download)
    path, status = downloader.download_story_docx("folder-id")
    assert path is not None and DriveDownloader.is_first_language_docx(path)
    assert downloaded == ["first-id"]
    assert "First language.docx" in status

    files.pop("First language.docx")
    path, status = downloader.download_story_docx("folder-id")
    assert path is None
    assert "First language document not found" in status
    assert downloaded == ["first-id"]


def test_review_rejects_preloaded_second_language(tmp_path):
    wrong_file = tmp_path / "Second Language.docx"
    wrong_file.write_bytes(b"placeholder")
    story = {
        "story_name": "Example",
        "drive_url": "https://drive.google.com/drive/folders/example",
        "docx_path": str(wrong_file),
    }
    result = prepare_story_review(story, {"cache_dir": str(tmp_path)}, download_if_missing=False)
    assert result["success"] is False
    assert "First language.docx" in result["error"]


def test_ai_suggestion_never_opens_candidate(tmp_path, monkeypatch):
    downloader = DriveDownloader(cache_dir=str(tmp_path))
    monkeypatch.setattr(
        downloader, "get_folder_items",
        lambda _folder: {"Second Language.docx": ("second-id", "docx"), "New.docx": ("new-id", "docx")},
    )
    monkeypatch.setattr(
        GroqAnswerResolver, "suggest_first_language_filename",
        lambda names, config: "New.docx",
    )
    downloads = []
    monkeypatch.setattr(
        downloader, "download_file_by_id",
        lambda file_id, destination: downloads.append(file_id),
    )
    path, status = downloader.download_story_docx("folder-id", ai_config={"groq_api_key": "test"})
    assert path is None
    assert "AI suggests checking 'New.docx'" in status
    assert downloads == []


def test_first_language_google_doc_inside_output_folder(tmp_path, monkeypatch):
    downloader = DriveDownloader(cache_dir=str(tmp_path))
    folders = {
        "story-id": {
            "New.docx": ("new-id", "docx"),
            "OUTPUT_Grade4_FirstLanguage": ("output-id", "folder"),
        },
        "output-id": {"EFL-The Treasure Map Math": ("doc-id", "google_doc")},
    }
    monkeypatch.setattr(downloader, "get_folder_items", lambda folder_id: folders[folder_id])
    exported = []

    def fake_export(file_id, destination):
        exported.append(file_id)
        destination.write_bytes(b"PK\x03\x04" + b"x" * 1100)
        return True

    monkeypatch.setattr(downloader, "export_google_doc_by_id", fake_export)
    monkeypatch.setattr(downloader, "download_file_by_id", lambda *_args: (_ for _ in ()).throw(AssertionError("Wrong DOCX downloaded")))
    path, status = downloader.download_story_docx("story-id")
    assert path is not None and DriveDownloader.is_first_language_docx(path)
    assert exported == ["doc-id"]
    assert "OUTPUT_Grade4_FirstLanguage" in status


def test_second_language_output_folder_is_skipped(tmp_path, monkeypatch):
    downloader = DriveDownloader(cache_dir=str(tmp_path))
    folders = {
        "story-id": {"OUTPUT_Grade4_SecondLanguage": ("second-output-id", "folder")},
        "second-output-id": {"First language.docx": ("wrong-id", "docx")},
    }
    visited = []

    def list_items(folder_id):
        visited.append(folder_id)
        return folders[folder_id]

    monkeypatch.setattr(downloader, "get_folder_items", list_items)
    path, status = downloader.download_story_docx("story-id")
    assert path is None
    assert "First language document not found" in status
    assert visited == ["story-id"]


def test_efl_name_takes_priority_over_story_title(tmp_path, monkeypatch):
    downloader = DriveDownloader(cache_dir=str(tmp_path))
    monkeypatch.setattr(downloader, "get_folder_items", lambda _folder: {
        "The Treasure Map Math.docx": ("story-id", "docx"),
        "EFL-The Treasure Map Math.docx": ("efl-id", "docx"),
        "Second Language.docx": ("second-id", "docx"),
    })
    chosen = []
    def download(file_id, destination):
        chosen.append(file_id)
        destination.write_bytes(b"PK\x03\x04" + b"x" * 1100)
        return True
    monkeypatch.setattr(downloader, "download_file_by_id", download)
    path, _ = downloader.download_story_docx("folder-id", story_name="The Treasure Map Math")
    assert path is not None and chosen == ["efl-id"]


def test_exact_story_title_used_when_no_language_label(tmp_path, monkeypatch):
    downloader = DriveDownloader(cache_dir=str(tmp_path))
    monkeypatch.setattr(downloader, "get_folder_items", lambda _folder: {
        "New.docx": ("new-id", "docx"),
        "The Treasure Map Math": ("story-id", "google_doc"),
    })
    exported = []
    def export(file_id, destination):
        exported.append(file_id)
        destination.write_bytes(b"PK\x03\x04" + b"x" * 1100)
        return True
    monkeypatch.setattr(downloader, "export_google_doc_by_id", export)
    path, _ = downloader.download_story_docx("folder-id", story_name="The Treasure Map Math")
    assert path is not None and DriveDownloader.is_first_language_docx(path)
    assert exported == ["story-id"]


def test_ambiguous_first_language_files_require_selection(tmp_path, monkeypatch):
    downloader = DriveDownloader(cache_dir=str(tmp_path))
    monkeypatch.setattr(downloader, "get_folder_items", lambda _folder: {
        "EFL.docx": ("efl-id", "docx"),
        "First language.docx": ("first-id", "docx"),
    })
    path, _ = downloader.download_story_docx("folder-id", story_name="Example")
    assert path is None and downloader.selection_required


def test_manual_choice_is_verified_and_second_language_blocked(tmp_path, monkeypatch):
    downloader = DriveDownloader(cache_dir=str(tmp_path))
    items = {"Story.docx": ("chosen-id", "docx"), "Second Language.docx": ("second-id", "docx")}
    monkeypatch.setattr(downloader, "get_folder_items", lambda _folder: items)
    downloaded = []
    def download(file_id, destination):
        downloaded.append(file_id)
        destination.write_bytes(b"PK\x03\x04" + b"x" * 1100)
        return True
    monkeypatch.setattr(downloader, "download_file_by_id", download)
    selection = {"folder_id": "folder-id", "file_id": "chosen-id", "name": "Story.docx", "kind": "docx"}
    path, _ = downloader.download_selected_file(selection)
    assert path is not None and DriveDownloader.is_first_language_docx(path)
    assert downloaded == ["chosen-id"]
    selection["file_id"] = "stale-id"
    assert downloader.download_selected_file(selection)[0] is None
    selection.update(file_id="second-id", name="Second Language.docx")
    assert downloader.download_selected_file(selection)[0] is None
    selection.update(file_id="chosen-id", name="Story.docx", folder_names=["OUTPUT_SecondLanguage"])
    assert downloader.download_selected_file(selection)[0] is None
    assert downloaded == ["chosen-id"]


def test_review_requests_picker_when_file_is_unclear(tmp_path, monkeypatch):
    monkeypatch.setattr(DriveDownloader, "get_folder_items", lambda _self, _folder: {
        "New.docx": ("new-id", "docx"),
        "Second Language.docx": ("second-id", "docx"),
    })
    story = {"story_name": "Different Story", "drive_url": "https://drive.google.com/drive/folders/folder-id"}
    result = prepare_story_review(story, {"cache_dir": str(tmp_path)})
    assert result["success"] is False
    assert result["selection_required"] is True


def test_ai_suggestion_rejects_second_language_and_invented_names(monkeypatch):
    from modules import ai_groq_parser

    class FakeCompletions:
        proposed = "Second Language.docx"

        def create(self, **_kwargs):
            message = type("Message", (), {"content": '{"filename": "' + self.proposed + '"}'})()
            return type("Response", (), {"choices": [type("Choice", (), {"message": message})()]})()

    completions = FakeCompletions()
    fake_client = type(
        "Client", (), {"chat": type("Chat", (), {"completions": completions})()}
    )()
    monkeypatch.setattr(ai_groq_parser, "GROQ_AVAILABLE", True)
    monkeypatch.setattr(ai_groq_parser, "Groq", lambda **_kwargs: fake_client)
    filenames = ["Second Language.docx", "New.docx"]
    assert GroqAnswerResolver.suggest_first_language_filename(filenames, {"groq_api_key": "test"}) is None
    completions.proposed = "Made Up.docx"
    assert GroqAnswerResolver.suggest_first_language_filename(filenames, {"groq_api_key": "test"}) is None
    completions.proposed = "New.docx"
    assert GroqAnswerResolver.suggest_first_language_filename(filenames, {"groq_api_key": "test"}) == "New.docx"


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
