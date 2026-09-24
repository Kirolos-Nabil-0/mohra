import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

class ProgressTracker:
    def __init__(self, filepath: str = "progress.json"):
        self.filepath = Path(filepath)
        self.data = self._load()

    def _load(self) -> Dict:
        if self.filepath.exists():
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"processed": {}, "summary": {"total_success": 0, "total_failed": 0, "total_dry_run": 0}}

    def _save(self):
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def is_processed(self, story_name: str) -> bool:
        entry = self.data["processed"].get(story_name.strip().lower())
        return bool(entry and entry.get("status") == "SUCCESS" and not entry.get("dry_run"))

    def record_success(self, story: Dict, questions_count: int, dry_run: bool = False):
        key = story["story_name"].strip().lower()
        self.data["processed"][key] = {
            "story_name": story["story_name"],
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
        key = story["story_name"].strip().lower()
        self.data["processed"][key] = {
            "story_name": story["story_name"],
            "sheet_name": story.get("sheet_name"),
            "row_index": story.get("row_index"),
            "status": "FAILED",
            "error": str(error_message),
            "timestamp": datetime.now().isoformat()
        }
        self.data["summary"]["total_failed"] += 1
        self._save()

    def get_summary(self) -> Dict:
        return self.data["summary"]
