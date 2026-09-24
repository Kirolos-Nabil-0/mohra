import re
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Tuple

class DriveDownloader:
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

    def get_all_folder_docx_files(self, folder_id: str) -> Dict[str, str]:
        """
        Fetches the Google Drive folder HTML and extracts file IDs for all .docx files.
        Returns dict: { 'First language.docx': '<id>', 'New.docx': '<id>', ... }
        """
        url = f"https://drive.google.com/drive/folders/{folder_id}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"Error fetching drive folder {folder_id}: {e}")
            return {}

        files = {}
        # Pattern 1: data-id with strong tag (Drive web UI format)
        for m in re.finditer(r'data-id=\"([a-zA-Z0-9_-]{25,})\"[^>]*?<strong[^>]*>([^\<]+?\.docx)<\/strong>', html, re.IGNORECASE):
            files[m.group(2).strip()] = m.group(1).strip()
            
        # Pattern 2: data-id with data-tooltip
        for m in re.finditer(r'data-id=\"([a-zA-Z0-9_-]{25,})\"[^>]*?data-tooltip=\"([^\"]+?\.docx)', html, re.IGNORECASE):
            files[m.group(2).strip()] = m.group(1).strip()

        # Pattern 3: JSON data block
        for m in re.finditer(r'\\x5b\\x22([a-zA-Z0-9_-]{25,})\\x22,\\x5b\\x22[a-zA-Z0-9_-]+\\x22\\x5d,\\x22([a-zA-Z0-9_\-\.\s]+?\.docx)\\x22', html, re.IGNORECASE):
            files[m.group(2).strip()] = m.group(1).strip()

        return files

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

    def download_story_docx(self, folder_url_or_id: str, preferred_lang: str = "First language", force_refresh: bool = False) -> Tuple[Optional[Path], str]:
        folder_id = self.extract_folder_id(folder_url_or_id) or folder_url_or_id
        if not folder_id:
            return None, "Invalid Drive folder URL/ID"

        # 1. Fetch all docx files in folder
        docx_map = self.get_all_folder_docx_files(folder_id)
        if not docx_map:
            return None, "No docx files found in folder"

        # 2. Selection priority:
        # Priority A: File matching preferred_lang (e.g. First language.docx)
        target_name = None
        target_id = None
        norm_pref = preferred_lang.lower().replace(" ", "")

        for name, f_id in docx_map.items():
            if norm_pref in name.lower().replace(" ", ""):
                target_name = name
                target_id = f_id
                break

        # Priority B: "Second Language" fallback
        if not target_id:
            for name, f_id in docx_map.items():
                if "second" in name.lower():
                    target_name = name
                    target_id = f_id
                    break

        # Priority C: "New.docx" fallback
        if not target_id:
            for name, f_id in docx_map.items():
                if "new" in name.lower():
                    target_name = name
                    target_id = f_id
                    break

        # Priority D: Any docx file found in folder
        if not target_id:
            target_name, target_id = next(iter(docx_map.items()))

        safe_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', target_name)
        cache_file = self.cache_dir / f"{folder_id}_{safe_name}"

        if not force_refresh and cache_file.exists() and cache_file.stat().st_size > 1000:
            return cache_file, f"Cached ({target_name})"

        success = self.download_file_by_id(target_id, cache_file)
        if success:
            return cache_file, f"Downloaded {target_name}"

        return None, f"Failed downloading {target_name}"
