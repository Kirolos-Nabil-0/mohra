# GUI Improvement Plan — Mohra App

## Overview

Improve the visual quality and usability of the Tkinter desktop GUI (`gui.py`) by:
- Adopting `ttkbootstrap` for modern Bootstrap-inspired widget styling
- Adding a branded header bar with app identity and live story counters
- Giving the DRY RUN toggle visual prominence (colored badge)
- Color-coding treeview rows by story status (pending / completed / error)
- Styling action buttons with semantic colors (primary / danger / secondary)
- Polishing the status bar with an icon prefix and a story count summary
- Keeping the dark terminal log console exactly as-is (intentional design)
- Adding `ttkbootstrap` to `requirements.txt`

**Theme choice:** Dark theme (`darkly`) — aligns with the existing dark log console aesthetic.  
**Scope:** Only `gui.py` and `requirements.txt` are modified. No logic changes.

---

## Sub-Task 1 — Add ttkbootstrap dependency

**Intent**  
Register `ttkbootstrap` in the project's declared dependencies so the environment can be reproduced correctly.

**Expected Outcomes**
- `ttkbootstrap>=1.10.0` appears in `requirements.txt`

**Todo List**
1. Append `ttkbootstrap>=1.10.0` to `requirements.txt`

**Relevant Context**
- [`requirements.txt`](requirements.txt)
- `Pillow` is already present (shared dependency — no conflict)

**Status:** [ ] pending

---

## Sub-Task 2 — Replace root window bootstrap and apply dark theme

**Intent**  
Swap `tk.Tk()` for `ttkbootstrap.Window` with the `darkly` theme. This single change re-skins all `ttk` widgets throughout the app automatically with no per-widget changes needed.

**Expected Outcomes**
- App launches with a dark (`darkly`) Bootstrap-style theme
- All existing `ttk` widgets (buttons, labels, frames, combobox, progressbar, treeview) are automatically re-styled
- Window geometry and minimum size remain unchanged

**Todo List**
1. Add `import ttkbootstrap as ttk_bs` at the top of `gui.py` alongside existing imports
2. In [`main()`](gui.py:355), replace `root = tk.Tk()` with `root = ttk_bs.Window(themename="darkly")`
3. Remove any manual `ttk.Style()` calls if added later that conflict

**Relevant Context**
- [`gui.py` `main()`](gui.py:355-362)
- `ttkbootstrap.Window` is a drop-in replacement for `tk.Tk()` — it accepts all the same geometry/title calls

**Status:** [ ] pending

---

## Sub-Task 3 — Add branded header bar

**Intent**  
Add a top banner row above all other frames that shows the app name, a subtitle, and live story count badges (total / pending / completed). This gives the app an identity and surfaces key stats immediately on open.

**Expected Outcomes**
- A full-width header frame renders at the very top of the window
- Displays: "🤖 Mohra" (large bold), "Readora Automation Tool" (small muted), and three colored badges: "Total: N", "Pending: N", "Done: N"
- Badge counts update whenever `_filter_stories()` is called
- Header background uses a slightly lighter dark shade to separate it from the body

**Todo List**
1. In `_setup_ui()`, before the top_frame is created, insert a new `header_frame` packed at the top
2. Add a bold large `ttk.Label` with text "🤖  Mohra" using `bootstyle="inverse-primary"` or explicit font sizing
3. Add a smaller subtitle label "Readora Automation Tool"
4. Add three badge labels: `self.lbl_total`, `self.lbl_pending`, `self.lbl_done` using `ttkbootstrap` badge styling
5. In `_filter_stories()`, after populating the treeview, update the three badge labels with current counts

**Relevant Context**
- [`gui.py` `_setup_ui()`](gui.py:53-155)
- [`gui.py` `_filter_stories()`](gui.py:242-267)
- ttkbootstrap label bootstyles: `"inverse-primary"`, `"inverse-success"`, `"inverse-warning"` for colored badges

**Status:** [ ] pending

---

## Sub-Task 4 — Restyle the DRY RUN toggle as a prominent visual badge

**Intent**  
The DRY RUN checkbox is a safety-critical control — users need to notice its state immediately. Currently it looks identical to any other checkbox. Make it visually prominent with a colored indicator that changes appearance based on its state.

**Expected Outcomes**
- When DRY RUN is ON: the checkbox area shows a green/success-styled indicator with text "✅ DRY RUN  —  Safe Mode (No DB Writes)"
- When DRY RUN is OFF: it shows a red/danger-styled indicator with text "⚠️  LIVE MODE  —  Database writes ENABLED"
- The label color updates dynamically when toggled

**Todo List**
1. Replace the plain `ttk.Checkbutton` with a `ttkbootstrap.Checkbutton` using `bootstyle="success-round-toggle"` (a large toggle switch widget)
2. Add a sibling `ttk.Label` `self.lbl_mode_indicator` next to the toggle in `top_frame`
3. In `_on_toggle_dry_run()`, update `self.lbl_mode_indicator`'s text and foreground color based on the new value

**Relevant Context**
- [`gui.py` lines 59-66](gui.py:59-66) — current checkbox definition
- [`gui.py` `_on_toggle_dry_run()`](gui.py:192-196)
- ttkbootstrap toggle style: `bootstyle="success-round-toggle"` renders as a pill-shaped on/off switch

