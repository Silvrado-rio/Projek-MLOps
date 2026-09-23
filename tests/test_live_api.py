"""Checks that live ingestion never silently falls back to demo metadata."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.ingest import FALLBACK_CHAPTERS, fetch_chapters


SETTINGS = {
    "endpoint": "https://api.mangadex.org/chapter",
    "limit": 1,
    "translated_language": "id",
    "timeout_seconds": 1,
}


class LiveApiModeTest(unittest.TestCase):
    @patch("src.ingest.time.sleep")
    @patch("src.ingest.urllib.request.urlopen", side_effect=OSError("network unreachable"))
    def test_live_mode_fails_instead_of_using_fallback(self, _urlopen, _sleep):
        with self.assertRaisesRegex(RuntimeError, "live ingestion dibatalkan"):
            fetch_chapters(SETTINGS, require_live_api=True)

    def test_offline_mode_remains_explicit(self):
        chapters, source, _payload = fetch_chapters(SETTINGS, offline=True)
        self.assertEqual(chapters, FALLBACK_CHAPTERS)
        self.assertEqual(source, "fallback_catalog")


if __name__ == "__main__":
    unittest.main()
