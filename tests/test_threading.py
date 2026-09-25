"""
Comprehensive Test Suite for Mohra Threading Framework & Concurrency Engine.
"""

import sys
import time
import threading
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from modules.threading_manager import (
    ThreadManager,
    BaseWorker,
    PeriodicWorker,
    CallableWorker,
    WorkerState,
    WorkerEventType,
    run_in_background,
)
from modules.progress import ProgressTracker
from config import load_config, save_config


def test_base_worker_lifecycle():
    print("Testing BaseWorker lifecycle...")
    manager = ThreadManager.get_instance()

    events_received = []
    def on_event(evt):
        events_received.append(evt)
    manager.add_event_listener(on_event)

    class SampleWorker(BaseWorker):
        def __init__(self):
            super().__init__(name="SampleWorkerTest", daemon=True)
            self.cleaned = False

        def work(self):
            self.report_progress(50.0, "Working...")
            self.sleep_interruptible(0.2)
            self.report_progress(100.0, "Finished!")
            return "SUCCESS_RESULT"

        def cleanup(self):
            self.cleaned = True

    worker = SampleWorker()
    manager.register_and_start(worker)
    worker.join(timeout=3.0)

    assert worker.state == WorkerState.COMPLETED, f"Expected COMPLETED, got {worker.state}"
    assert worker.result == "SUCCESS_RESULT"
    assert worker.cleaned is True
    assert worker.progress == 100.0
    print("  ✓ BaseWorker lifecycle and cleanup verified.")


def test_base_worker_cancellation():
    print("Testing BaseWorker cancellation...")
    manager = ThreadManager.get_instance()

    class LongRunningWorker(BaseWorker):
        def work(self):
            for i in range(100):
                self.check_cancellation()
                if not self.sleep_interruptible(0.1):
                    break
            return "DONE"

    worker = LongRunningWorker(name="CancelTestWorker")
    manager.register_and_start(worker)
    time.sleep(0.15)
    assert worker.is_running()

    # Cancel worker
    worker.stop(timeout=2.0)
    assert not worker.is_running()
    assert worker.state in (WorkerState.STOPPED, WorkerState.STOPPING)
    print("  ✓ BaseWorker cancellation verified.")


def test_periodic_worker():
    print("Testing PeriodicWorker...")
    manager = ThreadManager.get_instance()

    class CounterPeriodic(PeriodicWorker):
        def __init__(self):
            super().__init__(interval_seconds=0.1, name="PeriodicTestWorker")
            self.runs = 0

        def work_iteration(self):
            self.runs += 1

    worker = CounterPeriodic()
    manager.register_and_start(worker)
    time.sleep(0.35)
    worker.stop(timeout=2.0)

    assert worker.runs >= 2, f"Expected at least 2 runs, got {worker.runs}"
    assert not worker.is_running()
    print(f"  ✓ PeriodicWorker verified ({worker.runs} iterations ran).")


def test_run_in_background_decorator():
    print("Testing @run_in_background decorator...")

    @run_in_background(name="AsyncSquare")
    def compute_square(n: int):
        time.sleep(0.05)
        return n * n

    w = compute_square(7)
    assert isinstance(w, BaseWorker)
    w.join(timeout=2.0)
    assert w.result == 49
    print("  ✓ @run_in_background decorator verified.")


def test_concurrent_progress_tracker():
    print("Testing concurrent ProgressTracker thread-safety...")
    tracker = ProgressTracker("test_progress.json")

    def worker_entry(idx: int):
        for j in range(10):
            story = {"story_name": f"Story-{idx}-{j}", "sheet_name": "G1", "row_index": j}
            tracker.record_success(story, questions_count=5, dry_run=True)
            tracker.is_processed(f"Story-{idx}-{j}")

    threads = [threading.Thread(target=worker_entry, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    summary = tracker.get_summary()
    assert summary["total_dry_run"] == 50, f"Expected 50 total_dry_run, got {summary['total_dry_run']}"

    # Clean up test file
    Path("test_progress.json").unlink(missing_ok=True)
    print("  ✓ ProgressTracker concurrency and thread-safety verified.")


def test_browser_lock_mutex():
    print("Testing ThreadManager browser lock mutex...")
    manager = ThreadManager.get_instance()

    acquired = manager.acquire_browser_lock(timeout=1.0)
    assert acquired is True

    # Attempt second acquisition with short timeout should fail
    second = manager.acquire_browser_lock(timeout=0.1)
    assert second is False

    manager.release_browser_lock()

    # Now should succeed
    reacquired = manager.acquire_browser_lock(timeout=0.5)
    assert reacquired is True
    manager.release_browser_lock()
    print("  ✓ Browser lock mutex verified.")


def test_key_worker():
    print("Testing KeyWorker execution...")
    import time
    from modules.key import KeyWorker
    worker = KeyWorker(config={"dry_run": True})
    worker.start()
    time.sleep(0.5)
    worker.stop()
    worker.join(timeout=3.0)
    assert worker.state in (WorkerState.COMPLETED, WorkerState.STOPPED)
    print("  ✓ KeyWorker background execution verified.")


if __name__ == "__main__":
    print("==================================================")
    print("  Running Mohra Threading Framework Test Suite")
    print("==================================================")
    test_base_worker_lifecycle()
    test_base_worker_cancellation()
    test_periodic_worker()
    test_run_in_background_decorator()
    test_concurrent_progress_tracker()
    test_browser_lock_mutex()
    test_key_worker()
    print("==================================================")
    print("  All Threading Framework Tests PASSED Successfully!")
    print("==================================================")
