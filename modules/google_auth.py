import time
import sys
from pathlib import Path
from typing import Optional, Dict, Tuple
from playwright.sync_api import sync_playwright, Page, BrowserContext

from modules.chrome_launcher import launch_chrome_for_automation, find_chrome_executable


class GoogleSecurityError(Exception):
    """Raised when an attempt is made to access Google Sheet without verified account."""
    pass


class GoogleAuthenticator:
    """
    Manages Google / Gmail authentication with strict rule enforcement:
    STRICT RULE: NEVER open or interact with the Google Sheet unless the active session
    is hard-verified to be logged in as mohrawagdy58@gmail.com (or configured gmail_account).
    """

    def __init__(self, config: Dict):
        self.config = config
        self.email = config.get("gmail_account", "mohrawagdy58@gmail.com").strip()
        self.password = config.get("gmail_password", "").strip()
        self.sheet_url = config.get(
            "sheet_url",
            "https://docs.google.com/spreadsheets/d/14Nwv3_w83pvpAE7SqjDi2rMoQezuiCtJ0YCLC_w_2gk/edit?pli=1&gid=0#gid=0"
        )
        self.port = config.get("remote_debugging_port", 9222)
        self.profile_dir = Path(config.get("chrome_profile_dir", "./chrome_profile")).resolve()
        self.profile_dir.mkdir(parents=True, exist_ok=True)

    def verify_login_status(self, context: Optional[BrowserContext] = None) -> Tuple[bool, str]:
        """
        Hard-verifies whether Chrome has an active session for self.email.
        Returns: (is_logged_in, status_message)
        """
        if context is None:
            # Connect temporarily over CDP to inspect session
            try:
                with sync_playwright() as p:
                    browser = p.chromium.connect_over_cdp(f"http://localhost:{self.port}")
                    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
                    return self._check_session_internal(ctx)
            except Exception as e:
                return False, f"Could not connect to Chrome on port {self.port}: {e}"

        return self._check_session_internal(context)

    def _check_session_internal(self, context: BrowserContext) -> Tuple[bool, str]:
        # Fast check: verify Google auth cookies exist
        cookies = context.cookies()
        google_cookie_names = {c["name"] for c in cookies if "google" in c.get("domain", "")}
        if "SID" not in google_cookie_names and "SAPISID" not in google_cookie_names:
            return False, f"Not logged in to Google (no auth session cookies found)."

        # Deep check: verify account identity via myaccount.google.com
        inspect_page = context.new_page()
        try:
            inspect_page.goto("https://myaccount.google.com/?pli=1", timeout=12000)
            inspect_page.wait_for_timeout(1500)

            cur_url = inspect_page.url.lower()
            if "signin" in cur_url or "about" in cur_url or "servicelogin" in cur_url:
                return False, f"Not logged in (redirected to {inspect_page.url})"

            content = inspect_page.content().lower()
            if self.email.lower() in content:
                return True, f"Verified active session for {self.email}"
            
            return False, f"Logged in, but active account does NOT match target {self.email}"
        except Exception as e:
            return False, f"Verification error: {e}"
        finally:
            try:
                inspect_page.close()
            except Exception:
                pass

    def launch_and_login(self, headless: bool = False, open_sheet_after_verified: bool = False) -> bool:
        """
        Launches Google Chrome, verifies/prompts login for self.email.
        
        STRICT RULE ENFORCEMENT:
        Under NO circumstances is self.sheet_url opened if the account is not verified.
        """
        print(f"\n[GoogleAuth] Launching Chrome with profile at: {self.profile_dir}")
        print(f"[GoogleAuth] Target Gmail: {self.email}")

        # Step 1: Launch Chrome pointing ONLY to accounts.google.com (NEVER the sheet)
        launched = launch_chrome_for_automation(
            port=self.port,
            profile_dir=str(self.profile_dir),
            open_url="https://accounts.google.com/ServiceLogin"
        )

        with sync_playwright() as p:
            try:
                if launched:
                    print(f"[GoogleAuth] Connecting to Chrome on port {self.port}...")
                    browser = p.chromium.connect_over_cdp(f"http://localhost:{self.port}")
                    context = browser.contexts[0] if browser.contexts else browser.new_context()
                else:
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

                # Step 2: Check session
                is_logged_in, status = self._check_session_internal(context)
                if is_logged_in:
                    print(f"[GoogleAuth] ✅ {status}")
                else:
                    print(f"[GoogleAuth] ⚠️ {status}")
                    # Attempt autofill if password provided
                    main_page = context.pages[0] if context.pages else context.new_page()
                    if "accounts.google.com" not in main_page.url:
                        main_page.goto("https://accounts.google.com/ServiceLogin", timeout=20000)
                    
                    if self.password:
                        self._perform_login(main_page)

                    # Re-check login
                    is_logged_in, status = self._check_session_internal(context)

                # Step 3: Strict Rule Enforcement
                if not is_logged_in:
                    print(f"[GoogleAuth] 🚫 STRICT RULE ENFORCED: Chrome is NOT logged into {self.email}!")
                    print(f"[GoogleAuth] 🚫 Google Sheet will NEVER be opened while unauthenticated.")
                    print(f"[GoogleAuth] 👉 Please log in to {self.email} in the open Chrome window.")
                    return False

                # Step 4: Only open sheet if verified AND explicitly requested
                if open_sheet_after_verified:
                    print(f"[GoogleAuth] ✅ Verified {self.email}. Opening Google Sheet...")
                    sheet_page = context.new_page()
                    sheet_page.goto(self.sheet_url, timeout=35000)
                    sheet_page.wait_for_timeout(3000)
                    print("[GoogleAuth] Google Sheet loaded successfully for authenticated user.")

                return True

            except Exception as e:
                print(f"[GoogleAuth] Error during Google authentication check: {e}")
                return False

    def safe_open_sheet(self) -> Tuple[bool, str]:
        """
        Safely opens the Google Sheet in Chrome ONLY if strictly verified as self.email.
        If not verified, refuses to open and returns error.
        """
        with sync_playwright() as p:
            try:
                browser = p.chromium.connect_over_cdp(f"http://localhost:{self.port}")
                context = browser.contexts[0] if browser.contexts else browser.new_context()

                is_logged, reason = self._check_session_internal(context)
                if not is_logged:
                    msg = (
                        f"🚫 STRICT RULE VIOLATION BLOCKED: Cannot open Google Sheet!\n"
                        f"Reason: Chrome is not logged in as {self.email} ({reason}).\n"
                        f"Please sign in to Google first."
                    )
                    print(f"[GoogleAuth] {msg}")
                    return False, msg

                # Verified! Open sheet
                page = context.new_page()
                page.goto(self.sheet_url, timeout=35000)
                return True, f"Google Sheet opened successfully for verified user {self.email}"
            except Exception as e:
                return False, f"Failed to open sheet: {e}"

    def _perform_login(self, page: Page):
        """Attempts automated fill of email and password if on login page."""
        try:
            if "signin" not in page.url and "ServiceLogin" not in page.url:
                return

            email_field = page.locator('input[type="email"]')
            if email_field.count() > 0 and email_field.is_visible():
                email_field.fill(self.email)
                print(f"[GoogleAuth] Entered email: {self.email}")
                page.locator('button:has-text("Next"), #identifierNext').click()
                page.wait_for_timeout(3000)

            if self.password:
                pass_field = page.locator('input[type="password"]')
                if pass_field.count() > 0 and pass_field.is_visible():
                    pass_field.fill(self.password)
                    print("[GoogleAuth] Entered password.")
                    page.locator('button:has-text("Next"), #passwordNext').click()
                    page.wait_for_timeout(4000)

        except Exception as e:
            print(f"[GoogleAuth] Autofill notice: {e}")
