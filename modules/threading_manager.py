"""
Mohra Threading Architecture & Concurrency Engine.

This module provides the core threading foundation for the entire Mohra application:
- Standardized lifecycle management for background tasks (start, stop, pause, resume, cancel).
- Worker base classes (BaseWorker, PeriodicWorker, CallableWorker) for any module.
- Central ThreadManager singleton for worker registration, monitoring, coordination, and shutdown.
- Thread-safe event bus and progress reporting for UI (Tkinter/Rich/Tray) decoupling.
- Concurrency locks (e.g. Browser lock) to prevent simultaneous Chrome/Playwright collisions.
- Decorator (@run_in_background) for easily turning any function into a background thread.
"""

import time
import uuid
import queue
import logging
import inspect
import functools
import threading
import traceback
from enum import Enum
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable, Optional, Dict, List, Any, Union

logger = logging.getLogger("mohra.threading")


class WorkerState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class WorkerEventType(str, Enum):
    STATE_CHANGED = "STATE_CHANGED"
    PROGRESS = "PROGRESS"
    LOG = "LOG"
    RESULT = "RESULT"
    ERROR = "ERROR"


@dataclass
class WorkerEvent:
    worker_id: str
    worker_name: str
    event_type: WorkerEventType
    state: WorkerState
    progress: float = 0.0
    message: str = ""
    data: Any = None
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


