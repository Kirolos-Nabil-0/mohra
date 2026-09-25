import sys
import os
import json
import threading
from pathlib import Path

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
else:
    BASE_DIR = Path(__file__).resolve().parent
    BUNDLE_DIR = BASE_DIR

CONFIG_PATH = BASE_DIR / "config.json"
_CONFIG_LOCK = threading.RLock()

def get_bundle_resource(rel_path: str) -> Path:
    """Returns path to a bundled resource file, falling back to BASE_DIR."""
    p = BUNDLE_DIR / rel_path
    if p.exists():
        return p
    return BASE_DIR / rel_path

DEFAULT_CONFIG = {
    "sheet_url": "https://docs.google.com/spreadsheets/d/14Nwv3_w83pvpAE7SqjDi2rMoQezuiCtJ0YCLC_w_2gk/edit?pli=1&gid=0#gid=0",
    "sheet_source": "url",
    "assigned_to": "Mohra",
    "gmail_account": "mohrawagdy58@gmail.com",
    "gmail_password": "Mohra7788123",
    "readora_login_url": "https://www.readoralab.com/auth/login",
    "readora_books_url": "https://www.readoralab.com/super_admin/books",
    "readora_email": "super9@test.com",
    "readora_password": "12345678",
    "question_header": "Choose the correct answer ",
    "preferred_language_file": "First language",
    "content_type": "None",
    "dry_run": True,
    "headless": False,
    "slow_mo_ms": 200,
    "chrome_mode": "auto",
    "remote_debugging_port": 9222,
    "cache_dir": "./cache",
    "background_check_interval_seconds": 300,
    "watch_downloads_folder": True,
    "auto_process_pending": True,
    "auto_update": True,
    "auto_update_apply": False,
    "update_check_interval_seconds": 3600,
    "github_repo": "Kirolos-Nabil-0/mohra",
    "update_url": "https://api.github.com/repos/Kirolos-Nabil-0/mohra/releases/latest",
    "update_zip_url": "",
    "log_file": "logs/background.log",
    "key_module_enabled": True,
    "key_module_run_on_startup": True,
    "key_module_periodic": False,
    "key_module_interval_seconds": 60,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "groq_api_key": "",
    "groq_model": "qwen/qwen3.8-27b"
}

def load_config() -> dict:
    with _CONFIG_LOCK:
        config_bundle = get_bundle_resource("config.json")
        example_bundle = get_bundle_resource("config.example.json")
        if not CONFIG_PATH.exists():
            try:
                import shutil
                if config_bundle.exists() and config_bundle.resolve() != CONFIG_PATH.resolve():
                    shutil.copyfile(config_bundle, CONFIG_PATH)
                elif example_bundle.exists() and example_bundle.resolve() != CONFIG_PATH.resolve():
                    shutil.copyfile(example_bundle, CONFIG_PATH)
            except Exception:
                pass

        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    user_cfg = json.load(f)
                    cfg = DEFAULT_CONFIG.copy()
                    cfg.update(user_cfg)
                    return cfg
            except Exception as e:
                print(f"Warning: Failed to parse config.json ({e}). Using defaults.")
        return DEFAULT_CONFIG.copy()

def save_config(cfg: dict):
    with _CONFIG_LOCK:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
