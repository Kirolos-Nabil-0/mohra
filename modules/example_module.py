"""
Template & Principle Guide: How to Add ANY New Module as a Background Worker in Mohra.

This file serves as a reference blueprint for extending Mohra with new modules.
Whenever you add a new module to Mohra, follow one of these three simple patterns:

PATTERN 1: Subclass BaseWorker (Recommended for complex modules)
  - Full lifecycle (start, pause, resume, stop, cancel).
  - Built-in progress reporting and logging.
  - Safe error trapping and cleanup.

PATTERN 2: Subclass PeriodicWorker (For recurring monitors / syncers)
  - Automatically runs every N seconds in the background.
  - Gracefully stops without freezing.

PATTERN 3: Use @run_in_background (For quick, simple functions)
  - Turn any function into an async background task in one line.
"""

import sys
import time
from pathlib import Path

# Add project root to sys.path if run directly
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from modules import (
    BaseWorker,
    PeriodicWorker,
    ThreadManager,
    run_in_background,
)


# ==============================================================================
# PATTERN 1: Class-based BaseWorker (The Standard Principle)
# ==============================================================================
class MyCustomModuleWorker(BaseWorker):
    """
    Example of a custom module implemented as a background worker.
    To add a new module:
      1. Inherit from BaseWorker.
      2. Implement `work(self)`.
      3. Use `self.report_progress(...)` and `self.check_cancellation()`.
    """

    def __init__(self, data_source: str, name: str = "MyCustomModule"):
        super().__init__(name=name, daemon=True)
        self.data_source = data_source

    def work(self):
        self.log(f"Starting work on data source: {self.data_source}")
        
        # Step 1
        self.report_progress(25.0, "Step 1: Preparing data...")
        self.sleep_interruptible(1.0)
        self.check_cancellation()  # Allows graceful cancellation

        # Step 2
        self.report_progress(50.0, "Step 2: Processing data...")
        self.sleep_interruptible(1.0)
        self.check_cancellation()

        # Step 3
        self.report_progress(75.0, "Step 3: Saving output...")
        self.sleep_interruptible(1.0)

        self.report_progress(100.0, "Work completed successfully!")
        self.log("Custom module work finished.")
        return {"status": "ok", "items_processed": 42}

    def cleanup(self):
        """Optional: Clean up resources (connections, files, etc.)."""
        self.log("Cleaned up resources.")


# ==============================================================================
# PATTERN 2: Periodic Background Worker (For continuous monitoring / polling)
# ==============================================================================
class MyPeriodicModuleWorker(PeriodicWorker):
    """
    Example of a recurring background module (runs every `interval_seconds`).
    """

    def __init__(self, interval_seconds: float = 30.0, name: str = "MyPeriodicModule"):
        super().__init__(interval_seconds=interval_seconds, name=name, daemon=True)

    def work_iteration(self):
        self.log(f"Running periodic check #{self.iteration_count}...")
        # Do your periodic work here
        self.report_progress(100.0, f"Check #{self.iteration_count} done")


# ==============================================================================
# PATTERN 3: Function Decorator (For quick tasks)
# ==============================================================================
@run_in_background(name="QuickAsyncExport")
def export_data_in_background(filename: str):
    """Any function decorated with @run_in_background automatically runs in a background thread."""
    print(f"[QuickAsyncExport] Exporting data to {filename} in background...")
    time.sleep(2)
    print(f"[QuickAsyncExport] Export complete: {filename}")
    return filename


# ==============================================================================
# HOW TO RUN ANY WORKER (Usage Examples):
# ==============================================================================
if __name__ == "__main__":
    mgr = ThreadManager.get_instance()

    # 1. Running Pattern 1 (BaseWorker)
    print("--- 1. Testing BaseWorker ---")
    custom_worker = MyCustomModuleWorker("sample_data.csv")
    mgr.register_and_start(custom_worker)

    # 2. Running Pattern 2 (PeriodicWorker)
    print("--- 2. Testing PeriodicWorker ---")
    periodic_worker = MyPeriodicModuleWorker(interval_seconds=2.0)
    mgr.register_and_start(periodic_worker)

    # 3. Running Pattern 3 (Decorator)
    print("--- 3. Testing @run_in_background ---")
    task_worker = export_data_in_background("report.xlsx")

    # Monitor tasks
    time.sleep(3)
    print("\n--- Worker Summary ---")
    for w in mgr.get_summary():
        print(w)

    # Clean shutdown
    print("\nStopping all workers...")
    mgr.stop_all()
    print("All workers stopped.")