class BaseWorker:
    """
    Abstract Base Class for any Mohra module or task running in the background.

    Subclasses must implement the `work()` method.
    Provides cancellation via `should_stop()` / `check_cancellation()`,
    pause/resume capabilities, progress reporting, and thread-safe callbacks.
    """

    def __init__(self, name: Optional[str] = None, daemon: bool = True):
        self.worker_id: str = uuid.uuid4().hex[:8]
        self.name: str = name or f"{self.__class__.__name__}-{self.worker_id}"
        self.is_daemon: bool = daemon

        self._lock = threading.RLock()
        self._state: WorkerState = WorkerState.IDLE
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # Not paused initially

        self._thread: Optional[threading.Thread] = None
        self.progress: float = 0.0
        self.status_message: str = "Initialized"
        self.result: Any = None
        self.last_error: Optional[Exception] = None

        self.started_at: Optional[float] = None
        self.completed_at: Optional[float] = None

    @property
    def state(self) -> WorkerState:
        with self._lock:
            return self._state

    def _set_state(self, new_state: WorkerState, message: Optional[str] = None):
        with self._lock:
            self._state = new_state
            if message is not None:
                self.status_message = message
        self._emit_event(WorkerEventType.STATE_CHANGED, message=message or f"State changed to {new_state.value}")

    def is_running(self) -> bool:
        with self._lock:
            return self._state == WorkerState.RUNNING and (self._thread is not None and self._thread.is_alive())

    def is_paused(self) -> bool:
        with self._lock:
            return self._state == WorkerState.PAUSED

    def is_finished(self) -> bool:
        with self._lock:
            return self._state in (WorkerState.COMPLETED, WorkerState.STOPPED, WorkerState.ERROR)

    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    def check_cancellation(self):
        """Raises InterruptedError if worker has been requested to stop."""
        if self._stop_event.is_set():
            raise InterruptedError(f"Worker '{self.name}' execution was cancelled.")

    def wait_if_paused(self, check_interval: float = 0.2):
        """Blocks execution while paused until resumed or stopped."""
        while not self._pause_event.is_set():
            if self.should_stop():
                break
            time.sleep(check_interval)

    def sleep_interruptible(self, seconds: float, slice_step: float = 0.1) -> bool:
        """
        Sleeps for `seconds` in small increments, checking `should_stop()` regularly.
        Returns True if full sleep duration completed, False if interrupted by stop.
        """
        end_time = time.time() + seconds
        while time.time() < end_time:
            if self.should_stop():
                return False
            self.wait_if_paused(slice_step)
            step = min(slice_step, end_time - time.time())
            if step > 0:
                time.sleep(step)
        return not self.should_stop()

    def report_progress(self, percent: float, message: str = "", data: Any = None):
        """Reports progress (0.0 to 100.0) safely to any listeners / UI."""
        with self._lock:
            self.progress = max(0.0, min(100.0, percent))
            if message:
                self.status_message = message
        self._emit_event(WorkerEventType.PROGRESS, message=message, data=data)

    def log(self, message: str, level: str = "INFO"):
        """Emits a log event and forwards to standard logging."""
        formatted = f"[{self.name}] {message}"
        if level == "ERROR":
            logger.error(formatted)
        elif level == "WARNING":
            logger.warning(formatted)
        else:
            logger.info(formatted)
        self._emit_event(WorkerEventType.LOG, message=formatted)

    def _emit_event(self, event_type: WorkerEventType, message: Optional[str] = None, data: Any = None, error: Optional[str] = None):
        with self._lock:
            evt = WorkerEvent(
                worker_id=self.worker_id,
                worker_name=self.name,
                event_type=event_type,
                state=self._state,
                progress=self.progress,
                message=message or self.status_message,
                data=data,
                error=error,
            )
        # Notify ThreadManager singleton if active
        ThreadManager.get_instance().emit_event(evt)

    def start(self) -> "BaseWorker":
        """Starts the worker in a dedicated background thread."""
        with self._lock:
            if self.is_running():
                logger.warning(f"Worker '{self.name}' is already running.")
                return self
            self._stop_event.clear()
            self._pause_event.set()
            self.progress = 0.0
            self.last_error = None
            self.result = None
            self.started_at = time.time()
            self.completed_at = None

            self._thread = threading.Thread(
                target=self._run_wrapper,
                name=self.name,
                daemon=self.is_daemon
            )
            self._set_state(WorkerState.RUNNING, "Started")
            self._thread.start()
        return self

    def _run_wrapper(self):
        """Internal wrapper handling execution lifecycle, error catching, and cleanup."""
        try:
            self.on_start()
            self.result = self.work()
            if self.should_stop():
                self._set_state(WorkerState.STOPPED, "Stopped by user")
            else:
                self.progress = 100.0
                self._set_state(WorkerState.COMPLETED, "Completed successfully")
                self.on_success(self.result)
        except InterruptedError as e:
            self._set_state(WorkerState.STOPPED, str(e))
        except Exception as e:
            self.last_error = e
            err_msg = f"{e}\n{traceback.format_exc()}"
            self._set_state(WorkerState.ERROR, f"Error: {e}")
            self.log(f"Worker crashed: {err_msg}", level="ERROR")
            self._emit_event(WorkerEventType.ERROR, message=str(e), error=err_msg)
            self.on_error(e)
        finally:
            self.completed_at = time.time()
            try:
                self.cleanup()
            except Exception as ce:
                self.log(f"Error during cleanup: {ce}", level="WARNING")

    def stop(self, timeout: Optional[float] = None) -> bool:
        """Requests graceful cancellation and optionally joins until thread exits."""
        with self._lock:
            if not self.is_running():
                return True
            self._stop_event.set()
            self._pause_event.set()  # Unpause so it can reach exit point
            self._set_state(WorkerState.STOPPING, "Stopping...")

        if timeout and self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            return not self._thread.is_alive()
        return True

    def pause(self):
        """Pauses worker execution (honored at next check point)."""
        with self._lock:
            if self._state == WorkerState.RUNNING:
                self._pause_event.clear()
                self._set_state(WorkerState.PAUSED, "Paused")

    def resume(self):
        """Resumes paused worker execution."""
        with self._lock:
            if self._state == WorkerState.PAUSED:
                self._pause_event.set()
                self._set_state(WorkerState.RUNNING, "Resumed")

    def join(self, timeout: Optional[float] = None):
        """Waits for the worker thread to finish."""
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    # --- Overridable hooks ---
    def work(self) -> Any:
        """Subclasses MUST implement the actual work here."""
        raise NotImplementedError("Subclasses must implement work()")

    def on_start(self):
        """Optional hook executed right when thread begins."""
        pass

    def on_success(self, result: Any):
        """Optional hook executed upon successful completion."""
        pass

    def on_error(self, error: Exception):
        """Optional hook executed upon unhandled exception."""
        pass

    def cleanup(self):
        """Optional hook executed in `finally` block for resource cleanup."""
        pass


