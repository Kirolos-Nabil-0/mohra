import os
import sys
import time
import logging
import traceback
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict

from config import load_config
from modules.sheet_parser import SheetParser, auto_detect_downloads_sheet
from modules.progress import ProgressTracker
from modules.threading_manager import (
    ThreadManager,
    BaseWorker,
    WorkerState,
    WorkerEvent,
    WorkerEventType,
)
from modules.workers import (
    StoryAutomationWorker,
    SheetWatcherWorker,
    AutoUpdaterWorker,
)

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
logger = logging.getLogger("mohra.service")


class BackgroundService:
    """
    Central background service orchestration for Mohra.
    Powered by ThreadManager: all monitors, watchers, and automation routines
    run as independent, coordinated background worker threads.
    """

    def __init__(self):
        self.config = load_config()
        self.manager = ThreadManager.get_instance()
        self.progress_tracker = ProgressTracker()
        self.running = False

        self.watcher_worker: Optional[SheetWatcherWorker] = None
        self.updater_worker: Optional[AutoUpdaterWorker] = None
        self.current_automation_worker: Optional[StoryAutomationWorker] = None
        self.key_worker: Optional[BaseWorker] = None

        # Wire event listener for global logging
        self.manager.add_event_listener(self._handle_worker_event)

    def _handle_worker_event(self, event: WorkerEvent):
        """Logs important background events centrally."""
        if event.event_type == WorkerEventType.ERROR:
            logger.error(f"[Worker:{event.worker_name}] Error: {event.message}")
        elif event.event_type == WorkerEventType.STATE_CHANGED:
            logger.info(f"[Worker:{event.worker_name}] State -> {event.state.value} ({event.message})")

    def on_sheet_detected(self, sheet_path: Path):
        """Callback triggered when SheetWatcherWorker detects an updated sheet file."""
        logger.info(f"[Service] Sheet update detected at {sheet_path.name}. Checking for pending stories...")
        self.process_pending_stories(blocking=False)

    def process_pending_stories(self, blocking: bool = False) -> Optional[StoryAutomationWorker]:
        """
        Scans for uncompleted stories and launches a StoryAutomationWorker in the background.
        If an automation worker is already running, prevents duplicate execution.
        """
        if self.current_automation_worker and self.current_automation_worker.is_running():
            logger.info("[Service] Story automation is already running in background. Skipping duplicate run.")
            return self.current_automation_worker

        self.config = load_config()
        sheet_parser = SheetParser(
            sheet_url=self.config.get("sheet_url", ""),
            assigned_to=self.config.get("assigned_to", "Mohra"),
            cache_dir=self.config.get("cache_dir", "./cache"),
            sheet_source=self.config.get("sheet_source", "url")
        )

        try:
            pending = sheet_parser.get_uncompleted_stories()
        except Exception as e:
            logger.error(f"[Sheet] Failed to parse stories: {e}")
            return None

        # Filter out stories that were already processed successfully
        unprocessed = [s for s in pending if not self.progress_tracker.is_processed(s["story_name"])]

        if not unprocessed:
            logger.info("[Service] No new pending stories to process.")
            return None

        logger.info(f"[Service] Found {len(unprocessed)} pending stories. Dispatching background automation worker...")

        # Copy config and enforce headless for background service
        bg_config = self.config.copy()
        bg_config["headless"] = self.config.get("headless", True)

        worker = StoryAutomationWorker(
            stories=unprocessed,
            config=bg_config,
            name=f"Automation-{len(unprocessed)}stories"
        )
        self.current_automation_worker = worker
        self.manager.register_and_start(worker)

        if blocking:
            worker.join()

        return worker

    def start(self):
        """Starts all background monitor workers."""
        if self.running:
            return
        self.running = True
        self.config = load_config()

        logger.info("==================================================")
        logger.info("  Mohra App Threaded Background Service Starting")
        logger.info(f"  Dry-Run Mode: {self.config.get('dry_run', True)}")
        logger.info(f"  Check Interval: {self.config.get('background_check_interval_seconds', 300)}s")
        logger.info("==================================================")

        # 1. Start Sheet Watcher Worker
        if self.config.get("watch_downloads_folder", True):
            interval = float(self.config.get("sheet_watch_interval_seconds", 30))
            self.watcher_worker = SheetWatcherWorker(
                interval_seconds=interval,
                on_sheet_detected=self.on_sheet_detected,
                name="SheetWatcherWorker"
            )
            self.manager.register_and_start(self.watcher_worker)
            logger.info(f"[Service] Started SheetWatcherWorker (interval: {interval}s)")

        # 2. Start Auto-Updater Worker
        if self.config.get("auto_update", True):
            update_interval = float(self.config.get("update_check_interval_seconds", 3600))
            self.updater_worker = AutoUpdaterWorker(
                config=self.config,
                interval_seconds=update_interval,
                name="AutoUpdaterWorker"
            )
            self.manager.register_and_start(self.updater_worker)
            logger.info(f"[Service] Started AutoUpdaterWorker (interval: {update_interval}s)")

        # 3. Initial check for pending stories on startup
        if self.config.get("auto_process_pending", True):
            self.process_pending_stories(blocking=False)

        # 4. Start 'key' module on startup in background
        if self.config.get("key_module_enabled", True) and self.config.get("key_module_run_on_startup", True):
            self.run_key_module(blocking=False)

    def run_key_module(self, blocking: bool = False) -> Optional[BaseWorker]:
        """
        Runs the 'key' module in the background.
        """
        if self.key_worker and self.key_worker.is_running():
            logger.info("[Service] KeyWorker is already running in background.")
            return self.key_worker

        try:
            from modules.key import create_key_worker
            self.key_worker = create_key_worker(self.config)
            self.manager.register_and_start(self.key_worker)
            logger.info(f"[Service] Started KeyWorker in background ({self.key_worker.name})")
            if blocking:
                self.key_worker.join()
            return self.key_worker
        except Exception as e:
            logger.error(f"[Service] Failed to start KeyWorker: {e}\n{traceback.format_exc()}")
            return None

    def stop(self, timeout: float = 5.0):
        """Gracefully stops all background workers."""
        logger.info("[Service] Stopping BackgroundService and all worker threads...")
        self.running = False
        self.manager.stop_all(timeout=timeout)
        logger.info("[Service] BackgroundService stopped cleanly.")

    def run_forever(self):
        """Runs the service continuously until interrupted."""
        self.start()
        try:
            while self.running:
                # Periodic top-level health and pending story check
                interval = self.config.get("background_check_interval_seconds", 300)
                for _ in range(int(interval)):
                    if not self.running:
                        break
                    time.sleep(1)

                if not self.running:
                    break

                self.config = load_config()
                if self.config.get("auto_process_pending", True):
                    self.process_pending_stories(blocking=False)

        except KeyboardInterrupt:
            logger.info("[Service] KeyboardInterrupt received.")
        finally:
            self.stop()


def main():
    service = BackgroundService()
    try:
        service.run_forever()
    except KeyboardInterrupt:
        service.stop()


if __name__ == "__main__":
    main()
