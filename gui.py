import sys
import threading
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from config import load_config, save_config
from modules.sheet_parser import SheetParser
from modules.drive_downloader import DriveDownloader
from modules.docx_parser import DocxParser
from modules.progress import ProgressTracker
from modules.chrome_launcher import launch_chrome_for_automation
from modules.readora_client import ReadoraClient

class MohraAppGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Mohra App - Readora Automation Tool")
        self.root.geometry("1020x720")
        self.root.minsize(800, 600)

        self.config = load_config()
        self.sheet_parser = SheetParser(
            sheet_url=self.config.get("sheet_url", ""),
            assigned_to=self.config.get("assigned_to", "Mohra"),
            cache_dir=self.config.get("cache_dir", "./cache")
        )
        self.downloader = DriveDownloader(cache_dir=self.config.get("cache_dir", "./cache"))
        self.progress_tracker = ProgressTracker()
        self.all_stories = []

        self._setup_ui()
        self._load_stories_threaded()

    def _setup_ui(self):
        # 1. Top Control Frame
        top_frame = ttk.LabelFrame(self.root, text="Configuration & Controls", padding=10)
        top_frame.pack(fill=tk.X, padx=10, pady=5)

        # Mode Indicator
        self.dry_run_var = tk.BooleanVar(value=self.config.get("dry_run", True))
        mode_chk = ttk.Checkbutton(
            top_frame,
            text="DRY RUN (Safe Mode - No DB Writes)",
            variable=self.dry_run_var,
            command=self._on_toggle_dry_run
        )
        mode_chk.grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)

        # Sheet source label
        src_text = f"Sheet Source: {self.sheet_parser.source_type} ({self.sheet_parser.xlsx_path.name if self.sheet_parser.xlsx_path else 'None'})"
        self.sheet_lbl = ttk.Label(top_frame, text=src_text, foreground="blue")
        self.sheet_lbl.grid(row=0, column=1, sticky=tk.W, padx=10, pady=2)

        # Select custom sheet button
        btn_browse = ttk.Button(top_frame, text="Select Sheet File...", command=self._browse_sheet)
        btn_browse.grid(row=0, column=2, padx=5, pady=2)

        # Chrome Launch button
        btn_chrome = ttk.Button(top_frame, text="Open Chrome (mohrawagdy58@gmail.com)", command=self._launch_chrome)
        btn_chrome.grid(row=0, column=3, padx=5, pady=2)

        # 2. Filter & Action Frame
        action_frame = ttk.Frame(self.root, padding=5)
        action_frame.pack(fill=tk.X, padx=10, pady=2)

        ttk.Label(action_frame, text="Search:").pack(side=tk.LEFT, padx=5)
        self.search_var = tk.StringVar()
        self.search_var.trace("w", lambda *args: self._filter_stories())
        ttk.Entry(action_frame, textvariable=self.search_var, width=25).pack(side=tk.LEFT, padx=5)

        ttk.Label(action_frame, text="Filter:").pack(side=tk.LEFT, padx=(15, 5))
        self.filter_var = tk.StringVar(value="Pending Only")
        filter_cb = ttk.Combobox(action_frame, textvariable=self.filter_var, values=["Pending Only", "All Stories", "Completed Only"], state="readonly", width=15)
        filter_cb.pack(side=tk.LEFT, padx=5)
        filter_cb.bind("<<ComboboxSelected>>", lambda e: self._filter_stories())

        # Process Buttons
        self.btn_run_selected = ttk.Button(action_frame, text="Process Selected Story", command=self._process_selected)
        self.btn_run_selected.pack(side=tk.RIGHT, padx=5)

        self.btn_run_all = ttk.Button(action_frame, text="Process All Filtered", command=self._process_all_filtered)
        self.btn_run_all.pack(side=tk.RIGHT, padx=5)

        btn_refresh = ttk.Button(action_frame, text="Refresh", command=self._load_stories_threaded)
        btn_refresh.pack(side=tk.RIGHT, padx=5)

        # 3. Main Treeview
        tree_frame = ttk.Frame(self.root, padding=5)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        columns = ("grade", "row", "story", "status", "has_drive")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("grade", text="Grade / Sheet")
        self.tree.heading("row", text="Row")
        self.tree.heading("story", text="Story Title")
        self.tree.heading("status", text="Status / Comment")
        self.tree.heading("has_drive", text="Drive Link")

        self.tree.column("grade", width=100, anchor=tk.CENTER)
        self.tree.column("row", width=60, anchor=tk.CENTER)
        self.tree.column("story", width=420)
        self.tree.column("status", width=220)
        self.tree.column("has_drive", width=90, anchor=tk.CENTER)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 4. Logs Console Frame
        log_frame = ttk.LabelFrame(self.root, text="Activity Logs", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=False, padx=10, pady=5)
        log_frame.config(height=180)

        self.log_text = tk.Text(log_frame, height=9, bg="#1e1e1e", fg="#00ff66", font=("Consolas", 10))
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscroll=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def log(self, message: str):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)

    def _on_toggle_dry_run(self):
        val = self.dry_run_var.get()
        self.config["dry_run"] = val
        save_config(self.config)
        self.log(f"[Config] Dry-Run mode set to: {val}")

    def _browse_sheet(self):
        filepath = filedialog.askopenfilename(
            title="Select Sheet File",
            filetypes=[("Excel Files", "*.xlsx"), ("All Files", "*.*")]
        )
        if filepath:
            self.sheet_parser = SheetParser(local_file_path=filepath)
            self.sheet_lbl.config(text=f"Sheet Source: custom ({Path(filepath).name})")
            self._load_stories_threaded()

    def _launch_chrome(self):
        self.log("[Chrome] Launching Chrome with remote debugging on port 9222...")
        t = threading.Thread(target=launch_chrome_for_automation, kwargs={"port": 9222}, daemon=True)
        t.start()

    def _load_stories_threaded(self):
        def worker():
            self.log("[Sheet] Loading stories...")
            try:
                self.all_stories = self.sheet_parser.parse_stories()
                self.root.after(0, self._filter_stories)
                done_count = sum(1 for s in self.all_stories if s["is_done"])
                pending_count = len(self.all_stories) - done_count
                self.log(f"[Sheet] Loaded {len(self.all_stories)} stories ({pending_count} pending, {done_count} completed).")
            except Exception as e:
                self.log(f"[Error] Failed loading stories: {e}")

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _filter_stories(self):
        query = self.search_var.get().strip().lower()
        filter_mode = self.filter_var.get()

        for item in self.tree.get_children():
            self.tree.delete(item)

        for s in self.all_stories:
            if filter_mode == "Pending Only" and s["is_done"]:
                continue
            if filter_mode == "Completed Only" and not s["is_done"]:
                continue
            if query and query not in s["story_name"].lower():
                continue

            has_link = "Yes" if s["drive_url"] else "No"
            self.tree.insert("", tk.END, values=(
                s["sheet_name"],
                s["row_index"],
                s["story_name"],
                s["comment"] or "Pending",
                has_link
            ))

    def _get_selected_story(self):
        selected = self.tree.selection()
        if not selected:
            return None
        values = self.tree.item(selected[0], "values")
        sheet_name, row_idx, story_name = values[0], int(values[1]), values[2]
        for s in self.all_stories:
            if s["sheet_name"] == sheet_name and s["row_index"] == row_idx:
                return s
        return None

    def _process_selected(self):
        story = self._get_selected_story()
        if not story:
            messagebox.showwarning("Warning", "Please select a story from the list first.")
            return

        dry_run = self.dry_run_var.get()
        mode_str = "DRY RUN (Safe)" if dry_run else "LIVE UPDATE"
        if not messagebox.askyesno("Confirm", f"Process '{story['story_name']}' in {mode_str} mode?"):
            return

        def worker():
            self._run_story(story)

        threading.Thread(target=worker, daemon=True).start()

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
        if not messagebox.askyesno("Confirm", f"Process all {len(stories_to_run)} stories in {mode_str} mode?"):
            return

        def worker():
            client = ReadoraClient(self.config)
            try:
                client.start_browser()
                client.login()
                for s in stories_to_run:
                    self._run_story(s, client=client)
            finally:
                client.close()
                self.log("[Automation] Batch complete.")

        threading.Thread(target=worker, daemon=True).start()

    def _run_story(self, story, client=None):
        self.log(f"\n--- Processing: {story['story_name']} ---")
        if not story["drive_url"]:
            self.log("[Error] No Drive URL found.")
            return

        # 1. Download
        path, status = self.downloader.download_story_docx(story["drive_url"])
        self.log(f"[Download] {status} -> {path}")
        if not path:
            return

        # 2. Parse
        qs = DocxParser.parse_comprehension_questions(path)
        self.log(f"[Parser] Extracted {len(qs)} Comprehension Questions.")
        for q in qs[:2]:
            self.log(f"  Q{q['num']}: {q['raw_question']} (Ans: {q['answer']})")

        # 3. Readora
        owns_client = False
        if client is None:
            owns_client = True
            client = ReadoraClient(self.config)
            client.start_browser()
            client.login()

        try:
            b_info = client.search_book(story["story_name"])
            if not b_info:
                self.log(f"[Readora] Book '{story['story_name']}' not found!")
                return
            client.open_book_edit(b_info)
            client.edit_questions(qs, dry_run=self.dry_run_var.get())
            self.log(f"[Success] Completed '{story['story_name']}'!")
        except Exception as e:
            self.log(f"[Error] Readora interaction failed: {e}")
        finally:
            if owns_client:
                client.close()

def main():
    root = tk.Tk()
    app = MohraAppGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
