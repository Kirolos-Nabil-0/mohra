import os
import sys
import subprocess
import time
import urllib.request
import json
from pathlib import Path
from typing import Optional

def find_chrome_executable() -> Optional[str]:
    if sys.platform == "win32":
        candidates = [
            os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
    elif sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
    else:
        # Linux
        for c in ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"]:
            import shutil
            p = shutil.which(c)
            if p:
                return p
    return None

def is_cdp_ready(port: int = 9222) -> bool:
    try:
        req = urllib.request.Request(f"http://localhost:{port}/json/version")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode())
            return "webSocketDebuggerUrl" in data
    except Exception:
        return False

def launch_chrome_for_automation(port: int = 9222, profile_dir: Optional[str] = None, open_url: str = "https://accounts.google.com") -> bool:
    if is_cdp_ready(port):
        print(f"Chrome is already running with remote debugging on port {port}.")
        return True

    chrome_exe = find_chrome_executable()
    if not chrome_exe:
        print("Error: Could not locate Google Chrome executable.")
        return False

    if not profile_dir:
        # Use a persistent automation profile in the project directory
        profile_dir = str(Path("./chrome_profile").resolve())

    os.makedirs(profile_dir, exist_ok=True)

    args = [
        chrome_exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        open_url
    ]

    print(f"Launching Chrome with remote debugging on port {port}...")
    try:
        if sys.platform == "win32":
            subprocess.Popen(args, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"Failed to launch Chrome: {e}")
        return False

    # Wait up to 10s for CDP to become ready
    for _ in range(20):
        time.sleep(0.5)
        if is_cdp_ready(port):
            print("Chrome remote debugging connected successfully!")
            return True

    return False
