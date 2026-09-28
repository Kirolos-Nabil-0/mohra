"""A small Drive browser for choosing an ambiguous First Language document."""

import threading
import tkinter as tk
from tkinter import messagebox, ttk

import ttkbootstrap as tbs

from modules.drive_downloader import DriveDownloader


class DriveFilePicker(tbs.Toplevel):
    def __init__(self, parent, drive_url, story_name, cache_dir, on_choose, ai_suggestion=None):
        super().__init__(parent)
        self.parent = parent
        self.downloader = DriveDownloader(cache_dir=cache_dir)
        self.story_name = story_name
        self.ai_suggestion = ai_suggestion
        self.root_id = self.downloader.extract_folder_id(drive_url) or drive_url
        self.on_choose = on_choose
        self.folders = [(story_name or "Story folder", self.root_id)]
        self.items = {}
        self.request_number = 0

        self.title(f"Choose First Language file — {story_name}")
        self.geometry("760x520")
        self.minsize(580, 400)
        self.transient(parent)
        self.grab_set()

        header = tbs.Frame(self, padding=16)
        header.pack(fill=tk.X)
        tbs.Label(header, text="Choose the First Language file", font=("Segoe UI", 15, "bold")).pack(anchor=tk.W)
        tbs.Label(
            header,
            text=f"Story: {story_name}\nOpen folders and select the correct DOCX or Google Doc before applying changes.",
            wraplength=700,
        ).pack(anchor=tk.W, pady=(5, 0))

        nav = tbs.Frame(self, padding=(16, 0))
        nav.pack(fill=tk.X)
        self.back_button = tbs.Button(nav, text="← Back", command=self._back, bootstyle="secondary-outline")
        self.back_button.pack(side=tk.LEFT)
        self.path_label = tbs.Label(nav, text="", padding=(10, 0))
        self.path_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tbs.Button(nav, text="Refresh", command=self._load, bootstyle="secondary-outline").pack(side=tk.RIGHT)

        table = tbs.Frame(self, padding=(16, 10))
        table.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(table, columns=("type", "name", "match"), show="headings", selectmode="browse")
        self.tree.heading("type", text="Type")
        self.tree.heading("name", text="Name")
        self.tree.heading("match", text="Match")
        self.tree.column("type", width=120, stretch=False)
        self.tree.column("name", width=470)
        self.tree.column("match", width=125, stretch=False)
        scrollbar = ttk.Scrollbar(table, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._update_selection())
        self.tree.bind("<Double-1>", lambda _event: self._open_or_choose())
        self.tree.bind("<Return>", lambda _event: self._open_or_choose())

        footer = tbs.Frame(self, padding=(16, 0, 16, 16))
        footer.pack(fill=tk.X)
        self.hint = tbs.Label(footer, text="Loading Drive folder…", wraplength=690)
        self.hint.pack(anchor=tk.W, pady=(0, 9))
        buttons = tbs.Frame(footer)
        buttons.pack(fill=tk.X)
        tbs.Button(buttons, text="Cancel", command=self._close, bootstyle="secondary-outline").pack(side=tk.RIGHT)
        self.choose_button = tbs.Button(
            buttons, text="Use as First Language", command=self._choose,
            bootstyle="primary", state=tk.DISABLED,
        )
        self.choose_button.pack(side=tk.RIGHT, padx=(0, 8))
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _event: self._close())
        self._load()

    def _close(self):
        self.destroy()
        try:
            if self.parent.winfo_exists():
                self.parent.grab_set()
        except tk.TclError:
            pass

    def _load(self):
        self.request_number += 1
        request = self.request_number
        folder_id = self.folders[-1][1]
        self.path_label.config(text=" / ".join(name for name, _ in self.folders))
        self.back_button.config(state=tk.NORMAL if len(self.folders) > 1 else tk.DISABLED)
        self.choose_button.config(state=tk.DISABLED)
        self.hint.config(text="Loading Drive folder…")
        for row in self.tree.get_children():
            self.tree.delete(row)

        def worker():
            try:
                items = self.downloader.get_folder_items(folder_id)
                error = None
            except Exception as exc:
                items, error = {}, str(exc)
            try:
                self.after(0, self._show_items, request, items, error)
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _show_items(self, request, items, error):
        if not self.winfo_exists() or request != self.request_number:
            return
        self.items = items
        ordered = sorted(items.items(), key=lambda pair: (
            pair[1][1] != "folder",
            not self.downloader._has_first_language_label(pair[0]),
            not self.downloader._matches_story_name(pair[0], self.story_name),
            pair[0] != self.ai_suggestion,
            pair[0].casefold(),
        ))
        for index, (name, (_, kind)) in enumerate(ordered):
            label = {"folder": "Folder", "docx": "DOCX", "google_doc": "Google Doc"}[kind]
            if self.downloader._has_second_language_label(name):
                match = "Second Language"
            elif self.downloader._has_first_language_label(name):
                match = "First Language"
            elif kind != "folder" and self.downloader._matches_story_name(name, self.story_name):
                match = "Story title"
            elif kind != "folder" and name == self.ai_suggestion:
                match = "AI suggestion"
            else:
                match = ""
            self.tree.insert("", tk.END, iid=str(index), values=(label, name, match))
        if error:
            self.hint.config(text=f"Could not load this folder: {error}. Try Refresh or Back.")
        elif items:
            self.hint.config(text="Open a folder or select a document. AI suggestions need your verification. Second Language files cannot be chosen.")
        else:
            self.hint.config(text="No DOCX files, Google Docs, or folders found here. Try Back or Refresh.")

    def _selected(self):
        selected = self.tree.selection()
        if not selected:
            return None
        name = self.tree.item(selected[0], "values")[1]
        return (name, *self.items[name])

    def _update_selection(self):
        item = self._selected()
        second_language = any(self.downloader._has_second_language_label(name) for name, _ in self.folders[1:])
        allowed = bool(item and item[2] in ("docx", "google_doc")
                       and not self.downloader._has_second_language_label(item[0])
                       and not second_language)
        self.choose_button.config(state=tk.NORMAL if allowed else tk.DISABLED)
        if item and item[2] != "folder" and not allowed:
            self.hint.config(text="This file or folder is labelled Second Language. Choose a different file.")
        elif allowed:
            self.hint.config(text=f"Selected: {item[0]}. This document will be used for the First Language review.")

    def _open_or_choose(self):
        item = self._selected()
        if not item:
            return
        if item[2] == "folder":
            self.folders.append((item[0], item[1]))
            self._load()
        else:
            self._choose()

    def _back(self):
        if len(self.folders) > 1:
            self.folders.pop()
            self._load()

    def _choose(self):
        item = self._selected()
        if not item or item[2] not in ("docx", "google_doc"):
            return
        name, file_id, kind = item
        folder_names = [folder_name for folder_name, _ in self.folders[1:]]
        if self.downloader._has_second_language_label(name) or any(
            self.downloader._has_second_language_label(folder_name) for folder_name in folder_names
        ):
            messagebox.showwarning("Second Language file", "Choose a First Language document.", parent=self)
            return
        selection = {"folder_id": self.folders[-1][1], "file_id": file_id,
                     "name": name, "kind": kind, "folder_names": folder_names}
        self._close()
        self.on_choose(selection)
