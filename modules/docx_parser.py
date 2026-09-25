from __future__ import annotations
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Optional, Union, Any

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
        i = 0
        n = len(paragraphs)

        while i < n:
            line = paragraphs[i].strip()
            if not line:
                i += 1
                continue

            # Case 1: Single line containing Question + Choices (A... B... C...)
            # We look for A. / A) with B. / B) and C. / C)
            if re.search(r"(?:\s*|\b)A[\.\)]\s*.+?\bB[\.\)]\s*.+?\bC[\.\)]\s*", line):
                full_text = line
                # Look ahead: If the immediate next line is "Answer: X", join it
                if i + 1 < n and re.match(r"^(?:Answer|Ans|Key)[\s:]*[A-D]\b", paragraphs[i + 1].strip(), re.IGNORECASE):
                    full_text = full_text + " " + paragraphs[i + 1].strip()
                    i += 1

                q_obj = cls._parse_single_line_q(full_text, len(questions) + 1)
                if q_obj:
                    questions.append(q_obj)
                i += 1
                continue

            # Case 2: Multi-line question block
            # Current line is question title, followed by separate lines for choices (A., B., C., D.)
            if i + 1 < n and re.match(r"^[A-Da-d][\.\)]\s*", paragraphs[i + 1].strip()):
                q_title = line
                choices_dict = {}
                ans = None
                j = i + 1
                while j < n:
                    c_line = paragraphs[j].strip()
                    m_c = re.match(r"^([A-Da-d])[\.\)]\s*(.+)$", c_line)
                    if m_c:
                        letter = m_c.group(1).upper()
                        text = m_c.group(2).strip()
                        # Detect (Correct answer) / (correct) in option text
                        if re.search(r"\((?:correct(?:\s+answer)?)\)", text, re.IGNORECASE):
                            ans = letter
                            text = re.sub(r"\((?:correct(?:\s+answer)?)\)", "", text, flags=re.IGNORECASE).strip()
                        choices_dict[letter] = text
                        j += 1
                    elif re.match(r"^(?:Answer|Ans|Key)[\s:]*([A-D])\b", c_line, re.IGNORECASE):
                        ans_m = re.match(r"^(?:Answer|Ans|Key)[\s:]*([A-D])\b", c_line, re.IGNORECASE)
                        ans = ans_m.group(1).upper()
                        j += 1
                        break
                    else:
                        break

                if len(choices_dict) >= 3:
                    q_num = len(questions) + 1
                    raw_q = re.sub(r"^(?:Q\d+[\.\:]|\d+[\.\:])\s*", "", q_title).strip()
                    choices = []
                    for let in ["A", "B", "C", "D"]:
                        t = choices_dict.get(let, "")
                        clean_t = re.sub(r"^[A-Da-d][\.\)]\s*", "", t).strip()
                        choices.append({"letter": let, "text": f"{let}. {clean_t}"})

                    questions.append({
                        "num": q_num,
                        "question": f"{q_num}. {raw_q}",
                        "raw_question": raw_q,
                        "choices": choices,
                        "answer": ans or "A"
                    })
                    i = j
                    continue

            i += 1

        return questions

    @classmethod
    def _parse_single_line_q(cls, clean: str, q_num: int) -> Optional[Dict]:
        cleaned_line = re.sub(r"^(?:Q\d+[\.\:]|\d+[\.\:])\s*", "", clean)
        m = re.search(
            r"^(?P<q>.+?)(?:\s+|\b)A[\.\)]\s*(?P<a>.+?)\s+B[\.\)]\s*(?P<b>.+?)\s+C[\.\)]\s*(?P<c>.+?)(?:\s+D[\.\)]\s*(?P<d>.+))?$",
            cleaned_line,
            re.DOTALL
        )
        if not m:
            return None

        d = m.groupdict()
        raw_q = re.sub(r"^\d+[\.\)]\s*", "", d["q"]).strip()
        opts = {
            "A": (d["a"] or "").strip(),
            "B": (d["b"] or "").strip(),
            "C": (d["c"] or "").strip(),
            "D": (d["d"] or "").strip()
        }

        ans = None
        # Style 1: Answer: X / Ans: X at the end
        ans_target = opts["D"] or opts["C"]
        ans_m = re.search(r"(?:Answer|Ans|Key)[\s:]*([A-D])\b", ans_target, re.IGNORECASE)
        if ans_m:
            ans = ans_m.group(1).upper()
            if opts["D"]:
                opts["D"] = re.sub(r"(?:Answer|Ans|Key)[\s:]*[A-D]\b.*$", "", opts["D"], flags=re.IGNORECASE).strip()
            else:
                opts["C"] = re.sub(r"(?:Answer|Ans|Key)[\s:]*[A-D]\b.*$", "", opts["C"], flags=re.IGNORECASE).strip()

        # Style 2: (Correct answer) or (correct) inside option text
        for letter, opt_text in list(opts.items()):
            if re.search(r"\((?:correct(?:\s+answer)?)\)", opt_text, re.IGNORECASE):
                ans = letter
                opts[letter] = re.sub(r"\((?:correct(?:\s+answer)?)\)", "", opt_text, flags=re.IGNORECASE).strip()

        def format_choice(letter: str, txt: str) -> str:
            clean_t = re.sub(r"^[A-Da-d][\.\)]\s*", "", txt.strip()).strip()
            return f"{letter}. {clean_t}"

        return {
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
        }

    @staticmethod
    def validate_questions(questions: List[Dict]) -> Dict[str, Any]:
        """Validates extracted questions and returns issues, warnings, and summary."""
        issues = []
        for q in questions:
            num = q.get("num", "?")
            raw_q = q.get("raw_question", "")
            if not raw_q or len(raw_q) < 3:
                issues.append(f"Q{num}: Question text is unusually short or empty.")

            choices = q.get("choices", [])
            if len(choices) < 4:
                issues.append(f"Q{num}: Has only {len(choices)} choices instead of 4.")

            empty_choices = [c["letter"] for c in choices if not c["text"] or len(c["text"]) <= 3]
            if empty_choices:
                issues.append(f"Q{num}: Choices {', '.join(empty_choices)} appear empty.")

            ans = q.get("answer", "")
            if ans not in ("A", "B", "C", "D"):
                issues.append(f"Q{num}: Invalid answer key '{ans}'.")

        return {
            "total_questions": len(questions),
            "is_valid": len(issues) == 0,
            "issues": issues,
            "summary": "All questions verified successfully" if not issues else f"{len(issues)} potential issue(s) detected"
        }
