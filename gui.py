from __future__ import annotations
import sys
import json
import queue
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Callable
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import ttkbootstrap as tbs
from ttkbootstrap.constants import *

from config import load_config, save_config
from modules.sheet_parser import SheetParser
from modules.drive_downloader import DriveDownloader
from modules.docx_parser import DocxParser
from modules.progress import ProgressTracker
from modules.threading_manager import (
    ThreadManager,
    WorkerState,
    WorkerEventType,
    WorkerEvent,
)
from modules.workers import (
    StoryAutomationWorker,
    ChromeLauncherWorker,
    SheetWatcherWorker,
    AutoUpdaterWorker,
)
from modules.key import KeyWorker
from modules.telegram_service import Send_tele_msg
from modules.gui_review_dialog import DryRunReviewDialog


class MohraAppGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Mohra — Readora Automation Tool")
        self.root.geometry("1260x840")
        self.root.minsize(1020, 680)

        # Set application window icon
        try:
            from config import get_bundle_resource
            icon_ico = get_bundle_resource("assets/app.ico")
            if icon_ico.exists() and sys.platform == "win32":
                self.root.iconbitmap(str(icon_ico))
            else:
                icon_png = get_bundle_resource("assets/app.png")
                if icon_png.exists():
                    img = tk.PhotoImage(file=str(icon_png))
                    self.root.iconphoto(True, img)
        except Exception:
            pass

        self.config = load_config()
        self.manager = ThreadManager.get_instance()
        self.event_queue = self.manager.get_event_queue()

        self.sheet_parser = SheetParser(
            sheet_url=self.config.get("sheet_url", ""),
            assigned_to=self.config.get("assigned_to", "Mohra"),
            cache_dir=self.config.get("cache_dir", "./cache"),
            sheet_source=self.config.get("sheet_source", "url")
        )
        self.downloader = DriveDownloader(cache_dir=self.config.get("cache_dir", "./cache"))
        self.progress_tracker = ProgressTracker()
        self.all_stories = []
        self.active_worker = None

        self._setup_ui()
        self._load_stories_threaded()

        # Start all background monitoring processes and modules automatically on launch
        self._start_background_services()

        # Wire clean exit handler to stop background workers gracefully
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_app)

        # Start periodic polling for background thread events (100ms interval)
        self.root.after(100, self._poll_background_events)

    def _setup_ui(self):
        # ── 0. Branded Header ────────────────────────────────────────────────
        header_frame = tbs.Frame(self.root, bootstyle="dark", padding=(14, 10))
        header_frame.pack(fill=tk.X, padx=0, pady=0)

        tbs.Label(
            header_frame,
            text="🤖  Mohra",
            font=("Segoe UI", 18, "bold"),
            bootstyle="inverse-dark",
        ).pack(side=tk.LEFT, padx=(4, 8))

        tbs.Label(
            header_frame,
            text="Readora Automation Tool",
            font=("Segoe UI", 10),
            bootstyle="inverse-dark",
            foreground="#aaaaaa",
        ).pack(side=tk.LEFT, pady=(6, 0))

        # Story count badges and Settings button (right side of header)
        badge_frame = tbs.Frame(header_frame, bootstyle="dark")
        badge_frame.pack(side=tk.RIGHT, padx=8)

        self.lbl_total = tbs.Label(
            badge_frame, text="Total: –",
            font=("Segoe UI", 9, "bold"),
            bootstyle="inverse-secondary",
            padding=(8, 3),
        )
        self.lbl_total.pack(side=tk.LEFT, padx=4)

        self.lbl_pending = tbs.Label(
            badge_frame, text="Pending: –",
            font=("Segoe UI", 9, "bold"),
            bootstyle="inverse-warning",
            padding=(8, 3),
        )
        self.lbl_pending.pack(side=tk.LEFT, padx=4)

        self.lbl_done = tbs.Label(
            badge_frame, text="Done: –",
            font=("Segoe UI", 9, "bold"),
            bootstyle="inverse-success",
            padding=(8, 3),
        )
        self.lbl_done.pack(side=tk.LEFT, padx=4)

        # Check Updates button
        tbs.Button(
            badge_frame,
            text="🔄  Updates",
            bootstyle="secondary-outline",
            command=self._check_for_updates_ui,
            padding=(8, 3)
        ).pack(side=tk.LEFT, padx=(6, 2))

        # Prominent Settings button right in header
        tbs.Button(
            badge_frame,
            text="⚙️  Settings",
            bootstyle="info-outline",
            command=self._open_settings_dialog,
            padding=(10, 3)
        ).pack(side=tk.LEFT, padx=(6, 4))

        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # ── 1. Configuration & Controls (2 Responsive Rows) ───────────────────
        top_frame = tbs.LabelFrame(
            self.root, text="Configuration & Controls",
            bootstyle="secondary", padding=(12, 8)
        )
        top_frame.pack(fill=tk.X, padx=12, pady=(8, 4))

        # Row 1: Mode, Sheet Path, Google Session Status & Google Actions
        row1_frame = tbs.Frame(top_frame)
        row1_frame.pack(fill=tk.X, pady=(0, 6))

        r1_left = tbs.Frame(row1_frame)
        r1_left.pack(side=tk.LEFT, fill=tk.Y)

        r1_right = tbs.Frame(row1_frame)
        r1_right.pack(side=tk.RIGHT, fill=tk.Y)

        # DRY RUN toggle
        self.dry_run_var = tk.BooleanVar(value=self.config.get("dry_run", True))
        mode_chk = tbs.Checkbutton(
            r1_left,
            text="DRY RUN",
            variable=self.dry_run_var,
            command=self._on_toggle_dry_run,
            bootstyle="success-round-toggle",
        )
        mode_chk.pack(side=tk.LEFT, padx=(0, 6), pady=2)

        self.lbl_mode_indicator = tbs.Label(
            r1_left,
            font=("Segoe UI", 9, "bold"),
        )
        self.lbl_mode_indicator.pack(side=tk.LEFT, padx=(0, 10), pady=2)
        self._update_mode_label()

        # Sheet source label
        src_text = (
            f"📄  {self.sheet_parser.source_type}  ·  "
            f"{self.sheet_parser.xlsx_path.name if self.sheet_parser.xlsx_path else 'None'}"
        )
        self.sheet_lbl = tbs.Label(r1_left, text=src_text, bootstyle="info")
        self.sheet_lbl.pack(side=tk.LEFT, padx=4, pady=2)

        # Google Session Status + Google Actions on Row 1 Right
        self.google_status_lbl = tbs.Label(
            r1_right,
            text="🔑 Google: ⏳ Checking...",
            font=("Segoe UI", 9, "bold"),
            bootstyle="warning"
        )
        self.google_status_lbl.pack(side=tk.LEFT, padx=(0, 8), pady=2)

        tbs.Button(
            r1_right, text="🔑  Sign In Google",
            bootstyle="warning-outline", command=self._open_google_login
        ).pack(side=tk.LEFT, padx=3)

        tbs.Button(
            r1_right, text="📊  Open Sheet",
            bootstyle="secondary-outline", command=self._safe_open_google_sheet
        ).pack(side=tk.LEFT, padx=3)

        # Row 2: Action Buttons & Settings
        row2_frame = tbs.Frame(top_frame)
        row2_frame.pack(fill=tk.X, pady=(2, 0))

        r2_left = tbs.Frame(row2_frame)
        r2_left.pack(side=tk.LEFT, fill=tk.Y)

        r2_right = tbs.Frame(row2_frame)
        r2_right.pack(side=tk.RIGHT, fill=tk.Y)

        tbs.Button(
            r2_left, text="📂  Select Sheet File",
            bootstyle="secondary-outline", command=self._browse_sheet
        ).pack(side=tk.LEFT, padx=(0, 4))

        tbs.Button(
            r2_left, text="☁️  Sync Google Sheets",
            bootstyle="info-outline", command=self._sync_from_google_sheet
        ).pack(side=tk.LEFT, padx=4)

        tbs.Button(
            r2_left, text="🌐  Open Chrome",
            bootstyle="secondary-outline", command=self._launch_chrome
        ).pack(side=tk.LEFT, padx=4)

        tbs.Button(
            r2_right, text="⚙️  Settings",
            bootstyle="secondary-outline", command=self._open_settings_dialog
        ).pack(side=tk.RIGHT, padx=3)

        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=12)

        # ── 2. Filter & Action Bar ───────────────────────────────────────────
        action_frame = tbs.Frame(self.root, padding=(8, 6))
        action_frame.pack(fill=tk.X, padx=12, pady=2)

        tbs.Label(action_frame, text="🔍  Search:", font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *args: self._filter_stories())
        self.search_entry = tbs.Entry(action_frame, textvariable=self.search_var, width=22, bootstyle="dark")
        self.search_entry.pack(side=tk.LEFT, padx=(0, 12))

        tbs.Label(action_frame, text="Filter:", font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self.filter_var = tk.StringVar(value="Pending Only")
        filter_cb = tbs.Combobox(
            action_frame, textvariable=self.filter_var,
            values=["Pending Only", "All Stories", "Completed Only"],
            state="readonly", width=14, bootstyle="dark"
        )
        filter_cb.pack(side=tk.LEFT, padx=(0, 12))
        filter_cb.bind("<<ComboboxSelected>>", lambda e: self._filter_stories())

        # Action buttons (right-aligned, semantic colors)
        tbs.Button(
            action_frame, text="🔄  Refresh",
            bootstyle="secondary-outline", command=self._load_stories_threaded
        ).pack(side=tk.RIGHT, padx=4)

        self.btn_stop = tbs.Button(
            action_frame, text="⏹  Stop Task",
            bootstyle="danger", command=self._stop_current_task, state=tk.DISABLED
        )
        self.btn_stop.pack(side=tk.RIGHT, padx=4)

        self.btn_run_all = tbs.Button(
            action_frame, text="▶▶  Process All Filtered",
            bootstyle="success", command=self._process_all_filtered
        )
        self.btn_run_all.pack(side=tk.RIGHT, padx=4)

        self.btn_run_selected = tbs.Button(
            action_frame, text="▶  Process Selected",
            bootstyle="primary", command=self._process_selected
        )
        self.btn_run_selected.pack(side=tk.RIGHT, padx=4)

        self.btn_review = tbs.Button(
            action_frame, text="🔍  Review & Dry-Run",
            bootstyle="info", command=self._open_dry_run_review
        )
        self.btn_review.pack(side=tk.RIGHT, padx=4)

        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=12, pady=(4, 0))

        # ── 3. Main Treeview ─────────────────────────────────────────────────
        tree_frame = tbs.Frame(self.root, padding=(0, 4))
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=0)

        columns = ("grade", "row", "story", "status", "has_drive")
        self.tree = tbs.Treeview(
            tree_frame, columns=columns, show="headings",
            selectmode="browse", bootstyle="dark"
        )
        self.tree.heading("grade", text="Grade / Sheet")
        self.tree.heading("row", text="Row")
        self.tree.heading("story", text="Story Title")
        self.tree.heading("status", text="Status / Comment")
        self.tree.heading("has_drive", text="Drive Link")

        self.tree.column("grade", width=110, anchor=tk.CENTER)
        self.tree.column("row", width=60, anchor=tk.CENTER)
        self.tree.column("story", width=430)
        self.tree.column("status", width=230)
        self.tree.column("has_drive", width=90, anchor=tk.CENTER)

        # Row color tags
        self.tree.tag_configure("completed", background="#1a3a22", foreground="#7fffa0")
        self.tree.tag_configure("pending",   background="#2a2a2a", foreground="#dddddd")
        self.tree.tag_configure("error",     background="#3a1a1a", foreground="#ff8888")
        self.tree.tag_configure("odd",       background="#252525")
        self.tree.tag_configure("even",      background="#1e1e1e")

        # Double click or Enter to review & dry-run, right-click context menu
        self.tree.bind("<Double-1>", lambda e: self._open_dry_run_review())
        self.tree.bind("<Return>", lambda e: self._open_dry_run_review())
        self.tree.bind("<Button-2>", self._show_tree_context_menu)
        self.tree.bind("<Button-3>", self._show_tree_context_menu)

        # Global keyboard shortcuts: F5 to refresh, Ctrl/Cmd+F to focus search
        self.root.bind("<F5>", lambda e: self._load_stories_threaded())
        self.root.bind("<Control-f>", lambda e: self._focus_search())
        self.root.bind("<Command-f>", lambda e: self._focus_search())

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=12, pady=(4, 0))

        # ── 4. Status & Progress Bar ─────────────────────────────────────────
        status_frame = tbs.Frame(self.root, padding=(8, 6))
        status_frame.pack(fill=tk.X, padx=12, pady=0)

        self.lbl_task_status = tbs.Label(
            status_frame,
            text="💤  Idle",
            font=("Segoe UI", 9, "italic"),
            bootstyle="secondary",
        )
        self.lbl_task_status.pack(side=tk.LEFT, padx=4)

        self.progress_bar = tbs.Progressbar(
            status_frame, orient=tk.HORIZONTAL,
            mode="determinate", length=300, bootstyle="info-striped"
        )
        self.progress_bar.pack(side=tk.RIGHT, padx=5, fill=tk.X, expand=True)

        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=12, pady=(2, 0))

        # ── 5. Activity Logs Console ─────────────────────────────────────────
        log_frame = tbs.LabelFrame(
            self.root, text="  📋  Activity Logs",
            bootstyle="secondary", padding=6
        )
        log_frame.pack(fill=tk.BOTH, expand=False, padx=12, pady=(4, 10))
        log_frame.config(height=180)

        self.log_text = tk.Text(
            log_frame, height=8,
            bg="#0d1117", fg="#58a6ff",
            insertbackground="#58a6ff",
            font=("Consolas", 10),
            relief=tk.FLAT, bd=0,
        )
        self.log_text.tag_configure("error",   foreground="#ff7b72")
        self.log_text.tag_configure("success", foreground="#3fb950")
        self.log_text.tag_configure("warn",    foreground="#d29922")
        self.log_text.tag_configure("info",    foreground="#58a6ff")

        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscroll=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _update_mode_label(self):
        if self.dry_run_var.get():
            self.lbl_mode_indicator.config(
                text="✅  Safe Mode — No DB Writes",
                foreground="#3fb950",
            )
        else:
            self.lbl_mode_indicator.config(
                text="⚠️  LIVE MODE — Database writes ENABLED",
                foreground="#ff7b72",
            )

    def log(self, message: str):
        """Appends log text directly to the UI console with color tagging."""
        msg_lower = message.lower()
        if "[error" in msg_lower or "error" in msg_lower:
            tag = "error"
        elif "success" in msg_lower or "complete" in msg_lower or "done" in msg_lower:
            tag = "success"
        elif "warn" in msg_lower or "dry run" in msg_lower:
            tag = "warn"
        else:
            tag = "info"
        self.log_text.insert(tk.END, message + "\n", tag)
        self.log_text.see(tk.END)

    def _poll_background_events(self):
        """
        Polls the ThreadManager event queue from the main Tkinter thread.
        Guarantees thread-safe UI updates for all background events.
        """
        try:
            while True:
                evt: WorkerEvent = self.event_queue.get_nowait()
                if evt.event_type == WorkerEventType.LOG:
                    self.log(evt.message)
                elif evt.event_type == WorkerEventType.PROGRESS:
                    self.progress_bar["value"] = evt.progress
                    self.lbl_task_status.config(text=f"⚙️  [{evt.worker_name}] {evt.message}")
                elif evt.event_type == WorkerEventType.STATE_CHANGED:
                    if evt.state == WorkerState.RUNNING:
                        self.btn_stop.config(state=tk.NORMAL)
                        self.progress_bar.configure(bootstyle="success-striped")
                        self.lbl_task_status.config(text=f"⚙️  Running: {evt.worker_name} — {evt.message}")
                    elif evt.state == WorkerState.COMPLETED:
                        self.btn_stop.config(state=tk.DISABLED)
                        self.progress_bar.configure(bootstyle="info-striped")
                        self.progress_bar["value"] = 0
                        self.lbl_task_status.config(text=f"✅  Finished: {evt.worker_name}")
                        self.root.after(500, self._filter_stories)
                    elif evt.state == WorkerState.STOPPED:
                        self.btn_stop.config(state=tk.DISABLED)
                        self.progress_bar.configure(bootstyle="warning-striped")
                        self.progress_bar["value"] = 0
                        self.lbl_task_status.config(text=f"⏹  Stopped: {evt.worker_name}")
                        self.root.after(500, self._filter_stories)
                    elif evt.state == WorkerState.ERROR:
                        self.btn_stop.config(state=tk.DISABLED)
                        self.progress_bar.configure(bootstyle="danger-striped")
                        self.progress_bar["value"] = 0
                        self.lbl_task_status.config(text=f"❌  Error: {evt.worker_name} — {evt.message}")
                        self.root.after(500, self._filter_stories)
                elif evt.event_type == WorkerEventType.ERROR:
                    self.log(f"[ERROR in {evt.worker_name}] {evt.message}")
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._poll_background_events)

    def _start_background_services(self):
        """Launches all background monitoring processes and modules automatically."""
        # 1. Telegram Startup Notification
        try:
            Send_tele_msg("🟢 Mohra GUI Application Launched", config=self.config)
        except Exception:
            pass

        # 2. Sheet Watcher Worker (monitors Downloads folder for fresh sheet)
        if self.config.get("watch_downloads_folder", True):
            try:
                interval = float(self.config.get("sheet_watch_interval_seconds", 30))
                watcher = SheetWatcherWorker(
                    interval_seconds=interval,
                    on_sheet_detected=self._on_sheet_auto_detected,
                    name="SheetWatcherWorker"
                )
                self.manager.register_and_start(watcher)
                self.log(f"[Background] SheetWatcherWorker started (watching Downloads every {interval}s).")
            except Exception as e:
                self.log(f"[Background] Notice starting SheetWatcherWorker: {e}", level="WARNING")

        # 3. Auto-Updater Worker (checks for updates in background)
        if self.config.get("auto_update", True):
            try:
                update_interval = float(self.config.get("update_check_interval_seconds", 3600))
                updater = AutoUpdaterWorker(
                    config=self.config,
                    interval_seconds=update_interval,
                    on_update_available=self._on_update_detected_background,
                    name="AutoUpdaterWorker"
                )
                self.manager.register_and_start(updater)
                self.log(f"[Background] AutoUpdaterWorker started (check interval: {update_interval}s).")
            except Exception as e:
                self.log(f"[Background] Notice starting AutoUpdaterWorker: {e}", level="WARNING")

        # 4. Key & Tele Module (if enabled)
        if self.config.get("key_module_enabled", True) and self.config.get("key_module_run_on_startup", True):
            try:
                key_worker = KeyWorker(config=self.config, name="KeyWorker")
                self.manager.register_and_start(key_worker)
                self.log("[Background] KeyWorker started in background.")
            except Exception as e:
                self.log(f"[Background] Notice starting KeyWorker: {e}", level="INFO")

        # 5. Check Google Account session status (Strict Safety Rule verification)
        self.root.after(1200, self._check_google_session_async)
        self.root.after(45000, self._schedule_periodic_google_check)

    def _on_sheet_auto_detected(self, sheet_path: Path):
        self.log(f"[SheetWatcher] Fresh sheet detected: {sheet_path.name}. Auto-loading stories...")
        self.sheet_parser = SheetParser(local_file_path=str(sheet_path))
        self.root.after(0, lambda: self.sheet_lbl.config(text=f"📄  downloads  ·  {sheet_path.name}"))
        self._load_stories_threaded()

    def _on_close_app(self):
        """Stops all background workers gracefully on application exit."""
        self.log("[System] Stopping all background services...")
        try:
            self.manager.stop_all(timeout=2.0)
        except Exception:
            pass
        self.root.destroy()

    def _on_toggle_dry_run(self):
        val = self.dry_run_var.get()
        self.config["dry_run"] = val
        save_config(self.config)
        self._update_mode_label()
        self.log(f"[Config] Dry-Run mode set to: {val}")

    def _browse_sheet(self):
        filepath = filedialog.askopenfilename(
            title="Select Sheet File",
            filetypes=[("Excel Files", "*.xlsx"), ("All Files", "*.*")]
        )
        if filepath:
            self.sheet_parser = SheetParser(local_file_path=filepath)
            self.sheet_lbl.config(text=f"📄  custom  ·  {Path(filepath).name}")
            self._load_stories_threaded()

    def _sync_from_google_sheet(self):
        def worker_func():
            self.log("[Sheet] Syncing latest sheet from Google Sheets URL...")
            try:
                dest = self.sheet_parser.download_sheet(force_refresh=True)
                self.sheet_lbl.config(text=f"📄  google_sheet_url  ·  {dest.name}")
                self.log(f"[Sheet] Successfully downloaded fresh sheet to {dest}.")
                self.all_stories = self.sheet_parser.parse_stories()
                done_count = sum(1 for s in self.all_stories if s["is_done"])
                pending_count = len(self.all_stories) - done_count
                self.log(f"[Sheet] Loaded {len(self.all_stories)} stories ({pending_count} pending, {done_count} completed).")
                self.root.after(0, self._filter_stories)
            except Exception as e:
                self.log(f"[Sheet] Error syncing from Google Sheets: {e}")

        self.manager.submit_task(worker_func, name="SyncGoogleSheetTask")

    def _launch_chrome(self):
        self.log(f"[Chrome] Launching Chrome in background for {self.config.get('gmail_account')}...")
        worker = ChromeLauncherWorker(self.config, headless=False)
        self.manager.register_and_start(worker)
        self.root.after(4000, self._check_google_session_async)

    def _check_google_session_async(self):
        """Asynchronously tests if Chrome has an active session for the assigned Google account."""
        def worker():
            from modules.google_auth import GoogleAuthenticator
            auth = GoogleAuthenticator(self.config)
            is_logged, status_msg = auth.verify_login_status()
            target_acc = self.config.get("gmail_account", "mohrawagdy58@gmail.com")

            def update_ui():
                if is_logged:
                    self.google_status_lbl.config(
                        text=f"🔑 Google: 🟢 {target_acc}",
                        bootstyle="success"
                    )
                else:
                    self.google_status_lbl.config(
                        text=f"🔑 Google: 🔴 Unverified (Protected)",
                        bootstyle="danger"
                    )
            self.root.after(0, update_ui)

        threading.Thread(target=worker, daemon=True).start()

    def _schedule_periodic_google_check(self):
        self._check_google_session_async()
        self.root.after(45000, self._schedule_periodic_google_check)

    def _open_google_login(self):
        """Directs Chrome to Google ServiceLogin without touching the sheet."""
        target_acc = self.config.get("gmail_account", "mohrawagdy58@gmail.com")
        self.log(f"[GoogleAuth] Directing Chrome to Google sign-in for {target_acc}...")
        from modules.chrome_launcher import launch_chrome_for_automation
        profile_dir = str(Path(self.config.get("chrome_profile_dir", "./chrome_profile")).resolve())
        port = self.config.get("remote_debugging_port", 9222)
        launch_chrome_for_automation(port=port, profile_dir=profile_dir, open_url="https://accounts.google.com/ServiceLogin")
        self.root.after(4000, self._check_google_session_async)

    def _safe_open_google_sheet(self):
        """
        Safely opens the Google Sheet in Chrome ONLY if strictly verified as target account.
        STRICT RULE: The sheet will NEVER be opened while unauthenticated!
        """
        from modules.google_auth import GoogleAuthenticator
        auth = GoogleAuthenticator(self.config)
        target_acc = self.config.get("gmail_account", "mohrawagdy58@gmail.com")
        self.log(f"[GoogleSheet] Checking login verification before opening sheet...")

        def worker():
            is_logged, status_msg = auth.verify_login_status()
            if not is_logged:
                self.log(f"[GoogleSheet] 🚫 STRICT RULE BLOCKED: Not logged into {target_acc} ({status_msg}).", level="ERROR")
                def show_err():
                    self._check_google_session_async()
                    messagebox.showerror(
                        "Safety Rule: Access Blocked",
                        f"🚫 STRICT RULE ENFORCED:\n\n"
                        f"The Google Sheet can NEVER be opened while unauthenticated!\n\n"
                        f"Target Account: {target_acc}\n"
                        f"Status: {status_msg}\n\n"
                        f"👉 Please click '🔑 Sign In Google' to log into {target_acc} in Chrome first.",
                        parent=self.root
                    )
                self.root.after(0, show_err)
                return

            self.log(f"[GoogleSheet] ✅ Verified session for {target_acc}. Opening sheet...")
            success, msg = auth.safe_open_sheet()
            if success:
                self.log(f"[GoogleSheet] {msg}")
            else:
                self.log(f"[GoogleSheet] Failed to open: {msg}", level="ERROR")

        threading.Thread(target=worker, daemon=True).start()

    def _mark_story_done_in_sheet(self, story: Dict):
        """Marks Column D of story row as 'Done' in Google Sheet with strict login verification."""
        story_name = story.get("story_name", "Unknown")
        target_acc = self.config.get("gmail_account", "mohrawagdy58@gmail.com")
        self.log(f"[GoogleSheet] Requesting write-back for '{story_name}' (cell D{story.get('row_index')})...")

        def worker():
            from modules.google_sheet_updater import GoogleSheetUpdater
            updater = GoogleSheetUpdater(self.config)
            res = updater.mark_story_done(story, status_value="Done", dry_run=False)

            def on_done():
                if res.get("success"):
                    self.log(f"✅ Marked '{story_name}' as Done in tab '{res.get('sheet_name')}' cell {res.get('cell')}!")
                    story["comment"] = "Done"
                    story["is_done"] = True
                    self._filter_stories()
                    messagebox.showinfo(
                        "Google Sheet Updated",
                        f"✅ Successfully marked '{story_name}' as Done in Google Sheet!\n\n"
                        f"• Sheet Tab: {res.get('sheet_name')}\n"
                        f"• Cell: {res.get('cell')}\n"
                        f"• Value: Done",
                        parent=self.root
                    )
                elif res.get("blocked_by_safety_rule"):
                    self.log(f"[GoogleSheet] 🚫 Blocked by safety rule: Not logged into {target_acc}.", level="ERROR")
                    self._check_google_session_async()
                    messagebox.showerror(
                        "Safety Rule: Write Blocked",
                        f"🚫 STRICT RULE ENFORCED:\n\n"
                        f"Cannot modify the Google Sheet!\n\n"
                        f"You must be logged into Chrome as: {target_acc}\n\n"
                        f"👉 Click '🔑 Sign In Google' to log into {target_acc} first.",
                        parent=self.root
                    )
                else:
                    err = res.get("error", "Unknown error")
                    self.log(f"[GoogleSheet] Error: {err}", level="ERROR")
                    messagebox.showerror("Sheet Update Failed", f"Failed to update Google Sheet:\n\n{err}", parent=self.root)

            self.root.after(0, on_done)

        threading.Thread(target=worker, daemon=True).start()

    def _load_stories_threaded(self):
        def worker_func():
            self.log("[Sheet] Loading stories from sheet...")
            self.all_stories = self.sheet_parser.parse_stories()
            done_count = sum(1 for s in self.all_stories if s["is_done"])
            pending_count = len(self.all_stories) - done_count
            self.log(f"[Sheet] Loaded {len(self.all_stories)} stories ({pending_count} pending, {done_count} completed).")
            self.root.after(0, self._filter_stories)

        self.manager.submit_task(worker_func, name="LoadStoriesTask")

    def _filter_stories(self):
        query = self.search_var.get().strip().lower()
        filter_mode = self.filter_var.get()

        for item in self.tree.get_children():
            self.tree.delete(item)

        visible_total = 0
        visible_pending = 0
        visible_done = 0

        for idx, s in enumerate(self.all_stories):
            is_done = s["is_done"] or self.progress_tracker.is_processed(s["story_name"])
            if filter_mode == "Pending Only" and is_done:
                continue
            if filter_mode == "Completed Only" and not is_done:
                continue
            if query and query not in s["story_name"].lower():
                continue

            has_link = "✅  Yes" if s["drive_url"] else "–"
            if self.progress_tracker.is_processed(s["story_name"]):
                status_text = "✅  Completed (Automated)"
                row_tag = "completed"
            elif s.get("comment", "").lower().startswith("error") or s.get("comment", "").lower().startswith("fail"):
                status_text = f"❌  {s['comment']}"
                row_tag = "error"
            elif is_done:
                status_text = f"✅  {s['comment'] or 'Completed'}"
                row_tag = "completed"
            else:
                status_text = s["comment"] or "Pending"
                row_tag = "pending"

            self.tree.insert("", tk.END, values=(
                s["sheet_name"],
                s["row_index"],
                s["story_name"],
                status_text,
                has_link,
            ), tags=(row_tag,))

            visible_total += 1
            if row_tag == "completed":
                visible_done += 1
            else:
                visible_pending += 1

        # Update header badges
        self.lbl_total.config(text=f"  Total: {visible_total}  ")
        self.lbl_pending.config(text=f"  Pending: {visible_pending}  ")
        self.lbl_done.config(text=f"  Done: {visible_done}  ")

    def _get_selected_story(self):
        selected = self.tree.selection()
        if not selected:
            return None
        values = self.tree.item(selected[0], "values")
        sheet_name, row_idx = values[0], int(values[1])
        for s in self.all_stories:
            if s["sheet_name"] == sheet_name and s["row_index"] == row_idx:
                return s
        return None

    def _on_story_step_complete(self, story: dict, success: bool, msg: str):
        """Called by worker when a single story finishes to update UI immediately."""
        self.progress_tracker = ProgressTracker()
        self.root.after(0, self._filter_stories)

    def _open_dry_run_review(self, story: Optional[Dict] = None):
        """Opens interactive Dry-Run Review & Apply dialog for a story."""
        target = story or self._get_selected_story()
        if not target:
            messagebox.showwarning("Warning", "Please select a story from the list first.")
            return

        self.log(f"[Review] Opening Dry-Run Review Inspector for '{target['story_name']}'...")
        DryRunReviewDialog(
            parent=self.root,
            story=target,
            config=self.config,
            on_applied=self._on_story_applied_from_dialog
        )

    def _on_story_applied_from_dialog(self, story: Dict):
        story_name = story.get("story_name", "")
        self.log(f"[Review] Story '{story_name}' successfully applied live to Readora!")
        self.progress_tracker = ProgressTracker()
        self.root.after(0, self._filter_stories)

    def _show_tree_context_menu(self, event):
        """Shows right-click popup context menu on treeview items."""
        item = self.tree.identify_row(event.y)
        if not item:
            return
        self.tree.selection_set(item)
        story = self._get_selected_story()
        if not story:
            return

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(
            label="🔍 Review & Dry-Run (Inspect & Apply)",
            command=lambda: self._open_dry_run_review(story)
        )
        menu.add_command(
            label="🚀 Accept & Apply Live",
            command=lambda: self._apply_story_directly(story)
        )
        menu.add_command(
            label="📊 Mark Done in Google Sheet",
            command=lambda: self._mark_story_done_in_sheet(story)
        )
        menu.add_separator()
        if story.get("drive_url"):
            menu.add_command(
                label="🌐 Open Google Drive Link",
                command=lambda: self._open_url(story["drive_url"])
            )
        menu.add_command(
            label="📋 Copy Story Name",
            command=lambda: self._copy_to_clipboard(story["story_name"])
        )
        menu.tk_popup(event.x_root, event.y_root)

    def _open_url(self, url: str):
        import webbrowser
        webbrowser.open(url)

    def _copy_to_clipboard(self, text: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.log(f"[Clipboard] Copied '{text}' to clipboard.")

    def _open_settings_dialog(self):
        """Opens a modal to view and update settings, including Groq API Key."""
        dlg = tbs.Toplevel(self.root)
        dlg.title("⚙️ Application Settings")
        dlg.geometry("680x660")
        dlg.transient(self.root)
        dlg.grab_set()

        # Center dialog
        self.root.update_idletasks()
        try:
            x = self.root.winfo_x() + (self.root.winfo_width() - 680) // 2
            y = self.root.winfo_y() + (self.root.winfo_height() - 660) // 2
            dlg.geometry(f"680x660+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        pad_f = tbs.Frame(dlg, padding=16)
        pad_f.pack(fill=tk.BOTH, expand=True)

        tbs.Label(pad_f, text="⚙️  Application & AI Settings", font=("Segoe UI", 13, "bold"), bootstyle="info").pack(anchor=tk.W, pady=(0, 12))

        # Groq API Key section
        groq_box = tbs.LabelFrame(pad_f, text=" ⚡ Groq AI Settings (Auto-Healing & Answer Resolution) ", bootstyle="warning", padding=10)
        groq_box.pack(fill=tk.X, pady=(0, 12))

        tbs.Label(groq_box, text="Groq API Key (free from https://console.groq.com/keys):", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W)

        key_row = tbs.Frame(groq_box)
        key_row.pack(fill=tk.X, pady=(4, 6))

        groq_key_var = tk.StringVar(value=self.config.get("groq_api_key", ""))
        key_entry = tbs.Entry(key_row, textvariable=groq_key_var, show="•", font=("Consolas", 10))
        key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        def toggle_key_show():
            if key_entry.cget("show") == "•":
                key_entry.config(show="")
                btn_eye.config(text="🙈 Hide")
            else:
                key_entry.config(show="•")
                btn_eye.config(text="👁️ Show")

        btn_eye = tbs.Button(key_row, text="👁️ Show", bootstyle="secondary-outline", command=toggle_key_show)
        btn_eye.pack(side=tk.LEFT, padx=3)

        def test_groq_key():
            k = groq_key_var.get().strip()
            if not k:
                messagebox.showwarning("Key Missing", "Please enter a Groq API key first.", parent=dlg)
                return
            btn_test.config(text="⏳ Testing...", state=tk.DISABLED)
            def worker():
                try:
                    import requests
                    headers = {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
                    r = requests.get("https://api.groq.com/openai/v1/models", headers=headers, timeout=10.0)
                    if r.status_code == 200:
                        data = r.json()
                        models = [m.get("id") for m in data.get("data", []) if not m.get("id", "").startswith("whisper")]
                        sample = ", ".join(models[:3]) if models else "Standard models"
                        dlg.after(0, lambda: messagebox.showinfo(
                            "Success",
                            f"✅ Groq API Key is valid and working!\n\nAccessible Models ({len(models)}):\n{sample}...",
                            parent=dlg
                        ))
                    elif r.status_code == 401:
                        dlg.after(0, lambda: messagebox.showerror("Connection Failed", "Invalid Groq API key (401 Unauthorized).\nPlease check the key and try again.", parent=dlg))
                    else:
                        dlg.after(0, lambda: messagebox.showerror("Connection Failed", f"Groq API returned status {r.status_code}:\n{r.text}", parent=dlg))
                except Exception as e:
                    err_msg = str(e)
                    dlg.after(0, lambda msg=err_msg: messagebox.showerror("Connection Failed", f"Groq API Error:\n{msg}", parent=dlg))
                finally:
                    dlg.after(0, lambda: btn_test.config(text="🧪 Test Key", state=tk.NORMAL))
            threading.Thread(target=worker, daemon=True).start()

        btn_test = tbs.Button(key_row, text="🧪 Test Key", bootstyle="info-outline", command=test_groq_key)
        btn_test.pack(side=tk.LEFT, padx=3)

        # Google & Sheet Interaction section (Strict Protection)
        google_box = tbs.LabelFrame(
            pad_f,
            text=" 🛡️ Google Authentication & Sheet Interaction (Strict Protection) ",
            bootstyle="danger",
            padding=10
        )
        google_box.pack(fill=tk.X, pady=(0, 12))

        tbs.Label(
            google_box,
            text="Target Gmail Account (Must match logged-in Chrome session):",
            font=("Segoe UI", 9, "bold")
        ).grid(row=0, column=0, sticky=tk.W, pady=3)

        gmail_acc_var = tk.StringVar(value=self.config.get("gmail_account", "mohrawagdy58@gmail.com"))
        tbs.Entry(google_box, textvariable=gmail_acc_var, width=32).grid(row=0, column=1, sticky=tk.W, pady=3, padx=6)

        auto_sheet_var = tk.BooleanVar(value=self.config.get("auto_mark_sheet_done", True))
        tbs.Checkbutton(
            google_box,
            text="Auto-mark task as 'Done' in Google Sheet upon Readora success",
            variable=auto_sheet_var,
            bootstyle="success-round-toggle"
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=4)

        # Verification status row inside dialog
        status_row = tbs.Frame(google_box)
        status_row.grid(row=2, column=0, columnspan=2, fill=tk.X, pady=(4, 2))

        dlg_g_status = tbs.Label(
            status_row,
            text="Status: ⏳ Checking session...",
            font=("Segoe UI", 9, "bold"),
            bootstyle="warning"
        )
        dlg_g_status.pack(side=tk.LEFT, padx=(0, 10))

        def check_dlg_google_status():
            dlg_g_status.config(text="Status: ⏳ Checking Chrome...", bootstyle="warning")
            def worker():
                from modules.google_auth import GoogleAuthenticator
                cfg = self.config.copy()
                cfg["gmail_account"] = gmail_acc_var.get().strip()
                auth = GoogleAuthenticator(cfg)
                is_logged, msg = auth.verify_login_status()
                def update():
                    if is_logged:
                        dlg_g_status.config(text=f"Status: 🟢 Logged in as {cfg['gmail_account']}", bootstyle="success")
                    else:
                        dlg_g_status.config(text=f"Status: 🔴 Not Logged In ({msg})", bootstyle="danger")
                dlg.after(0, update)
            threading.Thread(target=worker, daemon=True).start()

        tbs.Button(status_row, text="🔄 Verify Session", bootstyle="secondary-outline", command=check_dlg_google_status).pack(side=tk.LEFT, padx=3)
        tbs.Button(status_row, text="🔑 Sign In in Chrome", bootstyle="warning-outline", command=self._open_google_login).pack(side=tk.LEFT, padx=3)

        check_dlg_google_status()

        tbs.Label(
            google_box,
            text="⚠️ STRICT RULE: Under NO circumstances will Google Sheet be opened or modified without verified login.",
            font=("Segoe UI", 8, "italic"),
            bootstyle="danger"
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))

        # General Settings section
        gen_box = tbs.LabelFrame(pad_f, text=" 📖 General & Readora Settings ", bootstyle="secondary", padding=10)
        gen_box.pack(fill=tk.BOTH, expand=True, pady=(0, 12))

        # Question Header
        tbs.Label(gen_box, text="Default Question Header:").grid(row=0, column=0, sticky=tk.W, pady=4)
        header_var = tk.StringVar(value=self.config.get("question_header", "Choose the correct answer "))
        tbs.Entry(gen_box, textvariable=header_var, width=35).grid(row=0, column=1, sticky=tk.W, pady=4, padx=6)

        # Readora Email
        tbs.Label(gen_box, text="Readora Login Email:").grid(row=1, column=0, sticky=tk.W, pady=4)
        email_var = tk.StringVar(value=self.config.get("readora_email", ""))
        tbs.Entry(gen_box, textvariable=email_var, width=35).grid(row=1, column=1, sticky=tk.W, pady=4, padx=6)

        # Readora Password
        tbs.Label(gen_box, text="Readora Password:").grid(row=2, column=0, sticky=tk.W, pady=4)
        pass_var = tk.StringVar(value=self.config.get("readora_password", ""))
        tbs.Entry(gen_box, textvariable=pass_var, show="•", width=35).grid(row=2, column=1, sticky=tk.W, pady=4, padx=6)

        # Bottom buttons
        btn_bar = tbs.Frame(pad_f)
        btn_bar.pack(fill=tk.X, pady=(6, 0))

        def save_and_close():
            self.config["groq_api_key"] = groq_key_var.get().strip()
            self.config["gmail_account"] = gmail_acc_var.get().strip()
            self.config["auto_mark_sheet_done"] = auto_sheet_var.get()
            self.config["question_header"] = header_var.get()
            self.config["readora_email"] = email_var.get().strip()
            self.config["readora_password"] = pass_var.get().strip()
            save_config(self.config)
            self._check_google_session_async()
            self.log("[Settings] Configuration updated and saved to config.json.")
            messagebox.showinfo("Saved", "Settings successfully saved!", parent=dlg)
            dlg.destroy()

        tbs.Button(btn_bar, text="💾 Save Settings", bootstyle="success", command=save_and_close).pack(side=tk.RIGHT, padx=4)
        tbs.Button(btn_bar, text="Cancel", bootstyle="secondary-outline", command=dlg.destroy).pack(side=tk.RIGHT, padx=4)

    def _apply_story_directly(self, story: Dict):
        if not messagebox.askyesno("Confirm Live Apply", f"Apply '{story['story_name']}' directly to Readora in LIVE mode?"):
            return
        run_config = self.config.copy()
        run_config["dry_run"] = False
        run_config["headless"] = self.config.get("headless", False)

        worker = StoryAutomationWorker(
            stories=[story],
            config=run_config,
            name=f"LiveApply-{story['story_name'][:15]}",
            on_story_complete=self._on_story_step_complete
        )
        self.active_worker = worker
        self.manager.register_and_start(worker)

    def _process_selected(self):
        story = self._get_selected_story()
        if not story:
            messagebox.showwarning("Warning", "Please select a story from the list first.")
            return

        dry_run = self.dry_run_var.get()
        if dry_run:
            # In Dry-Run mode, open interactive review dialog directly
            self._open_dry_run_review(story)
            return

        mode_str = "LIVE UPDATE"
        if not messagebox.askyesno("Confirm", f"Process '{story['story_name']}' in {mode_str} mode in the background?"):
            return

        run_config = self.config.copy()
        run_config["dry_run"] = False
        run_config["headless"] = self.config.get("headless", False)

        worker = StoryAutomationWorker(
            stories=[story],
            config=run_config,
            name=f"Story-{story['story_name'][:15]}",
            on_story_complete=self._on_story_step_complete
        )
        self.active_worker = worker
        self.manager.register_and_start(worker)

    def _process_all_filtered(self):
        stories_to_run = []
        for item in self.tree.get_children():
            values = self.tree.item(item, "values")
            sheet_name, row_idx = values[0], int(values[1])
            for s in self.all_stories:
                if s["sheet_name"] == sheet_name and s["row_index"] == row_idx:
                    stories_to_run.append(s)
                    break

        if not stories_to_run:
            messagebox.showinfo("Info", "No stories match current filter.")
            return

        dry_run = self.dry_run_var.get()
        mode_str = "DRY RUN (Safe)" if dry_run else "LIVE UPDATE"
        if not messagebox.askyesno("Confirm", f"Process all {len(stories_to_run)} stories in {mode_str} mode in the background?"):
            return

        run_config = self.config.copy()
        run_config["dry_run"] = dry_run
        run_config["headless"] = self.config.get("headless", False)

        worker = StoryAutomationWorker(
            stories=stories_to_run,
            config=run_config,
            name=f"Batch-{len(stories_to_run)}stories",
            on_story_complete=self._on_story_step_complete
        )
        self.active_worker = worker
        self.manager.register_and_start(worker)

    def _on_update_detected_background(self, info):
        """Called by background AutoUpdaterWorker when a new release is discovered."""
        summary = info.get("summary") if isinstance(info, dict) else str(info)
        self.log(f"[Updater] Background notification: {summary}")
        # Subtle prompt on main thread
        def _notify():
            if isinstance(info, dict) and info.get("has_update"):
                ver = info.get("latest_version", "")
                if messagebox.askyesno("Update Available", f"A new version of Mohra (v{ver}) is available!\n\nWould you like to install it now?", parent=self.root):
                    self._perform_update_download(info)
        self.root.after(0, _notify)

    def _check_for_updates_ui(self):
        """Manually checks for software updates in a background thread."""
        self.log("[Updater] Checking for software updates...")

        def _worker():
            from modules.updater import AutoUpdater, get_current_version
            updater = AutoUpdater(self.config)
            has_update, info = updater.check_for_updates()

            def _show_result():
                if has_update:
                    ver = info.get("latest_version", "New")
                    curr = get_current_version()
                    title = info.get("title", "")
                    notes = info.get("notes", "")
                    msg = f"A new version of Mohra is available!\n\nLatest Version: v{ver}\nCurrent Version: v{curr}\n"
                    if title:
                        msg += f"\nRelease: {title}\n"
                    if notes:
                        notes_preview = notes[:260] + ("..." if len(notes) > 260 else "")
                        msg += f"\nChanges:\n{notes_preview}\n"
                    msg += "\nWould you like to download and install this update now?"

                    if messagebox.askyesno("Update Available", msg, parent=self.root):
                        self._perform_update_download(info)
                else:
                    curr = get_current_version()
                    messagebox.showinfo("Up to Date", f"You are running the latest version of Mohra (v{curr}).", parent=self.root)

            self.root.after(0, _show_result)

        threading.Thread(target=_worker, daemon=True).start()

    def _perform_update_download(self, update_info: dict):
        """Downloads and applies update with a visual progress modal."""
        dlg = tbs.Toplevel(self.root)
        dlg.title("Installing Update")
        dlg.geometry("440x190")
        dlg.transient(self.root)
        dlg.grab_set()

        # Center dialog
        try:
            x = self.root.winfo_x() + (self.root.winfo_width() - 440) // 2
            y = self.root.winfo_y() + (self.root.winfo_height() - 190) // 2
            dlg.geometry(f"440x190+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        pad = tbs.Frame(dlg, padding=16)
        pad.pack(fill=tk.BOTH, expand=True)

        tbs.Label(pad, text="🚀 Mohra Auto-Update", font=("Segoe UI", 12, "bold"), bootstyle="info").pack(anchor=tk.W, pady=(0, 6))

        lbl_status = tbs.Label(pad, text="Preparing download...", font=("Segoe UI", 9))
        lbl_status.pack(anchor=tk.W, pady=(4, 8))

        prog = tbs.Progressbar(pad, mode="determinate", bootstyle="success-striped")
        prog.pack(fill=tk.X, pady=(4, 12))

        def _apply_worker():
            from modules.updater import AutoUpdater
            updater = AutoUpdater(self.config)

            def _on_prog(pct, msg):
                def _ui():
                    prog['value'] = pct
                    lbl_status.config(text=msg)
                self.root.after(0, _ui)

            success = updater.apply_update(update_info, on_progress=_on_prog)
            if not success:
                def _fail():
                    dlg.destroy()
                    messagebox.showerror("Update Failed", "Could not apply update automatically. Please check logs/updater.log.", parent=self.root)
                self.root.after(0, _fail)

        threading.Thread(target=_apply_worker, daemon=True).start()

    def _stop_current_task(self):
        """Cancels any running background worker."""
        if self.active_worker and self.active_worker.is_running():
            self.log(f"[Action] Requesting cancellation of '{self.active_worker.name}'...")
            self.active_worker.stop()
            self.btn_stop.config(state=tk.DISABLED)
        else:
            active = self.manager.list_active()
            for w in active:
                self.log(f"[Action] Stopping worker: {w.name}")
                w.stop()
            self.btn_stop.config(state=tk.DISABLED)


def main():
    root = tbs.Window(themename="darkly")
    app = MohraAppGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
