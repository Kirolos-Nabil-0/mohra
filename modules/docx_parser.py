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

        questions = []
        for line in paragraphs:
            clean = line.strip()

            # Must contain choices A, B, C, D
            if not re.search(r"\bA[\.\)]\s*.+?\bB[\.\)]\s*.+?\bC[\.\)]\s*.+?\bD[\.\)]\s*", clean):
                continue

            # Must contain an answer indicator: Answer: X or (Correct answer) / (correct)
            has_ans_suffix = bool(re.search(r"(?:Answer|Ans)[\s:]*[A-D]\b", clean, re.IGNORECASE))
            has_correct_tag = bool(re.search(r"\((?:correct(?:\s+answer)?)\)", clean, re.IGNORECASE))
            if not (has_ans_suffix or has_correct_tag):
                continue

            q_num = len(questions) + 1

            # Strip leading Q1., 1., etc.
            cleaned_line = re.sub(r"^(?:Q\d+[\.\:]|\d+[\.\:])\s*", "", clean)

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

            def format_choice(letter: str, txt: str) -> str:
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
