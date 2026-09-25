# Mohra App — Threading Architecture & Background Worker Guide

Mohra is architected with **threading as a foundational principle**. Every module, task, and service in Mohra is built to run asynchronously in background threads without blocking user interfaces, freezing the operating system, or causing race conditions.

---

## 🏛️ Core Principles

1. **Non-Blocking by Default**:
   Any operation that involves I/O, network requests, browser automation (Playwright), document parsing, or file watching executes on a background thread.
2. **Standardized Worker Lifecycle**:
   Every worker adheres to a predictable lifecycle:
   ```
   [IDLE] ───> [RUNNING] ───> [COMPLETED]
                 │   │
                 │   ├───> [PAUSED] ───> [RUNNING]
                 │
                 ├───> [STOPPING] ───> [STOPPED]
                 │
                 └───> [ERROR]
   ```
3. **Thread-Safe Shared State**:
   - `config.json` is protected with `threading.RLock()`.
   - `progress.json` (`ProgressTracker`) is protected with `threading.RLock()`.
   - Browser automation uses a semaphore mutex (`ThreadManager.acquire_browser_lock()`) to ensure multiple background threads never launch colliding Chrome profile sessions.
4. **Decoupled UI Updates**:
   Tkinter and console interfaces do not interact directly from background threads. Background threads emit typed `WorkerEvent` messages to a thread-safe queue, which the UI consumes on the main thread via `root.after()`.

---

## 🛠️ The 3 Patterns to Add ANY New Module to the Background

### Pattern 1: Subclass `BaseWorker` (Standard / Recommended)
Best for long-running tasks, multi-step workflows, or modules that need live progress reporting and cancellation support.

```python
from modules import BaseWorker, ThreadManager

class MyNewModuleWorker(BaseWorker):
    def __init__(self, target_data: str, name: str = "MyNewModule"):
        super().__init__(name=name, daemon=True)
        self.target_data = target_data

    def work(self):
        self.log("Worker started processing...")

        # Step 1
        self.report_progress(25.0, "Step 1: Ingesting data...")
        self.sleep_interruptible(2.0)
        self.check_cancellation()  # Checks if user clicked 'Stop'

        # Step 2
        self.report_progress(75.0, "Step 2: Processing...")
        self.sleep_interruptible(2.0)
        self.check_cancellation()

        # Step 3
        self.report_progress(100.0, "Done!")
        self.log("Worker completed successfully.")
        return {"result": "success"}

    def cleanup(self):
        """Optional: Clean up connections, file handles, or temporary files."""
        self.log("Cleaned up resources.")

# How to launch in background:
worker = MyNewModuleWorker("sample_input")
ThreadManager.get_instance().register_and_start(worker)
```

---

### Pattern 2: Subclass `PeriodicWorker` (For Watchers, Syncers & Polling)
Best for modules that need to run continuously every *N* seconds in the background (e.g. folder watchers, periodic API checks, backup syncers).

```python
from modules import PeriodicWorker, ThreadManager

class MyPeriodicPollerWorker(PeriodicWorker):
    def __init__(self, interval_seconds: float = 60.0):
        super().__init__(interval_seconds=interval_seconds, name="MyPoller", daemon=True)

    def work_iteration(self):
        """Executed automatically every `interval_seconds` until stopped."""
        self.log(f"Running periodic poll #{self.iteration_count}...")
        # Check files, poll APIs, etc.
        self.report_progress(100.0, f"Poll #{self.iteration_count} completed")

# How to launch in background:
poller = MyPeriodicPollerWorker(interval_seconds=30.0)
ThreadManager.get_instance().register_and_start(poller)
```

---

### Pattern 3: Use `@run_in_background` (For Quick Tasks)
Best for simple standalone functions or export tasks that don't need a dedicated class.

```python
import time
from modules import run_in_background

@run_in_background(name="QuickPdfExporter")
def export_pdf_in_background(story_title: str):
    time.sleep(3)
    print(f"Exported {story_title} to PDF!")
    return f"{story_title}.pdf"

# Running it immediately starts a background thread and returns a CallableWorker
worker = export_pdf_in_background("The Friendly Polar Bear")
```

Or using `submit_task` directly:
```python
from modules import ThreadManager

def my_task(x, y):
    return x + y

worker = ThreadManager.get_instance().submit_task(my_task, 10, 20, name="Calculation")
```

---

## 🎛️ Controlling & Monitoring Workers via `ThreadManager`

The central singleton `ThreadManager` provides full lifecycle visibility and control:

```python
from modules import ThreadManager

mgr = ThreadManager.get_instance()

# 1. List all active background workers
active_workers = mgr.list_active()

# 2. Get diagnostic summary table
summary = mgr.get_summary()
for item in summary:
    print(item["name"], item["state"], item["progress"], item["uptime_seconds"])

# 3. Stop a specific worker
mgr.stop("MyNewModule", timeout=5.0)

# 4. Stop ALL background workers gracefully (e.g. on app exit)
mgr.stop_all(timeout=5.0)

# 5. Listen to background events (LOG, PROGRESS, STATE_CHANGED, ERROR)
def on_event(event):
    print(f"[{event.worker_name}] {event.event_type.value}: {event.message}")

mgr.add_event_listener(on_event)
```

---

## 📦 Existing Workers in Mohra

| Worker Class | Module | Type | Purpose |
| :--- | :--- | :--- | :--- |
| `StoryAutomationWorker` | `modules/workers.py` | `BaseWorker` | Runs Drive download, docx parsing, and Readora Lab browser automation with live progress & cancellation. |
| `SheetWatcherWorker` | `modules/workers.py` | `PeriodicWorker` | Background monitor detecting new/updated Excel sheets in Downloads / cache. |
| `AutoUpdaterWorker` | `modules/workers.py` | `PeriodicWorker` | Background worker that checks for Git / Zip software updates. |
| `ChromeLauncherWorker` | `modules/workers.py` | `BaseWorker` | Background launcher for Google Chrome with remote debugging. |

---

## 🛡️ Best Practices for New Modules

1. **Always check cancellation**:
   In any loop or before expensive steps, call `self.check_cancellation()`. This ensures that when the user clicks **Stop** in the GUI or exits the app, the background worker halts promptly without hanging.
2. **Use interruptible sleep**:
   Instead of `time.sleep(10)`, use `self.sleep_interruptible(10.0)`. It wakes immediately if the worker is stopped.
3. **Playwright isolation**:
   Playwright sync APIs (`sync_playwright`) must be initialized and closed within the *same* thread. Creating a dedicated `BaseWorker` for browser automation guarantees this requirement.
4. **Use ThreadManager lock for shared resources**:
   Use `ThreadManager.get_instance().acquire_browser_lock()` if your module interacts with the user's Chrome automation profile.
