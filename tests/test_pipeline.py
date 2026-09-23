"""Small regression tests for the live-source contract and chapter deduplication."""

from __future__ import annotations

import csv
import math
import tempfile
import unittest
import urllib.error
from io import BytesIO
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from src.ingest import OFFLINE_PAYLOADS, fetch_mangadex, ingest
from src.transform import _build_features, transform
from src.validate import deduplicate_chapters


SETTINGS = {
    "base_url": "https://api.mangadex.org",
    "catalog_limit": 2,
    "chapter_limit": 10,
    "timeout_seconds": 1,
}


class PipelineTest(unittest.TestCase):
    def test_offline_mode_is_explicit(self):
        self.assertIs(fetch_mangadex(SETTINGS, offline=True), OFFLINE_PAYLOADS)

    @patch("src.ingest.time.sleep")
    @patch("src.ingest.urllib.request.urlopen", side_effect=OSError("network unreachable"))
    def test_live_failure_never_uses_fixture(self, _urlopen, _sleep):
        with self.assertRaisesRegex(RuntimeError, "setelah tiga percobaan"):
            fetch_mangadex(SETTINGS)

    @patch("src.ingest.urllib.request.urlopen")
    def test_http_400_reports_response_without_retry(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError(
            "https://api.mangadex.org/chapter",
            400,
            "Bad Request",
            {},
            BytesIO(b'{"errors":[{"detail":"invalid manga id"}]}'),
        )
        with self.assertRaisesRegex(RuntimeError, "HTTP 400.*invalid manga id"):
            fetch_mangadex(SETTINGS)
        self.assertEqual(urlopen.call_count, 1)

    @patch("src.ingest.time.sleep")
    @patch("src.ingest._request_json")
    def test_live_requests_one_manga_per_chapter_call(self, request_json, _sleep):
        catalog = {
            "data": [
                OFFLINE_PAYLOADS["catalog"]["data"][0],
                {"id": "demo-manga-2", "attributes": {"originalLanguage": "ko"}},
            ]
        }
        request_json.side_effect = [
            catalog,
            OFFLINE_PAYLOADS["statistics"],
            OFFLINE_PAYLOADS["chapters"],
            {"data": [], "total": 0},
        ]
        fetch_mangadex(SETTINGS)
        catalog_params = request_json.call_args_list[0].args[1]
        self.assertIn(("originalLanguage[]", "ko"), catalog_params)
        chapter_calls = request_json.call_args_list[2:]
        self.assertEqual(
            [[value for name, value in call.args[1] if name == "manga"] for call in chapter_calls],
            [["demo-manga-1"], ["demo-manga-2"]],
        )
        for call in chapter_calls:
            self.assertNotIn("translatedLanguage[]", {name for name, _value in call.args[1]})

    def test_chapter_dedup_keeps_first_mangadex_availability(self):
        records = [
            {
                "manga_id": "m1", "chapter_id": "later", "volume": "1", "chapter_number": "10",
                "translated_language": "id", "publish_at": "2024-01-02T00:00:00+00:00",
                "readable_at": None, "pages": 20,
            },
            {
                "manga_id": "m1", "chapter_id": "first", "volume": "1", "chapter_number": "10",
                "translated_language": "en", "publish_at": "2024-01-01T00:00:00+00:00",
                "readable_at": None, "pages": 20,
            },
        ]
        result = deduplicate_chapters(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["chapter_id"], "first")

    def test_offline_pipeline_writes_korean_manhwa_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)
            ingestion = ingest(output_root, SETTINGS, offline=True)
            manifest = transform(output_root, ingestion, {"max_invalid_ratio": 0.05})
            with Path(manifest["interim_file"]).open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(manifest["catalog_source"], "offline_fixture")
            self.assertEqual(manifest["duplicate_chapters"], 1)
            self.assertEqual(rows[0]["original_language"], "ko")
            self.assertEqual(rows[0]["latest_chapter_id"], "demo-chapter-en")

    def test_features_require_past_and_future_seven_day_snapshots(self):
        start = date(2024, 1, 1)
        snapshots = [
            {
                "snapshot_date": (start + timedelta(days=offset)).isoformat(),
                "manga_id": "m1",
                "title": "Demo",
                "follows": str(100 + offset),
                "average_rating": "8.0",
                "bayesian_rating": "7.5",
                "rating_votes": "10",
                "replies_count": str(offset),
            }
            for offset in range(15)
        ]
        features = _build_features(snapshots, [])
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["follows_delta_7d"], 7)
        self.assertAlmostEqual(
            features[0]["target_growth_next_7d"],
            round(math.log1p(114) - math.log1p(107), 6),
        )


if __name__ == "__main__":
    unittest.main()
