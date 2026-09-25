"""
Automatic Playwright Browser Installer & Verifier for Mohra
Ensures Playwright's Chromium browser is installed, including when running inside a frozen executable (.exe).
"""

import sys
import subprocess
from pathlib import Path


def ensure_playwright_chromium() -> bool:
    """
    Checks if Playwright Chromium browser is present.
    If not installed, attempts automatic installation and returns True on success.
    """
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            exe_path = p.chromium.executable_path
            if exe_path and Path(exe_path).exists():
                return True
    except Exception:
        # Chromium not yet installed or playwright failed to find binary
        pass

    print("[Playwright] Chromium browser not found. Installing Chromium (one-time setup)...")

    # Strategy 1: Use Playwright bundled driver executable (works inside PyInstaller .exe)
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
        driver_executable, driver_cli = compute_driver_executable()
        res = subprocess.run(
            [str(driver_executable), str(driver_cli), "install", "chromium"],
            env=get_driver_env(),
            capture_output=True,
            text=True
        )
        if res.returncode == 0:
            print("[Playwright] Chromium installed successfully!")
            return True
        else:
            print(f"[Playwright] Driver install output: {res.stderr or res.stdout}")
    except Exception as e:
        print(f"[Playwright] Driver execution failed: {e}")

    # Strategy 2: Fallback to python -m playwright install chromium (for dev/venv environments)
    try:
        res = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True
        )
        if res.returncode == 0:
            print("[Playwright] Chromium installed successfully via module!")
            return True
    except Exception as e:
        print(f"[Playwright] Module fallback failed: {e}")

    return False


if __name__ == "__main__":
    success = ensure_playwright_chromium()
    print("Result:", success)
