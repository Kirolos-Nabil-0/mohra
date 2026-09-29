import time
import re
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from playwright.sync_api import sync_playwright, Page, BrowserContext

from modules.google_auth import GoogleAuthenticator, GoogleSecurityError
from modules.sheet_parser import PATCH_SIZE, SheetParser, extract_spreadsheet_id


class GoogleSheetUpdater:
    """
    Automates marking story rows as 'Done' in Column D of the Google Spreadsheet.
    
    STRICT RULE:
    NEVER open, navigate to, or modify the Google Sheet unless Chrome is verified
    to have an active login session for mohrawagdy58@gmail.com.
    """

    def __init__(self, config: Dict):
        self.config = config
        self.email = config.get("gmail_account", "mohrawagdy58@gmail.com").strip()
        self.sheet_url = config.get(
            "sheet_url",
            "https://docs.google.com/spreadsheets/d/14Nwv3_w83pvpAE7SqjDi2rMoQezuiCtJ0YCLC_w_2gk/edit?pli=1&gid=0#gid=0"
        )
        self.port = config.get("remote_debugging_port", 9222)
        self.auth = GoogleAuthenticator(config)

    def claim_patch(self, selected_stories, assigned_to: str) -> Dict[str, Any]:
        """Claim a selected group of unassigned stories in the live Google Sheet."""
        owner = str(assigned_to or "").strip()
        if not owner:
            return {"success": False, "error": "Configure the teammate name before claiming a PATCH."}
        if not selected_stories:
            return {"success": False, "error": "The selected PATCH has no stories."}
        if len(selected_stories) > PATCH_SIZE:
            return {"success": False, "error": f"A PATCH can contain at most {PATCH_SIZE} stories."}

        sheet_name = str(selected_stories[0].get("sheet_name", ""))
        if not sheet_name or any(s.get("sheet_name") != sheet_name for s in selected_stories):
            return {"success": False, "error": "A PATCH must contain stories from one grade tab."}
        selected_rows = [int(story.get("row_index", 0)) for story in selected_stories]
        if any(row < 1 for row in selected_rows) or len(set(selected_rows)) != len(selected_rows):
            return {"success": False, "error": "The selected PATCH contains invalid or duplicate worksheet rows."}

        # Read the live workbook immediately before editing. Never use a stale
        # cached copy to decide whether another teammate has claimed a row.
        parser = SheetParser(
            sheet_url=self.sheet_url,
            assigned_to="",
            cache_dir=self.config.get("cache_dir", "./cache"),
            sheet_source="url",
            include_all_assignments=True,
        )
        try:
            live_stories = parser.parse_stories(force_download=True)
        except Exception as exc:
            return {"success": False, "error": f"Could not refresh the global Sheet before claiming: {exc}"}
        if parser.last_refresh_error:
            return {
                "success": False,
                "error": f"Could not verify the latest global Sheet; no rows were claimed: {parser.last_refresh_error}",
            }

        by_key = {(s["sheet_name"], int(s["row_index"])): s for s in live_stories}
        fresh_stories = []
        for selected in selected_stories:
            key = (selected.get("sheet_name"), int(selected.get("row_index", 0)))
            current = by_key.get(key)
            if current is None:
                return {
                    "success": False,
                    "conflict": True,
                    "error": f"The selected story at {key[0]} row {key[1]} is no longer in the global Sheet. Refresh and choose a PATCH again.",
                }
            selected_name = str(selected.get("raw_story_name", selected.get("story_name", ""))).strip().casefold()
            live_name = str(current.get("raw_story_name", current.get("story_name", ""))).strip().casefold()
            if selected_name and selected_name != live_name:
                return {
                    "success": False,
                    "conflict": True,
                    "error": f"The story at {key[0]} row {key[1]} changed. Refresh and choose a PATCH again.",
                }
            if not current.get("assignment_is_blank", False) or current.get("is_done", False):
                return {
                    "success": False,
                    "conflict": True,
                    "error": f"At least one story in this PATCH is now assigned or completed ({key[0]} row {key[1]}). No rows were changed; refresh and choose a PATCH again.",
                }
            fresh_stories.append(current)

        is_logged_in, status_msg = self.auth.verify_login_status()
        if not is_logged_in:
            return {
                "success": False,
                "blocked_by_safety_rule": True,
                "error": f"Chrome is not verified as {self.email}: {status_msg}",
            }

        written_cells = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(f"http://localhost:{self.port}")
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                is_logged_in, status_msg = self.auth.verify_login_status(context)
                if not is_logged_in:
                    return {
                        "success": False,
                        "blocked_by_safety_rule": True,
                        "error": f"Chrome is not verified as {self.email}: {status_msg}",
                    }

                spreadsheet_id = extract_spreadsheet_id(self.sheet_url)
                sheet_page = next(
                    (page for page in context.pages if spreadsheet_id and spreadsheet_id in page.url),
                    None,
                )
                if sheet_page is None:
                    sheet_page = context.new_page()
                    sheet_page.goto(self.sheet_url, timeout=40000)
                else:
                    sheet_page.bring_to_front()

                sheet_page.wait_for_selector(
                    "#waffle-grid-container, .grid-container, #t-name-box, .docs-sheet-tab-name",
                    timeout=30000,
                )
                sheet_page.wait_for_timeout(1500)
                if not self._switch_to_sheet_tab(sheet_page, sheet_name):
                    raise RuntimeError(f"Could not select the exact grade tab '{sheet_name}'.")
                sheet_page.wait_for_timeout(300)

                # Re-check the live grid itself after the workbook refresh. This
                # closes the gap where another teammate claims a row between
                # the XLSX preflight and our browser write.
                target_cells = []
                for story in fresh_stories:
                    row = int(story["row_index"])
                    columns = story.get("assignment_columns") or ["C"]
                    for column in columns:
                        cell = f"{column}{row}"
                        if not self._select_cell(sheet_page, cell):
                            raise RuntimeError(f"Could not select {sheet_name}!{cell}.")
                        current_value = self._read_selected_cell(sheet_page)
                        if current_value.strip():
                            return {
                                "success": False,
                                "conflict": True,
                                "error": (
                                    f"{sheet_name}!{cell} now contains an assignee. "
                                    "No cells were changed; refresh and choose a PATCH again."
                                ),
                            }
                        target_cells.append((cell, owner))

                for cell, value in target_cells:
                    if not self._select_cell(sheet_page, cell):
                        raise RuntimeError(f"Could not re-select {sheet_name}!{cell} for writing.")
                    written_cells.append(cell)
                    self._write_cell_value(sheet_page, value)

                self._wait_for_save(sheet_page, timeout_seconds=10.0)

        except Exception as exc:
            return {
                "success": False,
                "partial_write": bool(written_cells),
                "written_cells": written_cells,
                "error": (
                    f"PATCH claim stopped after writing {len(written_cells)} assignee cells: {exc}. "
                    "Refresh the Sheet and inspect these rows before retrying."
                ),
            }

        # Confirm the server-side workbook now contains the requested owner in
        # every assignee column before the GUI reports that the claim succeeded.
        try:
            verify_parser = SheetParser(
                sheet_url=self.sheet_url,
                assigned_to="",
                cache_dir=self.config.get("cache_dir", "./cache"),
                sheet_source="url",
                include_all_assignments=True,
            )
            verified_stories = verify_parser.parse_stories(force_download=True)
            if verify_parser.last_refresh_error:
                raise RuntimeError(verify_parser.last_refresh_error)
            verified_by_key = {
                (story["sheet_name"], int(story["row_index"])): story
                for story in verified_stories
            }
            for story in fresh_stories:
                current = verified_by_key.get((story["sheet_name"], int(story["row_index"])))
                expected_columns = story.get("assignment_columns") or ["C"]
                if current is None or any(
                    str(current.get("assignment_values", {}).get(column, "")).strip().casefold() != owner.casefold()
                    for column in expected_columns
                ):
                    raise RuntimeError(
                        f"The global Sheet did not confirm {story['sheet_name']} row {story['row_index']}."
                    )
                story["assignment_values"] = dict(current.get("assignment_values", {}))
                story["assigned_to"] = owner
                story["assignment_is_blank"] = False
                story["assignment_conflict"] = False
        except Exception as exc:
            return {
                "success": False,
                "partial_write": True,
                "written_cells": written_cells,
                "error": f"The claim may have been written, but read-back verification failed: {exc}",
            }

        return {
            "success": True,
            "sheet_name": sheet_name,
            "assigned_to": owner,
            "story_count": len(fresh_stories),
            "stories": fresh_stories,
            "written_cells": written_cells,
            "gmail_account": self.email,
        }

    def mark_story_done(
        self,
        story: Dict[str, Any],
        status_value: str = "Done",
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Marks Column D of the story's row in the Google Sheet as 'Done'.
        
        Args:
            story: Dict containing 'sheet_name' (e.g. 'Grade 1'), 'row_index' (e.g. 14), and 'story_name'.
            status_value: String to write into Column D (default 'Done').
            dry_run: If True, validates without writing.
            
        Returns:
            Dict with success status and details or error message.
        """
        story_name = story.get("story_name", "Unknown")
        sheet_name = story.get("sheet_name", "Grade 1")
        row_index = story.get("row_index")

        if not row_index:
            return {
                "success": False,
                "error": f"Story '{story_name}' has no row_index defined.",
                "blocked_by_safety_rule": False
            }

        cell_coord = f"D{row_index}"
        print(f"\n[GoogleSheetUpdater] Preparing to mark {cell_coord} in '{sheet_name}' as '{status_value}' for '{story_name}'...")

        if dry_run:
            print(f"[GoogleSheetUpdater] [DRY RUN] Would set cell {cell_coord} on tab '{sheet_name}' to '{status_value}'.")
            return {
                "success": True,
                "dry_run": True,
                "story_name": story_name,
                "sheet_name": sheet_name,
                "row_index": row_index,
                "cell": cell_coord,
                "value": status_value
            }

        with sync_playwright() as p:
            try:
                # ── STEP 1: Connect to Chrome CDP ──
                browser = p.chromium.connect_over_cdp(f"http://localhost:{self.port}")
                context = browser.contexts[0] if browser.contexts else browser.new_context()

                # ── STEP 2: HARD RULE CHECK ──
                # Verify Chrome is logged in as mohrawagdy58@gmail.com
                is_logged_in, status_msg = self.auth.verify_login_status(context)
                if not is_logged_in:
                    error_msg = (
                        f"🚫 STRICT RULE ENFORCED: Refusing to open Google Sheet!\n"
                        f"Chrome session is NOT logged in as {self.email}.\n"
                        f"Status: {status_msg}\n"
                        f"Please sign in to {self.email} in Chrome before updating the sheet."
                    )
                    print(f"[GoogleSheetUpdater] {error_msg}")
                    return {
                        "success": False,
                        "blocked_by_safety_rule": True,
                        "error": error_msg,
                        "story_name": story_name
                    }

                print(f"[GoogleSheetUpdater] ✅ Verified logged-in account: {self.email}. Proceeding safely.")

                # ── STEP 3: Find or Open Google Sheet Page ──
                spreadsheet_id = extract_spreadsheet_id(self.sheet_url)
                sheet_page: Optional[Page] = None
                for pg in context.pages:
                    if spreadsheet_id and spreadsheet_id in pg.url:
                        sheet_page = pg
                        break

                if sheet_page:
                    print(f"[GoogleSheetUpdater] Using existing open Google Sheet tab.")
                    sheet_page.bring_to_front()
                else:
                    print(f"[GoogleSheetUpdater] Opening Google Sheet in verified Chrome session...")
                    sheet_page = context.new_page()
                    sheet_page.goto(self.sheet_url, timeout=40000)

                # Wait for sheet grid to be ready
                sheet_page.wait_for_selector(
                    "#waffle-grid-container, .grid-container, #t-name-box, .docs-sheet-tab-name",
                    timeout=30000
                )
                sheet_page.wait_for_timeout(2000)

                # ── STEP 4: Switch to Worksheet Tab (e.g. 'Grade 1') ──
                tab_found = self._switch_to_sheet_tab(sheet_page, sheet_name)
                if not tab_found:
                    raise RuntimeError(f"Could not select the exact grade tab '{sheet_name}'.")

                sheet_page.wait_for_timeout(1000)

                # ── STEP 5: Focus Target Cell D{row_index} via Name Box ──
                cell_selected = self._select_cell(sheet_page, cell_coord)
                if not cell_selected:
                    raise RuntimeError(f"Failed to navigate to cell {cell_coord} in Google Sheet.")

                # ── STEP 6: Write 'Done' into Active Cell ──
                self._write_cell_value(sheet_page, status_value)

                # ── STEP 7: Confirm Save ──
                self._wait_for_save(sheet_page)

                print(f"[GoogleSheetUpdater] 🎉 SUCCESS! Marked cell {cell_coord} as '{status_value}' in '{sheet_name}' for '{story_name}'.")

                # Update in-memory story object
                story["comment"] = status_value
                story["is_done"] = True

                return {
                    "success": True,
                    "story_name": story_name,
                    "sheet_name": sheet_name,
                    "row_index": row_index,
                    "cell": cell_coord,
                    "value": status_value
                }

            except Exception as e:
                err = f"Failed to mark story done in Google Sheet: {e}"
                print(f"[GoogleSheetUpdater] Error: {err}")
                return {
                    "success": False,
                    "blocked_by_safety_rule": False,
                    "error": err,
                    "story_name": story_name
                }

    def _switch_to_sheet_tab(self, page: Page, sheet_name: str) -> bool:
        """Finds and clicks the sheet tab at the bottom of Google Sheets."""
        try:
            # Match exact tab name in bottom tab strip
            tab_locator = page.locator(f'.docs-sheet-tab-name:text-is("{sheet_name}")')
            if tab_locator.count() > 0:
                tab_locator.first.click()
                print(f"[GoogleSheetUpdater] Switched to tab '{sheet_name}'.")
                return True

            # Case-insensitive / partial match
            tab_locator = page.locator(f'div[role="tab"]:has-text("{sheet_name}")')
            if tab_locator.count() > 0:
                tab_locator.first.click()
                print(f"[GoogleSheetUpdater] Switched to tab '{sheet_name}' (partial match).")
                return True

            # Check all visible tab elements
            all_tabs = page.locator(".docs-sheet-tab-name").all_text_contents()
            print(f"[GoogleSheetUpdater] Available sheet tabs: {all_tabs}")
            for t_text in all_tabs:
                if sheet_name.strip().lower() in t_text.strip().lower():
                    page.locator(f'.docs-sheet-tab-name:text-is("{t_text}")').first.click()
                    print(f"[GoogleSheetUpdater] Matched and switched to tab '{t_text}'.")
                    return True

            return False
        except Exception as e:
            print(f"[GoogleSheetUpdater] Error selecting tab '{sheet_name}': {e}")
            return False

    def _select_cell(self, page: Page, cell_coord: str) -> bool:
        """Navigates to a specific cell (e.g. 'D14') using the Google Sheets Name Box."""
        expected = cell_coord.replace("$", "").strip().upper()

        def selection_confirmed() -> bool:
            name_box = page.locator("#t-name-box, input[aria-label='Name box'], .name-box-input")
            if name_box.count() == 0 or not name_box.first.is_visible():
                return False
            try:
                current = str(name_box.first.input_value() or "").replace("$", "").strip().upper()
            except Exception:
                return False
            return current == expected

        try:
            # 1. Try clicking Name Box
            name_box = page.locator("#t-name-box, input[aria-label='Name box'], .name-box-input")
            if name_box.count() > 0 and name_box.first.is_visible():
                name_box.first.click()
                page.wait_for_timeout(200)
                name_box.first.fill(cell_coord)
                page.keyboard.press("Enter")
                page.wait_for_timeout(350)
                if selection_confirmed():
                    print(f"[GoogleSheetUpdater] Jumped to cell {cell_coord} via Name Box.")
                    return True

            # 2. Try keyboard shortcut to focus Name Box (Ctrl+J or Cmd+J)
            page.keyboard.press("Control+j")
            page.wait_for_timeout(250)
            page.keyboard.type(cell_coord)
            page.keyboard.press("Enter")
            page.wait_for_timeout(350)
            if selection_confirmed():
                print(f"[GoogleSheetUpdater] Jumped to cell {cell_coord} via keyboard shortcut.")
                return True
            return False

        except Exception as e:
            print(f"[GoogleSheetUpdater] Cell selection error: {e}")
            return False

    def _read_selected_cell(self, page: Page) -> str:
        """Read the active cell from Google Sheets' formula bar."""
        formula_input = page.locator("#t-formula-bar-input")
        if formula_input.count() > 0 and formula_input.first.is_visible():
            try:
                return str(formula_input.first.input_value() or "")
            except Exception:
                pass

        formula_textbox = page.locator("div.cell-input[role='textbox']")
        if formula_textbox.count() > 0 and formula_textbox.first.is_visible():
            try:
                return str(formula_textbox.first.inner_text() or formula_textbox.first.text_content() or "")
            except Exception:
                pass
        raise RuntimeError("Could not read the selected cell from the Google Sheets formula bar.")

    def _write_cell_value(self, page: Page, value: str):
        """Writes the value into the selected cell via formula bar or keyboard entry."""
        # Try formula bar first
        formula_bar = page.locator("#t-formula-bar-input, div.cell-input[role='textbox']")
        if formula_bar.count() > 0 and formula_bar.first.is_visible():
            formula_bar.first.click()
            page.wait_for_timeout(200)
            # Select all existing text in formula bar and replace
            page.keyboard.press("Meta+a" if "darwin" in sys_platform() else "Control+a")
            page.keyboard.type(value)
            page.keyboard.press("Enter")
            page.wait_for_timeout(500)
            print(f"[GoogleSheetUpdater] Entered '{value}' into formula bar.")
        else:
            # Directly type into active cell
            page.keyboard.press("Enter")
            page.wait_for_timeout(200)
            page.keyboard.type(value)
            page.keyboard.press("Enter")
            page.wait_for_timeout(500)
            print(f"[GoogleSheetUpdater] Entered '{value}' via keyboard into active cell.")

    def _wait_for_save(self, page: Page, timeout_seconds: float = 6.0):
        """Waits for Google Sheets to sync changes to Google Drive."""
        start = time.time()
        while time.time() - start < timeout_seconds:
            page.wait_for_timeout(800)
            try:
                # Check for save status element or tooltip
                status_elem = page.locator("#docs-save-status, .docs-save-status")
                if status_elem.count() > 0:
                    text = status_elem.first.text_content() or ""
                    if "saved" in text.lower() or "drive" in text.lower():
                        print(f"[GoogleSheetUpdater] Confirmed: {text.strip()}")
                        return
            except Exception:
                pass
        print("[GoogleSheetUpdater] Auto-save delay elapsed.")


def sys_platform() -> str:
    import sys
    return sys.platform
