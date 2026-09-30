import io
import unittest
import zipfile
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from modules.docx_parser import DocxParser
from modules.readora_client import ReadoraClient
from modules import review_service


def make_docx(lines):
    body = "".join(f"<w:p><w:r><w:t>{escape(line)}</w:t></w:r></w:p>" for line in lines)
    xml = f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


class VocabularyQuizTests(unittest.TestCase):
    def test_multiline_quiz_is_separate_and_uses_explicit_key(self):
        data = make_docx([
            "Comprehension Questions",
            "Why does water need cleaning?",
            "A. For safety", "B. For colour", "C. For speed", "D. For storage", "Answer: A",
            "Cambridge EFL Vocabulary Quiz", "Grade / Stage: Grade 4", "CEFR Level: A2",
            "In the story, essential means something is…",
            "A. bright and colourful", "B. very small", "C. very important and needed", "D. easy to carry", "Correct answer: C",
            "In the story, filtration means…",
            "A. cleaning water by using a filter", "B. storing water in a tank", "C. moving water through pipes", "D. mixing water with sand", "Correct answer: A",
        ])
        comprehension = DocxParser.parse_comprehension_questions(data)
        vocabulary = DocxParser.parse_vocabulary_questions(data)
        self.assertEqual(len(comprehension), 1)
        self.assertEqual([q["answer"] for q in vocabulary], ["C", "A"])
        self.assertTrue(DocxParser.validate_questions(vocabulary)["is_valid"])

    def test_missing_vocabulary_key_is_invalid(self):
        data = make_docx([
            "Vocabulary Quiz",
            "In the story, essential means…",
            "A. bright", "B. small", "C. important", "D. easy",
        ])
        questions = DocxParser.parse_vocabulary_questions(data)
        self.assertEqual(len(questions), 1)
        self.assertIsNone(questions[0]["answer"])
        self.assertFalse(DocxParser.validate_questions(questions)["is_valid"])

    def test_cached_quizzes_and_marked_inline_choices(self):
        paths = [p for p in Path("cache/docx").glob("*.docx") if DocxParser.has_vocabulary_quiz(p)]
        self.assertGreaterEqual(len(paths), 6)
        for path in paths:
            with self.subTest(path=path.name):
                self.assertEqual(len(DocxParser.parse_comprehension_questions(path)), 10)
                vocabulary = DocxParser.parse_vocabulary_questions(path)
                self.assertEqual(len(vocabulary), 10)
                self.assertTrue(DocxParser.validate_questions(vocabulary)["is_valid"])

    def test_review_marks_absent_quiz_as_skipped(self):
        data = make_docx(["Comprehension Questions", "Why?", "A. One", "B. Two", "C. Three", "D. Four", "Answer: A"])
        with TemporaryDirectory() as directory:
            path = Path(directory) / "First_language.docx"
            path.write_bytes(data)
            result = review_service.prepare_story_review(
                {"story_name": "Test", "drive_url": "https://drive.google.com/test", "docx_path": str(path)},
                {"cache_dir": directory}, download_if_missing=False,
            )
        self.assertTrue(result["success"])
        self.assertFalse(result["vocab_present"])
        self.assertEqual(result["vocab_questions"], [])

    def test_partial_save_never_marks_story_done(self):
        calls = []

        class Client:
            fail_vocabulary = True

            def __init__(self, _config):
                self.last_extracted_old_state = {"questions": []}

            def start_browser(self): pass
            def login(self): pass
            def search_book(self, _name): return {"matched_title": "Test", "edit_url": "test"}
            def open_book_edit(self, _book): pass
            def close(self): pass
            def edit_questions(self, _questions, dry_run=False, kind="comprehension"):
                calls.append(kind)
                if kind == "vocabulary" and self.fail_vocabulary:
                    raise RuntimeError("second modal rejected update")
                return True

        class Tracker:
            def record_failure(self, _story, error): calls.append(error)
            def record_success(self, *_args, **_kwargs): calls.append("success")

        class Manager:
            def acquire_browser_lock(self): return True
            def release_browser_lock(self): pass

        with patch.object(review_service, "ReadoraClient", Client), \
             patch.object(review_service, "ProgressTracker", Tracker), \
             patch.object(review_service.ThreadManager, "get_instance", return_value=Manager()):
            def question(rubric, answer):
                return {"num": 1, "question": f"1. {rubric}", "raw_question": rubric,
                        "choices": [{"letter": letter, "text": f"{letter}. Choice {letter}"} for letter in "ABCD"],
                        "answer": answer}
            result = review_service.execute_accept_and_apply(
                {"story_name": "Test"}, [question("Why?", "A")],
                {"auto_mark_sheet_done": False}, vocab_questions=[question("What?", "B")],
            )
        self.assertFalse(result["success"])
        self.assertTrue(result["partial_save"])
        self.assertEqual(calls[:2], ["comprehension", "vocabulary"])
        self.assertNotIn("success", calls)

        calls.clear()
        Client.fail_vocabulary = False
        with patch.object(review_service, "ReadoraClient", Client), \
             patch.object(review_service, "ProgressTracker", Tracker), \
             patch.object(review_service.ThreadManager, "get_instance", return_value=Manager()):
            result = review_service.execute_accept_and_apply(
                {"story_name": "Test"}, [question("Why?", "A")],
                {"auto_mark_sheet_done": False}, vocab_questions=[question("What?", "B")],
            )
        self.assertTrue(result["success"])
        self.assertEqual(calls, ["comprehension", "vocabulary", "success"])

    def test_saved_questions_must_match_read_back(self):
        wanted = [{"question": "1. What?", "choices": [{"text": "A. One"}], "answer": "A"}]
        self.assertTrue(ReadoraClient._questions_match(wanted, [dict(wanted[0])]))
        self.assertFalse(ReadoraClient._questions_match(wanted, [{**wanted[0], "answer": "B"}]))

    def test_split_line_comprehension_with_trailing_answer_key(self):
        data = make_docx([
            "Comprehension Questions",
            "Why do children love exercising?",
            "A. It is boring B. It is fun and keeps them healthy ✅ C. They are forced D. No reason",
            "What is the best way to stay fit?",
            "A. Sleep all day B. Eat junk food C. Daily physical activity D. Watch TV",
            "Answer Key",
            "1. B",
            "2. C",
            "Vocabulary Quiz",
            "In the text, active means...",
            "A. sleeping B. moving around C. quiet D. still",
            "Answer: B",
        ])
        comp = DocxParser.parse_comprehension_questions(data)
        self.assertEqual(len(comp), 2)
        self.assertEqual(comp[0]["answer"], "B")
        self.assertEqual(comp[1]["answer"], "C")
        self.assertTrue(DocxParser.validate_questions(comp)["is_valid"])

        vocab = DocxParser.parse_vocabulary_questions(data)
        self.assertEqual(len(vocab), 1)
        self.assertEqual(vocab[0]["answer"], "B")
        self.assertTrue(DocxParser.validate_questions(vocab)["is_valid"])


if __name__ == "__main__":
    unittest.main()
