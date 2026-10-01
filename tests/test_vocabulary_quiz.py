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

    def test_vocab_ignored_enables_validation_in_dialog(self):
        import tkinter as tk
        from modules.gui_review_dialog import DryRunReviewDialog
        root = tk.Tk()
        root.withdraw()
        try:
            story = {"story_name": "The Big Debate", "sheet_name": "Grade 5", "row_index": 134, "drive_url": "https://drive.google.com/test"}
            cfg = {"cache_dir": "./cache"}
            dialog = DryRunReviewDialog(root, story, cfg)
            dialog.comprehension_questions = [
                {
                    "num": 1,
                    "question": "1. Test?",
                    "raw_question": "Test?",
                    "choices": [
                        {"letter": "A", "text": "A. One"},
                        {"letter": "B", "text": "B. Two"},
                        {"letter": "C", "text": "C. Three"},
                        {"letter": "D", "text": "D. Four"},
                    ],
                    "answer": "A"
                }
            ]
            dialog.vocab_present = True
            dialog.vocab_questions = []
            dialog._vocab_ignored = False
            dialog._refresh_validation()

            # Initially disabled because vocab has 0 questions
            self.assertEqual(str(dialog.btn_apply.cget("state")), tk.DISABLED)
            self.assertEqual(str(dialog.btn_dry_run_test.cget("state")), tk.DISABLED)
            self.assertEqual(dialog.lbl_vocab_status.cget("text"), "Vocabulary: needs correction")

            # Toggle ignore vocabulary
            dialog._toggle_ignore_vocab()
            self.assertTrue(dialog._vocab_ignored)
            self.assertEqual(str(dialog.btn_apply.cget("state")), tk.NORMAL)
            self.assertEqual(str(dialog.btn_dry_run_test.cget("state")), tk.NORMAL)
            self.assertIn("ignored", dialog.lbl_vocab_status.cget("text"))

            # Toggle back to include vocabulary
            dialog._toggle_ignore_vocab()
            self.assertFalse(dialog._vocab_ignored)
            self.assertEqual(str(dialog.btn_apply.cget("state")), tk.DISABLED)

            dialog.destroy()
        finally:
            root.destroy()

    def test_extract_vocab_questions_with_groq_mock(self):
        import modules.ai_groq_parser as ai_module
        from modules.ai_groq_parser import GroqAnswerResolver
        from unittest.mock import MagicMock
        data = make_docx(["Vocabulary Quiz", "1. Essential means...", "A. Crucial", "B. Small", "C. Fast", "D. Red", "Answer: A"])
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '''{
            "questions": [
                {
                    "num": 1,
                    "raw_question": "Essential means...",
                    "choices": [
                        {"letter": "A", "text": "Crucial"},
                        {"letter": "B", "text": "Small"},
                        {"letter": "C", "text": "Fast"},
                        {"letter": "D", "text": "Red"}
                    ],
                    "answer": "A",
                    "source": "Mock"
                }
            ]
        }'''
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_groq_cls = MagicMock(return_value=mock_client)

        with patch.object(GroqAnswerResolver, "get_api_key", return_value="fake_key"), \
             patch.object(ai_module, "GROQ_AVAILABLE", True), \
             patch.object(ai_module, "Groq", mock_groq_cls, create=True):
            res = GroqAnswerResolver.extract_vocab_questions_with_groq(data, {})
            self.assertTrue(res["success"])
            self.assertEqual(res["count"], 1)
            self.assertEqual(res["questions"][0]["answer"], "A")
            self.assertEqual(len(res["questions"][0]["choices"]), 4)

    def test_add_and_delete_question_in_dialog(self):
        import tkinter as tk
        from modules.gui_review_dialog import DryRunReviewDialog
        root = tk.Tk()
        root.withdraw()
        try:
            story = {"story_name": "Test Story", "sheet_name": "Grade 5", "row_index": 1, "drive_url": "https://drive.google.com/test"}
            cfg = {"cache_dir": "./cache"}
            dialog = DryRunReviewDialog(root, story, cfg)
            dialog.comprehension_questions = [
                {"num": 1, "question": "1. Q1?", "raw_question": "Q1?", "choices": [{"letter": "A", "text": "A. 1"}, {"letter": "B", "text": "B. 2"}, {"letter": "C", "text": "C. 3"}, {"letter": "D", "text": "D. 4"}], "answer": "A"}
            ]
            dialog.questions = dialog.comprehension_questions
            self.assertEqual(len(dialog.questions), 1)

            # Add question
            dialog._on_add_question()
            self.assertEqual(len(dialog.questions), 2)
            self.assertEqual(dialog.questions[1]["num"], 2)

            # Delete question
            dialog.selected_q_idx = 1
            dialog._on_delete_question()
            self.assertEqual(len(dialog.questions), 1)

            dialog.destroy()
        finally:
            root.destroy()

    def test_automatic_vocab_ai_fallback_in_prepare_review(self):
        from modules.ai_groq_parser import GroqAnswerResolver
        data = make_docx([
            "Comprehension Questions",
            "Why is the sky blue?",
            "A. Light B. Air C. Water D. Stars",
            "Answer: A",
            "Vocabulary Quiz",
            "Non standard unparseable vocabulary line",
        ])
        with TemporaryDirectory() as directory:
            path = Path(directory) / "First_language.docx"
            path.write_bytes(data)
            story = {"story_name": "Test", "drive_url": "https://drive.google.com/test", "docx_path": str(path)}
            cfg = {"cache_dir": directory, "groq_api_key": "fake_key"}

            mock_vocab_q = [
                {"num": 1, "question": "1. Word?", "raw_question": "Word?", "choices": [{"letter": "A", "text": "A. Def1"}, {"letter": "B", "text": "B. Def2"}, {"letter": "C", "text": "C. Def3"}, {"letter": "D", "text": "D. Def4"}], "answer": "B"}
            ]
            with patch.object(GroqAnswerResolver, "is_available", return_value=True), \
                 patch.object(GroqAnswerResolver, "get_api_key", return_value="fake_key"), \
                 patch.object(GroqAnswerResolver, "extract_vocab_questions_with_groq", return_value={"success": True, "questions": mock_vocab_q}):
                res = review_service.prepare_story_review(story, cfg, download_if_missing=False)
                self.assertTrue(res["success"])
                self.assertTrue(res["vocab_present"])
                self.assertEqual(len(res["vocab_questions"]), 1)
                self.assertEqual(res["vocab_questions"][0]["answer"], "B")


if __name__ == "__main__":
    unittest.main()


