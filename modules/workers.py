"""
Standardized Background Workers for Mohra Modules.

This module encapsulates existing and future workflows into threaded BaseWorkers:
- StoryAutomationWorker: Batch and single-story Readora automation in background with live progress.
- SheetWatcherWorker: Periodic background detector for updated spreadsheet files.
- AutoUpdaterWorker: Periodic background checker for Git/Zip software updates.
- ChromeLauncherWorker: Background launcher for Chrome profile with remote debugging.
"""

import time
import traceback
from pathlib import Path
from typing import List, Dict, Optional, Any, Callable

from modules.threading_manager import (
    BaseWorker,
    PeriodicWorker,
    ThreadManager,
    WorkerState,
)
from modules.drive_downloader import DriveDownloader
from modules.docx_parser import DocxParser
from modules.progress import ProgressTracker
from modules.readora_client import ReadoraClient
from modules.ai_groq_parser import GroqAnswerResolver
from modules.sheet_parser import SheetParser, auto_detect_downloads_sheet
from modules.updater import AutoUpdater


class StoryAutomationWorker(BaseWorker):
    """
    Background worker that runs the Readora automation pipeline for one or more stories.
    Executes in a dedicated background thread with thread-safe progress reporting and cancellation.
    """

    def __init__(
        self,
        stories: List[Dict],
        config: dict,
        name: Optional[str] = None,
        on_story_complete: Optional[Callable[[Dict, bool, str], None]] = None,
    ):
        worker_name = name or f"StoryAutomation-{len(stories)}stories"
        super().__init__(name=worker_name, daemon=True)
        self.stories = stories
        self.config = config
        self.on_story_complete = on_story_complete

        self.downloader = DriveDownloader(cache_dir=self.config.get("cache_dir", "./cache"))
        self.progress_tracker = ProgressTracker()
        self.client: Optional[ReadoraClient] = None
        self.lock_acquired = False

    def work(self) -> Dict[str, Any]:
        if not self.stories:
            self.log("No stories provided to process.")
            self.report_progress(100.0, "No stories to process.")
            return {"total": 0, "success": 0, "failed": 0}

        mgr = ThreadManager.get_instance()
        self.log(f"Acquiring browser lock for automation of {len(self.stories)} stories...")
        self.report_progress(0.0, "Waiting for browser lock...")

        # Acquire browser lock to guarantee exclusive access to Chrome profile
        self.lock_acquired = mgr.acquire_browser_lock()
        self.check_cancellation()

        dry_run = self.config.get("dry_run", True)
        mode_str = "DRY-RUN (Safe)" if dry_run else "LIVE"
        self.log(f"Browser lock acquired. Launching Playwright browser in {mode_str} mode...")
        self.report_progress(5.0, "Initializing Readora client...")

        self.client = ReadoraClient(self.config)
        self.client.start_browser()
        self.check_cancellation()

        self.report_progress(10.0, "Logging into Readora...")
        self.client.login()
        self.check_cancellation()

        total = len(self.stories)
        success_count = 0
        failed_count = 0

        for i, story in enumerate(self.stories, 1):
            if self.should_stop():
                self.log(f"Worker stop requested. Halting at story {i}/{total}.")
                break
            self.wait_if_paused()

            story_name = story.get("story_name", "Unknown")
            base_pct = 10.0 + (float(i - 1) / total) * 85.0
            self.report_progress(base_pct, f"[{i}/{total}] Processing '{story_name}'...")
            self.log(f"--- [{i}/{total}] Processing Story: '{story_name}' ---")

            # Check if already processed
            if not dry_run and self.progress_tracker.is_processed(story_name):
                self.log(f"Skipping '{story_name}' - already completed.")
                success_count += 1
                if self.on_story_complete:
                    self.on_story_complete(story, True, "Already completed")
                continue

            success = self._process_single_story(story, i, total, base_pct)
            if success:
                success_count += 1
            else:
                failed_count += 1

            if self.on_story_complete:
                try:
                    self.on_story_complete(story, success, "Success" if success else "Failed")
                except Exception as cb_err:
                    self.log(f"Callback error: {cb_err}", level="WARNING")

            # Small delay between stories
            self.sleep_interruptible(1.5)

        self.report_progress(100.0, f"Completed: {success_count} success, {failed_count} failed.")
        self.log(f"Automation finished. Success: {success_count}, Failed: {failed_count}.")
        return {"total": total, "success": success_count, "failed": failed_count}

    def _process_single_story(self, story: Dict, index: int, total: int, base_pct: float) -> bool:
        story_name = story.get("story_name", "Unknown")
        drive_url = story.get("drive_url")
        dry_run = self.config.get("dry_run", True)

        if not drive_url:
            msg = f"No Drive URL found for '{story_name}'"
            self.log(msg, level="ERROR")
            self.progress_tracker.record_failure(story, msg)
            return False

        # 1. Download docx (or use pre-loaded path)
        if story.get("docx_path") and Path(story["docx_path"]).exists():
            docx_path = Path(story["docx_path"])
            status = "Pre-loaded"
        else:
            pref_lang = self.config.get("preferred_language_file", "First language")
            self.report_progress(base_pct + 2.0, f"[{index}/{total}] Downloading docx for '{story_name}'...")
            docx_path, status = self.downloader.download_story_docx(drive_url, preferred_lang=pref_lang)
            self.check_cancellation()

        if not docx_path or not docx_path.exists():
            self.log(f"Download failed for '{story_name}': {status}", level="ERROR")
            self.progress_tracker.record_failure(story, status)
            return False

        # 2. Parse docx (or use reviewed custom questions)
        if story.get("custom_questions"):
            questions = story["custom_questions"]
            self.log(f"Using {len(questions)} reviewed questions for '{story_name}'")
        else:
            self.report_progress(base_pct + 5.0, f"[{index}/{total}] Parsing docx questions for '{story_name}'...")
            questions = DocxParser.parse_comprehension_questions(docx_path)

            # AI Fallback: If 0 questions or missing answers, and Groq available:
            if not questions and GroqAnswerResolver.is_available() and GroqAnswerResolver.get_api_key(self.config):
                self.log(f"0 questions parsed by rules. Triggering Groq AI full extraction for '{story_name}'...", level="INFO")
                ai_full = GroqAnswerResolver.extract_all_questions_with_groq(docx_path, self.config)
                if ai_full.get("success") and ai_full.get("questions"):
                    questions = ai_full["questions"]
                    self.log(f"⚡ Groq AI successfully extracted {len(questions)} questions from '{docx_path.name}'!")

            has_missing = any(not q.get("answer") or q.get("answer") not in ["A", "B", "C", "D"] for q in questions)
            if has_missing and GroqAnswerResolver.is_available() and GroqAnswerResolver.get_api_key(self.config):
                self.log(f"Resolving inconsistent/missing answer keys via Groq AI for '{story_name}'...", level="INFO")
                ai_res = GroqAnswerResolver.resolve_answers_with_groq(docx_path, questions, self.config)
                if ai_res.get("success"):
                    questions = ai_res["questions"]
                    self.log(f"⚡ Groq AI resolved answer keys! ({ai_res.get('changes_count', 0)} changes)")

            if not questions:
                msg = f"No comprehension questions found in {docx_path.name}"
                self.log(msg, level="ERROR")
                self.progress_tracker.record_failure(story, msg)
                return False
            self.log(f"Parsed {len(questions)} questions from '{docx_path.name}'")

        # 3. Readora Lab interaction
        self.report_progress(base_pct + 8.0, f"[{index}/{total}] Searching book on Readora: '{story_name}'...")
        book_info = self.client.search_book(story_name)
        self.check_cancellation()

        if not book_info:
            msg = f"Book '{story_name}' not found on Readora"
            self.log(msg, level="WARNING")
            self.progress_tracker.record_failure(story, msg)
            return False

        self.report_progress(base_pct + 12.0, f"[{index}/{total}] Editing questions for '{story_name}'...")
        self.client.open_book_edit(book_info)
        self.check_cancellation()

        success = self.client.edit_questions(questions, dry_run=dry_run)
        if success:
            self.progress_tracker.record_success(story, len(questions), dry_run=dry_run)
            mode_tag = "DRY-RUN SUCCESS" if dry_run else "LIVE SUCCESS"
            self.log(f"[{mode_tag}] Successfully processed '{story_name}'!")

            # Google Sheet update with strict login rule verification
            if not dry_run and self.config.get("auto_mark_sheet_done", True):
                try:
                    from modules.google_sheet_updater import GoogleSheetUpdater
                    sheet_updater = GoogleSheetUpdater(self.config)
                    sheet_res = sheet_updater.mark_story_done(story, status_value="Done", dry_run=False)
                    if sheet_res.get("success"):
                        self.log(f"✅ Google Sheet updated: Marked {sheet_res.get('cell')} as Done in tab '{story.get('sheet_name')}'!")
                    elif sheet_res.get("blocked_by_safety_rule"):
                        self.log(f"⚠️ Google Sheet NOT updated: Strict safety rule blocked access (must be logged in as {self.config.get('gmail_account', 'mohrawagdy58@gmail.com')}).", level="WARNING")
                    else:
                        self.log(f"Google Sheet notice: {sheet_res.get('error')}", level="WARNING")
                except Exception as sheet_err:
                    self.log(f"Notice: Google Sheet update encountered: {sheet_err}", level="WARNING")

            return True
        else:
            self.progress_tracker.record_failure(story, "Failed during question editing")
            self.log(f"Failed editing questions for '{story_name}'", level="ERROR")
            return False

    def cleanup(self):
        """Always clean up browser client and release browser lock."""
        if self.client:
            try:
                self.client.close()
                self.log("Readora client closed.")
            except Exception as e:
                self.log(f"Error closing Readora client: {e}", level="WARNING")
            self.client = None

        if self.lock_acquired:
            ThreadManager.get_instance().release_browser_lock()
            self.lock_acquired = False
            self.log("Browser lock released.")