class PeriodicWorker(BaseWorker):
    """
    Worker designed for periodic background routines (watchers, syncers, updates).
    Loops calling `work_iteration()` every `interval_seconds` until stopped.
    """

    def __init__(self, interval_seconds: float, name: Optional[str] = None, daemon: bool = True):
        super().__init__(name=name, daemon=daemon)
        self.interval_seconds: float = max(0.05, float(interval_seconds))
        self.iteration_count: int = 0

    def work(self) -> Any:
        while not self.should_stop():
            self.wait_if_paused()
            if self.should_stop():
                break

            self.iteration_count += 1
            try:
                self.work_iteration()
            except Exception as e:
                self.log(f"Error in iteration #{self.iteration_count}: {e}", level="ERROR")
                # Do not kill the periodic loop on minor iteration errors, just sleep and continue
                time.sleep(1)

            # Interruptible sleep between cycles
            if not self.sleep_interruptible(self.interval_seconds):
                break

    def work_iteration(self):
        """Subclasses implement one single iteration cycle here."""
        raise NotImplementedError("Subclasses must implement work_iteration()")


class CallableWorker(BaseWorker):
    """
    Worker that executes an arbitrary function/callable in the background.
    """

    def __init__(
        self,
        target: Callable,
        args: tuple = (),
        kwargs: Optional[dict] = None,
        name: Optional[str] = None,
        on_success: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        daemon: bool = True
    ):
        target_name = getattr(target, "__name__", "callable")
        worker_name = name or f"Task-{target_name}"
        super().__init__(name=worker_name, daemon=daemon)
        self.target = target
        self.args = args or ()
        self.kwargs = kwargs or {}
        self._on_success_cb = on_success
        self._on_error_cb = on_error

    def work(self) -> Any:
        # Check if target accepts a worker or cancellation parameter
        sig = inspect.signature(self.target)
        call_kwargs = dict(self.kwargs)
        if "worker" in sig.parameters:
            call_kwargs["worker"] = self

        return self.target(*self.args, **call_kwargs)

    def on_success(self, result: Any):
        if self._on_success_cb:
            try:
                self._on_success_cb(result)
            except Exception as e:
                self.log(f"Error in on_success callback: {e}", level="WARNING")

    def on_error(self, error: Exception):
        if self._on_error_cb:
            try:
                self._on_error_cb(error)
            except Exception as e:
                self.log(f"Error in on_error callback: {e}", level="WARNING")


