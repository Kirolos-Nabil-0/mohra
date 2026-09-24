import os
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    "sheet_url": "https://docs.google.com/spreadsheets/d/14Nwv3_w83pvpAE7SqjDi2rMoQezuiCtJ0YCLC_w_2gk/edit?gid=0#gid=0",
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
    "update_check_interval_seconds": 3600,
    "update_url": "",
    "update_zip_url": "",
    "log_file": "logs/background.log"
}

def load_config() -> dict:
    example_path = BASE_DIR / "config.example.json"
    if not CONFIG_PATH.exists() and example_path.exists():
        try:
            import shutil
            shutil.copyfile(example_path, CONFIG_PATH)
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
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
