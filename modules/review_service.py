"""
Review and Approval Service for Mohra Readora Automation.

Enables interactive Dry-Run previews, detailed question & choices inspection,
inline corrections, and direct "Accept & Apply" live execution.
"""

from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
import traceback

from modules.drive_downloader import DriveDownloader
from modules.docx_parser import DocxParser
from modules.progress import ProgressTracker
from modules.readora_client import ReadoraClient
from modules.threading_manager import ThreadManager
from modules.ai_groq_parser import GroqAnswerResolver


def prepare_story_review(
    story: Dict,
    config: dict,
    download_if_missing: bool = True,
    on_status: Optional[Callable[[str], None]] = None
) -> Dict[str, Any]:
    """
    Prepares a complete dry-run inspection packet for a story:
    - Finds or downloads the docx from Google Drive
    - Parses all questions, choices (A, B, C, D), and answers
    - Validates questions and choices
    - Formats changes summary
    """
    story_name = story.get("story_name", "Unknown")
    drive_url = story.get("drive_url")

    if on_status:
        on_status(f"Preparing review for '{story_name}'...")

    if not drive_url:
        return {
            "success": False,
            "story": story,
            "error": f"No Google Drive URL found for story '{story_name}'.",
            "questions": [],
            "validation": {"is_valid": False, "issues": ["Missing Google Drive link"], "total_questions": 0},
            "docx_path": None,
        }

    # 1. Download or locate docx
    cache_dir = config.get("cache_dir", "./cache")
    downloader = DriveDownloader(cache_dir=cache_dir)
    pref_lang = config.get("preferred_language_file", "First language")

    docx_path = None
    status_msg = ""

    # Check if story has pre-existing path
    if story.get("docx_path") and Path(story["docx_path"]).exists():
        docx_path = Path(story["docx_path"])
        status_msg = "Cached locally"
    elif download_if_missing:
        if on_status:
            on_status(f"Downloading {pref_lang}.docx from Google Drive...")
        docx_path, status_msg = downloader.download_story_docx(drive_url, preferred_lang=pref_lang)

    if not docx_path or not docx_path.exists():
        return {
            "success": False,
            "story": story,
            "error": f"Failed to obtain docx file: {status_msg}",
            "questions": [],
            "validation": {"is_valid": False, "issues": [status_msg], "total_questions": 0},
            "docx_path": None,
        }

    # 2. Parse questions
    if on_status:
        on_status("Parsing questions and choices from docx...")
    try:
        questions = DocxParser.parse_comprehension_questions(docx_path)
    except Exception as e:
        questions = []

    # 2.5 Automatic AI Fallback & Self-Healing (Groq)
    # Automatically triggers when:
    # a) 0 questions parsed from docx (non-standard formatting)
    # b) One or more questions have missing/unresolved answer keys
    has_missing_keys = any(not q.get("answer") or q.get("answer") not in ["A", "B", "C", "D"] for q in questions)

    if (len(questions) == 0 or has_missing_keys) and GroqAnswerResolver.is_available() and GroqAnswerResolver.get_api_key(config):
        if len(questions) == 0:
            if on_status:
                on_status("⚡ AI Auto-Worker: Regex found 0 questions. Auto-extracting via Groq AI...")
            ai_extract = GroqAnswerResolver.extract_all_questions_with_groq(docx_path, config)
            if ai_extract.get("success") and ai_extract.get("questions"):
                questions = ai_extract["questions"]
                status_msg = f"{status_msg} (⚡ Groq AI auto-extracted {len(questions)} questions)"
        elif has_missing_keys:
            missing_count = sum(1 for q in questions if not q.get("answer") or q.get("answer") not in ["A", "B", "C", "D"])
            if on_status:
                on_status(f"⚡ AI Auto-Worker: Auto-resolving {missing_count} missing answer keys via Groq AI...")
            ai_res = GroqAnswerResolver.resolve_answers_with_groq(docx_path, questions, config)
            if ai_res.get("success"):
                questions = ai_res["questions"]
                status_msg = f"{status_msg} (⚡ Groq AI auto-resolved {ai_res.get('changes_count', 0)} answers)"

    # 3. Validate questions
    validation = DocxParser.validate_questions(questions)

    # 4. Review payload
    header_text = config.get("question_header", "Choose the correct answer ")
    content_type = config.get("content_type", "None")

    return {
        "success": True,
        "story": story,
        "docx_path": str(docx_path),
        "status_msg": status_msg,
        "questions": questions,
        "validation": validation,
        "config_preview": {
            "question_header": header_text,
            "content_type": content_type,
            "preferred_lang": pref_lang
        },
        "error": None
    }


def fetch_story_readora_state(
    story: Dict,
    config: dict,
    on_status: Optional[Callable[[str], None]] = None
) -> Dict[str, Any]:
    """
    Safely connects to Readora Lab to retrieve the CURRENT (OLD) state of the story:
    question header, content type, and existing questions.
    Performs NO writes/modifications.
    """
    story_name = story.get("story_name", "Unknown")
    mgr = ThreadManager.get_instance()

    if on_status:
        on_status(f"Acquiring browser lock to fetch Readora state for '{story_name}'...")

    lock_acquired = mgr.acquire_browser_lock()
    client = None
    try:
        if on_status:
            on_status("Connecting to Readora Lab to inspect current questions...")

        client_config = config.copy()
        client_config["dry_run"] = True
        client_config["headless"] = config.get("headless", False)

        client = ReadoraClient(client_config)
        client.start_browser()

        if on_status:
            on_status("Logging in to Readora...")
        client.login()

        if on_status:
            on_status(f"Searching for '{story_name}'...")
        res = client.fetch_existing_questions(story_name)
        return res

    except Exception as e:
        err = f"Failed to fetch Readora current state: {e}"
        return {"success": False, "error": err}
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass
        if lock_acquired:
            mgr.release_browser_lock()


