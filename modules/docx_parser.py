import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Optional, Union

class DocxParser:
    @staticmethod
    def extract_paragraphs(docx_source: Union[str, Path, bytes]) -> List[str]:
        paragraphs = []
        if isinstance(docx_source, (str, Path)):
            f_in = open(docx_source, "rb")
        else:
            import io
            f_in = io.BytesIO(docx_source)

        try:
            with zipfile.ZipFile(f_in) as z:
                if "word/document.xml" not in z.namelist():
                    raise ValueError("Invalid docx: word/document.xml not found")
                xml_content = z.read("word/document.xml")
                root = ET.fromstring(xml_content)
                for p in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                    text = "".join(t.text or "" for t in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")).strip()
                    if text:
                        paragraphs.append(text)
        finally:
            if isinstance(docx_source, (str, Path)):
                f_in.close()

        return paragraphs

    @classmethod
    def parse_comprehension_questions(cls, docx_source: Union[str, Path, bytes]) -> List[Dict]:
        paragraphs = cls.extract_paragraphs(docx_source)
        
        # 1. Locate Question Section
        # Matches headings: "Comprehension Questions", "Multiple-Choice Questions (MCQs)", "MCQs", etc.
        section_start_regex = re.compile(
            r"(?:comprehension\s+questions|multiple-choice\s+questions|mcqs?)",
            re.IGNORECASE
        )
        section_end_regex = re.compile(
            r"^(?:vocabulary\s+quiz|vocab\s+quiz|justification|curriculum\s+alignment|part\s+\d+|chapter\s+\d+)",
            re.IGNORECASE
        )

        in_section = False
        section_lines = []
        for p in paragraphs:
            clean = p.strip()
            if not clean:
                continue
            if not in_section:
                if section_start_regex.search(clean):
                    in_section = True
                    continue
            else:
                # Stop if next non-question section starts
                if section_end_regex.search(clean):
                    break
                section_lines.append(clean)

        # Fallback: scan whole document for question lines if section heading not matched
        if not section_lines:
            for p in paragraphs:
                if (re.search(r"A[\.\)]\s*.+?\s*B[\.\)]", p) and 
                    (re.search(r"Answer\s*:\s*[A-D]", p, re.IGNORECASE) or re.search(r"\(correct(?:\s+answer)?\)", p, re.IGNORECASE))):
                    section_lines.append(p.strip())

        questions = []
        for line in section_lines:
            # Check if line contains choices A. and B.
            if not re.search(r"\bA[\.\)]\s*.+?\bB[\.\)]\s*", line):
                continue

            q_num = len(questions) + 1

            # Strip leading Q1., 1., etc.
            cleaned_line = re.sub(r"^(?:Q\d+[\.\:]|\d+[\.\:])\s*", "", line.strip())

            # Regex matching: Question + A + B + C + D
            m = re.search(
                r"^(?P<q>.+?)\s+A[\.\)]\s*(?P<a>.+?)\s+B[\.\)]\s*(?P<b>.+?)\s+C[\.\)]\s*(?P<c>.+?)\s+D[\.\)]\s*(?P<d>.+)$",
                cleaned_line,
                re.DOTALL
            )
            if not m:
                continue

            d = m.groupdict()
            raw_q = d["q"].strip()
            # Clean leading number if still present in raw_q
            raw_q = re.sub(r"^\d+[\.\)]\s*", "", raw_q).strip()

            opts = {
                "A": d["a"].strip(),
                "B": d["b"].strip(),
                "C": d["c"].strip(),
                "D": d["d"].strip()
            }

            ans = None

            # Style 1: Answer: X at the end of option D
            ans_m = re.search(r"(?:Answer|Ans)[\s:]*([A-D])\b", opts["D"], re.IGNORECASE)
            if ans_m:
                ans = ans_m.group(1).upper()
                opts["D"] = re.sub(r"(?:Answer|Ans)[\s:]*[A-D]\b.*$", "", opts["D"], flags=re.IGNORECASE).strip()

            # Style 2: (Correct answer) or (correct) inside one of the options
            for letter, opt_text in list(opts.items()):
                if re.search(r"\((?:correct(?:\s+answer)?)\)", opt_text, re.IGNORECASE):
                    ans = letter
                    opts[letter] = re.sub(r"\((?:correct(?:\s+answer)?)\)", "", opt_text, flags=re.IGNORECASE).strip()

            # Clean any stray formatting from options
            def format_choice(letter: str, txt: str) -> str:
                # Remove leading letter if duplicated
                clean_t = re.sub(r"^[A-Da-d][\.\)]\s*", "", txt.strip()).strip()
                return f"{letter}. {clean_t}"

            questions.append({
                "num": q_num,
                "question": f"{q_num}. {raw_q}",
                "raw_question": raw_q,
                "choices": [
                    {"letter": "A", "text": format_choice("A", opts["A"])},
                    {"letter": "B", "text": format_choice("B", opts["B"])},
                    {"letter": "C", "text": format_choice("C", opts["C"])},
                    {"letter": "D", "text": format_choice("D", opts["D"])},
                ],
                "answer": ans or "A"
            })

        return questions