class SheetWatcherWorker(PeriodicWorker):
    """
    Background worker that continuously watches for spreadsheet changes or new downloads.
    Emits an event and triggers an optional callback whenever an update is detected.
    """

    def __init__(
        self,
        interval_seconds: float = 60.0,
        on_sheet_detected: Optional[Callable[[Path], None]] = None,
        name: str = "SheetWatcher",
    ):
        super().__init__(interval_seconds=interval_seconds, name=name, daemon=True)
        self.on_sheet_detected = on_sheet_detected
        self.last_sheet_path: Optional[Path] = None
        self.last_sheet_mtime: float = 0.0

    def work_iteration(self):
        sheet_path = auto_detect_downloads_sheet()
        if not sheet_path or not sheet_path.exists():
            return

        try:
            mtime = sheet_path.stat().st_mtime
            if self.last_sheet_path != sheet_path or mtime > self.last_sheet_mtime:
                self.last_sheet_path = sheet_path
                self.last_sheet_mtime = mtime
                self.log(f"Detected updated sheet file: {sheet_path.name}")
                self.report_progress(100.0, f"Sheet updated: {sheet_path.name}", data={"path": str(sheet_path)})

                if self.on_sheet_detected:
                    try:
                        self.on_sheet_detected(sheet_path)
                    except Exception as e:
                        self.log(f"Error in on_sheet_detected callback: {e}", level="ERROR")
        except Exception as e:
            self.log(f"Error checking sheet: {e}", level="WARNING")