def execute_dry_run_test(
    story: Dict,
    questions: List[Dict],
    config: dict,
    on_status: Optional[Callable[[str], None]] = None
) -> Dict[str, Any]:
    """
    Safely executes dry-run validation in browser.
    Extracts the old state, populates the new questions into the modal to verify,
    and closes without saving (Escape).
    """
    story_name = story.get("story_name", "Unknown")
    mgr = ThreadManager.get_instance()

    if on_status:
        on_status(f"Acquiring browser lock for dry-run test of '{story_name}'...")

    lock_acquired = mgr.acquire_browser_lock()
    client = None
    try:
        if on_status:
            on_status("Launching browser in safe dry-run mode...")

        dry_config = config.copy()
        dry_config["dry_run"] = True
        dry_config["headless"] = config.get("headless", False)

        client = ReadoraClient(dry_config)
        client.start_browser()

        if on_status:
            on_status("Logging in to Readora...")
        client.login()

        if on_status:
            on_status(f"Searching for '{story_name}'...")
        book_info = client.search_book(story_name)
        if not book_info:
            return {"success": False, "error": f"Book '{story_name}' not found on Readora Lab."}

        if on_status:
            on_status(f"Opening book edit page: {book_info.get('matched_title')}...")
        client.open_book_edit(book_info)

        if on_status:
            on_status(f"Populating modal with {len(questions)} questions (Dry-Run mode)...")
        client.edit_questions(questions, dry_run=True)

        old_state = getattr(client, "last_extracted_old_state", None)

        return {
            "success": True,
            "story_name": story_name,
            "matched_title": book_info.get("matched_title"),
            "old_state": old_state,
            "questions_count": len(questions)
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass
        if lock_acquired:
            mgr.release_browser_lock()


def execute_accept_and_apply(
    story: Dict,
    questions: List[Dict],
    config: dict,
    on_status: Optional[Callable[[str], None]] = None
) -> Dict[str, Any]:
    """
    Executes live update directly to Readora Lab with reviewed questions.
    Thread-safe with browser locking and automatic ProgressTracker recording.
    """
    story_name = story.get("story_name", "Unknown")
    mgr = ThreadManager.get_instance()
    progress_tracker = ProgressTracker()

    if on_status:
        on_status(f"Acquiring browser lock for '{story_name}'...")

    lock_acquired = mgr.acquire_browser_lock()
    client = None

    try:
        if on_status:
            on_status("Launching Readora browser client (LIVE MODE)...")

        # Copy config and enforce live mode
        live_config = config.copy()
        live_config["dry_run"] = False
        live_config["headless"] = config.get("headless", False)

        client = ReadoraClient(live_config)
        client.start_browser()

        if on_status:
            on_status("Logging in to Readora Lab...")
        client.login()

        if on_status:
            on_status(f"Searching for book '{story_name}'...")
        book_info = client.search_book(story_name)

        if not book_info:
            err = f"Book '{story_name}' not found on Readora Lab."
            progress_tracker.record_failure(story, err)
            return {"success": False, "error": err}

        if on_status:
            on_status(f"Opening book edit page: {book_info.get('matched_title')}...")
        client.open_book_edit(book_info)

        if on_status:
            on_status(f"Applying {len(questions)} reviewed questions and saving...")
        # Live apply
        saved = client.edit_questions(questions, dry_run=False)

        old_state = getattr(client, "last_extracted_old_state", None)

        if saved:
            progress_tracker.record_success(story, len(questions), dry_run=False)
            if on_status:
                on_status(f"Successfully applied {len(questions)} questions to '{story_name}'!")

            sheet_res = None
            if config.get("auto_mark_sheet_done", True):
                try:
                    if on_status:
                        on_status("Checking Google account & updating Google Sheet status...")
                    from modules.google_sheet_updater import GoogleSheetUpdater
                    sheet_updater = GoogleSheetUpdater(config)
                    sheet_res = sheet_updater.mark_story_done(story, status_value="Done", dry_run=False)
                    if sheet_res.get("success"):
                        if on_status:
                            on_status(f"✅ Marked cell {sheet_res.get('cell')} as Done in '{story.get('sheet_name')}'!")
                    elif sheet_res.get("blocked_by_safety_rule"):
                        if on_status:
                            on_status(f"⚠️ Readora updated, but Google Sheet NOT updated (Strict Rule: Not logged in as {config.get('gmail_account', 'mohrawagdy58@gmail.com')}).")
                    else:
                        if on_status:
                            on_status(f"Google Sheet notice: {sheet_res.get('error')}")
                except Exception as sheet_err:
                    if on_status:
                        on_status(f"Notice: Google Sheet update encountered: {sheet_err}")

            return {
                "success": True,
                "story_name": story_name,
                "matched_title": book_info.get("matched_title"),
                "questions_count": len(questions),
                "edit_url": book_info.get("edit_url"),
                "old_state": old_state,
                "sheet_result": sheet_res
            }
        else:
            err = "Readora rejected or failed during question update."
            progress_tracker.record_failure(story, err)
            return {"success": False, "error": err}

    except Exception as e:
        err = f"Exception during Accept & Apply: {e}\n{traceback.format_exc()}"
        progress_tracker.record_failure(story, str(e))
        return {"success": False, "error": str(e)}

    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass
        if lock_acquired:
            mgr.release_browser_lock()
