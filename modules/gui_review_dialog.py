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
        self.old_state: Optional[Dict[str, Any]] = None  # Old questions (from Readora Lab)
        self.docx_path: Optional[str] = None
        self.selected_q_idx: int = 0
        self.selected_diff_idx: int = 0
        self.is_executing = False
        self._loading_editor = False

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
            bootstyle="dark-outline",
            command=self._open_docx_externally,
            state=tk.DISABLED
        )
        self.btn_open_docx.pack(side=tk.LEFT, padx=3)

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

        tbs.Button(
            btn_frame,
            text="❌  Dismiss",
            bootstyle="secondary-outline",
            command=self.destroy
        ).pack(side=tk.RIGHT, padx=6)

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
        left_frame = tbs.LabelFrame(workspace_frame, text=" Parsed Docx Questions ", bootstyle="info", width=340, padding=8)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(0, 8))
        left_frame.pack_propagate(False)

        q_cols = ("num", "snippet", "ans")
        self.q_tree = tbs.Treeview(
            left_frame, columns=q_cols, show="headings",
            selectmode="browse", bootstyle="dark"
        )
        self.q_tree.heading("num", text="#")
        self.q_tree.heading("snippet", text="Question Rubric")
        self.q_tree.heading("ans", text="Ans Key")

        self.q_tree.column("num", width=38, anchor=tk.CENTER)
        self.q_tree.column("snippet", width=230)
        self.q_tree.column("ans", width=55, anchor=tk.CENTER)

        self.q_tree.tag_configure("valid", foreground="#7fffa0")
        self.q_tree.tag_configure("odd", background="#222222")
        self.q_tree.tag_configure("even", background="#1a1a1a")

        q_scroll = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self.q_tree.yview)
        self.q_tree.configure(yscroll=q_scroll.set)
        self.q_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        q_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.q_tree.bind("<<TreeviewSelect>>", self._on_question_selected)

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
        def docx_worker():
            res = prepare_story_review(
                self.story,
                self.config,
                download_if_missing=True,
                on_status=lambda msg: self.safe_after(0, self._update_status, msg)
            )
            self.safe_after(0, self._on_docx_preparation_done, res)

        threading.Thread(target=docx_worker, daemon=True).start()

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
            self.old_state = res["old_state"]
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

    def _on_docx_preparation_done(self, res: Dict[str, Any]):
        self.pbar.stop()
        self.pbar.pack_forget()

        if not res["success"]:
            self.lbl_status.config(text=f"❌ Error: {res.get('error')}", bootstyle="danger")
            self.lbl_new_badge.config(text="Docx Failed", bootstyle="inverse-danger")
            messagebox.showerror("Error", res.get("error", "Failed to prepare story review."), parent=self)
            return

        self.questions = res["questions"]
        self.docx_path = res["docx_path"]
        self.btn_open_docx.config(state=tk.NORMAL)

        total_q = len(self.questions)
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
            text=f"✅ Ready! Docx parsed with {total_q} questions. Review Old vs New comparison below.",
            bootstyle="success"
        )

        self.btn_apply.config(state=tk.NORMAL)
        self.btn_dry_run_test.config(state=tk.NORMAL)
        self.btn_ai_resolve.config(state=tk.NORMAL)

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

        # Automatically start fetching Readora state if not yet fetched
        if self.old_state is None:
            self._fetch_readora_state_async()

    def _on_ai_resolve_answers(self):
        """Uses Groq AI to detect freeform answer keys or styling-based answers (highlights, underlines, etc.)."""
        if not self.questions or not self.docx_path:
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

        self.btn_ai_resolve.config(state=tk.DISABLED, text="⚡ Resolving...")
        self.lbl_status.config(text="🤖 Groq AI analyzing document for freeform answer keys and styling annotations...", bootstyle="warning")
        self.pbar.pack(side=tk.RIGHT)
        self.pbar.start(10)

        def worker():
            res = GroqAnswerResolver.resolve_answers_with_groq(
                self.docx_path,
                self.questions,
                self.config
            )
            self.safe_after(0, self._on_ai_resolve_completed, res)

        threading.Thread(target=worker, daemon=True).start()

    def _on_ai_resolve_completed(self, res: Dict[str, Any]):
        self.pbar.stop()
        self.pbar.pack_forget()
        self.btn_ai_resolve.config(state=tk.NORMAL, text="⚡ AI Resolve Keys (Groq)")

        if res["success"]:
            self.questions = res["questions"]
            changes_cnt = res.get("changes_count", 0)
            self._populate_questions_tree()
            self._populate_diff_tree()

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
    def _populate_diff_tree(self):
        for item in self.diff_tree.get_children():
            self.diff_tree.delete(item)

        old_questions = (self.old_state or {}).get("questions", [])
        new_questions = self.questions

        max_rows = max(len(old_questions), len(new_questions))
        if max_rows == 0:
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

            ans = q.get("answer", "A")
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

    # ══════════════════════════════════════════════════════════════════════════
    # ACCEPT & APPLY / DRY RUN TEST ACTIONS
    # ══════════════════════════════════════════════════════════════════════════
    def _on_accept_and_apply(self):
        """Live application to Readora Lab with user approval."""
        if self.is_executing:
            return

        story_name = self.story.get("story_name", "Story")
        num_q = len(self.questions)
        old_count = len((self.old_state or {}).get("questions", []))

        msg = (
            f"Are you ready to write these changes live to Readora Lab?\n\n"
            f"• Story: {story_name}\n"
            f"• Current Questions on Readora (OLD): {old_count}\n"
            f"• Verified Questions to Write (NEW): {num_q}\n"
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
                self.questions,
                self.config,
                on_status=lambda s: self.safe_after(0, self._update_status, s)
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
            self.btn_apply.config(state=tk.NORMAL)
            self.btn_dry_run_test.config(state=tk.NORMAL)
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

        def worker():
            res = execute_dry_run_test(
                self.story,
                self.questions,
                self.config,
                on_status=lambda s: self.safe_after(0, self._update_status, s)
            )
            self.safe_after(0, self._on_dry_run_test_completed, res)

        threading.Thread(target=worker, daemon=True).start()

    def _on_dry_run_test_completed(self, res: Dict[str, Any]):
        self.pbar.stop()
        self.pbar.pack_forget()
        self.is_executing = False
        self.btn_apply.config(state=tk.NORMAL)
        self.btn_dry_run_test.config(state=tk.NORMAL)

        if res["success"]:
            # If old_state was captured during dry-run, store and update
            if "old_state" in res and res["old_state"]:
                self.old_state = res["old_state"]
                self._on_readora_state_fetched(res)

            old_count = len((self.old_state or {}).get("questions", []))
            new_count = len(self.questions)

            self.lbl_status.config(text="✅ Dry-run test succeeded! (Safe, no changes saved)", bootstyle="success")
            messagebox.showinfo(
                "Dry-Run Test Passed",
                f"Dry-run test verified successfully for '{res.get('story_name')}'!\n\n"
                f"• Matched Book on Readora: {res.get('matched_title')}\n"
                f"• Current Questions on Server (OLD): {old_count}\n"
                f"• Verified Questions to Apply (NEW): {new_count}\n"
                f"• Status: Safe test completed without database writes.\n\n"
                f"You can now review the Old vs New Diff and click 'Accept & Apply' whenever ready.",
                parent=self
            )
        else:
            err = res.get("error", "Unknown error")
            self.lbl_status.config(text=f"❌ Dry-run test failed: {err}", bootstyle="danger")
            messagebox.showerror("Dry-Run Failed", f"Dry-run test encountered an error:\n{err}", parent=self)
