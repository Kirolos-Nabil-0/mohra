import re
import html
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Tuple

from modules.ai_groq_parser import GroqAnswerResolver

class DriveDownloader:
    @staticmethod
    def is_first_language_docx(path_or_name) -> bool:
        name = Path(path_or_name).name
        return name.lower().endswith(".docx") and DriveDownloader._has_first_language_label(name)

    def __init__(self, cache_dir: str = "./cache"):
        self.cache_dir = Path(cache_dir) / "docx"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def extract_folder_id(url: str) -> Optional[str]:
        if not url:
            return None
        m = re.search(r'/folders/([a-zA-Z0-9-_]+)', url)
        if m:
            return m.group(1)
        m = re.search(r'[?&]id=([a-zA-Z0-9-_]+)', url)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _has_first_language_label(name: str) -> bool:
        return bool(
            re.search(r"(?<![a-z])(?:first[ _-]*language|efl)(?![a-z])", name, re.IGNORECASE)
            and not DriveDownloader._has_second_language_label(name)
        )

    @staticmethod
    def _matches_story_name(name: str, story_name: Optional[str]) -> bool:
        if not story_name or DriveDownloader._has_second_language_label(name):
            return False
        stem = re.sub(r"\.docx$", "", name, flags=re.IGNORECASE)
        normalize = lambda value: "".join(ch for ch in value.casefold() if ch.isalnum())
        return bool(normalize(stem)) and normalize(stem) == normalize(story_name)

    @staticmethod
    def _has_second_language_label(name: str) -> bool:
        return bool(re.search(r"(?<![a-z])second(?:[ _-]*language)?(?![a-z])", name, re.IGNORECASE))

    def get_folder_items(self, folder_id: str) -> Dict[str, Tuple[str, str]]:
        """List visible DOCX files, Google Docs, and folders in a Drive folder."""
        url = f"https://drive.google.com/drive/folders/{folder_id}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                page = resp.read().decode("utf-8", errors="ignore")
        except Exception as exc:
            print(f"Error fetching drive folder {folder_id}: {exc}")
            return {}

        items = {}
        # Drive includes the visible item name and type beside its data-id.
        pattern = (
            r'data-id="([a-zA-Z0-9_-]{25,})"[^>]*data-tooltip="([^"]+)"'
            r'[^>]*>\s*<span[^>]*>\s*<strong[^>]*>([^<]+)</strong>'
        )
        for match in re.finditer(pattern, page, re.IGNORECASE):
            file_id, tooltip, name = match.groups()
            name = html.unescape(name).strip()
            tooltip = html.unescape(tooltip).lower()
            if "folder" in tooltip:
                kind = "folder"
            elif "google docs" in tooltip:
                kind = "google_doc"
            elif name.lower().endswith(".docx"):
                kind = "docx"
            else:
                continue
            items[name] = (file_id, kind)

        # Older Drive markup can expose DOCX entries without the tooltip markup.
        for match in re.finditer(
            r'data-id="([a-zA-Z0-9_-]{25,})"[^>]*?<strong[^>]*>([^<]+?\.docx)</strong>',
            page, re.IGNORECASE,
        ):
            items.setdefault(html.unescape(match.group(2)).strip(), (match.group(1), "docx"))
        for match in re.finditer(
            r'\\x5b\\x22([a-zA-Z0-9_-]{25,})\\x22,\\x5b\\x22[a-zA-Z0-9_-]+\\x22\\x5d,\\x22([a-zA-Z0-9_\-\.\s]+?\.docx)\\x22',
            page, re.IGNORECASE,
        ):
            items.setdefault(match.group(2).strip(), (match.group(1), "docx"))
        return items

    def get_all_folder_docx_files(self, folder_id: str) -> Dict[str, str]:
        return {
            name: file_id for name, (file_id, kind) in self.get_folder_items(folder_id).items()
            if kind == "docx"
        }

    def download_file_by_id(self, file_id: str, dest_path: Path) -> bool:
        download_urls = [
            f"https://drive.usercontent.google.com/download?id={file_id}&export=download",
            f"https://drive.google.com/uc?export=download&id={file_id}"
        ]
        for url in download_urls:
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    }
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = resp.read()
                    if len(data) > 500 and data[:4] == b"PK\x03\x04":
                        with open(dest_path, "wb") as f:
                            f.write(data)
                        return True
            except Exception:
                pass
        return False

    def export_google_doc_by_id(self, file_id: str, dest_path: Path) -> bool:
        url = f"https://docs.google.com/document/d/{file_id}/export?format=docx"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
            if len(data) > 500 and data[:4] == b"PK\x03\x04":
                dest_path.write_bytes(data)
                return True
        except Exception:
            pass
        return False

    def download_story_docx(self, folder_url_or_id: str, preferred_lang: str = "First language", force_refresh: bool = False, ai_config: Optional[dict] = None, story_name: Optional[str] = None) -> Tuple[Optional[Path], str]:
        self.selection_required = False
        self.ai_suggestion = None
        if preferred_lang.strip().lower() != "first language":
            return None, "Only First language.docx is supported"
        folder_id = self.extract_folder_id(folder_url_or_id) or folder_url_or_id
        if not folder_id:
            return None, "Invalid Drive folder URL/ID"

        root_items = self.get_folder_items(folder_id)
        candidates = []
        output_folders = []
        output_items = {}
        suggestions = [name for name, (_, kind) in root_items.items() if kind in ("docx", "google_doc")]

        # A clearly named file in the story folder takes priority.
        for name, (file_id, kind) in root_items.items():
            if kind in ("docx", "google_doc") and self._has_first_language_label(name):
                candidates.append((folder_id, name, file_id, kind, ""))

        if not candidates:
            output_folders = [
                (name, file_id) for name, (file_id, kind) in root_items.items()
                if kind == "folder" and "output" in name.lower()
                and not self._has_second_language_label(name)
            ]
            for output_name, output_id in output_folders[:10]:
                child_items = self.get_folder_items(output_id)
                output_items[output_id] = child_items
                child_docs = [(name, file_id, kind) for name, (file_id, kind) in child_items.items()
                              if kind in ("docx", "google_doc")]
                suggestions.extend(name for name, _, _ in child_docs)
                explicit = [entry for entry in child_docs if self._has_first_language_label(entry[0])]
                selected = explicit
                if not selected and self._has_first_language_label(output_name) and len(child_docs) == 1:
                    selected = child_docs
                for name, file_id, kind in selected:
                    candidates.append((output_id, name, file_id, kind, output_name))

        # The worksheet title is useful only when no language-labelled file exists.
        if not candidates:
            for name, (file_id, kind) in root_items.items():
                if kind in ("docx", "google_doc") and self._matches_story_name(name, story_name):
                    candidates.append((folder_id, name, file_id, kind, ""))
            if not candidates:
                for output_name, output_id in output_folders[:10]:
                    for name, (file_id, kind) in output_items[output_id].items():
                        if kind in ("docx", "google_doc") and self._matches_story_name(name, story_name):
                            candidates.append((output_id, name, file_id, kind, output_name))

        if len(candidates) != 1:
            self.selection_required = True
            message = ("Multiple First language documents found; choose one in Drive"
                       if candidates else "First language document not found in story or OUTPUT folders")
            if not candidates:
                suggestion = GroqAnswerResolver.suggest_first_language_filename(suggestions, ai_config)
                if suggestion:
                    self.ai_suggestion = suggestion
                    message += f". AI suggests checking '{suggestion}' in the file picker; verify its language before choosing it"
            return None, message

        source_folder_id, target_name, target_id, kind, output_name = candidates[0]
        cache_name = target_name if kind == "docx" and self.is_first_language_docx(target_name) else "First_language.docx"
        safe_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', cache_name)
        cache_file = self.cache_dir / f"{source_folder_id}_{target_id}_{safe_name}"
        source_label = f" from {output_name}" if output_name else ""

        if not force_refresh and cache_file.exists() and cache_file.stat().st_size > 1000:
            return cache_file, f"Cached ({target_name}{source_label})"

        success = (self.export_google_doc_by_id(target_id, cache_file) if kind == "google_doc"
                   else self.download_file_by_id(target_id, cache_file))
        if success:
            action = "Exported" if kind == "google_doc" else "Downloaded"
            return cache_file, f"{action} {target_name}{source_label}"

        return None, f"Failed obtaining {target_name}{source_label}"

    def download_selected_file(self, selection: dict, force_refresh: bool = False) -> Tuple[Optional[Path], str]:
        """Download the file explicitly chosen in the Drive browser, after checking its folder."""
        folder_id = selection.get("folder_id", "")
        file_id = selection.get("file_id", "")
        name = selection.get("name", "")
        kind = selection.get("kind", "")
        folder_names = selection.get("folder_names", [])
        if (kind not in ("docx", "google_doc") or self._has_second_language_label(name)
                or any(self._has_second_language_label(folder) for folder in folder_names)):
            return None, "Selected file is not a usable First language document"
        if self.get_folder_items(folder_id).get(name) != (file_id, kind):
            return None, "Selected Drive file is no longer available; choose it again"
        cache_file = self.cache_dir / f"{folder_id}_{file_id}_First_language.docx"
        if not force_refresh and cache_file.exists() and cache_file.stat().st_size > 1000:
            return cache_file, f"Selected {name} (cached)"
        success = (self.export_google_doc_by_id(file_id, cache_file) if kind == "google_doc"
                   else self.download_file_by_id(file_id, cache_file))
        if success:
            return cache_file, f"Selected {name} as First language"
        return None, f"Failed obtaining selected file {name}"
