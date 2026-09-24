import os
import sys
import json
import shutil
import zipfile
import urllib.request
import subprocess
from pathlib import Path
from typing import Tuple, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
VERSION_FILE = BASE_DIR / "version.json"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
UPDATER_LOG = LOG_DIR / "updater.log"

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

def log_updater(message: str):
    from datetime import datetime
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

def get_current_version() -> str:
    if VERSION_FILE.exists():
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("version", "1.0.0")
        except Exception:
            pass
    return "1.0.0"

class AutoUpdater:
    def __init__(self, config: dict):
        self.config = config
        self.update_url = config.get("update_url", "")
        self.is_git_repo = (BASE_DIR / ".git").exists()

    def get_git_branch(self) -> str:
        code, out, _ = run_silent_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        return out.strip() if code == 0 and out else self.config.get("update_branch", "main")

    def check_for_updates(self) -> Tuple[bool, str]:
        """
        Checks whether an update is available.
        Returns: (has_update, update_description)
        """
        # 1. Git method (Primary when git is logged in)
        if self.is_git_repo:
            branch = self.get_git_branch()
            log_updater(f"Checking for Git updates on branch '{branch}'...")
            
            # Fetch silently from remote
            code, _, err = run_silent_cmd(["git", "fetch", "origin", branch])
            if code != 0:
                # Also try general git fetch
                code, _, err = run_silent_cmd(["git", "fetch", "origin"])

            if code == 0:
                _, local_hash, _ = run_silent_cmd(["git", "rev-parse", "HEAD"])
                _, remote_hash, _ = run_silent_cmd(["git", "rev-parse", f"origin/{branch}"])
                
                if not remote_hash:
                    _, remote_hash, _ = run_silent_cmd(["git", "rev-parse", "@{u}"])

                if local_hash and remote_hash and local_hash != remote_hash:
                    # Get commit message and count
                    _, commit_msg, _ = run_silent_cmd(["git", "log", "-1", "--pretty=format:%h: %s", remote_hash])
                    _, count_str, _ = run_silent_cmd(["git", "rev-list", "--count", f"{local_hash}..{remote_hash}"])
                    return True, f"Git: {count_str or '1'} new commit(s) available -> {commit_msg}"
                else:
                    return False, f"Git: Up to date ({local_hash[:7] if local_hash else 'HEAD'})"
            else:
                log_updater(f"git fetch warning: {err}")

        # 2. HTTP / Remote Version JSON method (Fallback)
        if self.update_url:
            try:
                version_endpoint = self.update_url
                if not version_endpoint.endswith(".json") and "version" not in version_endpoint:
                    version_endpoint = version_endpoint.rstrip("/") + "/version.json"

                req = urllib.request.Request(
                    version_endpoint,
                    headers={"User-Agent": "MohraAutoUpdater/1.0"}
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    remote_info = json.loads(resp.read().decode("utf-8"))
                    remote_ver = remote_info.get("version", "")
                    curr_ver = get_current_version()
                    if remote_ver and remote_ver != curr_ver:
                        return True, f"HTTP: Version {remote_ver} available (Current: {curr_ver})"
            except Exception as e:
                log_updater(f"Failed checking HTTP update URL: {e}")

        return False, "Already up to date"

    def apply_update(self) -> bool:
        """
        Silently pulls and applies the update, syncs dependencies, and returns True on success.
        """
        log_updater("Initiating silent update deployment...")

        # 1. Apply via Git
        if self.is_git_repo:
            branch = self.get_git_branch()
            _, old_head, _ = run_silent_cmd(["git", "rev-parse", "HEAD"])

            # Stash any local non-tracked modifications so pull never conflicts
            run_silent_cmd(["git", "stash"])

            log_updater(f"Running git pull origin {branch}...")
            code, out, err = run_silent_cmd(["git", "pull", "origin", branch])
            if code != 0:
                log_updater(f"git pull origin {branch} failed ({err}). Trying default git pull...")
                code, out, err = run_silent_cmd(["git", "pull"])
                if code != 0:
                    log_updater(f"CRITICAL: git pull failed: {err}")
                    run_silent_cmd(["git", "stash", "pop"])
                    return False

            _, new_head, _ = run_silent_cmd(["git", "rev-parse", "HEAD"])
            log_updater(f"Git pull succeeded ({old_head[:7]} -> {new_head[:7]}): {out}")

            # Restore local stash if any
            run_silent_cmd(["git", "stash", "pop"])

            # Check if requirements.txt changed
            if old_head and new_head:
                _, diff_files, _ = run_silent_cmd(["git", "diff", "--name-only", old_head, new_head])
                if "requirements.txt" in diff_files:
                    log_updater("Detected changes in requirements.txt. Updating dependencies...")
                    self._update_python_dependencies()

            return True

        # 2. Apply via HTTP Zip archive (Fallback)
        if self.update_url and self.config.get("update_zip_url"):
            zip_url = self.config["update_zip_url"]
            log_updater(f"Downloading update package from {zip_url}...")
            temp_zip = BASE_DIR / "cache" / "update.zip"
            temp_zip.parent.mkdir(parents=True, exist_ok=True)

            try:
                req = urllib.request.Request(zip_url, headers={"User-Agent": "MohraAutoUpdater/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    with open(temp_zip, "wb") as f:
                        f.write(resp.read())

                protected = {"config.json", "progress.json", "logs", "cache", "venv"}

                with zipfile.ZipFile(temp_zip, "r") as z:
                    for member in z.infolist():
                        filename = Path(member.filename).parts[0] if Path(member.filename).parts else ""
                        if filename in protected or member.filename in protected:
                            continue
                        z.extract(member, BASE_DIR)

                temp_zip.unlink(missing_ok=True)
                log_updater("Extracted update package successfully.")
                self._update_python_dependencies()
                return True
            except Exception as e:
                log_updater(f"HTTP update failed: {e}")
                return False

        return False

    def _update_python_dependencies(self):
        """Silently installs any updated dependencies from requirements.txt."""
        req_file = BASE_DIR / "requirements.txt"
        if not req_file.exists():
            return

        pip_exe = BASE_DIR / "venv" / "Scripts" / "pip.exe"
        if not pip_exe.exists():
            pip_exe = BASE_DIR / "venv" / "bin" / "pip"

        if pip_exe.exists():
            log_updater("Silently upgrading python dependencies in virtual environment...")
            code, out, err = run_silent_cmd([str(pip_exe), "install", "-r", str(req_file), "--quiet"])
            if code == 0:
                log_updater("Dependencies updated successfully.")
            else:
                log_updater(f"pip install notice: {err}")

    def restart_app(self):
        """Restarts the background application silently on Windows/Mac."""
        log_updater("Hot-restarting Mohra automation background service...")

        if sys.platform == "win32":
            vbs_launcher = BASE_DIR / "silent_start.vbs"
            if vbs_launcher.exists():
                subprocess.Popen(["wscript.exe", str(vbs_launcher)], creationflags=CREATE_NO_WINDOW)
            else:
                pythonw_exe = BASE_DIR / "venv" / "Scripts" / "pythonw.exe"
                if not pythonw_exe.exists():
                    pythonw_exe = "pythonw"
                subprocess.Popen([str(pythonw_exe), str(BASE_DIR / "tray_app.py")], creationflags=CREATE_NO_WINDOW)
        else:
            python_exe = BASE_DIR / "venv" / "bin" / "python"
            subprocess.Popen([str(python_exe), str(BASE_DIR / "tray_app.py")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        log_updater("Service restart triggered. Terminating current instance.")
        sys.exit(0)

    def check_and_apply_update_silently(self) -> bool:
        """
        Complete one-step silent check, apply, and restart.
        Called automatically by background service.
        """
        has_update, info = self.check_for_updates()
        if not has_update:
            return False

        log_updater(f"New update detected: {info}")
        applied = self.apply_update()
        if applied:
            log_updater("Update applied successfully! Restarting service...")
            self.restart_app()
            return True
        return False
