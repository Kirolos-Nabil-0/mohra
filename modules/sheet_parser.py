import re
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Optional

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
    def __init__(self, sheet_url: str = "", assigned_to: str = "Mohra", cache_dir: str = "./cache", local_file_path: Optional[str] = None):
        self.sheet_url = sheet_url
        self.assigned_to = assigned_to.strip().lower()
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Check custom path, then auto-detected Downloads, then cache fallback
        if local_file_path and Path(local_file_path).exists():
            self.xlsx_path = Path(local_file_path)
            self.source_type = "custom_local"
        else:
            downloaded = auto_detect_downloads_sheet()
            if downloaded and downloaded.exists():
                self.xlsx_path = downloaded
                self.source_type = "downloads_folder"
            else:
                self.xlsx_path = self.cache_dir / "latest_sheet.xlsx"
                self.source_type = "cache"

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
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        dest = self.cache_dir / "latest_sheet.xlsx"
        with urllib.request.urlopen(req) as resp:
            content = resp.read()
            with open(dest, "wb") as f:
                f.write(content)
        self.xlsx_path = dest
        self.source_type = "downloaded_export"
        return self.xlsx_path

    def parse_stories(self, force_download: bool = False) -> List[Dict]:
        if force_download or not self.xlsx_path or not self.xlsx_path.exists():
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
                sheet_map[r_id] = s_name

            # 3. Parse workbook rels to map rId to xml filename
            rels_map = {}
            if "xl/_rels/workbook.xml.rels" in z.namelist():
                wb_rels_xml = z.read("xl/_rels/workbook.xml.rels")
                wb_rels_root = ET.fromstring(wb_rels_xml)
                for r in wb_rels_root:
                    rels_map[r.attrib["Id"]] = r.attrib.get("Target", "")

            # 4. Iterate over sheets
            ns = {
                "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
                "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            }

            for r_id, s_name in sheet_map.items():
                target_rel = rels_map.get(r_id, "")
                if not target_rel:
                    continue
                # normalize path
                sheet_file = f"xl/{target_rel.lstrip('/')}"
                if sheet_file not in z.namelist():
                    continue

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
                for row in s_root.findall(".//s:row", ns):
                    r_idx = int(row.attrib["r"])
                    cols = {}
                    for c in row.findall("s:c", ns):
                        cell_ref = c.attrib["r"]
                        col_letter = re.match(r"([A-Z]+)", cell_ref).group(1)
                        t = c.attrib.get("t")
                        v = c.find("s:v", ns)
                        val = ""
                        if v is not None and v.text is not None:
                            val = shared_strings[int(v.text)] if t == "s" and int(v.text) < len(shared_strings) else v.text
                        cols[col_letter] = (val.strip(), hyperlinks.get(cell_ref, ""))

                    # Header detection
                    if r_idx in (1, 2) and any("story" in str(v[0]).lower() for v in cols.values()):
                        for col_k, (col_v, _) in cols.items():
                            header_cols[col_k] = col_v.lower().strip()
                        continue

                    # Check assignment
                    assigned_val = cols.get("C", ("", ""))[0]
                    story_val, story_link = cols.get("B", ("", ""))
                    comment_val = cols.get("D", ("", ""))[0]

                    if self.assigned_to in assigned_val.lower():
                        drive_url = story_link or ""
                        if not drive_url and "drive.google.com" in story_val:
                            drive_url = story_val
                        
                        folder_id = extract_drive_folder_id(drive_url)
                        is_done = "done" in comment_val.lower()

                        clean_story_name = story_val.strip().replace("_", "'")
                        clean_story_name = re.sub(r"\s+", " ", clean_story_name)

                        stories.append({
                            "sheet_name": s_name,
                            "row_index": r_idx,
                            "story_name": clean_story_name,
                            "raw_story_name": story_val,
                            "assigned_to": assigned_val,
                            "comment": comment_val,
                            "is_done": is_done,
                            "drive_url": drive_url,
                            "folder_id": folder_id
                        })

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
