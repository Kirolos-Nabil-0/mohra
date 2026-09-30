import re
import posixpath
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Optional


PATCH_SIZE = 30


def group_unassigned_patches(stories: List[Dict], patch_size: int = PATCH_SIZE) -> List[Dict]:
    """Group pending, unassigned stories into row-ordered patches per worksheet."""
    if patch_size < 1:
        raise ValueError("patch_size must be at least 1")

    grade_order = []
    by_grade = {}
    for story in stories:
        grade = story.get("sheet_name", "Sheet")
        if grade not in by_grade:
            by_grade[grade] = []
            grade_order.append(grade)
        if story.get("assignment_is_blank", not str(story.get("assigned_to", "")).strip()) and not story.get("is_done", False):
            by_grade[grade].append(story)

    patches = []
    for grade in grade_order:
        candidates = sorted(by_grade[grade], key=lambda story: int(story.get("row_index", 0)))
        for offset in range(0, len(candidates), patch_size):
            patch_stories = candidates[offset:offset + patch_size]
            patches.append({
                "sheet_name": grade,
                "patch_number": offset // patch_size + 1,
                "stories": patch_stories,
            })
    return patches

def extract_spreadsheet_id(url: str) -> Optional[str]:
    m = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', url)
    return m.group(1) if m else None

def extract_drive_folder_id(url: str) -> Optional[str]:
    if not url:
        return None
    # match /folders/<id> or ?id=<id> or open?id=<id>
    m = re.search(r'/folders/([a-zA-Z0-9-_]+)', url)
    if m:
        return m.group(1)
    m = re.search(r'[?&]id=([a-zA-Z0-9-_]+)', url)
    if m:
        return m.group(1)
    return None

