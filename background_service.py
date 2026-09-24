import os
import sys
import time
import logging
import traceback
from pathlib import Path
from datetime import datetime

from config import load_config
from modules.sheet_parser import SheetParser, auto_detect_downloads_sheet
from modules.drive_downloader import DriveDownloader
from modules.docx_parser import DocxParser
from modules.progress import ProgressTracker
from modules.readora_client import ReadoraClient
from modules.updater import AutoUpdater

# Ensure logs dir exists
LOG_DIR = Path("./logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "background.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

class BackgroundService:
    def __init__(self):
        self.config = load_config()
        self.running = True
        self.last_sheet_mtime = 0
        self.last_sheet_path = None
        self.progress_tracker = ProgressTracker()
        self.downloader = DriveDownloader(cache_dir=self.config.get("cache_dir", "./cache"))
        self.updater = AutoUpdater(self.config)
        self.last_update_check = 0

    def check_sheet_update(self) -> bool:
        """Returns True if a new or updated sheet file is detected in Downloads or cache."""
        sheet_path = auto_detect_downloads_sheet()
        if not sheet_path or not sheet_path.exists():
            return False

        try:
            mtime = sheet_path.stat().st_mtime
            if self.last_sheet_path != sheet_path or mtime > self.last_sheet_mtime:
                logging.info(f"[Watcher] Detected sheet file update: {sheet_path.name} (mtime: {mtime})")
                self.last_sheet_mtime = mtime
                self.last_sheet_path = sheet_path
                return True
        except Exception as e:
            logging.error(f"[Watcher] Error checking sheet file: {e}")
        return False

    def process_pending_stories(self):
        """Scans for pending stories and processes them sequentially."""
        self.config = load_config()
        sheet_parser = SheetParser(
            sheet_url=self.config.get("sheet_url", ""),
            assigned_to=self.config.get("assigned_to", "Mohra"),
            cache_dir=self.config.get("cache_dir", "./cache")
        )

        try:
            pending = sheet_parser.get_uncompleted_stories()
        except Exception as e:
            logging.error(f"[Sheet] Failed to parse stories: {e}")
            return

        # Filter out stories that were already processed successfully
        unprocessed = [s for s in pending if not self.progress_tracker.is_processed(s["story_name"])]

        if not unprocessed:
            logging.info("[Service] No new pending stories to process.")
            return

        logging.info(f"[Service] Found {len(unprocessed)} pending stories to process.")

        # Ensure headless is True for silent background execution
        bg_config = self.config.copy()
        bg_config["headless"] = self.config.get("headless", True)

        client = None
        try:
            client = ReadoraClient(bg_config)
            client.start_browser()
            client.login()

            for story in unprocessed:
                if not self.running:
                    logging.info("[Service] Stop requested, exiting story loop.")
                    break

                story_name = story["story_name"]
                logging.info(f"--- Processing: '{story_name}' ({story.get('sheet_name')} - Row {story.get('row_index')}) ---")

                # Download docx
                docx_path, status = self.downloader.download_story_docx(
                    story["drive_url"],
                    preferred_lang=bg_config.get("preferred_language_file", "First language")
                )
                if not docx_path or not docx_path.exists():
                    logging.error(f"Failed to download docx for '{story_name}': {status}")
                    self.progress_tracker.record_failure(story, status)
                    continue

                # Parse questions
                qs = DocxParser.parse_comprehension_questions(docx_path)
                if not qs:
                    logging.error(f"No comprehension questions extracted for '{story_name}'")
                    self.progress_tracker.record_failure(story, "No questions extracted")
                    continue

                logging.info(f"Extracted {len(qs)} questions for '{story_name}'. Interacting with Readora...")

                # Search book
                book_info = client.search_book(story_name)
                if not book_info:
                    logging.warning(f"Book '{story_name}' not found on Readora Lab.")
                    self.progress_tracker.record_failure(story, "Not found on Readora")
                    continue

                # Edit questions
                client.open_book_edit(book_info)
                success = client.edit_questions(qs, dry_run=bg_config.get("dry_run", True))

                if success:
                    self.progress_tracker.record_success(story, len(qs), dry_run=bg_config.get("dry_run", True))
                    mode_str = "DRY-RUN" if bg_config.get("dry_run", True) else "LIVE"
                    logging.info(f"[{mode_str} SUCCESS] Successfully processed '{story_name}'.")
                else:
                    self.progress_tracker.record_failure(story, "Edit questions failed")

                # Small delay between stories
                time.sleep(2)

        except Exception as e:
            logging.error(f"[ReadoraClient] Critical error during batch processing: {e}\n{traceback.format_exc()}")
        finally:
            if client:
                try:
                    client.close()
                except Exception:
                    pass

    def check_auto_update(self):
        """Silently checks for updates and applies them if enabled."""
        if not self.config.get("auto_update", True):
            return

        now = time.time()
        interval = self.config.get("update_check_interval_seconds", 3600)
        if now - self.last_update_check < interval:
            return

        self.last_update_check = now
        logging.info("[Updater] Checking for software updates...")
        try:
            self.updater.config = self.config
            applied = self.updater.check_and_apply_update_silently()
            if applied:
                logging.info("[Updater] Update applied! Restarting service...")
                self.running = False
        except Exception as e:
            logging.error(f"[Updater] Error during auto-update check: {e}")

    def run_forever(self):
        logging.info("==================================================")
        logging.info("  Mohra App Background Service Started")
        logging.info("  Runs continuously and auto-checks for updates")
        logging.info(f"  Dry-Run Mode: {self.config.get('dry_run', True)}")
        logging.info(f"  Check Interval: {self.config.get('background_check_interval_seconds', 300)} seconds")
        logging.info("==================================================")

        # Initial check on startup
        self.check_auto_update()
        self.check_sheet_update()
        if self.config.get("auto_process_pending", True):
            self.process_pending_stories()

        while self.running:
            try:
                interval = self.config.get("background_check_interval_seconds", 300)
                # Sleep in short increments so we can exit cleanly
                for _ in range(interval):
                    if not self.running:
                        break
                    time.sleep(1)

                if not self.running:
                    break

                # Reload config in case user changed it in GUI/config.json
                self.config = load_config()

                # Check for software updates
                self.check_auto_update()

                sheet_updated = False
                if self.config.get("watch_downloads_folder", True):
                    sheet_updated = self.check_sheet_update()

                if sheet_updated or self.config.get("auto_process_pending", True):
                    logging.info("[Service] Periodic cycle: Checking for pending stories...")
                    self.process_pending_stories()

            except Exception as e:
                logging.error(f"[Service Crash Prevention] Caught top-level error: {e}\n{traceback.format_exc()}")
                logging.info("[Service] Resuming in 30 seconds...")
                time.sleep(30)

        logging.info("[Service] Background worker stopped gracefully.")

    def stop(self):
        logging.info("[Service] Stop signal received.")
        self.running = False

def main():
    service = BackgroundService()
    try:
        service.run_forever()
    except KeyboardInterrupt:
        service.stop()

if __name__ == "__main__":
    main()