class AutoUpdaterWorker(PeriodicWorker):
    """
    Background worker that checks for software updates periodically.
    """

    def __init__(
        self,
        config: dict,
        interval_seconds: float = 3600.0,
        on_update_available: Optional[Callable[[str], None]] = None,
        name: str = "AutoUpdater",
    ):
        super().__init__(interval_seconds=interval_seconds, name=name, daemon=True)
        self.config = config
        self.updater = AutoUpdater(config)
        self.on_update_available = on_update_available

    def work_iteration(self):
        if not self.config.get("auto_update", True):
            return

        self.log("Checking for updates in background...")
        try:
            has_update, info = self.updater.check_for_updates()
            if has_update:
                desc = info.get("summary") if isinstance(info, dict) else str(info)
                self.log(f"Update available: {desc}")
                self.report_progress(50.0, f"Update available: {desc}", data=info if isinstance(info, dict) else {"desc": desc})
                if self.on_update_available:
                    self.on_update_available(desc if not isinstance(info, dict) else info)

                if self.config.get("auto_update_apply", False):
                    self.log("Applying update automatically...")
                    self.updater.apply_update(info if isinstance(info, dict) else None)
            else:
                self.log("Application is up to date.")
        except Exception as e:
            self.log(f"Update check notice: {e}", level="WARNING")


class ChromeLauncherWorker(BaseWorker):
    """
    Background worker that launches Chrome with Google login and debugging port.
    """

    def __init__(self, config: dict, headless: bool = False, name: str = "ChromeLauncher"):
        super().__init__(name=name, daemon=True)
        self.config = config
        self.headless = headless

    def work(self) -> bool:
        self.log("Launching Chrome in background...")
        self.report_progress(20.0, "Launching Chrome...")
        from modules.google_auth import GoogleAuthenticator
        ga = GoogleAuthenticator(self.config)
        success = ga.launch_and_login(headless=self.headless, open_sheet_after_verified=False)
        self.report_progress(100.0, "Chrome launch finished.")
        return bool(success)