def auto_detect_downloads_sheet() -> Optional[Path]:
    downloads = Path.home() / "Downloads"
    if not downloads.exists():
        return None
    patterns = [
        "*Data Entry*Website*First Language*.xlsx",
        "*First Language*.xlsx",
        "*Data Entry*.xlsx"
    ]
    for pat in patterns:
        matches = sorted(downloads.glob(pat), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            return matches[0]
    return None

class SheetParser:
    def __init__(self, sheet_url: str = "", assigned_to: str = "Mohra", cache_dir: str = "./cache", local_file_path: Optional[str] = None, sheet_source: str = "url", include_all_assignments: bool = False):
        self.sheet_url = sheet_url
        self.assigned_to = assigned_to.strip().lower()
        self.include_all_assignments = bool(include_all_assignments)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.sheet_source = sheet_source
        self.last_parse_summary = {
            "worksheets": [],
            "rows_scanned": 0,
            "stories_loaded": 0,
        }
        self.last_refresh_error = None

        # Check custom path, then sheet_source, then cache fallback
        if local_file_path and Path(local_file_path).exists():
            self.xlsx_path = Path(local_file_path)
            self.source_type = "custom_local"
        elif self.sheet_source == "url" and self.sheet_url:
            self.xlsx_path = self.cache_dir / "latest_sheet.xlsx"
            self.source_type = "google_sheet_url"
        else:
            downloaded = auto_detect_downloads_sheet()
            if downloaded and downloaded.exists():
                self.xlsx_path = downloaded
                self.source_type = "downloads_folder"
            else:
                self.xlsx_path = self.cache_dir / "latest_sheet.xlsx"
                self.source_type = "google_sheet_url" if self.sheet_url else "cache"

    def download_sheet(self, force_refresh: bool = False) -> Path:
        if not self.sheet_url:
            raise ValueError("No sheet URL provided for downloading.")
        spread_id = extract_spreadsheet_id(self.sheet_url)
        if not spread_id:
            raise ValueError(f"Could not extract spreadsheet ID from URL: {self.sheet_url}")

        export_url = f"https://docs.google.com/spreadsheets/d/{spread_id}/export?format=xlsx"
        
        req = urllib.request.Request(
            export_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }
        )
        dest = self.cache_dir / "latest_sheet.xlsx"
        public_error = None
        content = b""
        try:
            with urllib.request.urlopen(req) as resp:
                content = resp.read()
            if not content.startswith(b"PK"):
                raise ValueError("Google returned a non-Excel response; authenticated access may be required.")
        except Exception as exc:
            public_error = exc
            try:
                content = self._download_export_with_verified_gmail(export_url)
            except Exception as auth_exc:
                raise RuntimeError(
                    f"Could not download the global Google Sheet. Public export failed ({public_error}); "
                    f"verified Gmail export failed ({auth_exc})."
                ) from auth_exc

        with open(dest, "wb") as f:
            f.write(content)
        self.xlsx_path = dest
        self.source_type = "google_sheet_url"
        return self.xlsx_path

    def _download_export_with_verified_gmail(self, export_url: str) -> bytes:
        """Fetch a private spreadsheet export through the verified Chrome session."""
        from config import load_config
        from modules.google_auth import GoogleAuthenticator
        from playwright.sync_api import sync_playwright

        config = load_config()
        auth = GoogleAuthenticator(config)
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(f"http://localhost:{auth.port}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            is_logged_in, status = auth.verify_login_status(context)
            if not is_logged_in:
                raise PermissionError(f"Chrome is not verified as {auth.email}: {status}")
            response = context.request.get(
                export_url,
                headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
                timeout=30000,
            )
            if not response.ok:
                raise RuntimeError(f"Google export returned HTTP {response.status}")
            content = response.body()
            if not content.startswith(b"PK"):
                raise ValueError("Verified Gmail export did not return an Excel workbook.")
            return content

    def parse_stories(self, force_download: bool = False) -> List[Dict]:
        self.last_refresh_error = None
        should_download = force_download or not self.xlsx_path or not self.xlsx_path.exists()
        if should_download and self.sheet_url:
            try:
                self.download_sheet(force_refresh=True)
            except Exception as e:
                self.last_refresh_error = str(e)
                if self.xlsx_path and self.xlsx_path.exists():
                    pass
                else:
                    downloaded = auto_detect_downloads_sheet()
                    if downloaded and downloaded.exists():
                        self.xlsx_path = downloaded
                        self.source_type = "downloads_folder"
                    else:
                        raise e
        elif should_download:
            self.download_sheet(force_refresh=True)

        stories = []
        with zipfile.ZipFile(self.xlsx_path, "r") as z:
            # 1. Parse shared strings
            shared_strings = []
            if "xl/sharedStrings.xml" in z.namelist():
                strings_xml = z.read("xl/sharedStrings.xml")
                strings_root = ET.fromstring(strings_xml)
                for si in strings_root.findall("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"):
                    t_val = "".join(t.text or "" for t in si.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"))
                    shared_strings.append(t_val)

            # 2. Parse workbook to get sheet names
            wb_xml = z.read("xl/workbook.xml")
            wb_root = ET.fromstring(wb_xml)
            sheet_map = {}
            for s in wb_root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet"):
                s_name = s.attrib.get("name", "Sheet")
                r_id = s.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", "")
                sheet_map[r_id] = {"name": s_name, "state": s.attrib.get("state", "visible")}

            # 3. Parse workbook rels to map rId to xml filename
            rels_map = {}
            if "xl/_rels/workbook.xml.rels" in z.namelist():
                wb_rels_xml = z.read("xl/_rels/workbook.xml.rels")
                wb_rels_root = ET.fromstring(wb_rels_xml)
                for r in wb_rels_root:
                    rels_map[r.attrib["Id"]] = {
                        "target": r.attrib.get("Target", ""),
                        "type": r.attrib.get("Type", ""),
                    }

            # 4. Iterate over sheets
            ns = {
                "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
                "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            }

            worksheets = []
            rows_scanned = 0
            for r_id, sheet in sheet_map.items():
                relationship = rels_map.get(r_id)
                if relationship is None:
                    raise ValueError(f"Workbook sheet '{sheet['name']}' has no relationship entry.")
                target_rel = relationship.get("target", "")
                if not target_rel or not relationship.get("type", "").endswith("/worksheet"):
                    continue
                # Worksheet relationship targets are relative to xl/ unless
                # they are package-absolute paths beginning with /xl/.
                target_path = target_rel.lstrip("/")
                if not target_path.startswith("xl/"):
                    target_path = posixpath.join("xl", target_path)
                sheet_file = posixpath.normpath(target_path)
                if sheet_file not in z.namelist():
                    raise ValueError(
                        f"Workbook worksheet '{sheet['name']}' points to missing file '{sheet_file}'."
                    )
                worksheets.append(sheet["name"])

                # Parse hyperlinks for this sheet
                sheet_rels_file = sheet_file.replace("worksheets/", "worksheets/_rels/") + ".rels"
                hyperlinks = {}
                if sheet_rels_file in z.namelist():
                    s_rels_root = ET.fromstring(z.read(sheet_rels_file))
                    s_rel_map = {r.attrib["Id"]: r.attrib.get("Target", "") for r in s_rels_root}
                    
                    s_root = ET.fromstring(z.read(sheet_file))
                    for h in s_root.findall(".//s:hyperlink", ns):
                        ref = h.attrib.get("ref")
                        rel_id = h.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                        if ref and rel_id:
                            hyperlinks[ref] = s_rel_map.get(rel_id, "")
                else:
                    s_root = ET.fromstring(z.read(sheet_file))

                # Parse rows
                header_cols = {}
                story_assign_col = "C"
                vocab_assign_col = "E"
                for row in s_root.findall(".//s:row", ns):
                    rows_scanned += 1
                    r_idx = int(row.attrib["r"])
                    cols = {}
                    for c in row.findall("s:c", ns):
                        cell_ref = c.attrib["r"]
                        col_letter = re.match(r"([A-Z]+)", cell_ref).group(1)
                        t = c.attrib.get("t")
                        v = c.find("s:v", ns)
                        val = ""
                        if t == "inlineStr":
                            inline = c.find("s:is", ns)
                            if inline is not None:
                                val = "".join(
                                    text.text or ""
                                    for text in inline.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
                                )
                        elif v is not None and v.text is not None:
                            if t == "s":
                                shared_index = int(v.text)
                                if shared_index < 0 or shared_index >= len(shared_strings):
                                    raise ValueError(
                                        f"Invalid shared-string index {shared_index} in {sheet_file}!{cell_ref}."
                                    )
                                val = shared_strings[shared_index]
                            else:
                                val = v.text
                        cols[col_letter] = (val.strip(), hyperlinks.get(cell_ref, ""))

                    # Header detection
                    if r_idx in (1, 2) and any("story" in str(v[0]).lower() for v in cols.values()):
                        for col_k, (col_v, _) in cols.items():
                            header_cols[col_k] = col_v.lower().strip()
                        assign_cols = [
                            col_k for col_k, label in header_cols.items()
                            if re.sub(r"[^a-z]", "", label) in {"assignedto", "assigndto", "assignee"}
                        ]
                        assign_cols.sort(key=lambda col: (len(col), col))
                        if "C" in assign_cols:
                            story_assign_col = "C"
                        elif assign_cols:
                            story_assign_col = assign_cols[0]
                        else:
                            story_assign_col = "C"
                        vocab_assign_col = next((c for c in assign_cols if c != story_assign_col), None)
                        continue

                    assigned_val = cols.get(story_assign_col, ("", ""))[0].strip()
                    vocab_assigned_val = cols.get(vocab_assign_col, ("", ""))[0].strip() if vocab_assign_col else ""

                    assignment_values = {story_assign_col: assigned_val}
                    if vocab_assign_col and vocab_assigned_val:
                        assignment_values[vocab_assign_col] = vocab_assigned_val

                    story_val, story_link = cols.get("B", ("", ""))
                    comment_val = cols.get("D", ("", ""))[0]
                    assignment_matches = bool(
                        self.assigned_to
                        and (
                            assigned_val.casefold() == self.assigned_to
                            or vocab_assigned_val.casefold() == self.assigned_to
                        )
                    )

                    if story_val.strip() and (self.include_all_assignments or assignment_matches):
                        drive_url = story_link or ""
                        if not drive_url and "drive.google.com" in story_val:
                            drive_url = story_val
                        
                        folder_id = extract_drive_folder_id(drive_url)
                        is_done = "done" in comment_val.lower()

                        from modules.title_utils import (
                            clean_story_title,
                            canonical_title_key,
                            matches_search_query,
                            calculate_title_similarity,
                        )

                        clean_story_name = clean_story_title(story_val)

                        # Primary display: Story assignee; fallback to vocab assignee if story assignee is empty
                        display_assigned = assigned_val or vocab_assigned_val

                        stories.append({
                            "sheet_name": sheet["name"],
                            "row_index": r_idx,
                            "story_name": clean_story_name,
                            "raw_story_name": story_val,
                            "assigned_to": display_assigned,
                            "story_assigned_to": assigned_val,
                            "vocab_assigned_to": vocab_assigned_val,
                            "assignment_columns": [story_assign_col],
                            "assignment_values": assignment_values,
                            "assignment_is_blank": not bool(assigned_val),
                            "assignment_conflict": False,
                            "comment": comment_val,
                            "is_done": is_done,
                            "drive_url": drive_url,
                            "folder_id": folder_id
                        })

            self.last_parse_summary = {
                "worksheets": worksheets,
                "rows_scanned": rows_scanned,
                "stories_loaded": len(stories),
            }

        return stories

    def get_uncompleted_stories(self, force_refresh: bool = False) -> List[Dict]:
        all_stories = self.parse_stories(force_download=force_refresh)
        return [s for s in all_stories if not s["is_done"]]

    def find_story(self, name_query: str, force_refresh: bool = False) -> Optional[Dict]:
        from modules.title_utils import (
            canonical_title_key,
            matches_search_query,
            calculate_title_similarity,
        )

        all_stories = self.parse_stories(force_download=force_refresh)
        query = name_query.strip()
        if not query:
            return None

        # 1. Exact canonical key match (resilient to dashes, apostrophes, commas, punctuation)
        q_canon = canonical_title_key(query)
        if q_canon:
            for s in all_stories:
                if canonical_title_key(s["story_name"]) == q_canon or canonical_title_key(s.get("raw_story_name", "")) == q_canon:
                    return s

        # 2. Resilient search query match (tokens, punctuation differences, substrings)
        for s in all_stories:
            if matches_search_query(query, s["story_name"]) or matches_search_query(query, s.get("raw_story_name", "")):
                return s

        # 3. High-confidence fuzzy similarity fallback
        best_story = None
        best_score = 0.0
        for s in all_stories:
            sim = max(
                calculate_title_similarity(query, s["story_name"]),
                calculate_title_similarity(query, s.get("raw_story_name", "")),
            )
            if sim > best_score:
                best_score = sim
                best_story = s

        if best_story and best_score >= 0.80:
            return best_story

        return None