class ThreadManager:
    """
    Central Singleton Thread Manager for Mohra.

    Coordinates all background workers, provides event dispatching,
    shared resource locks (e.g. browser mutex), and application-wide graceful shutdown.
    """

    _instance: Optional["ThreadManager"] = None
    _singleton_lock = threading.RLock()

    @classmethod
    def get_instance(cls) -> "ThreadManager":
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self._lock = threading.RLock()
        self._workers: Dict[str, BaseWorker] = {}
        self._event_queue: queue.Queue = queue.Queue(maxsize=50000)
        self._listeners: List[Callable[[WorkerEvent], None]] = []
        self._browser_lock = threading.Semaphore(1)  # Only 1 browser instance runs automation at a time

    def register(self, worker: BaseWorker) -> str:
        """Registers a worker with the manager."""
        with self._lock:
            self._workers[worker.worker_id] = worker
            logger.debug(f"Registered worker '{worker.name}' ({worker.worker_id})")
            return worker.worker_id

    def start(self, worker_id_or_name: str) -> bool:
        """Starts a registered worker by its ID or Name."""
        worker = self.get(worker_id_or_name)
        if worker:
            worker.start()
            return True
        return False

    def register_and_start(self, worker: BaseWorker) -> BaseWorker:
        """Registers a worker and immediately starts it."""
        self.register(worker)
        worker.start()
        return worker

    def stop(self, worker_id_or_name: str, timeout: Optional[float] = 5.0) -> bool:
        """Gracefully stops a worker."""
        worker = self.get(worker_id_or_name)
        if worker:
            return worker.stop(timeout=timeout)
        return False

    def stop_all(self, timeout: float = 5.0):
        """Signals all running workers to stop and joins them."""
        with self._lock:
            active = [w for w in self._workers.values() if w.is_running()]

        logger.info(f"Stopping all {len(active)} active background workers...")
        for w in active:
            try:
                w.stop()
            except Exception as e:
                logger.error(f"Error stopping worker '{w.name}': {e}")

        # Join with timeout
        end_time = time.time() + timeout
        for w in active:
            remaining = max(0.1, end_time - time.time())
            w.join(timeout=remaining)

        logger.info("All background workers stopped.")

    def get(self, worker_id_or_name: str) -> Optional[BaseWorker]:
        """Finds a worker by ID or by Name."""
        with self._lock:
            if worker_id_or_name in self._workers:
                return self._workers[worker_id_or_name]
            for w in self._workers.values():
                if w.name == worker_id_or_name:
                    return w
        return None

    def list_all(self) -> List[BaseWorker]:
        with self._lock:
            return list(self._workers.values())

    def list_active(self) -> List[BaseWorker]:
        with self._lock:
            return [w for w in self._workers.values() if w.is_running()]

    def get_summary(self) -> List[Dict[str, Any]]:
        """Returns diagnostic status dictionary for all registered workers."""
        with self._lock:
            workers = list(self._workers.values())

        summary = []
        for w in workers:
            uptime = (time.time() - w.started_at) if w.started_at and w.is_running() else (
                (w.completed_at - w.started_at) if w.started_at and w.completed_at else 0
            )
            summary.append({
                "id": w.worker_id,
                "name": w.name,
                "state": w.state.value,
                "progress": round(w.progress, 1),
                "message": w.status_message,
                "uptime_seconds": round(uptime, 1),
                "error": str(w.last_error) if w.last_error else None
            })
        return summary

    def submit_task(
        self,
        target: Callable,
        *args,
        name: Optional[str] = None,
        on_success: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        daemon: bool = True,
        **kwargs
    ) -> CallableWorker:
        """
        Submits any function to run immediately in a managed background thread.
        """
        worker = CallableWorker(
            target=target,
            args=args,
            kwargs=kwargs,
            name=name,
            on_success=on_success,
            on_error=on_error,
            daemon=daemon
        )
        return self.register_and_start(worker)

    def submit_periodic(
        self,
        iteration_func: Callable,
        interval_seconds: float,
        name: Optional[str] = None,
        daemon: bool = True
    ) -> PeriodicWorker:
        """
        Submits a function to run periodically in the background.
        """
        class InlinePeriodic(PeriodicWorker):
            def work_iteration(self):
                iteration_func()

        worker = InlinePeriodic(interval_seconds=interval_seconds, name=name, daemon=daemon)
        return self.register_and_start(worker)

    # --- Event Bus ---
    def emit_event(self, event: WorkerEvent):
        """Thread-safe event broadcast to queue and listeners."""
        try:
            self._event_queue.put_nowait(event)
        except queue.Full:
            pass

        with self._lock:
            listeners = list(self._listeners)

        for listener in listeners:
            try:
                listener(event)
            except Exception as e:
                logger.warning(f"Error in event listener: {e}")

    def add_event_listener(self, listener: Callable[[WorkerEvent], None]):
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_event_listener(self, listener: Callable[[WorkerEvent], None]):
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def get_event_queue(self) -> queue.Queue:
        """Returns the thread-safe queue containing all worker events."""
        return self._event_queue

    # --- Browser Automation Mutex ---
    def acquire_browser_lock(self, timeout: Optional[float] = None) -> bool:
        """Acquires exclusive lock for browser automation."""
        if timeout is None:
            return self._browser_lock.acquire()
        return self._browser_lock.acquire(timeout=timeout)

    def release_browser_lock(self):
        """Releases the browser lock."""
        try:
            self._browser_lock.release()
        except ValueError:
            pass


def run_in_background(name: Optional[str] = None, daemon: bool = True):
    """
    Decorator to execute any function or method asynchronously in the background.
    Returns the created CallableWorker.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            mgr = ThreadManager.get_instance()
            task_name = name or fn.__name__
            return mgr.submit_task(fn, *args, name=task_name, daemon=daemon, **kwargs)
        return wrapper
    return decorator
