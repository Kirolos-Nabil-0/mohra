import os
import sys
import json
import shutil
import zipfile
import urllib.request
import subprocess
from pathlib import Path
from typing import Tuple, Optional, Dict, Any, Callable
from datetime import datetime

try:
    from config import BASE_DIR, get_bundle_resource
except ImportError:
    if getattr(sys, "frozen", False):
        BASE_DIR = Path(sys.executable).resolve().parent
    else:
        BASE_DIR = Path(__file__).resolve().parent.parent

    def get_bundle_resource(p: str) -> Path:
        return BASE_DIR / p

VERSION_FILE = BASE_DIR / "version.json"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
UPDATER_LOG = LOG_DIR / "updater.log"

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
DETACHED_PROCESS = 0x00000008 if sys.platform == "win32" else 0


def log_updater(message: str):
    line = f"{datetime.now().isoformat()} [Updater] {message}\n"
    print(line.strip())
    try:
        with open(UPDATER_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def run_silent_cmd(cmd: list, cwd: Path = BASE_DIR) -> Tuple[int, str, str]:
    """Runs a subprocess with completely hidden window on Windows."""
    try:
        res = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=CREATE_NO_WINDOW
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def parse_version(ver_str: str) -> tuple:
    """Parses version strings like 'v1.0.1' or '1.2.0-beta' into a comparable tuple."""
    if not ver_str:
        return (0, 0, 0)
    cleaned = ver_str.strip().lstrip("vV")
    parts = []
    for token in cleaned.split("."):
        digits = ""
        for ch in token:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def get_current_version() -> str:
    """Retrieves current application version."""
    # Check BASE_DIR first, then bundled resource
    targets = [VERSION_FILE, get_bundle_resource("version.json")]
    for target in targets:
        if target.exists():
            try:
                with open(target, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    ver = data.get("version")
                    if ver:
                        return ver
            except Exception:
                pass
    return "1.0.0"


class AutoUpdater:
    """
    Handles auto-updates for Mohra across:
    1. GitHub Releases (Binary distribution / Portable .exe / Setup installer)
    2. Git Repository (when running from source)
    3. Direct HTTP Package (Fallback)
    """

    def __init__(self, config: dict):
        self.config = config
        self.repo = config.get("github_repo", "Kirolos-Nabil-0/mohra")
        self.github_token = config.get("github_token") or os.getenv("GITHUB_TOKEN") or os.getenv("MOHRA_GITHUB_TOKEN", "")
        self.is_git_repo = (BASE_DIR / ".git").exists()
        self.is_frozen = getattr(sys, "frozen", False)

    def get_git_branch(self) -> str:
        code, out, _ = run_silent_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        return out.strip() if code == 0 and out else self.config.get("update_branch", "main")

    def check_for_updates(self) -> Tuple[bool, Dict[str, Any]]:
        """
        Checks whether a newer release is available on GitHub or Git.
        Returns: (has_update, update_info)
        """
        curr_ver_str = get_current_version()
        curr_ver = parse_version(curr_ver_str)
        info: Dict[str, Any] = {
            "has_update": False,
            "current_version": curr_ver_str,
            "latest_version": curr_ver_str,
            "title": "",
            "notes": "",
            "download_url": "",
            "installer_url": "",
            "source": "none",
            "summary": "Application is up to date."
        }

        # 1. Primary: Check GitHub Releases API
        if self.repo:
            log_updater(f"Checking GitHub Releases for '{self.repo}' (Current: v{curr_ver_str})...")
            try:
                api_url = f"https://api.github.com/repos/{self.repo}/releases/latest"
                req_headers = {
                    "User-Agent": "MohraAutoUpdater/1.0",
                    "Accept": "application/vnd.github.v3+json"
                }
                if self.github_token:
                    req_headers["Authorization"] = f"Bearer {self.github_token}"

                req = urllib.request.Request(api_url, headers=req_headers)
                with urllib.request.urlopen(req, timeout=12) as resp:
                    rel_data = json.loads(resp.read().decode("utf-8"))
                    tag = rel_data.get("tag_name", "")
                    remote_ver = parse_version(tag)
                    tag_clean = tag.lstrip("vV")

                    if remote_ver > curr_ver:
                        info["has_update"] = True
                        info["latest_version"] = tag_clean
                        info["title"] = rel_data.get("name") or f"Release {tag}"
                        info["notes"] = rel_data.get("body", "").strip()
                        info["source"] = "github_release"

                        # Find matching assets (Mohra-Windows.zip or Mohra_Setup_*.exe)
                        for asset in rel_data.get("assets", []):
                            name = asset.get("name", "").lower()
                            url = asset.get("browser_download_url", "")
                            if name.endswith(".zip") and "mohra" in name:
                                info["download_url"] = url
                            elif name.endswith(".exe") and ("setup" in name or "installer" in name):
                                info["installer_url"] = url

                        # Fallback download url if zip wasn't found
                        if not info["download_url"] and info["installer_url"]:
                            info["download_url"] = info["installer_url"]

                        info["summary"] = f"GitHub Release v{tag_clean} available (Current: v{curr_ver_str})"
                        log_updater(f"Found update: {info['summary']}")
                        return True, info
                    else:
                        log_updater(f"GitHub release v{tag_clean} is not newer than current v{curr_ver_str}.")
            except urllib.error.HTTPError as e:
                log_updater(f"GitHub Releases API returned {e.code}: {e.reason}")
            except Exception as e:
                log_updater(f"GitHub Releases check failed: {e}")

        # 2. Secondary: Check raw version.json on GitHub
        if self.repo:
            try:
                raw_url = f"https://raw.githubusercontent.com/{self.repo}/main/version.json"
                req_headers = {"User-Agent": "MohraAutoUpdater/1.0"}
                if self.github_token:
                    req_headers["Authorization"] = f"Bearer {self.github_token}"
                req = urllib.request.Request(raw_url, headers=req_headers)
                with urllib.request.urlopen(req, timeout=8) as resp:
                    raw_data = json.loads(resp.read().decode("utf-8"))
                    remote_raw_str = raw_data.get("version", "")
                    remote_ver = parse_version(remote_raw_str)
                    if remote_ver > curr_ver:
                        info["has_update"] = True
                        info["latest_version"] = remote_raw_str
                        info["title"] = f"Version {remote_raw_str}"
                        info["source"] = "github_raw"
                        info["summary"] = f"New version {remote_raw_str} available on GitHub."
                        return True, info
            except Exception as e:
                log_updater(f"GitHub raw version check notice: {e}")

        # 3. Development / Git repo mode
        if self.is_git_repo:
            branch = self.get_git_branch()
            log_updater(f"Checking for Git commits on branch '{branch}'...")
            code, _, _ = run_silent_cmd(["git", "fetch", "origin", branch])
            if code == 0:
                _, local_hash, _ = run_silent_cmd(["git", "rev-parse", "HEAD"])
                _, remote_hash, _ = run_silent_cmd(["git", "rev-parse", f"origin/{branch}"])
                if local_hash and remote_hash and local_hash != remote_hash:
                    _, commit_msg, _ = run_silent_cmd(["git", "log", "-1", "--pretty=format:%h: %s", remote_hash])
                    _, count_str, _ = run_silent_cmd(["git", "rev-list", "--count", f"{local_hash}..{remote_hash}"])
                    info["has_update"] = True
                    info["source"] = "git"
                    info["summary"] = f"Git: {count_str or '1'} new commit(s) -> {commit_msg}"
                    return True, info

        return False, info

    def download_file(self, url: str, target_path: Path, on_progress: Optional[Callable[[float, str], None]] = None) -> bool:
        """Downloads a remote file with progress reporting."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            req_headers = {"User-Agent": "MohraAutoUpdater/1.0"}
            if self.github_token:
                req_headers["Authorization"] = f"Bearer {self.github_token}"
                if "api.github.com" in url:
                    req_headers["Accept"] = "application/octet-stream"

            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                total_bytes = int(resp.headers.get("content-length", 0))
                downloaded = 0
                chunk_size = 65536

                with open(target_path, "wb") as f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_bytes > 0 and on_progress:
                            pct = min(100.0, (downloaded / total_bytes) * 100.0)
                            on_progress(pct, f"Downloaded {downloaded // 1024} KB / {total_bytes // 1024} KB ({int(pct)}%)")

            return target_path.exists() and target_path.stat().st_size > 0
        except Exception as e:
            log_updater(f"Download failed from {url}: {e}")
            if target_path.exists():
                target_path.unlink(missing_ok=True)
            return False

    def apply_update(self, update_info: Optional[Dict[str, Any]] = None, on_progress: Optional[Callable[[float, str], None]] = None) -> bool:
        """
        Applies update seamlessly:
        - If Git repository: pulls updates and syncs dependencies.
        - If packaged Windows App: downloads update package, launches detached updater script, and hot-restarts.
        """
        if update_info is None:
            has_update, update_info = self.check_for_updates()
            if not has_update:
                log_updater("No update available to apply.")
                return False

        source = update_info.get("source", "")
        log_updater(f"Applying update from source '{source}'...")

        # ── 1. Git Repository Update ──────────────────────────────────────────
        if self.is_git_repo and source == "git":
            branch = self.get_git_branch()
            run_silent_cmd(["git", "stash"])
            code, out, err = run_silent_cmd(["git", "pull", "origin", branch])
            if code != 0:
                code, out, err = run_silent_cmd(["git", "pull"])
            run_silent_cmd(["git", "stash", "pop"])

            if code == 0:
                log_updater("Git pull succeeded.")
                self._update_python_dependencies()
                return True
            else:
                log_updater(f"Git update failed: {err}")
                return False

        # ── 2. Binary / Packaged Executable Update ───────────────────────────
        download_url = update_info.get("download_url") or update_info.get("installer_url")
        if not download_url:
            log_updater("Cannot apply update: No download URL found in release assets.")
            return False

        cache_dir = BASE_DIR / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        is_installer = download_url.lower().endswith(".exe")
        target_file = cache_dir / ("update_installer.exe" if is_installer else "update.zip")

        if on_progress:
            on_progress(10.0, "Downloading latest update package...")
        log_updater(f"Downloading binary package from {download_url}...")

        success = self.download_file(download_url, target_file, on_progress)
        if not success:
            log_updater("Failed to download update binary.")
            return False

        if on_progress:
            on_progress(80.0, "Extracting and preparing update...")

        # If it's a Windows Installer .exe
        if is_installer:
            log_updater("Launching silent setup installer...")
            try:
                # Launch installer silently, which replaces files and relaunches
                subprocess.Popen(
                    [str(target_file), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                    creationflags=DETACHED_PROCESS
                )
                log_updater("Setup installer launched. Terminating current instance.")
                sys.exit(0)
            except Exception as e:
                log_updater(f"Failed to launch setup installer: {e}")
                return False

        # If it's a Zip package (portable Mohra-Windows.zip)
        extract_folder = cache_dir / "pending_update"
        if extract_folder.exists():
            shutil.rmtree(extract_folder, ignore_errors=True)
        extract_folder.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(target_file, "r") as z:
                # Extract all files
                z.extractall(extract_folder)
            target_file.unlink(missing_ok=True)
            log_updater(f"Extracted update package to {extract_folder}")
        except Exception as e:
            log_updater(f"Failed extracting zip package: {e}")
            return False

        # On Windows, replace files via detached script
        if sys.platform == "win32":
            return self._launch_windows_detached_updater(extract_folder)
        else:
            # On macOS / Linux
            try:
                for item in extract_folder.iterdir():
                    dst = BASE_DIR / item.name
                    if item.is_dir():
                        shutil.copytree(item, dst, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dst)
                shutil.rmtree(extract_folder, ignore_errors=True)
                log_updater("Update applied directly.")
                return True
            except Exception as e:
                log_updater(f"Direct update failed: {e}")
                return False

    def _launch_windows_detached_updater(self, update_folder: Path) -> bool:
        """
        Creates and executes a detached Windows batch script that:
        1. Waits 2 seconds for Mohra.exe to exit.
        2. Copies pending update files into BASE_DIR (protecting user data).
        3. Cleans up pending update files.
        4. Launches the updated Mohra.exe.
        """
        bat_path = BASE_DIR / "apply_update.bat"
        exe_name = "Mohra.exe" if (BASE_DIR / "Mohra.exe").exists() else "gui.py"

        bat_content = f"""@echo off
chcp 65001 >nul
title Updating Mohra...
echo [Updater] Waiting for application to exit...
timeout /t 2 /nobreak >nul

set "SOURCE={update_folder}"
set "DEST={BASE_DIR}"

echo [Updater] Copying updated files to %DEST%...
:: Copy updated files and folders
xcopy /s /e /y /q "%SOURCE%\\*" "%DEST%\\" >nul

:: Clean up pending files
rd /s /q "%SOURCE%" >nul 2>nul

echo [Updater] Starting updated application...
cd /d "%DEST%"
if exist "{exe_name}" (
    start "" "{exe_name}"
) else if exist "Mohra.exe" (
    start "" "Mohra.exe"
)

:: Self delete this batch script
del "%~f0" >nul 2>nul & exit
"""
        try:
            with open(bat_path, "w", encoding="utf-8") as f:
                f.write(bat_content)

            log_updater(f"Created detached updater script at {bat_path}")
            # Launch detached process with CREATE_NO_WINDOW
            subprocess.Popen(
                ["cmd.exe", "/c", str(bat_path)],
                cwd=str(BASE_DIR),
                creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW
            )
            log_updater("Detached updater process started. Exiting current process for clean replacement.")
            # Immediate exit
            sys.exit(0)
        except Exception as e:
            log_updater(f"Failed to launch detached batch updater: {e}")
            return False

    def _update_python_dependencies(self):
        """Silently upgrades requirements.txt in venv."""
        req_file = BASE_DIR / "requirements.txt"
        if not req_file.exists():
            return
        pip_exe = BASE_DIR / "venv" / "Scripts" / "pip.exe"
        if not pip_exe.exists():
            pip_exe = BASE_DIR / "venv" / "bin" / "pip"
        if pip_exe.exists():
            log_updater("Upgrading python dependencies in virtual environment...")
            run_silent_cmd([str(pip_exe), "install", "-r", str(req_file), "--quiet"])

    def restart_app(self):
        """Restarts the application cleanly."""
        log_updater("Hot-restarting Mohra application...")
        if self.is_frozen:
            exe_path = Path(sys.executable)
            subprocess.Popen([str(exe_path)], creationflags=DETACHED_PROCESS)
            sys.exit(0)

        if sys.platform == "win32":
            vbs = BASE_DIR / "silent_start.vbs"
            if vbs.exists():
                subprocess.Popen(["wscript.exe", str(vbs)], creationflags=CREATE_NO_WINDOW)
            else:
                pythonw = BASE_DIR / "venv" / "Scripts" / "pythonw.exe"
                if not pythonw.exists():
                    pythonw = "pythonw"
                subprocess.Popen([str(pythonw), str(BASE_DIR / "gui.py")], creationflags=CREATE_NO_WINDOW)
        else:
            python = BASE_DIR / "venv" / "bin" / "python"
            subprocess.Popen([str(python), str(BASE_DIR / "gui.py")])

        sys.exit(0)

    def check_and_apply_update_silently(self) -> bool:
        """Called by background service or auto-updater worker."""
        has_update, info = self.check_for_updates()
        if not has_update:
            return False

        log_updater(f"Background Auto-Updater found update: {info.get('summary')}")
        if self.config.get("auto_update_apply", True):
            return self.apply_update(info)
        return False
