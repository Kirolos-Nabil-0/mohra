import time
import sys
from pathlib import Path
from typing import Optional, Dict
from playwright.sync_api import sync_playwright, Page, BrowserContext

from modules.chrome_launcher import launch_chrome_for_automation, find_chrome_executable

class GoogleAuthenticator:
    """
    Manages Google / Gmail authentication and opens the assigned Google Sheet.
    Persists session data in the profile directory so login is only required once.
    """
    def __init__(self, config: Dict):
        self.config = config
        self.email = config.get("gmail_account", "mohrawagdy58@gmail.com")
        self.password = config.get("gmail_password", "")
        self.sheet_url = config.get("sheet_url", "https://docs.google.com/spreadsheets/d/14Nwv3_w83pvpAE7SqjDi2rMoQezuiCtJ0YCLC_w_2gk/edit?gid=0#gid=0")
        self.port = config.get("remote_debugging_port", 9222)
        self.profile_dir = Path(config.get("chrome_profile_dir", "./chrome_profile")).resolve()
        self.profile_dir.mkdir(parents=True, exist_ok=True)

    def launch_and_login(self, headless: bool = False) -> bool:
        """
        Launches Google Chrome, verifies/performs login to mohrawagdy58@gmail.com,
        and opens the target Google Sheet.
        """
        print(f"\n[GoogleAuth] Launching Chrome with profile at: {self.profile_dir}")
        print(f"[GoogleAuth] Target Gmail: {self.email}")

        # Step 1: Launch official Chrome with remote debugging (bypasses Google bot detection)
        launched = launch_chrome_for_automation(
            port=self.port,
            profile_dir=str(self.profile_dir),
            open_url="https://accounts.google.com"
        )

        with sync_playwright() as p:
            browser = None
            context = None
            page = None

            try:
                if launched:
                    print(f"[GoogleAuth] Connecting to Chrome on port {self.port}...")
                    browser = p.chromium.connect_over_cdp(f"http://localhost:{self.port}")
                    context = browser.contexts[0] if browser.contexts else browser.new_context()
                    page = context.pages[0] if context.pages else context.new_page()
                else:
                    # Fallback to persistent context with official chrome channel
                    print("[GoogleAuth] Launching persistent Chrome context...")
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=str(self.profile_dir),
                        channel="chrome",
                        headless=headless,
                        args=[
                            "--disable-blink-features=AutomationControlled",
                            "--no-first-run",
                            "--no-default-browser-check"
                        ]
                    )
                    page = context.pages[0] if context.pages else context.new_page()

                # Step 2: Check current Google session status
                page.goto("https://myaccount.google.com/?pli=1", timeout=30000)
                page.wait_for_timeout(2000)

                if "signin" in page.url or "accounts.google.com/ServiceLogin" in page.url or "v3/signin" in page.url:
                    print(f"[GoogleAuth] Not logged in. Entering credentials for {self.email}...")
                    self._perform_login(page)
                else:
                    print(f"[GoogleAuth] Google session active. Verified account.")

                # Step 3: Open the Google Sheet
                print(f"[GoogleAuth] Opening Google Sheet: {self.sheet_url}")
                page.goto(self.sheet_url, timeout=30000)
                page.wait_for_timeout(3000)
                print("[GoogleAuth] Google Sheet loaded successfully!")
                return True

            except Exception as e:
                print(f"[GoogleAuth] Error during Google authentication: {e}")
                return False

    def _perform_login(self, page: Page):
        """Attempts automated fill of email and password."""
        try:
            # Email input
            email_field = page.locator('input[type="email"]')
            email_field.wait_for(state="visible", timeout=10000)
            email_field.fill(self.email)
            print("[GoogleAuth] Entered email.")

            # Click Next
            page.locator('button:has-text("Next"), #identifierNext').click()
            page.wait_for_timeout(3000)

            # Check for password field
            if self.password:
                pass_field = page.locator('input[type="password"]')
                pass_field.wait_for(state="visible", timeout=10000)
                pass_field.fill(self.password)
                print("[GoogleAuth] Entered password.")

                # Click Next
                page.locator('button:has-text("Next"), #passwordNext').click()
                page.wait_for_timeout(4000)

            # Check if 2FA or verification prompt is needed
            if "challenge" in page.url or "signin/v2/challenge" in page.url:
                print("[GoogleAuth] Notice: Google requested a security check/2FA verification.")
                print("[GoogleAuth] Please complete the verification in the open Chrome browser.")
                # Give user time to approve 2FA
                page.wait_for_url("**/myaccount.google.com**", timeout=60000)
                print("[GoogleAuth] Verification completed!")

        except Exception as e:
            print(f"[GoogleAuth] Automated login step encountered: {e}")
            print("[GoogleAuth] Please complete sign-in in the open Chrome window if prompted.")
