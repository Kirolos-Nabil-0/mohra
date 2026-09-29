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
                assignment_columns = ["C"]
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
                        assignment_columns = [
                            col_k for col_k, label in header_cols.items()
                            if re.sub(r"[^a-z]", "", label) in {"assignedto", "assigndto", "assignee"}
                        ] or ["C"]
                        assignment_columns.sort(key=lambda col: (len(col), col))
                        continue

                    assignment_values = {
                        col: cols.get(col, ("", ""))[0]
                        for col in assignment_columns
                    }
                    nonempty_assignments = [
                        (col, value) for col, value in assignment_values.items() if value.strip()
                    ]
                    distinct_assignees = {value.strip().casefold() for _, value in nonempty_assignments}
                    assignment_conflict = len(distinct_assignees) > 1
                    if assignment_conflict:
                        assigned_val = " / ".join(f"{col}: {value}" for col, value in nonempty_assignments)
                    else:
                        assigned_val = nonempty_assignments[0][1] if nonempty_assignments else ""
                    story_val, story_link = cols.get("B", ("", ""))
                    comment_val = cols.get("D", ("", ""))[0]
                    assignment_matches = bool(
                        self.assigned_to
                        and not assignment_conflict
                        and assigned_val.strip().casefold() == self.assigned_to
                    )

                    if story_val.strip() and (self.include_all_assignments or assignment_matches):
                        drive_url = story_link or ""
                        if not drive_url and "drive.google.com" in story_val:
                            drive_url = story_val
                        
                        folder_id = extract_drive_folder_id(drive_url)
                        is_done = "done" in comment_val.lower()

                        clean_story_name = story_val.strip().replace("_", "'")
                        clean_story_name = re.sub(r"\s+", " ", clean_story_name)

                        stories.append({
                            "sheet_name": sheet["name"],
                            "row_index": r_idx,
                            "story_name": clean_story_name,
                            "raw_story_name": story_val,
                            "assigned_to": assigned_val,
                            "assignment_columns": list(assignment_values),
                            "assignment_values": assignment_values,
                            "assignment_is_blank": not bool(nonempty_assignments),
                            "assignment_conflict": assignment_conflict,
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
        all_stories = self.parse_stories(force_download=force_refresh)
        query = name_query.strip().lower()
        # Exact match first
        for s in all_stories:
            if s["story_name"].lower() == query:
                return s
        # Substring match
        for s in all_stories:
            if query in s["story_name"].lower():
                return s
        return None
