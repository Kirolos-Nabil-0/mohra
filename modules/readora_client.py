import time
import re
import difflib
from typing import List, Dict, Optional, Any
from playwright.sync_api import sync_playwright, Page, BrowserContext, Browser

class ReadoraClient:
    def __init__(self, config: dict):
        self.config = config
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def start_browser(self):
        self.playwright = sync_playwright().start()
        port = self.config.get("remote_debugging_port", 9222)
        chrome_mode = self.config.get("chrome_mode", "auto")
        headless = self.config.get("headless", False)
        slow_mo = self.config.get("slow_mo_ms", 150)

        # 1. Try connecting over CDP if mode is auto or existing_chrome
        if chrome_mode in ("auto", "existing_chrome"):
            try:
                self.browser = self.playwright.chromium.connect_over_cdp(f"http://localhost:{port}")
                self.context = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
                self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
                print(f"[ReadoraClient] Connected to running Chrome via CDP on port {port}.")
                return
            except Exception:
                if chrome_mode == "existing_chrome":
                    raise ConnectionError(f"Could not connect to Chrome on port {port}. Please ensure Chrome is running.")

        # 2. Launch persistent context or standard browser
        profile_dir = self.config.get("chrome_profile_dir", "./chrome_profile")
        try:
            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=headless,
                slow_mo=slow_mo,
                args=["--no-first-run", "--no-default-browser-check"]
            )
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            print("[ReadoraClient] Launched browser with persistent profile.")
        except Exception as e:
            print(f"[ReadoraClient] Fallback to standard Chromium launch: {e}")
            self.browser = self.playwright.chromium.launch(headless=headless, slow_mo=slow_mo)
            self.context = self.browser.new_context()
            self.page = self.context.new_page()

    def close(self):
        try:
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass

    def login(self) -> bool:
        if not self.page:
            self.start_browser()

        login_url = self.config.get("readora_login_url", "https://www.readoralab.com/auth/login")
        email = self.config.get("readora_email", "super9@test.com")
        password = self.config.get("readora_password", "12345678")

        print("[ReadoraClient] Checking login status...")
        self.page.goto(self.config.get("readora_books_url", "https://www.readoralab.com/super_admin/books"), timeout=25000)
        self.page.wait_for_timeout(1000)

        # If already on super_admin, we are logged in
        if "/super_admin" in self.page.url:
            print("[ReadoraClient] Already logged in to Readora.")
            return True

        print(f"[ReadoraClient] Logging in as {email}...")
        self.page.goto(login_url, timeout=25000)
        self.page.wait_for_selector('input[type="text"], input[type="email"], input[placeholder*="Email"]', timeout=10000)

        # Fill Email
        email_input = self.page.locator('input[type="email"], input[name="email"], input[id*="email"], input:visible').first
        email_input.fill(email)

        # Fill Password
        pass_input = self.page.locator('input[type="password"]').first
        pass_input.fill(password)

        # Submit
        submit_btn = self.page.locator('button:has-text("تسجيل الدخول"), button:has-text("Log In"), button:has-text("Login")').first
        if submit_btn.is_visible():
            submit_btn.click()
        else:
            pass_input.press("Enter")

        self.page.wait_for_url("**/super_admin**", timeout=15000)
        print("[ReadoraClient] Successfully logged in!")
        return True

    def search_book(self, story_name: str) -> Optional[Dict]:
        books_url = self.config.get("readora_books_url", "https://www.readoralab.com/super_admin/books")
        print(f"[ReadoraClient] Searching for book: '{story_name}'...")
        self.page.goto(books_url, timeout=25000)
        self.page.wait_for_load_state("networkidle")

        # Find search bar in header
        search_input = self.page.locator('header input, input[placeholder*="Search"]').first
        search_input.wait_for(state="visible", timeout=10000)
        
        # Clean title for searching (remove underscores, quotes)
        clean_title = re.sub(r"[_']", " ", story_name)
        clean_title = re.sub(r"\s+", " ", clean_title).strip()

        search_input.fill(clean_title)
        search_input.press("Enter")
        self.page.wait_for_timeout(2000)
        self.page.wait_for_load_state("networkidle")

        # Inspect cards in main
        cards = self.page.locator('main a[href*="/super_admin/books/"]')
        count = cards.count()
        if count == 0:
            print(f"[ReadoraClient] No search results found for '{clean_title}'.")
            return None

        titles = []
        for i in range(count):
            txt = cards.nth(i).inner_text().strip()
            # If text is empty, check img alt or child text
            if not txt:
                txt = cards.nth(i).locator("img").get_attribute("alt") or ""
            titles.append((i, txt))

        # Best match
        norm_query = clean_title.lower()
        best_idx = None
        best_score = 0.0

        for idx, t in titles:
            norm_t = t.lower()
            if norm_query in norm_t or norm_t in norm_query:
                best_idx = idx
                break
            score = difflib.SequenceMatcher(None, norm_query, norm_t).ratio()
            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx is not None:
            matched_title = titles[best_idx][1]
            print(f"[ReadoraClient] Found matching book: '{matched_title}' (Index {best_idx})")
            
            # Find the corresponding edit button
            # In the card container:
            matched_card = cards.nth(best_idx).locator("..")
            edit_btn = matched_card.locator('button[aria-label="edit-book"]')
            if not edit_btn.is_visible():
                edit_btn = self.page.locator('button[aria-label="edit-book"]').nth(best_idx)

            return {
                "matched_title": matched_title,
                "card_index": best_idx,
                "edit_button": edit_btn
            }

        return None

    def open_book_edit(self, book_info: Dict) -> bool:
        print("[ReadoraClient] Navigating to book edit page...")
        edit_btn = book_info["edit_button"]
        edit_btn.click()
        self.page.wait_for_url("**/edit", timeout=15000)
        self.page.wait_for_load_state("networkidle")
        print(f"[ReadoraClient] Opened edit page: {self.page.url}")
        return True

    def edit_questions(self, questions: List[Dict], dry_run: bool = True) -> bool:
        print(f"[ReadoraClient] Opening 'Edit Questions' dialog ({len(questions)} questions to apply)...")
        edit_q_btn = self.page.locator('button:has-text("EDIT QUESTIONS"), button:has-text("Edit Questions")').first
        edit_q_btn.wait_for(state="visible", timeout=10000)
        edit_q_btn.click()

        # Wait for modal form
        modal_form = self.page.locator('.MuiModal-root form, form:has(h6:has-text("Edit Bulk Question"))').first
        modal_form.wait_for(state="visible", timeout=10000)

        # 1. Fill Question Header
        header_text = self.config.get("question_header", "Choose the correct answer ")
        print(f"[ReadoraClient] Setting Question Header to: '{header_text}'")
        header_input = modal_form.locator('input[type="text"]').first
        header_input.fill(header_text)

        # 2. Set Content Type to 'None'
        content_type_select = modal_form.locator('.MuiSelect-select').first
        if content_type_select.is_visible():
            curr_type = content_type_select.inner_text().strip()
            if curr_type.lower() != "none":
                print(f"[ReadoraClient] Changing Content Type from '{curr_type}' to 'None'...")
                content_type_select.click()
                self.page.wait_for_timeout(300)
                none_option = self.page.locator('ul[role="listbox"] li:has-text("None")').first
                if none_option.is_visible():
                    none_option.click()
                    self.page.wait_for_timeout(300)

        # 3. Delete any existing questions
        existing_delete_btns = modal_form.locator('form button[aria-label="deleteQuestion"]')
        existing_count = existing_delete_btns.count()
        if existing_count > 0:
            print(f"[ReadoraClient] Found {existing_count} existing questions. Deleting...")
            # Click delete button while existing questions remain
            for i in range(existing_count):
                btn = modal_form.locator('form button[aria-label="deleteQuestion"]').first
                if btn.is_visible():
                    btn.click()
                    self.page.wait_for_timeout(200)

        # 4. Add each question
        print(f"[ReadoraClient] Adding {len(questions)} MCQ questions...")
        add_mcq_btn = modal_form.locator('button:has-text("ADD MCQ QUESTION")').first

        for q_idx, q in enumerate(questions):
            print(f"  [Q {q_idx+1}/{len(questions)}] {q['question']} (Answer: {q['answer']})")
            add_mcq_btn.click()
            self.page.wait_for_timeout(300)

            # Get the newly created question form (last child form)
            q_form = modal_form.locator('form').last

            # Fill question rubric text
            rubric_input = q_form.locator('input[type="text"]').first
            rubric_input.fill(q["question"])

            # Add choices
            add_choice_btn = q_form.locator('button:has-text("ADD CHOICE")').first
            for c_idx, choice in enumerate(q["choices"]):
                add_choice_btn.click()
                self.page.wait_for_timeout(200)

                # Choice text input: index c_idx + 1 (since rubric is index 0)
                choice_input = q_form.locator('input[type="text"]').nth(c_idx + 1)
                choice_input.fill(choice["text"])

                # Checkbox for correct answer
                if choice["letter"].upper() == q["answer"].upper():
                    checkbox = q_form.locator('input[type="checkbox"]:not(.MuiSwitch-input)').nth(c_idx)
                    if not checkbox.is_checked():
                        checkbox.click()
                        self.page.wait_for_timeout(100)

            # Ensure Auto Corrected switch is ON
            auto_switch = q_form.locator('input[type="checkbox"].MuiSwitch-input').first
            if auto_switch.is_visible() and not auto_switch.is_checked():
                auto_switch.click()
                self.page.wait_for_timeout(100)

        # 5. Save or Dry Run
        if dry_run:
            print("\n[DRY RUN] All questions populated in UI! Dry-run mode enabled: NOT saving changes.")
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(500)
            return True
        else:
            print("\n[ReadoraClient] Saving questions: Clicking 'UPDATE' button...")
            update_btn = modal_form.locator('button:has-text("UPDATE")').first
            update_btn.click()
            self.page.wait_for_timeout(2000)
            self.page.wait_for_load_state("networkidle")
            print("[ReadoraClient] Questions saved successfully!")
            return True