**Status:** [ ] pending

---

## Sub-Task 5 — Apply semantic button styles

**Intent**  
Buttons currently all look identical. Give each button a semantic color that communicates its intent: primary actions are blue, destructive/stop is red, utility actions are secondary/outline.

**Expected Outcomes**
- "Process Selected Story" → `bootstyle="primary"` (blue, filled)
- "Process All Filtered" → `bootstyle="success"` (green, filled)
- "⏹ Stop Background Task" → `bootstyle="danger"` (red)
- "Refresh" → `bootstyle="secondary-outline"`
- "Select Sheet File..." → `bootstyle="secondary"`
- "Sync from Google Sheets" → `bootstyle="info"`
- "Open Chrome (Google Login)" → `bootstyle="secondary"`

**Todo List**
1. For each `ttk.Button` in `_setup_ui()`, replace with `ttk_bs.Button(..., bootstyle=<style>)` or add the bootstyle kwarg if using ttkbootstrap's `ttk.Button` alias
2. Ensure the Stop button's disabled visual state still renders correctly (ttkbootstrap respects `state=tk.DISABLED`)

**Relevant Context**
- [`gui.py` lines 74-111](gui.py:74-111) — all button definitions
- ttkbootstrap button bootstyles: `"primary"`, `"success"`, `"danger"`, `"info"`, `"secondary"`, `"secondary-outline"`

**Status:** [ ] pending

---

## Sub-Task 6 — Color-code treeview rows by story status

**Intent**  
All treeview rows currently look identical regardless of status. Color-code them so users can instantly scan: green = completed, yellow = pending, red = error, default = neutral.

**Expected Outcomes**
- Rows where `status_text` starts with "Completed" have a green-tinted row background
- Rows where `status_text` is "Pending" have the default dark row color
- Rows where `status_text` contains "Error" or "Failed" have a red-tinted row background
- The treeview alternates subtle row shading for readability (zebra striping)
- Selected row highlight remains visible

**Todo List**
1. After creating `self.tree`, define tag styles using `self.tree.tag_configure("completed", background="#1a3a1a", foreground="#7fff7f")`
2. Define `"pending"` tag with default/muted colors
3. Define `"error"` tag with `background="#3a1a1a"`, `foreground="#ff7f7f"`
4. Define `"odd"` and `"even"` tags for zebra striping
5. In `_filter_stories()`, when calling `self.tree.insert(...)`, compute the appropriate tag(s) and pass as `tags=(tag,)` argument
6. Ensure tag colors fit the `darkly` theme palette (dark backgrounds, muted pastels for text)

**Relevant Context**
- [`gui.py` lines 117-134](gui.py:117-134) — treeview setup
- [`gui.py` lines 259-267](gui.py:259-267) — row insertion in `_filter_stories()`
- `ttk.Treeview.tag_configure()` supports `background`, `foreground`, `font`

**Status:** [ ] pending

---

## Sub-Task 7 — Polish the status bar

**Intent**  
The status bar is a single italic label next to a progress bar. Make it more informative and visually clear by adding a state icon prefix and separating the worker name from the message.

**Expected Outcomes**
- Status label shows an icon prefix: "⚙️ Running...", "✅ Finished", "💤 Idle", "❌ Error"
- Progress bar uses `bootstyle="success"` (green fill) during RUNNING state and `bootstyle="danger"` for ERROR state
- A thin horizontal separator (`ttk.Separator`) divides the treeview from the status bar

**Todo List**
1. Replace `ttk.Progressbar` with `ttk_bs.Progressbar` and set initial `bootstyle="info"`
2. In `_poll_background_events()`, when setting state RUNNING, also update `self.progress_bar.configure(bootstyle="success")`
3. When state is ERROR, update progressbar to `bootstyle="danger"`
4. When state is COMPLETED/STOPPED, reset progressbar to `bootstyle="info"` and set value to 0
5. Add a `ttk.Separator(orient=tk.HORIZONTAL)` between the tree_frame and status_frame

**Relevant Context**
- [`gui.py` lines 136-144](gui.py:136-144) — status bar definition
- [`gui.py` lines 162-190](gui.py:162-190) — event polling where status updates happen

**Status:** [ ] pending

---

## Sub-Task 8 — Final layout spacing and padding pass

**Intent**  
Tighten up consistent padding, add visual separators between sections, and ensure the overall layout breathes properly with the new dark theme.

**Expected Outcomes**
- All `LabelFrame` sections have consistent `padding=10`
- `padx=12, pady=6` used consistently for all outer frames
- A `ttk.Separator` between the filter/action bar and the treeview
- The log console `LabelFrame` header text is slightly larger/bold to distinguish it as a section
- Window title updated to "Mohra — Readora Automation Tool"

**Todo List**
1. Audit all `pack()` calls and normalize `padx`/`pady` values to `padx=12, pady=6`
2. Add `ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=12)` between action_frame and tree_frame
3. Update `self.root.title(...)` to `"Mohra — Readora Automation Tool"`
4. Set `log_frame` label font to something slightly more prominent: pass `labelanchor="n"` and use a ttk style override if needed

**Relevant Context**
- [`gui.py` lines 53-156](gui.py:53-155) — full `_setup_ui()` method

**Status:** [ ] pending
