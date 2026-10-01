"""
Interactive Dry-Run Review, Diff & Approval Dialog for Mohra GUI.

Features:
- Side-by-Side Old (Readora Lab) vs New (Docx) Comparison
- Real-time diff detection (Answer changes, Rubric modifications, New additions)
- Full question & choices inspector and editor
- Safe dry-run testing with live browser verification
- Accept & Apply live execution
"""

import sys
import os
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import ttkbootstrap as tbs
from ttkbootstrap.constants import *

from config import save_config
from modules.review_service import (
    prepare_story_review,
    fetch_story_readora_state,
    execute_dry_run_test,
    execute_accept_and_apply
)
from modules.ai_groq_parser import GroqAnswerResolver
from modules.docx_parser import DocxParser
from modules.gui_drive_file_picker import DriveFilePicker
from modules.threading_manager import ThreadManager
from modules.progress import ProgressTracker


class DryRunReviewDialog(tbs.Toplevel):
    def __init__(
        self,
        parent: tk.Tk,
        story: Dict,
        config: dict,
        on_applied: Optional[Callable[[Dict], None]] = None
    ):
        super().__init__(parent)
        self.parent = parent
        self.story = story
        self.config = config.copy()
        self.on_applied = on_applied

        self.questions: List[Dict] = []           # New questions (from docx, editable)
        self.comprehension_questions: List[Dict] = []
        self.vocab_questions: List[Dict] = []
        self.vocab_present = False
        self._vocab_ignored = False
        self.old_state: Optional[Dict[str, Any]] = None  # Old questions (from Readora Lab)
        self.old_comprehension_state: Optional[Dict[str, Any]] = None
        self.old_vocab_state: Optional[Dict[str, Any]] = None
        self.docx_path: Optional[str] = None
        self.selected_q_idx: int = 0
        self.selected_diff_idx: int = 0
        self.is_executing = False
        self._loading_editor = False
        self._preparation_request = 0
        self._picker_suggestion = None

        story_title = story.get("story_name", "Story")
        self.title(f"🔍 Dry-Run Review & Diff — {story_title}")
        self.geometry("1180x820")
        self.minsize(980, 680)
        self.transient(parent)
        self.grab_set()

        # Handle close and escape gracefully
        self.protocol("WM_DELETE_WINDOW", self._on_close_window)
        self.bind("<Escape>", lambda e: self._on_close_window())

        # Center on parent window
        self._center_window()

        self._build_ui()
        self._start_async_preparation()

    def safe_after(self, ms: int, func, *args):
        """Safely schedule a callback only if this window is still alive."""
        try:
            if self.winfo_exists():
                self.after(ms, func, *args)
        except Exception:
            pass

    def _on_close_window(self):
        if self.is_executing:
            if not messagebox.askyesno(
                "Operation In Progress",
                "A Readora operation is currently running in the background.\nAre you sure you want to dismiss this window?",
                parent=self
            ):
                return
        self.destroy()

    def _center_window(self):
        self.update_idletasks()
        try:
            pw = self.parent.winfo_width()
            ph = self.parent.winfo_height()
            px = self.parent.winfo_x()
            py = self.parent.winfo_y()
            w = 1180
            h = 820
            x = px + max(0, (pw - w) // 2)
            y = py + max(0, (ph - h) // 2)
            self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

    def _build_ui(self):
        story_name = self.story.get("story_name", "Unknown Story")
        sheet_name = self.story.get("sheet_name", "Sheet")
        row_idx = self.story.get("row_index", "?")

        # ── 1. Top Header Bar ────────────────────────────────────────────────
        hdr_frame = tbs.Frame(self, bootstyle="dark", padding=(16, 10))
        hdr_frame.pack(fill=tk.X, padx=0, pady=0)

        tbs.Label(
            hdr_frame,
            text=f"📖  {story_name}",
            font=("Segoe UI", 14, "bold"),
            bootstyle="inverse-dark"
        ).pack(side=tk.LEFT, padx=(0, 10))

        badges_frame = tbs.Frame(hdr_frame, bootstyle="dark")
        badges_frame.pack(side=tk.RIGHT)

        tbs.Label(
            badges_frame,
            text=f"Grade: {sheet_name}",
            font=("Segoe UI", 9, "bold"),
            bootstyle="inverse-info",
            padding=(8, 3)
        ).pack(side=tk.LEFT, padx=3)

        tbs.Label(
            badges_frame,
            text=f"Row: {row_idx}",
            font=("Segoe UI", 9, "bold"),
            bootstyle="inverse-secondary",
            padding=(8, 3)
        ).pack(side=tk.LEFT, padx=3)

        self.lbl_old_badge = tbs.Label(
            badges_frame,
            text="🔴 Readora: Checking...",
            font=("Segoe UI", 9, "bold"),
            bootstyle="inverse-warning",
            padding=(8, 3)
        )
        self.lbl_old_badge.pack(side=tk.LEFT, padx=3)

        self.lbl_new_badge = tbs.Label(
            badges_frame,
            text="🟢 Docx: Parsing...",
            font=("Segoe UI", 9, "bold"),
            bootstyle="inverse-primary",
            padding=(8, 3)
        )
        self.lbl_new_badge.pack(side=tk.LEFT, padx=3)

        # ── 2. Old vs New Comparison Banner Card ─────────────────────────────
        comparison_card = tbs.Frame(self, padding=(12, 8), bootstyle="secondary")
        comparison_card.pack(fill=tk.X, padx=12, pady=(6, 4))

        # Left Column: Old State (Readora Lab)
        old_card_col = tbs.Frame(comparison_card, bootstyle="secondary")
        old_card_col.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 8))

        tbs.Label(
            old_card_col,
            text="🔴 CURRENT IN READORA LAB (OLD)",
            font=("Segoe UI", 9, "bold"),
            bootstyle="inverse-secondary"
        ).pack(anchor=tk.W)

        self.lbl_old_summary = tbs.Label(
            old_card_col,
            text="Questions: Fetching...  ·  Header: --  ·  Type: --",
            font=("Segoe UI", 9),
            bootstyle="inverse-secondary"
        )
        self.lbl_old_summary.pack(anchor=tk.W, pady=(2, 0))

        # Center Action: Fetch / Refresh Button
        center_col = tbs.Frame(comparison_card, bootstyle="secondary")
        center_col.pack(side=tk.LEFT, padx=8)

        self.btn_fetch_readora = tbs.Button(
            center_col,
            text="🔄 Fetch Readora State",
            bootstyle="info-outline",
            command=self._fetch_readora_state_async
        )
        self.btn_fetch_readora.pack(pady=2)

        # Right Column: New State (Docx to Apply)
        new_card_col = tbs.Frame(comparison_card, bootstyle="secondary")
        new_card_col.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(8, 4))

        tbs.Label(
            new_card_col,
            text="🟢 INCOMING FROM DOCX (NEW TO APPLY)",
            font=("Segoe UI", 9, "bold"),
            bootstyle="inverse-secondary"
        ).pack(anchor=tk.W)

        hdr_setting = self.config.get("question_header", "Choose the correct answer ").strip()
        self.lbl_new_summary = tbs.Label(
            new_card_col,
            text=f"Questions: Parsing docx...  ·  Target Header: «{hdr_setting}»  ·  Type: «None»",
            font=("Segoe UI", 9),
            bootstyle="inverse-secondary"
        )
        btn_box = tbs.Frame(new_card_col, bootstyle="secondary")
        btn_box.pack(anchor=tk.E, pady=(2, 0))

        self.btn_ai_resolve = tbs.Button(
            btn_box,
            text="⚡ AI Resolve Keys (Groq)",
            bootstyle="warning-outline",
            command=self._on_ai_resolve_answers,
            state=tk.DISABLED
        )
        self.btn_ai_resolve.pack(side=tk.LEFT, padx=3)

        self.btn_open_docx = tbs.Button(
            btn_box,
            text="📂 Open Docx File",
            bootstyle="info-outline",
            command=self._open_docx_externally,
            state=tk.DISABLED
        )
        self.btn_open_docx.pack(side=tk.LEFT, padx=3)

        self.btn_choose_docx = tbs.Button(
            btn_box,
            text="Choose Drive file",
            bootstyle="info-outline",
            command=self._open_drive_file_picker,
        )
        self.btn_choose_docx.pack(side=tk.LEFT, padx=3)

        section_bar = tbs.Frame(self, padding=(12, 3))
        section_bar.pack(fill=tk.X)
        tbs.Label(section_bar, text="Review section:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 8))
        self.section_var = tk.StringVar(value="Comprehension")
        self.section_picker = ttk.Combobox(section_bar, textvariable=self.section_var,
                                           values=("Comprehension", "Vocabulary Quiz"), state="readonly", width=22)
        self.section_picker.pack(side=tk.LEFT)
        self.section_picker.bind("<<ComboboxSelected>>", self._on_section_changed)
        self.lbl_vocab_status = tbs.Label(section_bar, text="Vocabulary: loading", bootstyle="secondary")
        self.lbl_vocab_status.pack(side=tk.LEFT, padx=12)

        self.btn_toggle_vocab = tbs.Button(
            section_bar,
            text="Ignore Vocabulary Quiz",
            bootstyle="warning-outline",
            command=self._toggle_ignore_vocab,
        )
        # Packed conditionally in _refresh_validation

        # ── 3. Notebook Tabs: [Diff (Old vs New)] | [New Editor] | [Old Readora] ──
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

        # Tab 1: ⚖️ Old vs New Comparison (Diff)
        self.tab_diff = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(self.tab_diff, text="  ⚖️  Old vs New Diff (Comparison)  ")

        # Tab 2: 🟢 New Questions & Editor
        self.tab_editor = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(self.tab_editor, text="  🟢  New Questions Editor (Docx)  ")

        # Tab 3: 🔴 Current Readora Questions
        self.tab_old_view = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(self.tab_old_view, text="  🔴  Current Readora Questions  ")

        self._build_tab_diff(self.tab_diff)
        self._build_tab_editor(self.tab_editor)
        self._build_tab_old_view(self.tab_old_view)

        # ── 4. Status, Progress & Live Feedback ──────────────────────────────
        status_bar = tbs.Frame(self, padding=(12, 6))
        status_bar.pack(fill=tk.X, padx=12, pady=2)

        self.lbl_status = tbs.Label(
            status_bar,
            text="⏳ Loading story docx and inspecting Readora Lab questions...",
            font=("Segoe UI", 9, "italic"),
            bootstyle="secondary"
        )
        self.lbl_status.pack(side=tk.LEFT)

        self.pbar = tbs.Progressbar(status_bar, mode="indeterminate", length=220, bootstyle="info-striped")
        self.pbar.pack(side=tk.RIGHT)
        self.pbar.start(10)

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=12, pady=4)

        # ── 5. Action Buttons Bar ────────────────────────────────────────────
        btn_frame = tbs.Frame(self, padding=(14, 10))
        btn_frame.pack(fill=tk.X, padx=12, pady=(0, 8))

        self.lbl_btn_hint = tbs.Label(
            btn_frame,
            text="",
            font=("Segoe UI", 9, "italic"),
            bootstyle="warning"
        )
        self.lbl_btn_hint.pack(side=tk.LEFT, padx=6)

        self.btn_apply = tbs.Button(
            btn_frame,
            text="🚀  Accept & Apply to Readora",
            bootstyle="success",
            command=self._on_accept_and_apply,
            state=tk.DISABLED
        )
        self.btn_apply.pack(side=tk.RIGHT, padx=6)

        self.btn_dry_run_test = tbs.Button(
            btn_frame,
            text="🧪  Dry-Run Test Only",
            bootstyle="warning-outline",
            command=self._on_test_dry_run_only,
            state=tk.DISABLED
        )
        self.btn_dry_run_test.pack(side=tk.RIGHT, padx=6)

        self.btn_dismiss = tbs.Button(
            btn_frame,
            text="❌  Dismiss",
            bootstyle="secondary",
            command=self.destroy
        )
        self.btn_dismiss.pack(side=tk.RIGHT, padx=6)

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 1: OLD VS NEW DIFF BUILDER
    # ══════════════════════════════════════════════════════════════════════════
    def _build_tab_diff(self, parent: ttk.Frame):
        # Upper half: Diff Overview Treeview
        tree_frame = tbs.LabelFrame(parent, text=" Questions Comparison Matrix (Old Readora vs New Docx) ", bootstyle="info", padding=6)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        diff_cols = ("num", "old_q", "old_ans", "new_q", "new_ans", "diff")
        self.diff_tree = tbs.Treeview(
            tree_frame, columns=diff_cols, show="headings",
            selectmode="browse", bootstyle="dark", height=7
        )
        self.diff_tree.heading("num", text="#")
        self.diff_tree.heading("old_q", text="🔴 Current Readora Question (Old)")
        self.diff_tree.heading("old_ans", text="🔴 Old Key")
        self.diff_tree.heading("new_q", text="🟢 Incoming Docx Question (New)")
        self.diff_tree.heading("new_ans", text="🟢 New Key")
        self.diff_tree.heading("diff", text="Diff / Action")

        self.diff_tree.column("num", width=36, anchor=tk.CENTER)
        self.diff_tree.column("old_q", width=330)
        self.diff_tree.column("old_ans", width=65, anchor=tk.CENTER)
        self.diff_tree.column("new_q", width=330)
        self.diff_tree.column("new_ans", width=65, anchor=tk.CENTER)
        self.diff_tree.column("diff", width=140, anchor=tk.CENTER)

        self.diff_tree.tag_configure("modified", foreground="#ffbb33")
        self.diff_tree.tag_configure("ans_changed", foreground="#ff6b6b", font=("Segoe UI", 9, "bold"))
        self.diff_tree.tag_configure("identical", foreground="#7fffa0")
        self.diff_tree.tag_configure("new_q", foreground="#33b5e5")
        self.diff_tree.tag_configure("odd", background="#222222")
        self.diff_tree.tag_configure("even", background="#1a1a1a")

        diff_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.diff_tree.yview)
        self.diff_tree.configure(yscroll=diff_scroll.set)
        self.diff_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        diff_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.diff_tree.bind("<<TreeviewSelect>>", self._on_diff_row_selected)

        # Lower half: Side-by-Side Detailed Comparison Cards
        detail_container = ttk.Frame(parent)
        detail_container.pack(fill=tk.BOTH, expand=True)

        # Left Detail: Old Question Card
        self.diff_left_card = tbs.LabelFrame(detail_container, text=" 🔴 Current on Readora Lab (OLD) ", bootstyle="danger", padding=8)
        self.diff_left_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))

        self.lbl_diff_old_rubric = tk.Text(self.diff_left_card, height=3, font=("Segoe UI", 9), bg="#1e1e1e", fg="#dddddd", relief=tk.FLAT)
        self.lbl_diff_old_rubric.pack(fill=tk.X, pady=(0, 6))

        self.diff_old_choices_frame = tbs.Frame(self.diff_left_card)
        self.diff_old_choices_frame.pack(fill=tk.BOTH, expand=True)

        self.lbl_diff_old_ans = tbs.Label(self.diff_left_card, text="Answer: --", font=("Segoe UI", 9, "bold"), bootstyle="danger")
        self.lbl_diff_old_ans.pack(anchor=tk.W, pady=(4, 0))

        # Right Detail: New Question Card
        self.diff_right_card = tbs.LabelFrame(detail_container, text=" 🟢 Incoming from Docx (NEW TO APPLY) ", bootstyle="success", padding=8)
        self.diff_right_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0))

        self.lbl_diff_new_rubric = tk.Text(self.diff_right_card, height=3, font=("Segoe UI", 9), bg="#1e1e1e", fg="#ffffff", relief=tk.FLAT)
        self.lbl_diff_new_rubric.pack(fill=tk.X, pady=(0, 6))

        self.diff_new_choices_frame = tbs.Frame(self.diff_right_card)
        self.diff_new_choices_frame.pack(fill=tk.BOTH, expand=True)

        self.lbl_diff_new_ans = tbs.Label(self.diff_right_card, text="Answer: --", font=("Segoe UI", 9, "bold"), bootstyle="success")
        self.lbl_diff_new_ans.pack(anchor=tk.W, pady=(4, 0))

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 2: NEW QUESTIONS & EDITOR BUILDER
    # ══════════════════════════════════════════════════════════════════════════
    def _build_tab_editor(self, parent: ttk.Frame):
        workspace_frame = ttk.Frame(parent)
        workspace_frame.pack(fill=tk.BOTH, expand=True)

        # Left Side: Questions List
        left_frame = tbs.LabelFrame(workspace_frame, text=" Questions Manager ", bootstyle="info", width=360, padding=8)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(0, 8))
        left_frame.pack_propagate(False)

        tree_frame = tbs.Frame(left_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        q_cols = ("num", "snippet", "ans")
        self.q_tree = tbs.Treeview(
            tree_frame, columns=q_cols, show="headings",
            selectmode="browse", bootstyle="dark"
        )
        self.q_tree.heading("num", text="#")
        self.q_tree.heading("snippet", text="Question Rubric")
        self.q_tree.heading("ans", text="Ans Key")

        self.q_tree.column("num", width=36, anchor=tk.CENTER)
        self.q_tree.column("snippet", width=240)
        self.q_tree.column("ans", width=55, anchor=tk.CENTER)

        self.q_tree.tag_configure("valid", foreground="#7fffa0")
        self.q_tree.tag_configure("odd", background="#222222")
        self.q_tree.tag_configure("even", background="#1a1a1a")

        q_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.q_tree.yview)
        self.q_tree.configure(yscroll=q_scroll.set)
        self.q_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        q_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.q_tree.bind("<<TreeviewSelect>>", self._on_question_selected)

        # Question Action Toolbar
        tb1 = tbs.Frame(left_frame)
        tb1.pack(fill=tk.X, pady=(6, 2))

        self.btn_add_q = tbs.Button(
            tb1,
            text="➕ Add Question",
            bootstyle="success-outline",
            command=self._on_add_question
        )
        self.btn_add_q.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        self.btn_del_q = tbs.Button(
            tb1,
            text="🗑️ Delete",
            bootstyle="danger-outline",
            command=self._on_delete_question
        )
        self.btn_del_q.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

        tb2 = tbs.Frame(left_frame)
        tb2.pack(fill=tk.X, pady=(2, 0))

        self.btn_paste_import = tbs.Button(
            tb2,
            text="📋 Paste Questions",
            bootstyle="info-outline",
            command=self._open_paste_importer_modal
        )
        self.btn_paste_import.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        self.btn_view_docx_text = tbs.Button(
            tb2,
            text="📄 View Docx",
            bootstyle="secondary-outline",
            command=self._open_docx_text_viewer
        )
        self.btn_view_docx_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

        # Right Side: Detailed Question Inspector & Editor
        right_frame = tbs.LabelFrame(workspace_frame, text=" Question Inspector & Editor (Make adjustments here) ", bootstyle="primary", padding=12)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tbs.Label(right_frame, text="Question Rubric Text:", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W, pady=(0, 2))
        self.txt_q_rubric = tk.Text(right_frame, height=3, font=("Segoe UI", 10), bg="#1e1e1e", fg="#ffffff", relief=tk.FLAT)
        self.txt_q_rubric.pack(fill=tk.X, pady=(0, 8))
        self.txt_q_rubric.bind("<KeyRelease>", lambda e: self._on_editor_changed())

        # Choices A, B, C, D Frame
        choices_frame = tbs.Frame(right_frame)
        choices_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        self.choice_vars = {}
        self.ans_var = tk.StringVar(value="A")

        tbs.Label(choices_frame, text="Choices & Answer Key Selection:", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W, pady=(2, 6))

        for letter in ["A", "B", "C", "D"]:
            row = tbs.Frame(choices_frame)
            row.pack(fill=tk.X, pady=3)

            rbtn = tbs.Radiobutton(
                row,
                text=f"{letter}.",
                value=letter,
                variable=self.ans_var,
                command=self._on_editor_changed,
                bootstyle="success-toolbutton"
            )
            rbtn.pack(side=tk.LEFT, padx=(0, 6))

            var = tk.StringVar()
            var.trace_add("write", lambda *args: self._on_editor_changed())
            self.choice_vars[letter] = var

            entry = tbs.Entry(row, textvariable=var, font=("Segoe UI", 10))
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.lbl_selected_ans_hint = tbs.Label(
            right_frame,
            text="Selected Correct Answer: Option A",
            font=("Segoe UI", 9, "bold"),
            bootstyle="success"
        )
        self.lbl_selected_ans_hint.pack(anchor=tk.W, pady=(4, 0))

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 3: CURRENT READORA QUESTIONS BUILDER
    # ══════════════════════════════════════════════════════════════════════════
    def _build_tab_old_view(self, parent: ttk.Frame):
        tab_frame = ttk.Frame(parent)
        tab_frame.pack(fill=tk.BOTH, expand=True)

        top_bar = tbs.Frame(tab_frame, padding=4)
        top_bar.pack(fill=tk.X)

        self.lbl_tab_old_meta = tbs.Label(
            top_bar,
            text="Readora Lab: Current server state.",
            font=("Segoe UI", 9, "bold"),
            bootstyle="danger"
        )
        self.lbl_tab_old_meta.pack(side=tk.LEFT)

        cols = ("num", "rubric", "ans")
        self.old_tree = tbs.Treeview(
            tab_frame, columns=cols, show="headings",
            selectmode="browse", bootstyle="dark"
        )
        self.old_tree.heading("num", text="#")
        self.old_tree.heading("rubric", text="Existing Question on Readora")
        self.old_tree.heading("ans", text="Ans")

        self.old_tree.column("num", width=38, anchor=tk.CENTER)
        self.old_tree.column("rubric", width=700)
        self.old_tree.column("ans", width=60, anchor=tk.CENTER)

        old_scroll = ttk.Scrollbar(tab_frame, orient=tk.VERTICAL, command=self.old_tree.yview)
        self.old_tree.configure(yscroll=old_scroll.set)
        self.old_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        old_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    # ══════════════════════════════════════════════════════════════════════════
    # ASYNC PREPARATION & FETCHING
    # ══════════════════════════════════════════════════════════════════════════
    def _start_async_preparation(self):
        """Prepares docx parsing and starts background Readora inspection."""
        self._preparation_request += 1
        request = self._preparation_request
        story = self.story.copy()
        def docx_worker():
            res = prepare_story_review(
                story,
                self.config,
                download_if_missing=True,
                on_status=lambda msg: self.safe_after(0, self._update_status, msg)
            )
            self.safe_after(0, self._on_docx_preparation_done, request, res)

        threading.Thread(target=docx_worker, daemon=True).start()

    def _open_drive_file_picker(self):
        drive_url = self.story.get("drive_url")
        if not drive_url:
            messagebox.showerror("Drive link missing", "This story has no Google Drive folder link.", parent=self)
            return
        DriveFilePicker(
            self, drive_url, self.story.get("story_name", "Story"),
            self.config.get("cache_dir", "./cache"), self._on_drive_file_chosen,
            ai_suggestion=self._picker_suggestion,
        )

    def _toggle_ignore_vocab(self):
        """Allows user to skip/ignore an unvalidated or empty Vocabulary Quiz to proceed with Comprehension."""
        self._vocab_ignored = not self._vocab_ignored
        if self._vocab_ignored:
            self.section_var.set("Comprehension")
            self._on_section_changed()
        self._refresh_validation()

    def _on_drive_file_chosen(self, selection):
        self.story["selected_drive_file"] = selection
        self._picker_suggestion = None
        self.story.pop("docx_path", None)
        self.questions = []
        self.comprehension_questions = []
        self.vocab_questions = []
        self.vocab_present = False
        self._vocab_ignored = False
        self.docx_path = None
        self.btn_apply.config(state=tk.DISABLED)
        self.btn_dry_run_test.config(state=tk.DISABLED)
        self.btn_open_docx.config(state=tk.DISABLED)
        self.lbl_new_badge.config(text="Docx: Loading selected file", bootstyle="inverse-primary")
        self.lbl_status.config(text=f"Loading selected file: {selection['name']}…", bootstyle="info")
        self.pbar.pack(side=tk.RIGHT)
        self.pbar.start(10)
        self._start_async_preparation()

    def _fetch_readora_state_async(self):
        """Fetches current questions from Readora Lab in background."""
        self.btn_fetch_readora.config(state=tk.DISABLED, text="⏳ Fetching...")
        self.lbl_old_badge.config(text="🔴 Fetching...", bootstyle="inverse-warning")

        def worker():
            res = fetch_story_readora_state(
                self.story,
                self.config,
                on_status=lambda s: self.safe_after(0, self._update_status, s)
            )
            self.safe_after(0, self._on_readora_state_fetched, res)

        threading.Thread(target=worker, daemon=True).start()

    def _on_readora_state_fetched(self, res: Dict[str, Any]):
        self.btn_fetch_readora.config(state=tk.NORMAL, text="🔄 Refresh Readora")

        if res.get("success") and "old_state" in res:
            self.old_comprehension_state = res["old_state"]
            self.old_vocab_state = res.get("old_vocab_state")
            self.old_state = self.old_vocab_state if self.section_var.get() == "Vocabulary Quiz" else self.old_comprehension_state
            old_q_list = self.old_state.get("questions", [])
            old_count = len(old_q_list)
            old_hdr = self.old_state.get("header", "None")
            old_type = self.old_state.get("content_type", "None")

            self.lbl_old_badge.config(
                text=f"🔴 Readora: {old_count} Questions",
                bootstyle="inverse-danger" if old_count > 0 else "inverse-secondary"
            )
            self.lbl_old_summary.config(
                text=f"Questions: {old_count} currently on server  ·  Header: «{old_hdr}»  ·  Type: «{old_type}»"
            )
            self.lbl_tab_old_meta.config(
                text=f"Readora Lab: Book '{res.get('matched_title', '')}' has {old_count} questions. (Header: «{old_hdr}», Type: «{old_type}»)"
            )

            # Populate Old Treeview
            for item in self.old_tree.get_children():
                self.old_tree.delete(item)
            for i, q in enumerate(old_q_list):
                tag = "even" if i % 2 == 0 else "odd"
                self.old_tree.insert(
                    "", tk.END, iid=f"old_{i}",
                    values=(str(q.get("num", i+1)), q.get("question", ""), f"[{q.get('answer', '')}]"),
                    tags=(tag,)
                )

            # Refresh Diff matrix
            self._populate_diff_tree()
            self._update_status(f"✅ Readora state loaded: {old_count} existing questions.")
        else:
            err = res.get("error", "Could not fetch Readora state")
            self.lbl_old_badge.config(text="🔴 Readora: Not Loaded", bootstyle="inverse-secondary")
            self.lbl_old_summary.config(text=f"⚠️ {err}")

    def _update_status(self, msg: str):
        self.lbl_status.config(text=msg)

    def _on_docx_preparation_done(self, request: int, res: Dict[str, Any]):
        if request != self._preparation_request:
            return
        self.pbar.stop()
        self.pbar.pack_forget()

        if not res["success"]:
            self.lbl_status.config(text=f"❌ Error: {res.get('error')}", bootstyle="danger")
            self.lbl_new_badge.config(text="Docx Failed", bootstyle="inverse-danger")
            self.btn_apply.config(state=tk.DISABLED)
            self.btn_dry_run_test.config(state=tk.DISABLED)
            if res.get("selection_required"):
                self._picker_suggestion = res.get("ai_suggestion")
                self._open_drive_file_picker()
            else:
                messagebox.showerror("Document error", res.get("error", "Failed to prepare story review."), parent=self)
            return

        self.comprehension_questions = res["questions"]
        self.vocab_questions = res.get("vocab_questions", [])
        self.vocab_present = res.get("vocab_present", False)
        self._vocab_ignored = False
        self.section_picker.config(values=("Comprehension", "Vocabulary Quiz") if self.vocab_present else ("Comprehension",))
        if not self.vocab_present:
            self.section_var.set("Comprehension")
        self.questions = self.vocab_questions if self.section_var.get() == "Vocabulary Quiz" else self.comprehension_questions
        self.old_state = self.old_vocab_state if self.section_var.get() == "Vocabulary Quiz" else self.old_comprehension_state
        self.lbl_vocab_status.config(text=(f"Vocabulary: {len(self.vocab_questions)} questions" if self.vocab_present else "Vocabulary: skipped (section absent)"))
        self.docx_path = res["docx_path"]
        self.story["docx_path"] = self.docx_path
        self.btn_open_docx.config(state=tk.NORMAL)

        total_q = len(self.comprehension_questions)
        if total_q == 0:
            self.lbl_new_badge.config(text="0 Questions", bootstyle="inverse-warning")
            self.lbl_status.config(text="⚠️ No comprehension questions found in docx.", bootstyle="warning")
            self.btn_apply.config(state=tk.DISABLED)
            self.btn_dry_run_test.config(state=tk.DISABLED)
            return

        self.lbl_new_badge.config(
            text=f"🟢 Docx: {total_q} Questions",
            bootstyle="inverse-success"
        )
        hdr_setting = self.config.get("question_header", "Choose the correct answer ").strip()
        self.lbl_new_summary.config(
            text=f"Questions: {total_q} ready to apply  ·  Target Header: «{hdr_setting}»  ·  Type: «None»"
        )
        self.lbl_status.config(
            text=f"✅ Ready! {res.get('status_msg', 'DOCX loaded')} · {total_q} questions. Review Old vs New below.",
            bootstyle="success"
        )

        self._refresh_validation()
        self._update_ai_btn_text()

        # Check if any question is missing answer key
        missing_keys = [q["num"] for q in self.questions if not q.get("answer") or q.get("answer") not in ["A", "B", "C", "D"]]
        if missing_keys:
            api_key = GroqAnswerResolver.get_api_key(self.config)
            if api_key:
                self.lbl_status.config(
                    text=f"⚡ Auto-Worker: Questions {missing_keys} have unresolved keys. Auto-running Groq AI...",
                    bootstyle="warning"
                )
                self.safe_after(200, self._on_ai_resolve_answers)
            else:
                self.lbl_status.config(
                    text=f"⚠️ Notice: Questions {missing_keys} have no answer key. Click '⚡ AI Resolve Keys' to auto-detect them with Groq.",
                    bootstyle="warning"
                )

        # Populate trees
        self._populate_questions_tree()
        self._populate_diff_tree()
        if self.vocab_present and not self._vocab_ignored and (not self.vocab_questions or not DocxParser.validate_questions(self.vocab_questions)["is_valid"]):
            self.section_var.set("Vocabulary Quiz")
            self._on_section_changed()
            self.lbl_status.config(text="Vocabulary Quiz needs correction before this story can be applied (or click 'Ignore Vocabulary Quiz').", bootstyle="warning")

        # Automatically start fetching Readora state if not yet fetched
        if self.old_state is None:
            self._fetch_readora_state_async()

    def _update_ai_btn_text(self):
        is_vocab = self.section_var.get() == "Vocabulary Quiz"
        if not self.docx_path:
            self.btn_ai_resolve.config(state=tk.DISABLED, text="⚡ AI Resolve Keys (Groq)")
        elif is_vocab:
            if not self.questions:
                self.btn_ai_resolve.config(state=tk.NORMAL, text="⚡ AI Extract Vocab (Groq)")
            else:
                self.btn_ai_resolve.config(state=tk.NORMAL, text="⚡ AI Resolve Keys (Groq)")
        else:
            self.btn_ai_resolve.config(state=tk.NORMAL, text="⚡ AI Resolve Keys (Groq)")

    def _on_section_changed(self, _event=None):
        is_vocab = self.section_var.get() == "Vocabulary Quiz"
        self.questions = self.vocab_questions if is_vocab else self.comprehension_questions
        self.old_state = self.old_vocab_state if is_vocab else self.old_comprehension_state
        self.selected_q_idx = 0
        self.selected_diff_idx = 0
        self._update_ai_btn_text()
        self.lbl_old_badge.config(text=f"Readora: {len((self.old_state or {}).get('questions', []))} {self.section_var.get()} questions")
        self.lbl_old_summary.config(text=f"{self.section_var.get()}: {len((self.old_state or {}).get('questions', []))} existing questions")
        self.lbl_new_summary.config(text=f"{self.section_var.get()}: {len(self.questions)} incoming questions")
        self.lbl_new_badge.config(text=f"Docx: {len(self.questions)} {self.section_var.get()} questions")
        self.lbl_tab_old_meta.config(text=f"Readora Lab: Current {self.section_var.get()} questions")
        for item in self.old_tree.get_children():
            self.old_tree.delete(item)
        for i, q in enumerate((self.old_state or {}).get("questions", [])):
            self.old_tree.insert("", tk.END, iid=f"old_{i}", values=(i + 1, q.get("question", ""), f"[{q.get('answer', '')}]"))
        self._populate_questions_tree()
        self._populate_diff_tree()
        if not self.questions:
            self.txt_q_rubric.delete("1.0", tk.END)
            for variable in self.choice_vars.values():
                variable.set("")
            self.ans_var.set("")

    def _refresh_validation(self):
        comp = DocxParser.validate_questions(self.comprehension_questions)
        vocab = DocxParser.validate_questions(self.vocab_questions)
        vocab_active = self.vocab_present and not self._vocab_ignored
        is_vocab_blocking = vocab_active and (not self.vocab_questions or not vocab["is_valid"])
        valid = bool(self.comprehension_questions) and comp["is_valid"] and not is_vocab_blocking
        state = tk.NORMAL if valid and not self.is_executing else tk.DISABLED
        self.btn_apply.config(state=state)
        self.btn_dry_run_test.config(state=state)

        if self.vocab_present:
            self.btn_toggle_vocab.pack(side=tk.LEFT, padx=6)
            if self._vocab_ignored:
                self.btn_toggle_vocab.config(text="Include Vocabulary Quiz", bootstyle="info-outline")
                self.lbl_vocab_status.config(text="Vocabulary: ignored by user", bootstyle="secondary")
                self.lbl_btn_hint.config(text="")
            elif not self.vocab_questions or not vocab["is_valid"]:
                self.btn_toggle_vocab.config(text="Ignore Vocabulary Quiz", bootstyle="warning-outline")
                self.lbl_vocab_status.config(text="Vocabulary: needs correction", bootstyle="warning")
                self.lbl_btn_hint.config(text="⚠️ Apply/Dry-Run locked: Vocab Quiz has issues. Click 'Ignore Vocabulary Quiz' to bypass.")
            else:
                self.btn_toggle_vocab.config(text="Ignore Vocabulary Quiz", bootstyle="secondary-outline")
                self.lbl_vocab_status.config(text=f"Vocabulary: {len(self.vocab_questions)} ready", bootstyle="success")
                self.lbl_btn_hint.config(text="")
        else:
            self.btn_toggle_vocab.pack_forget()
            self.lbl_vocab_status.config(text="Vocabulary: skipped (section absent)", bootstyle="secondary")
            if not bool(self.comprehension_questions):
                self.lbl_btn_hint.config(text="⚠️ Apply/Dry-Run locked: No comprehension questions found.")
            elif not comp["is_valid"]:
                issues = "; ".join(comp.get("issues", []))
                self.lbl_btn_hint.config(text=f"⚠️ Apply/Dry-Run locked: Comprehension issues ({issues})")
            else:
                self.lbl_btn_hint.config(text="")

    def _on_ai_resolve_answers(self):
        """Uses Groq AI to detect freeform answer keys, styling-based answers, or extract vocab questions."""
        if not self.docx_path:
            return

        is_vocab = self.section_var.get() == "Vocabulary Quiz"
        if not self.questions and not is_vocab:
            return

        api_key = GroqAnswerResolver.get_api_key(self.config)
        if not api_key:
            prompt_key = simpledialog.askstring(
                "Groq API Key Required",
                "Enter your Groq API Key:\n(Get a free key instantly at https://console.groq.com/keys)",
                parent=self
            )
            if not prompt_key or not prompt_key.strip():
                return
            api_key = prompt_key.strip()
            self.config["groq_api_key"] = api_key
            save_config(self.config)

        self.btn_ai_resolve.config(state=tk.DISABLED, text="⚡ Working...")
        self.pbar.pack(side=tk.RIGHT)
        self.pbar.start(10)

        if is_vocab and not self.questions:
            self.lbl_status.config(text="🤖 Groq AI extracting vocabulary questions from document...", bootstyle="warning")
            def worker():
                res = GroqAnswerResolver.extract_vocab_questions_with_groq(
                    self.docx_path,
                    self.config
                )
                self.safe_after(0, self._on_ai_resolve_completed, res, True)
            threading.Thread(target=worker, daemon=True).start()
        else:
            self.lbl_status.config(text="🤖 Groq AI analyzing document for freeform answer keys and styling annotations...", bootstyle="warning")
            def worker():
                res = GroqAnswerResolver.resolve_answers_with_groq(
                    self.docx_path,
                    self.questions,
                    self.config
                )
                self.safe_after(0, self._on_ai_resolve_completed, res, False)
            threading.Thread(target=worker, daemon=True).start()

    def _on_ai_resolve_completed(self, res: Dict[str, Any], is_extract: bool = False):
        self.pbar.stop()
        self.pbar.pack_forget()
        is_vocab = self.section_var.get() == "Vocabulary Quiz"
        self._update_ai_btn_text()

        if res["success"]:
            self.questions = res["questions"]
            if is_vocab:
                self.vocab_questions = self.questions
                self.lbl_new_badge.config(text=f"Docx: {len(self.questions)} Vocabulary Quiz questions")
                self.lbl_new_summary.config(text=f"Vocabulary Quiz: {len(self.questions)} incoming questions")
            else:
                self.comprehension_questions = self.questions

            self._populate_questions_tree()
            self._populate_diff_tree()
            self._refresh_validation()

            if is_extract:
                cnt = len(self.questions)
                if cnt > 0:
                    self.lbl_status.config(text=f"✨ Groq AI extracted {cnt} vocabulary questions!", bootstyle="success")
                    messagebox.showinfo(
                        "AI Extraction Complete",
                        f"Groq AI successfully extracted {cnt} vocabulary question(s) from document.",
                        parent=self
                    )
                else:
                    self.lbl_status.config(text="⚠️ Groq AI found no vocabulary questions in document.", bootstyle="warning")
                    messagebox.showinfo(
                        "AI Extraction",
                        "Groq AI inspected the document, but found no multiple-choice vocabulary quiz questions.\n"
                        "If this story does not include a vocabulary quiz, click 'Ignore Vocabulary Quiz' to proceed.",
                        parent=self
                    )
            else:
                changes_cnt = res.get("changes_count", 0)
                if changes_cnt > 0:
                    change_details = "\n".join(f"• Q{c['num']}: {c['old_answer'] or 'None'} ➔ {c['new_answer']} ({c['source']})" for c in res.get("changes", []))
                    self.lbl_status.config(text=f"✨ Groq AI resolved {changes_cnt} answer keys from document!", bootstyle="success")
                    messagebox.showinfo(
                        "AI Answer Key Resolution",
                        f"Groq AI successfully resolved {changes_cnt} answer key(s) from document context / styling:\n\n{change_details}",
                        parent=self
                    )
                else:
                    self.lbl_status.config(text="✅ Groq AI verified: All answer keys are consistent.", bootstyle="success")
                    messagebox.showinfo(
                        "AI Verification Complete",
                        "Groq AI inspected the document styling and footer keys.\nAll existing answer keys are already consistent.",
                        parent=self
                    )
        else:
            err = res.get("error", "Unknown error")
            self.lbl_status.config(text=f"❌ Groq AI error: {err}", bootstyle="danger")
            messagebox.showerror("Groq AI Error", f"Could not resolve answers with Groq:\n{err}", parent=self)

    # ══════════════════════════════════════════════════════════════════════════
    # DIFF LOGIC & RENDERING
    # ══════════════════════════════════════════════════════════════════════════
    def _clear_diff_details(self):
        """Clears both sides of the diff inspection cards when no rows are available."""
        self.diff_left_card.config(text=" 🔴 Current on Readora Lab ")
        self.lbl_diff_old_rubric.delete("1.0", tk.END)
        self.lbl_diff_old_rubric.insert(tk.END, "No questions in this section.")
        for child in self.diff_old_choices_frame.winfo_children():
            child.destroy()
        self.lbl_diff_old_ans.config(text="Answer: --")

        self.diff_right_card.config(text=" 🟢 Incoming from Docx (NEW TO APPLY) ")
        self.lbl_diff_new_rubric.delete("1.0", tk.END)
        self.lbl_diff_new_rubric.insert(tk.END, "No questions in this section.")
        for child in self.diff_new_choices_frame.winfo_children():
            child.destroy()
        self.lbl_diff_new_ans.config(text="Answer: --")

    def _populate_diff_tree(self):
        for item in self.diff_tree.get_children():
            self.diff_tree.delete(item)

        old_questions = (self.old_state or {}).get("questions", [])
        new_questions = self.questions

        max_rows = max(len(old_questions), len(new_questions))
        if max_rows == 0:
            self._clear_diff_details()
            return

        for i in range(max_rows):
            old_q = old_questions[i] if i < len(old_questions) else None
            new_q = new_questions[i] if i < len(new_questions) else None

            num_str = str(i + 1)
            old_text = old_q.get("question", "") if old_q else "(None)"
            old_ans = f"[{old_q.get('answer', '')}]" if old_q else "[-]"

            new_text = new_q.get("raw_question", "") if new_q else "(None)"
            new_ans = f"[{new_q.get('answer', '')}]" if new_q else "[-]"

            # Diff analysis
            tag = "even" if i % 2 == 0 else "odd"
            diff_label = "✓ Match"
            diff_tag = "identical"

            if old_q is None and new_q is not None:
                diff_label = "✨ New Question"
                diff_tag = "new_q"
            elif old_q is not None and new_q is None:
                diff_label = "🗑️ Will Remove"
                diff_tag = "modified"
            elif old_q and new_q:
                old_clean_q = old_q.get("question", "").strip().lower()
                new_clean_q = new_q.get("raw_question", "").strip().lower()

                ans_diff = old_q.get("answer", "").upper() != new_q.get("answer", "").upper()
                text_diff = old_clean_q != new_clean_q and (f"{i+1}. " + new_clean_q) != old_clean_q

                if ans_diff and text_diff:
                    diff_label = f"⚠️ Key: {old_q.get('answer')}➔{new_q.get('answer')} + Text"
                    diff_tag = "ans_changed"
                elif ans_diff:
                    diff_label = f"⚠️ Key: {old_q.get('answer')}➔{new_q.get('answer')}"
                    diff_tag = "ans_changed"
                elif text_diff:
                    diff_label = "✏️ Text Changed"
                    diff_tag = "modified"
                else:
                    diff_label = "✓ Same Rubric & Key"
                    diff_tag = "identical"

            self.diff_tree.insert(
                "", tk.END, iid=f"diff_{i}",
                values=(num_str, old_text[:45], old_ans, new_text[:45], new_ans, diff_label),
                tags=(tag, diff_tag)
            )

        if max_rows > 0:
            self.diff_tree.selection_set("diff_0")
            self._load_diff_details(0)

    def _on_diff_row_selected(self, event):
        sel = self.diff_tree.selection()
        if not sel:
            return
        idx = int(sel[0].replace("diff_", ""))
        self.selected_diff_idx = idx
        self._load_diff_details(idx)

    def _load_diff_details(self, idx: int):
        old_questions = (self.old_state or {}).get("questions", [])
        new_questions = self.questions

        old_q = old_questions[idx] if idx < len(old_questions) else None
        new_q = new_questions[idx] if idx < len(new_questions) else None

        # 1. Populate Old Question Box
        self.lbl_diff_old_rubric.delete("1.0", tk.END)
        for child in self.diff_old_choices_frame.winfo_children():
            child.destroy()

        if old_q:
            self.diff_left_card.config(text=f" 🔴 Current on Readora Lab (Question #{idx+1}) ")
            self.lbl_diff_old_rubric.insert(tk.END, old_q.get("question", ""))
            old_ans = old_q.get("answer", "")
            self.lbl_diff_old_ans.config(text=f"Current Readora Correct Answer: Option {old_ans}")

            for c in old_q.get("choices", []):
                let = c.get("letter", "")
                is_corr = c.get("correct", False) or (let.upper() == old_ans.upper())
                style = "danger" if is_corr else "secondary"
                row = tbs.Frame(self.diff_old_choices_frame)
                row.pack(fill=tk.X, pady=2)
                tbs.Label(row, text=f"[{let}]", font=("Segoe UI", 9, "bold"), bootstyle=style).pack(side=tk.LEFT, padx=(0, 6))
                tbs.Label(row, text=c.get("text", ""), font=("Segoe UI", 9)).pack(side=tk.LEFT, fill=tk.X, expand=True)
                if is_corr:
                    tbs.Label(row, text="✓ CORRECT", font=("Segoe UI", 8, "bold"), bootstyle="inverse-danger", padding=(4, 1)).pack(side=tk.RIGHT)
        else:
            self.diff_left_card.config(text=f" 🔴 Current on Readora Lab (Question #{idx+1}) ")
            self.lbl_diff_old_rubric.insert(tk.END, "No existing question on Readora Lab at this index.")
            self.lbl_diff_old_ans.config(text="Answer: None")

        # 2. Populate New Question Box
        self.lbl_diff_new_rubric.delete("1.0", tk.END)
        for child in self.diff_new_choices_frame.winfo_children():
            child.destroy()

        if new_q:
            self.diff_right_card.config(text=f" 🟢 Incoming from Docx (Question #{idx+1}) ")
            self.lbl_diff_new_rubric.insert(tk.END, new_q.get("raw_question", ""))
            new_ans = new_q.get("answer", "")
            self.lbl_diff_new_ans.config(text=f"Incoming Docx Correct Answer: Option {new_ans}")

            for c in new_q.get("choices", []):
                let = c.get("letter", "")
                is_corr = (let.upper() == new_ans.upper())
                style = "success" if is_corr else "secondary"
                row = tbs.Frame(self.diff_new_choices_frame)
                row.pack(fill=tk.X, pady=2)
                tbs.Label(row, text=f"[{let}]", font=("Segoe UI", 9, "bold"), bootstyle=style).pack(side=tk.LEFT, padx=(0, 6))
                tbs.Label(row, text=c.get("text", ""), font=("Segoe UI", 9)).pack(side=tk.LEFT, fill=tk.X, expand=True)
                if is_corr:
                    tbs.Label(row, text="✓ CORRECT", font=("Segoe UI", 8, "bold"), bootstyle="inverse-success", padding=(4, 1)).pack(side=tk.RIGHT)
        else:
            self.diff_right_card.config(text=f" 🟢 Incoming from Docx (Question #{idx+1}) ")
            self.lbl_diff_new_rubric.insert(tk.END, "No docx question at this index.")
            self.lbl_diff_new_ans.config(text="Answer: None")

    # ══════════════════════════════════════════════════════════════════════════
    # EDITOR LOGIC
    # ══════════════════════════════════════════════════════════════════════════
    def _populate_questions_tree(self):
        for item in self.q_tree.get_children():
            self.q_tree.delete(item)

        for i, q in enumerate(self.questions):
            tag = "even" if i % 2 == 0 else "odd"
            snippet = q.get("raw_question", "")[:45] + ("..." if len(q.get("raw_question", "")) > 45 else "")
            self.q_tree.insert(
                "", tk.END, iid=str(i),
                values=(str(q["num"]), snippet, f"[{q['answer']}]"),
                tags=(tag, "valid")
            )

        if self.questions:
            self.q_tree.selection_set("0")
            self._load_question_to_editor(0)

    def _on_question_selected(self, event):
        sel = self.q_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        self.selected_q_idx = idx
        self._load_question_to_editor(idx)

    def _load_question_to_editor(self, idx: int):
        if idx < 0 or idx >= len(self.questions):
            return
        self._loading_editor = True
        try:
            q = self.questions[idx]

            self.txt_q_rubric.delete("1.0", tk.END)
            self.txt_q_rubric.insert(tk.END, q.get("raw_question", ""))

            for c in q.get("choices", []):
                letter = c["letter"]
                clean_text = c["text"]
                if clean_text.startswith(f"{letter}."):
                    clean_text = clean_text[2:].strip()
                elif clean_text.startswith(f"{letter})"):
                    clean_text = clean_text[2:].strip()
                if letter in self.choice_vars:
                    self.choice_vars[letter].set(clean_text)

            ans = q.get("answer") or ""
            self.ans_var.set(ans)
            self.lbl_selected_ans_hint.config(text=f"Selected Correct Answer: Option {ans}")
        finally:
            self._loading_editor = False

    def _on_editor_changed(self):
        """Saves editor changes into the in-memory questions list and updates Treeview and Diff."""
        if getattr(self, "_loading_editor", False):
            return
        if self.selected_q_idx < 0 or self.selected_q_idx >= len(self.questions):
            return

        raw_q = self.txt_q_rubric.get("1.0", tk.END).strip()
        ans = self.ans_var.get()
        self.lbl_selected_ans_hint.config(text=f"Selected Correct Answer: Option {ans}")

        q = self.questions[self.selected_q_idx]
        q["raw_question"] = raw_q
        q["question"] = f"{q['num']}. {raw_q}"
        q["answer"] = ans

        choices = []
        for letter in ["A", "B", "C", "D"]:
            t = self.choice_vars[letter].get().strip()
            choices.append({"letter": letter, "text": f"{letter}. {t}"})
        q["choices"] = choices
        self._refresh_validation()

        # Update Treeview row
        snippet = raw_q[:45] + ("..." if len(raw_q) > 45 else "")
        self.q_tree.item(str(self.selected_q_idx), values=(str(q["num"]), snippet, f"[{ans}]"))

        # Also refresh Diff tree to show updated text/answer
        self._populate_diff_tree()

    def _open_docx_externally(self):
        if not self.docx_path or not Path(self.docx_path).exists():
            return
        path_obj = Path(self.docx_path).resolve()
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", str(path_obj)])
            elif sys.platform == "win32":
                os.startfile(str(path_obj))
            else:
                subprocess.run(["xdg-open", str(path_obj)])
        except Exception as e:
            messagebox.showwarning("Open File", f"Could not open file: {e}")

    def _on_add_question(self):
        """Adds a new blank question to the active section list."""
        new_num = len(self.questions) + 1
        new_q = {
            "num": new_num,
            "question": f"{new_num}. ",
            "raw_question": "",
            "choices": [
                {"letter": "A", "text": "A. "},
                {"letter": "B", "text": "B. "},
                {"letter": "C", "text": "C. "},
                {"letter": "D", "text": "D. "},
            ],
            "answer": "A"
        }
        self.questions.append(new_q)
        is_vocab = self.section_var.get() == "Vocabulary Quiz"
        if is_vocab:
            self.vocab_questions = self.questions
            self.vocab_present = True
            self._vocab_ignored = False
            self.lbl_vocab_status.config(text=f"Vocabulary: {len(self.vocab_questions)} questions", bootstyle="success")
            self.lbl_new_badge.config(text=f"Docx: {len(self.questions)} Vocabulary Quiz questions")
        else:
            self.comprehension_questions = self.questions
            self.lbl_new_badge.config(text=f"Docx: {len(self.questions)} Questions")

        self._populate_questions_tree()
        self._populate_diff_tree()
        self._refresh_validation()

        idx = len(self.questions) - 1
        self.q_tree.selection_set(str(idx))
        self.q_tree.see(str(idx))
        self.selected_q_idx = idx
        self._load_question_to_editor(idx)
        self.txt_q_rubric.focus_set()

    def _on_delete_question(self):
        """Deletes the currently selected question and re-numbers remaining questions."""
        if not self.questions or self.selected_q_idx < 0 or self.selected_q_idx >= len(self.questions):
            return

        del self.questions[self.selected_q_idx]
        for i, q in enumerate(self.questions):
            q["num"] = i + 1
            raw = q.get("raw_question", "")
            q["question"] = f"{i + 1}. {raw}"

        is_vocab = self.section_var.get() == "Vocabulary Quiz"
        if is_vocab:
            self.vocab_questions = self.questions
            self.lbl_vocab_status.config(text=f"Vocabulary: {len(self.vocab_questions)} questions", bootstyle="success" if self.vocab_questions else "secondary")
            self.lbl_new_badge.config(text=f"Docx: {len(self.questions)} Vocabulary Quiz questions")
        else:
            self.comprehension_questions = self.questions
            self.lbl_new_badge.config(text=f"Docx: {len(self.questions)} Questions")

        self._populate_questions_tree()
        self._populate_diff_tree()
        self._refresh_validation()

        if self.questions:
            new_idx = min(self.selected_q_idx, len(self.questions) - 1)
            self.selected_q_idx = new_idx
            self.q_tree.selection_set(str(new_idx))
            self._load_question_to_editor(new_idx)
        else:
            self.selected_q_idx = -1
            self.txt_q_rubric.delete("1.0", tk.END)
            for v in self.choice_vars.values():
                v.set("")
            self.ans_var.set("A")

    def _open_paste_importer_modal(self):
        """Opens an interactive modal allowing the user to paste raw questions text, preview, and import."""
        modal = tbs.Toplevel(self)
        sec_name = self.section_var.get()
        modal.title(f"📋 Paste & Import Questions — {sec_name}")
        modal.geometry("740x630")
        modal.minsize(620, 500)
        modal.transient(self)
        modal.grab_set()

        hdr = tbs.Frame(modal, padding=(14, 10), bootstyle="dark")
        hdr.pack(fill=tk.X)
        tbs.Label(
            hdr,
            text=f"📋 Quick Import into {sec_name}",
            font=("Segoe UI", 12, "bold"),
            bootstyle="inverse-dark"
        ).pack(side=tk.LEFT)

        body = tbs.Frame(modal, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        tbs.Label(
            body,
            text="Paste raw questions text below (copied from Word, Docs, or PDF):\n"
                 "Standard format: Question rubric on one line, choices (A., B., C., D.) on subsequent lines, with optional 'Answer: A'.",
            font=("Segoe UI", 9)
        ).pack(anchor=tk.W, pady=(0, 6))

        txt_input = tk.Text(body, height=12, font=("Segoe UI", 10), bg="#1e1e1e", fg="#ffffff", relief=tk.FLAT)
        txt_input.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        preview_box = tbs.LabelFrame(body, text=" Parsed Questions Preview ", padding=8, bootstyle="info")
        preview_box.pack(fill=tk.X, pady=(0, 8))

        lbl_preview = tbs.Label(preview_box, text="Paste questions above and click 'Preview Parsed Questions'.", font=("Segoe UI", 9))
        lbl_preview.pack(anchor=tk.W)

        parsed_holder = {"questions": []}

        def do_parse():
            raw = txt_input.get("1.0", tk.END).strip()
            if not raw:
                lbl_preview.config(text="⚠️ Please paste question text first.", bootstyle="warning")
                parsed_holder["questions"] = []
                return

            lines = [l.strip() for l in raw.splitlines() if l.strip()]
            parsed = DocxParser._parse_questions(lines, require_answer=False)
            if parsed:
                parsed_holder["questions"] = parsed
                missing_ans = sum(1 for q in parsed if not q.get("answer") or q.get("answer") not in ["A", "B", "C", "D"])
                ans_note = f" ({missing_ans} missing answer keys)" if missing_ans > 0 else " (All answer keys identified)"
                first_preview = parsed[0].get("raw_question", "")[:60]
                lbl_preview.config(
                    text=f"✅ Successfully parsed {len(parsed)} question(s){ans_note}!\nFirst: {first_preview}... [Key: {parsed[0].get('answer', '?')}]",
                    bootstyle="success"
                )
            else:
                parsed_holder["questions"] = []
                lbl_preview.config(
                    text="⚠️ Could not parse standard questions from this text. Ensure each question has A., B., C., D. options, or click '⚡ Format with Groq AI'.",
                    bootstyle="danger"
                )

        def do_groq_format():
            raw = txt_input.get("1.0", tk.END).strip()
            if not raw:
                messagebox.showwarning("Empty text", "Please paste question text before running AI format.", parent=modal)
                return

            api_key = GroqAnswerResolver.get_api_key(self.config)
            if not api_key:
                messagebox.showerror("Groq Required", "Groq API key is not configured.", parent=modal)
                return

            lbl_preview.config(text="🤖 Groq AI formatting questions...", bootstyle="warning")
            modal.update()

            def worker():
                try:
                    from groq import Groq
                    import json
                    client = Groq(api_key=api_key)
                    sys_prompt = (
                        "You are an exam parser. Read the text and extract all multiple-choice questions.\n"
                        "Return JSON ONLY with this schema: {\"questions\": [{\"num\": 1, \"raw_question\": \"...\", \"choices\": [{\"letter\": \"A\", \"text\": \"A. ...\"}, ...], \"answer\": \"A\"}]}"
                    )
                    resp = client.chat.completions.create(
                        model=(self.config or {}).get("groq_model", "llama-3.3-70b-versatile"),
                        messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": raw[:7500]}],
                        response_format={"type": "json_object"},
                        temperature=0.1
                    )
                    content = json.loads(resp.choices[0].message.content)
                    q_list = content.get("questions", [])
                    fmt = []
                    for i, q in enumerate(q_list):
                        choices = []
                        for c in q.get("choices", []):
                            let = str(c.get("letter", "")).upper()
                            t = str(c.get("text", "")).strip()
                            clean_t = re.sub(r"^[A-Da-d][\.\)]\s*", "", t).strip()
                            choices.append({"letter": let, "text": f"{let}. {clean_t}"})
                        raw_q = q.get("raw_question") or q.get("question", "")
                        raw_q = re.sub(r"^(?:Q\d+[\.\:]|\d+[\.\:])\s*", "", raw_q).strip()
                        fmt.append({
                            "num": i + 1,
                            "question": f"{i + 1}. {raw_q}",
                            "raw_question": raw_q,
                            "choices": choices,
                            "answer": q.get("answer", "A")
                        })
                    self.safe_after(0, lambda: on_groq_done(fmt))
                except Exception as exc:
                    self.safe_after(0, lambda: lbl_preview.config(text=f"❌ Groq format failed: {exc}", bootstyle="danger"))

            def on_groq_done(fmt):
                if fmt:
                    parsed_holder["questions"] = fmt
                    lbl_preview.config(
                        text=f"✨ Groq AI structured {len(fmt)} question(s) successfully!\nFirst: {fmt[0]['raw_question'][:60]}... [Key: {fmt[0]['answer']}]",
                        bootstyle="success"
                    )
                else:
                    lbl_preview.config(text="⚠️ Groq AI did not find any questions in text.", bootstyle="warning")

            threading.Thread(target=worker, daemon=True).start()

        def do_import():
            if not parsed_holder["questions"]:
                do_parse()
            if not parsed_holder["questions"]:
                messagebox.showerror("No questions", "Could not import: No questions parsed yet.", parent=modal)
                return

            replace = replace_var.get()
            imported_q = parsed_holder["questions"]
            is_vocab = self.section_var.get() == "Vocabulary Quiz"

            if replace:
                self.questions = imported_q
            else:
                start_num = len(self.questions)
                for i, q in enumerate(imported_q):
                    q["num"] = start_num + i + 1
                    raw_q = q.get("raw_question", "")
                    q["question"] = f"{q['num']}. {raw_q}"
                self.questions.extend(imported_q)

            if is_vocab:
                self.vocab_questions = self.questions
                self.vocab_present = True
                self._vocab_ignored = False
                self.lbl_vocab_status.config(text=f"Vocabulary: {len(self.vocab_questions)} questions", bootstyle="success")
                self.lbl_new_badge.config(text=f"Docx: {len(self.questions)} Vocabulary Quiz questions")
            else:
                self.comprehension_questions = self.questions
                self.lbl_new_badge.config(text=f"Docx: {len(self.questions)} Questions")

            self._populate_questions_tree()
            self._populate_diff_tree()
            self._refresh_validation()
            if self.questions:
                self.q_tree.selection_set("0")
                self._load_question_to_editor(0)

            modal.destroy()
            messagebox.showinfo("Import Successful", f"Imported {len(imported_q)} question(s) into {self.section_var.get()}!", parent=self)

        btn_bar = tbs.Frame(body)
        btn_bar.pack(fill=tk.X)

        replace_var = tk.BooleanVar(value=True)
        tbs.Checkbutton(btn_bar, text="Replace existing questions in this section", variable=replace_var, bootstyle="round-toggle").pack(side=tk.LEFT)

        tbs.Button(btn_bar, text="📥 Import to Section", bootstyle="success", command=do_import).pack(side=tk.RIGHT, padx=(4, 0))
        tbs.Button(btn_bar, text="🔍 Preview", bootstyle="info-outline", command=do_parse).pack(side=tk.RIGHT, padx=4)
        tbs.Button(btn_bar, text="⚡ Format with Groq AI", bootstyle="warning-outline", command=do_groq_format).pack(side=tk.RIGHT, padx=4)

    def _open_docx_text_viewer(self):
        """Displays full text from docx so user can view, highlight, and convert paragraphs to questions."""
        if not self.docx_path or not Path(self.docx_path).exists():
            messagebox.showwarning("Docx not loaded", "No docx file loaded for this story yet.", parent=self)
            return

        modal = tbs.Toplevel(self)
        story_name = self.story.get("story_name", "Story")
        modal.title(f"📄 Docx Document Text — {story_name}")
        modal.geometry("740x600")
        modal.minsize(580, 440)
        modal.transient(self)
        modal.grab_set()

        hdr = tbs.Frame(modal, padding=(14, 10), bootstyle="dark")
        hdr.pack(fill=tk.X)
        tbs.Label(
            hdr,
            text=f"📄 Extracted Document Text — {story_name}",
            font=("Segoe UI", 12, "bold"),
            bootstyle="inverse-dark"
        ).pack(side=tk.LEFT)

        body = tbs.Frame(modal, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        tbs.Label(
            body,
            text="You can highlight text below and click 'Convert Selected Text to Question', or copy text into the Importer:",
            font=("Segoe UI", 9)
        ).pack(anchor=tk.W, pady=(0, 6))

        txt_frame = tbs.Frame(body)
        txt_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        txt_view = tk.Text(txt_frame, font=("Segoe UI", 10), bg="#1e1e1e", fg="#ffffff", relief=tk.FLAT)
        scroll = ttk.Scrollbar(txt_frame, orient=tk.VERTICAL, command=txt_view.yview)
        txt_view.configure(yscroll=scroll.set)
        txt_view.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        try:
            paras = DocxParser.extract_paragraphs(self.docx_path)
            txt_view.insert(tk.END, "\n\n".join(paras))
        except Exception as e:
            txt_view.insert(tk.END, f"Could not read docx: {e}")

        bottom_bar = tbs.Frame(body)
        bottom_bar.pack(fill=tk.X)

        def convert_selection():
            try:
                selected_text = txt_view.selection_get().strip()
            except Exception:
                selected_text = ""

            if not selected_text:
                messagebox.showwarning("No selection", "Please highlight/select question text in the viewer first.", parent=modal)
                return

            lines = [l.strip() for l in selected_text.splitlines() if l.strip()]
            parsed = DocxParser._parse_questions(lines, require_answer=False)
            if parsed:
                for q in parsed:
                    new_num = len(self.questions) + 1
                    q["num"] = new_num
                    q["question"] = f"{new_num}. {q.get('raw_question', '')}"
                    self.questions.append(q)

                is_vocab = self.section_var.get() == "Vocabulary Quiz"
                if is_vocab:
                    self.vocab_questions = self.questions
                    self.vocab_present = True
                    self._vocab_ignored = False
                    self.lbl_vocab_status.config(text=f"Vocabulary: {len(self.vocab_questions)} questions", bootstyle="success")
                else:
                    self.comprehension_questions = self.questions

                self._populate_questions_tree()
                self._populate_diff_tree()
                self._refresh_validation()
                idx = len(self.questions) - 1
                self.q_tree.selection_set(str(idx))
                self._load_question_to_editor(idx)
                modal.destroy()
                messagebox.showinfo("Converted", f"Added {len(parsed)} question(s) into {self.section_var.get()}!", parent=self)
            else:
                messagebox.showerror(
                    "Parsing failed",
                    "Could not detect question rubric and choices (A, B, C, D) in the selected text.\nTry using '📋 Paste Questions' instead.",
                    parent=modal
                )

        tbs.Button(bottom_bar, text="➕ Convert Selected Text to Question", bootstyle="success", command=convert_selection).pack(side=tk.LEFT)
        tbs.Button(bottom_bar, text="Close", bootstyle="secondary", command=modal.destroy).pack(side=tk.RIGHT)

    # ══════════════════════════════════════════════════════════════════════════
    # ACCEPT & APPLY / DRY RUN TEST ACTIONS
    # ══════════════════════════════════════════════════════════════════════════
    def _on_accept_and_apply(self):
        """Live application to Readora Lab with user approval."""
        if self.is_executing:
            return

        story_name = self.story.get("story_name", "Story")
        self._refresh_validation()
        if str(self.btn_apply.cget("state")) == tk.DISABLED:
            messagebox.showerror("Questions need correction", "Correct all comprehension and vocabulary questions before applying.", parent=self)
            return
        num_q = len(self.comprehension_questions)
        old_count = len((self.old_comprehension_state or {}).get("questions", []))
        vocab_active = self.vocab_present and not self._vocab_ignored
        vocab_summary = (
            f"{len(self.vocab_questions)} questions" if vocab_active
            else "Skipped (ignored by user)" if self._vocab_ignored
            else "Skipped (section absent)"
        )

        msg = (
            f"Are you ready to write these changes live to Readora Lab?\n\n"
            f"• Story: {story_name}\n"
            f"• Current Questions on Readora (OLD): {old_count}\n"
            f"• Verified Questions to Write (NEW): {num_q}\n"
            f"• Vocabulary Quiz: {vocab_summary}\n"
            f"• Action: Existing questions will be replaced & answers saved\n\n"
            f"Click 'Yes' to accept and apply immediately."
        )
        if not messagebox.askyesno("Accept & Apply Confirmation", msg, parent=self):
            return

        self.is_executing = True
        self.btn_apply.config(state=tk.DISABLED)
        self.btn_dry_run_test.config(state=tk.DISABLED)

        self.pbar.pack(side=tk.RIGHT)
        self.pbar.start(10)
        self.lbl_status.config(text="🚀 Connecting to Readora and applying changes...", bootstyle="info")

        def worker():
            res = execute_accept_and_apply(
                self.story,
                self.comprehension_questions,
                self.config,
                on_status=lambda s: self.safe_after(0, self._update_status, s),
                vocab_questions=self.vocab_questions if vocab_active else None,
            )
            self.safe_after(0, self._on_apply_completed, res)

        threading.Thread(target=worker, daemon=True).start()

    def _on_apply_completed(self, res: Dict[str, Any]):
        self.pbar.stop()
        self.pbar.pack_forget()
        self.is_executing = False

        if res["success"]:
            sheet_info = ""
            sheet_res = res.get("sheet_result")
            if sheet_res:
                if sheet_res.get("success"):
                    sheet_info = f"\n• Google Sheet: ✅ Marked cell {sheet_res.get('cell')} as Done in '{sheet_res.get('sheet_name')}'"
                elif sheet_res.get("blocked_by_safety_rule"):
                    sheet_info = f"\n• Google Sheet: ⚠️ Not updated (Strict Rule: Chrome not logged into {self.config.get('gmail_account', 'mohrawagdy58@gmail.com')})"
                else:
                    sheet_info = f"\n• Google Sheet: {sheet_res.get('error', 'Skipped')}"

            self.lbl_status.config(text="🎉 LIVE SUCCESS: Questions saved to Readora!", bootstyle="success")
            messagebox.showinfo(
                "Success",
                f"Successfully updated '{res.get('story_name')}' on Readora!\n\n"
                f"• Questions Written: {res.get('questions_count')}\n"
                f"• Vocabulary Questions Written: {res.get('vocab_questions_count', 0)}\n"
                f"• Readora Matched Title: {res.get('matched_title')}\n"
                f"• Status: Marked Completed in Progress Tracker"
                f"{sheet_info}",
                parent=self
            )
            if self.on_applied:
                self.on_applied(self.story)
            self.destroy()
        else:
            err = res.get("error", "Unknown error")
            self.lbl_status.config(text=f"❌ Failed: {err}", bootstyle="danger")
            self._refresh_validation()
            messagebox.showerror("Update Failed", f"Failed to apply to Readora:\n{err}", parent=self)

    def _on_test_dry_run_only(self):
        """Runs browser dry-run test without saving to database, capturing live old state."""
        if self.is_executing:
            return

        self.is_executing = True
        self.btn_apply.config(state=tk.DISABLED)
        self.btn_dry_run_test.config(state=tk.DISABLED)

        self.pbar.pack(side=tk.RIGHT)
        self.pbar.start(10)
        self.lbl_status.config(text="🧪 Testing dry-run in browser (Safe - no DB writes)...", bootstyle="warning")

        vocab_active = self.vocab_present and not self._vocab_ignored

        def worker():
            res = execute_dry_run_test(
                self.story,
                self.comprehension_questions,
                self.config,
                on_status=lambda s: self.safe_after(0, self._update_status, s),
                vocab_questions=self.vocab_questions if vocab_active else None,
            )
            self.safe_after(0, self._on_dry_run_test_completed, res)

        threading.Thread(target=worker, daemon=True).start()

    def _on_dry_run_test_completed(self, res: Dict[str, Any]):
        self.pbar.stop()
        self.pbar.pack_forget()
        self.is_executing = False
        self._refresh_validation()

        if res["success"]:
            # If old_state was captured during dry-run, store and update
            if "old_state" in res and res["old_state"]:
                self.old_comprehension_state = res["old_state"]
                self.old_vocab_state = res.get("old_vocab_state")
                self._on_readora_state_fetched(res)

            old_count = len((self.old_comprehension_state or {}).get("questions", []))
            new_count = len(self.comprehension_questions)

            self.lbl_status.config(text="✅ Dry-run test succeeded! (Safe, no changes saved)", bootstyle="success")
            messagebox.showinfo(
                "Dry-Run Test Passed",
                f"Dry-run test verified successfully for '{res.get('story_name')}'!\n\n"
                f"• Matched Book on Readora: {res.get('matched_title')}\n"
                f"• Current Questions on Server (OLD): {old_count}\n"
                f"• Verified Questions to Apply (NEW): {new_count}\n"
                f"• Vocabulary Questions Tested: {res.get('vocab_questions_count', 0)}\n"
                f"• Status: Safe test completed without database writes.\n\n"
                f"You can now review the Old vs New Diff and click 'Accept & Apply' whenever ready.",
                parent=self
            )
        else:
            err = res.get("error", "Unknown error")
            self.lbl_status.config(text=f"❌ Dry-run test failed: {err}", bootstyle="danger")
            messagebox.showerror("Dry-Run Failed", f"Dry-run test encountered an error:\n{err}", parent=self)
