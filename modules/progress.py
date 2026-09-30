import json
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

try:
    from config import BASE_DIR
except ImportError:
    BASE_DIR = Path(__file__).resolve().parent.parent

class ProgressTracker:
    def __init__(self, filepath: str = "progress.json"):
        fp = Path(filepath)
        if not fp.is_absolute():
            fp = BASE_DIR / fp
        self.filepath = fp
        self._lock = threading.RLock()
        with self._lock:
            self.data = self._load()

    def _load(self) -> Dict:
        with self._lock:
            if self.filepath.exists():
                try:
                    with open(self.filepath, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
            return {"processed": {}, "summary": {"total_success": 0, "total_failed": 0, "total_dry_run": 0}}

    def _save(self):
        with self._lock:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)

    def is_processed(self, story_name: str) -> bool:
        with self._lock:
            key = story_name.strip().lower()
            entry = self.data["processed"].get(key)
            if entry and entry.get("status") == "SUCCESS" and not entry.get("dry_run"):
                return True

            # Canonical key fallback to handle apostrophes, dashes, and special characters
            from modules.title_utils import canonical_title_key
            target_canon = canonical_title_key(story_name)
            if target_canon:
                for p_key, p_entry in self.data["processed"].items():
                    stored_canon = p_entry.get("canonical_name") or canonical_title_key(p_entry.get("story_name", p_key))
                    if stored_canon == target_canon:
                        if p_entry.get("status") == "SUCCESS" and not p_entry.get("dry_run"):
                            return True
            return False

    def record_success(self, story: Dict, questions_count: int, dry_run: bool = False):
        with self._lock:
            from modules.title_utils import clean_story_title, canonical_title_key
            key = clean_story_title(story["story_name"]).lower()
            self.data["processed"][key] = {
                "story_name": story["story_name"],
                "canonical_name": canonical_title_key(story["story_name"]),
                "sheet_name": story.get("sheet_name"),
                "row_index": story.get("row_index"),
                "status": "SUCCESS",
                "questions_count": questions_count,
                "dry_run": dry_run,
                "timestamp": datetime.now().isoformat()
            }
            if dry_run:
                self.data["summary"]["total_dry_run"] += 1
            else:
                self.data["summary"]["total_success"] += 1
            self._save()

    def record_failure(self, story: Dict, error_message: str):
        with self._lock:
            from modules.title_utils import clean_story_title, canonical_title_key
            key = clean_story_title(story["story_name"]).lower()
            self.data["processed"][key] = {
                "story_name": story["story_name"],
                "canonical_name": canonical_title_key(story["story_name"]),
                "sheet_name": story.get("sheet_name"),
                "row_index": story.get("row_index"),
                "status": "FAILED",
                "error": str(error_message),
                "timestamp": datetime.now().isoformat()
            }
            self.data["summary"]["total_failed"] += 1
            self._save()

    def get_summary(self) -> Dict:
        with self._lock:
            return dict(self.data["summary"])

