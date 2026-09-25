"""
AI-powered Docx Parser and Inconsistent Answer Key Resolver using Groq API.

Features:
- Fast Llama 3.3 70B inference (< 400ms)
- Resolves freeform answer keys (e.g., "Answers: 1 is b, 2 is d, three was c")
- Resolves visual styling markers: Highlights, Underlines, Italics, Colors, and Bolds
- Safe fallback: preserves existing regex-parsed questions if API call fails
"""

import os
import re
import json
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Optional, Union, Any

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False


class GroqAnswerResolver:
    @staticmethod
    def is_available() -> bool:
        return GROQ_AVAILABLE

    @staticmethod
    def get_api_key(config: Optional[dict] = None) -> Optional[str]:
        if config and config.get("groq_api_key"):
            return config["groq_api_key"].strip()
        return os.environ.get("GROQ_API_KEY", "").strip() or None

    @classmethod
    def extract_rich_styled_text(cls, docx_source: Union[str, Path, bytes]) -> str:
        """
        Extracts document text while annotating visual styling tags:
        [HIGHLIGHT: text], [UNDERLINE: text], [ITALIC: text], [COLOR_HEX: text]
        to help the AI detect formatting-based answer indicators.
        """
        if isinstance(docx_source, (str, Path)):
            f_in = open(docx_source, "rb")
        else:
            import io
            f_in = io.BytesIO(docx_source)

        styled_lines = []
        try:
            with zipfile.ZipFile(f_in) as z:
                if "word/document.xml" not in z.namelist():
                    return ""
                xml_content = z.read("word/document.xml")
                root = ET.fromstring(xml_content)

                w_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
                p_tag = f"{{{w_ns}}}p"
                r_tag = f"{{{w_ns}}}r"
                t_tag = f"{{{w_ns}}}t"
                rPr_tag = f"{{{w_ns}}}rPr"
                highlight_tag = f"{{{w_ns}}}highlight"
                u_tag = f"{{{w_ns}}}u"
                i_tag = f"{{{w_ns}}}i"
                b_tag = f"{{{w_ns}}}b"
                color_tag = f"{{{w_ns}}}color"

                for p in root.iter(p_tag):
                    line_parts = []
                    for r in p.iter(r_tag):
                        # Extract run text
                        r_text = "".join(t.text or "" for t in r.iter(t_tag))
                        if not r_text:
                            continue

                        rPr = r.find(rPr_tag)
                        is_highlighted = False
                        is_underlined = False
                        is_italic = False
                        is_colored = False
                        color_val = ""

                        if rPr is not None:
                            if rPr.find(highlight_tag) is not None:
                                is_highlighted = True
                            if rPr.find(u_tag) is not None:
                                is_underlined = True
                            if rPr.find(i_tag) is not None:
                                is_italic = True
                            color_el = rPr.find(color_tag)
                            if color_el is not None:
                                color_val = color_el.attrib.get(f"{{{w_ns}}}val", "")
                                if color_val and color_val.lower() not in ("auto", "000000"):
                                    is_colored = True

                        formatted = r_text
                        if is_highlighted:
                            formatted = f"[HIGHLIGHT: {formatted}]"
                        if is_underlined:
                            formatted = f"[UNDERLINE: {formatted}]"
                        if is_colored:
                            formatted = f"[COLOR_{color_val}: {formatted}]"
                        if is_italic and not is_highlighted and not is_underlined:
                            formatted = f"[ITALIC: {formatted}]"

                        line_parts.append(formatted)

                    line_str = "".join(line_parts).strip()
                    if line_str:
                        styled_lines.append(line_str)
        finally:
            if isinstance(docx_source, (str, Path)):
                f_in.close()

        return "\n".join(styled_lines)

    @classmethod
    def resolve_answers_with_groq(
        cls,
        docx_source: Union[str, Path, bytes],
        questions: List[Dict],
        config: Optional[dict] = None
    ) -> Dict[str, Any]:
        """
        Uses Groq LLM (llama-3.3-70b-versatile) to resolve inconsistent answer keys:
        - Freeform answer keys at the bottom ("Answers: 1 is b, 2 is d, three was c")
        - Formatting indicators ([HIGHLIGHT: ...], [UNDERLINE: ...], [ITALIC: ...], etc.)
        """
        api_key = cls.get_api_key(config)
        if not api_key:
            return {
                "success": False,
                "error": "No Groq API key configured. Please set your Groq API key in Settings or export GROQ_API_KEY.",
                "questions": questions
            }

        if not GROQ_AVAILABLE:
            return {
                "success": False,
                "error": "The 'groq' python package is not installed.",
                "questions": questions
            }

        styled_doc = cls.extract_rich_styled_text(docx_source)
        if not styled_doc:
            return {
                "success": False,
                "error": "Could not read docx text content.",
                "questions": questions
            }

        model_name = (config or {}).get("groq_model", "llama-3.3-70b-versatile")
        client = Groq(api_key=api_key)

        # Build prompt showing questions and full docx text
        q_summary = []
        for q in questions:
            choices_txt = " | ".join(f"{c['letter']}: {c['text']}" for c in q.get("choices", []))
            q_summary.append(f"Question {q['num']}: {q.get('raw_question', '')}\nChoices: {choices_txt}\nCurrent Parsed Answer: {q.get('answer', 'UNKNOWN')}")

        questions_block = "\n\n".join(q_summary)

        system_prompt = (
            "You are an expert educational exam parser specialized in identifying correct answer keys in messy documents.\n"
            "Examine the document text and questions, paying close attention to:\n"
            "1. Freeform answer keys written anywhere in the document (often at the very bottom, e.g., 'Answers: 1 is b, 2 is d, three was c' or '1-B 2-D 3-A').\n"
            "2. Visual styling annotations such as [HIGHLIGHT: ...], [UNDERLINE: ...], [COLOR_...: ...], [ITALIC: ...], or text like '(correct answer)'.\n"
            "3. If no explicit teacher key exists, determine the factually correct choice (A, B, C, or D).\n\n"
            "Output valid JSON ONLY matching this exact schema:\n"
            "{\n"
            '  "resolved_answers": [\n'
            '    {\n'
            '      "num": 1,\n'
            '      "answer": "B",\n'
            '      "confidence": "high",\n'
            '      "source": "Found in footer: Answers: 1 is b"\n'
            '    }\n'
            "  ]\n"
            "}"
        )

        user_prompt = (
            f"=== DOCUMENT TEXT (WITH STYLING ANNOTATIONS) ===\n{styled_doc[:6000]}\n\n"
            f"=== QUESTIONS TO RESOLVE ===\n{questions_block}\n\n"
            f"Resolve the exact correct answer key (A, B, C, or D) for each of the {len(questions)} questions."
        )

        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=2048,
            )

            content = response.choices[0].message.content
            parsed_json = json.loads(content)
            resolved_list = parsed_json.get("resolved_answers", [])

            # Map resolved answers back to questions
            resolved_map = {item["num"]: item for item in resolved_list if "num" in item and "answer" in item}

            updated_questions = []
            changes_made = []

            for q in questions:
                q_copy = dict(q)
                num = q_copy.get("num")
                if num in resolved_map:
                    res_item = resolved_map[num]
                    new_ans = str(res_item["answer"]).strip().upper()
                    old_ans = str(q_copy.get("answer", "")).strip().upper()

                    if new_ans in ["A", "B", "C", "D"]:
                        if old_ans != new_ans:
                            changes_made.append({
                                "num": num,
                                "old_answer": old_ans,
                                "new_answer": new_ans,
                                "source": res_item.get("source", "Groq AI Resolution")
                            })
                        q_copy["answer"] = new_ans
                        q_copy["ai_resolved"] = True
                        q_copy["ai_source"] = res_item.get("source", "Groq AI")

                updated_questions.append(q_copy)

            return {
                "success": True,
                "model_used": model_name,
                "changes_count": len(changes_made),
                "changes": changes_made,
                "questions": updated_questions
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Groq API error: {str(e)}",
                "questions": questions
            }

    @classmethod
    def extract_all_questions_with_groq(
        cls,
        docx_source: Union[str, Path, bytes],
        config: Optional[dict] = None
    ) -> Dict[str, Any]:
        """
        When standard regex finds 0 questions (e.g. non-standard structure or tables),
        Groq extracts all comprehension questions, choices, and answers from the full document.
        """
        api_key = cls.get_api_key(config)
        if not api_key or not GROQ_AVAILABLE:
            return {"success": False, "error": "Groq not available or API key missing", "questions": []}

        styled_doc = cls.extract_rich_styled_text(docx_source)
        if not styled_doc:
            return {"success": False, "error": "Could not read docx text content", "questions": []}

        model_name = (config or {}).get("groq_model", "llama-3.3-70b-versatile")
        client = Groq(api_key=api_key)

        system_prompt = (
            "You are an expert exam parser. Read the document text and extract all comprehension multiple-choice questions.\n"
            "Rules:\n"
            "1. Identify every question and its 4 choices (A, B, C, D).\n"
            "2. Determine the correct answer (A, B, C, or D):\n"
            "   - Look for freeform answer keys at the bottom of the document (e.g., 'Answers: 1 is b, 2 is d, three was c').\n"
            "   - Look for styling annotations like [HIGHLIGHT: ...], [UNDERLINE: ...], [ITALIC: ...], or '(correct answer)'.\n"
            "   - If no teacher indicator is found, choose the factually correct option.\n"
            "3. Return valid JSON ONLY matching this schema:\n"
            "{\n"
            '  "questions": [\n'
            '    {\n'
            '      "num": 1,\n'
            '      "question": "1. What did the author do?",\n'
            '      "raw_question": "What did the author do?",\n'
            '      "choices": [\n'
            '        {"letter": "A", "text": "A. Choice 1"},\n'
            '        {"letter": "B", "text": "B. Choice 2"},\n'
            '        {"letter": "C", "text": "C. Choice 3"},\n'
            '        {"letter": "D", "text": "D. Choice 4"}\n'
            '      ],\n'
            '      "answer": "B",\n'
            '      "source": "Found in footer: Answers: 1 is b"\n'
            '    }\n'
            '  ]\n'
            "}"
        )

        user_prompt = f"=== DOCUMENT CONTENT ===\n{styled_doc[:7500]}\n\nExtract all comprehension questions in structured JSON."

        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=3000,
            )
            content = response.choices[0].message.content
            parsed_json = json.loads(content)
            q_list = parsed_json.get("questions", [])

            formatted_questions = []
            for i, q in enumerate(q_list):
                q_num = q.get("num", i + 1)
                raw_q = q.get("raw_question") or q.get("question", "")
                raw_q = re.sub(r"^(?:Q\d+[\.\:]|\d+[\.\:])\s*", "", raw_q).strip()

                choices = []
                for c in q.get("choices", []):
                    let = str(c.get("letter", "")).strip().upper()
                    t = str(c.get("text", "")).strip()
                    clean_t = re.sub(r"^[A-Da-d][\.\)]\s*", "", t).strip()
                    choices.append({"letter": let, "text": f"{let}. {clean_t}"})

                ans = str(q.get("answer", "A")).strip().upper()
                if ans not in ["A", "B", "C", "D"]:
                    ans = "A"

                formatted_questions.append({
                    "num": q_num,
                    "question": f"{q_num}. {raw_q}",
                    "raw_question": raw_q,
                    "choices": choices,
                    "answer": ans,
                    "ai_extracted": True,
                    "ai_source": q.get("source", "Groq AI")
                })

            return {
                "success": True,
                "questions": formatted_questions,
                "count": len(formatted_questions)
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Groq AI full extraction error: {str(e)}",
                "questions": []
            }
