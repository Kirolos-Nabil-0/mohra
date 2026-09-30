"""
Unit tests for special character handling in story names:
- Commas (",")
- Dashes ("-")
- Apostrophes ("'", "’", "`")
- Dashes used in place of apostrophes ("-s", "-t", "-d", "-m", "-ll", "-ve", "-re")
- Underscores used as apostrophes ("_s", "_t")
- Google Drive archive timestamps ("-20260720T094718Z-1-001")
- Trailing underscores ("The Little Bridge of Music_")
- SheetParser find_story
- DriveDownloader _matches_story_name
- ProgressTracker is_processed
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from modules.title_utils import (
    clean_story_title,
    canonical_title_key,
    titles_match,
    matches_search_query,
    generate_readora_search_queries,
)
from modules.drive_downloader import DriveDownloader
from modules.sheet_parser import SheetParser
from modules.progress import ProgressTracker
from modules.readora_client import ReadoraClient


class TestSpecialCharacterMatching(unittest.TestCase):
    def test_clean_story_title_special_characters(self):
        # 1. Comma handling
        self.assertEqual(clean_story_title("Ready, Set, Camp"), "Ready, Set, Camp")
        self.assertEqual(clean_story_title("The Lion, the Witch and the Wardrobe"), "The Lion, the Witch and the Wardrobe")

        # 2. Dash in place of apostrophe
        self.assertEqual(clean_story_title("Don-t Look Back"), "Don't Look Back")
        self.assertEqual(clean_story_title("Can-t Stop"), "Can't Stop")
        self.assertEqual(clean_story_title("Jack-s Adventure"), "Jack's Adventure")
        self.assertEqual(clean_story_title("It-s Time"), "It's Time")
        self.assertEqual(clean_story_title("We-ll Find It"), "We'll Find It")

        # 3. Normal dashes in compound words preserved
        self.assertEqual(clean_story_title("Spider-Man_s Day of Helping"), "Spider-Man's Day of Helping")
        self.assertEqual(clean_story_title("X-ray Fish"), "X-ray Fish")
        self.assertEqual(clean_story_title("Self-Portrait"), "Self-Portrait")

        # 4. Underscores used as apostrophes
        self.assertEqual(clean_story_title("Tommy_s Tomato Adventure"), "Tommy's Tomato Adventure")
        self.assertEqual(clean_story_title("Lily_s Color Quest"), "Lily's Color Quest")

        # 5. Trailing underscores stripped cleanly (not converted to apostrophes)
        self.assertEqual(clean_story_title("Why Do We Wash Our Hands_"), "Why Do We Wash Our Hands")
        self.assertEqual(clean_story_title("The Little Bridge of Music_"), "The Little Bridge of Music")

        # 6. Google Drive archive timestamp suffixes stripped
        self.assertEqual(clean_story_title("Lily_s Color Quest-20260720T094718Z-1-001"), "Lily's Color Quest")
        self.assertEqual(clean_story_title("The Train That Loved Cookies_-20260810T110327Z-1-001"), "The Train That Loved Cookies")

    def test_canonical_title_key_equivalence(self):
        # Commas, dashes, apostrophes, and spaces all collapse to exact same key
        cases = [
            ("Ready, Set, Camp", ["Ready, Set, Camp", "Ready Set Camp", "Ready - Set - Camp", "ready,set,camp"]),
            ("Don't Look Back", ["Don't Look Back", "Don-t Look Back", "Don’t Look Back", "Dont Look Back", "Don t Look Back"]),
            ("Jack's Adventure", ["Jack's Adventure", "Jack-s Adventure", "Jack’s Adventure", "Jacks Adventure", "Jack_s Adventure"]),
            ("Lily's Color Quest", ["Lily's Color Quest", "Lily_s Color Quest-20260720T094718Z-1-001", "Lily-s Color Quest"]),
            ("The Little Bridge of Music", ["The Little Bridge of Music", "The Little Bridge of Music_", "The Little Bridge of Music-"]),
        ]
        for canonical_name, variants in cases:
            expected_key = canonical_title_key(canonical_name)
            for var in variants:
                self.assertEqual(canonical_title_key(var), expected_key, f"Failed for variant: {var}")

    def test_titles_match(self):
        self.assertTrue(titles_match("Don-t Look Back", "Don't Look Back"))
        self.assertTrue(titles_match("Don't Look Back", "Don’t Look Back"))
        self.assertTrue(titles_match("Ready, Set, Camp", "Ready Set Camp"))
        self.assertTrue(titles_match("Lily_s Color Quest-20260720T094718Z-1-001", "Lily's Color Quest"))
        self.assertTrue(titles_match("Tommy_s Tomato Adventure", "Tommy's Tomato Adventure"))
        self.assertTrue(titles_match("The Little Bridge of Music_", "The Little Bridge of Music"))

    def test_matches_search_query(self):
        # User types with dash instead of apostrophe
        self.assertTrue(matches_search_query("don-t", "Don't Look Back"))
        self.assertTrue(matches_search_query("don't", "Don-t Look Back"))
        self.assertTrue(matches_search_query("dont", "Don't Look Back"))

        # User searches without commas
        self.assertTrue(matches_search_query("ready set", "Ready, Set, Camp"))
        self.assertTrue(matches_search_query("ready, set", "Ready, Set, Camp"))

        # User searches compound words with space or dash
        self.assertTrue(matches_search_query("spider man", "Spider-Man's Day of Helping"))
        self.assertTrue(matches_search_query("spider-man", "Spider-Man's Day of Helping"))

    def test_readora_search_queries_generation(self):
        # Verify alternative query generation for Readora search bar
        q_comma = generate_readora_search_queries("Ready, Set, Camp")
        self.assertIn("Ready, Set, Camp", q_comma)
        self.assertIn("Ready Set Camp", q_comma)

        q_dash_apos = generate_readora_search_queries("Don-t Look Back")
        self.assertIn("Don't Look Back", q_dash_apos)
        self.assertIn("Dont Look Back", q_dash_apos)

        q_timestamp = generate_readora_search_queries("Lily_s Color Quest-20260720T094718Z-1-001")
        self.assertIn("Lily's Color Quest", q_timestamp)

    def test_drive_downloader_matches_story_name(self):
        # File in drive folder vs story name from sheet
        self.assertTrue(DriveDownloader._matches_story_name("Lily's Color Quest.docx", "Lily_s Color Quest-20260720T094718Z-1-001"))
        self.assertTrue(DriveDownloader._matches_story_name("Don't Look Back.docx", "Don-t Look Back"))
        self.assertTrue(DriveDownloader._matches_story_name("Ready Set Camp.docx", "Ready, Set, Camp"))
        self.assertTrue(DriveDownloader._matches_story_name("Why Do We Wash Our Hands.docx", "Why Do We Wash Our Hands_"))
        self.assertFalse(DriveDownloader._matches_story_name("Second Language.docx", "Don't Look Back"))

    def test_progress_tracker_canonical_lookup(self, tmp_path=None):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            tracker_file = tf.name

        tracker = ProgressTracker(filepath=tracker_file)
        # Record success using sheet title with dash as apostrophe
        tracker.record_success({"story_name": "Don-t Look Back"}, questions_count=10)

        # Should match when queried with proper apostrophe
        self.assertTrue(tracker.is_processed("Don't Look Back"))
        # Should match with curly apostrophe
        self.assertTrue(tracker.is_processed("Don’t Look Back"))
        # Should match with raw dash
        self.assertTrue(tracker.is_processed("Don-t Look Back"))
        # Should match without apostrophe
        self.assertTrue(tracker.is_processed("Dont Look Back"))
        # Unrelated story should not match
        self.assertFalse(tracker.is_processed("Other Story"))

    def test_readora_client_search_book_mocked(self):
        client = ReadoraClient(config={})
        client.page = MagicMock()

        # Mock page locator
        mock_input = MagicMock()
        mock_cards = MagicMock()
        client.page.locator.side_effect = lambda sel: (
            mock_cards if "main a" in sel else mock_input
        )

        # Simulate Readora showing card "Don't Look Back" when searched
        card_el = MagicMock()
        card_el.inner_text.return_value = "Don't Look Back"
        card_el.get_attribute.return_value = "/super_admin/books/12345"

        mock_cards.count.return_value = 1
        mock_cards.nth.return_value = card_el

        # Search with dash in place of apostrophe
        res = client.search_book("Don-t Look Back")
        self.assertIsNotNone(res)
        self.assertEqual(res["matched_title"], "Don't Look Back")
        self.assertIn("12345/edit", res["edit_url"])


if __name__ == "__main__":
    unittest.main()
