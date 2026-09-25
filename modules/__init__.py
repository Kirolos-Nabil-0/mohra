# Mohra automation modules
from modules.threading_manager import (
    BaseWorker,
    PeriodicWorker,
    CallableWorker,
    ThreadManager,
    WorkerState,
    WorkerEvent,
    WorkerEventType,
    run_in_background,
)
from modules.telegram_service import Send_tele_msg

# Optional runtime integrations should not prevent standalone utilities such
# as Send_tele_msg from being imported on a machine without browser/input
# automation dependencies installed.
try:
    from modules.workers import (
        StoryAutomationWorker,
        SheetWatcherWorker,
        AutoUpdaterWorker,
        ChromeLauncherWorker,
    )
except ModuleNotFoundError:
    StoryAutomationWorker = None
    SheetWatcherWorker = None
    AutoUpdaterWorker = None
    ChromeLauncherWorker = None

try:
    from modules.key import (
        KeyWorker,
        KeyPeriodicWorker,
        create_key_worker,
    )
except ModuleNotFoundError:
    KeyWorker = None
    KeyPeriodicWorker = None
    create_key_worker = None

__all__ = [
    "BaseWorker",
    "PeriodicWorker",
    "CallableWorker",
    "ThreadManager",
    "WorkerState",
    "WorkerEvent",
    "WorkerEventType",
    "run_in_background",
    "StoryAutomationWorker",
    "SheetWatcherWorker",
    "AutoUpdaterWorker",
    "ChromeLauncherWorker",
    "KeyWorker",
    "KeyPeriodicWorker",
    "create_key_worker",
    "Send_tele_msg",
]
