import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from app.services.scraper_srv import ScraperService

class TestBotIdentification(unittest.TestCase):
    def setUp(self):
        self.scraper = ScraperService()
        self.monitor_token = "1209926912:AAF8zrjCKM4a-x8ZEH-F3KSWtomgAw_9w9Q"

    @patch("app.services.scraper_srv.settings")
    def test_is_monitor_bot_exact_match(self, mock_settings):
        mock_settings.bot_tokens = [self.monitor_token]
        
        # Exact match
        self.assertTrue(self.scraper.is_monitor_bot(self.monitor_token))
        
        # Match with whitespace
        self.assertTrue(self.scraper.is_monitor_bot(f"  {self.monitor_token}  "))
        self.assertTrue(self.scraper.is_monitor_bot(f"\n{self.monitor_token}\t"))

    @patch("app.services.scraper_srv.settings")
    def test_is_monitor_bot_id_match(self, mock_settings):
        mock_settings.bot_tokens = [self.monitor_token]
        
        # Same ID, different secret (simulating format variations or rotations)
        different_secret = "1209926912:DIFFERENT_SECRET"
        self.assertTrue(self.scraper.is_monitor_bot(different_secret))
        
        # Whitespace and same ID
        self.assertTrue(self.scraper.is_monitor_bot("  1209926912:XYZ  "))

    @patch("app.services.scraper_srv.settings")
    def test_is_monitor_bot_no_match(self, mock_settings):
        mock_settings.bot_tokens = [self.monitor_token]
        
        # Completely different token
        other_token = "987654321:OTHER_SECRET"
        self.assertFalse(self.scraper.is_monitor_bot(other_token))
        
        # Empty inputs
        self.assertFalse(self.scraper.is_monitor_bot(""))
        self.assertFalse(self.scraper.is_monitor_bot(None))

    @patch("app.services.scraper_srv.settings")
    def test_is_monitor_bot_missing_settings(self, mock_settings):
        mock_settings.bot_tokens = None
        self.assertFalse(self.scraper.is_monitor_bot(self.monitor_token))

    @patch("app.services.scraper_srv.settings")
    def test_is_monitor_bot_multi_token_list(self, mock_settings):
        """Test that is_monitor_bot works with multiple tokens in the list."""
        mock_settings.bot_tokens = [
            "111111111:AAAA",
            self.monitor_token,
            "333333333:CCCC"
        ]
        # Should match the second token
        self.assertTrue(self.scraper.is_monitor_bot(self.monitor_token))
        # Should match by ID for first token
        self.assertTrue(self.scraper.is_monitor_bot("111111111:DIFFERENT"))
        # Should NOT match an unknown token
        self.assertFalse(self.scraper.is_monitor_bot("999999999:UNKNOWN"))

if __name__ == "__main__":
    unittest.main()
