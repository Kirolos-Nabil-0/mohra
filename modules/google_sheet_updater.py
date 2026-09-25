import time
import re
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from playwright.sync_api import sync_playwright, Page, BrowserContext

from modules.google_auth import GoogleAuthenticator, GoogleSecurityError


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
                sheet_page: Optional[Page] = None
                for pg in context.pages:
                    if "docs.google.com/spreadsheets" in pg.url:
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
                    print(f"[GoogleSheetUpdater] Warning: Tab '{sheet_name}' not explicitly clicked, assuming active tab.")

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
        try:
            # 1. Try clicking Name Box
            name_box = page.locator("#t-name-box, input[aria-label='Name box'], .name-box-input")
            if name_box.count() > 0 and name_box.first.is_visible():
                name_box.first.click()
                page.wait_for_timeout(200)
                name_box.first.fill(cell_coord)
                page.keyboard.press("Enter")
                page.wait_for_timeout(500)
                print(f"[GoogleSheetUpdater] Jumped to cell {cell_coord} via Name Box.")
                return True

            # 2. Try keyboard shortcut to focus Name Box (Ctrl+J or Cmd+J)
            page.keyboard.press("Control+j")
            page.wait_for_timeout(250)
            page.keyboard.type(cell_coord)
            page.keyboard.press("Enter")
            page.wait_for_timeout(500)
            print(f"[GoogleSheetUpdater] Jumped to cell {cell_coord} via keyboard shortcut.")
            return True

        except Exception as e:
            print(f"[GoogleSheetUpdater] Cell selection error: {e}")
            return False

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
