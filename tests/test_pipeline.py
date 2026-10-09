"""Small regression tests for the live-source contract and chapter deduplication."""

from __future__ import annotations

import csv
import json
import math
import tempfile
import unittest
import urllib.error
from io import BytesIO, StringIO
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.ingest import OFFLINE_PAYLOADS, fetch_mangadex, ingest
from src.run_pipeline import main
from src.transform import _build_features, transform
from src.validate import deduplicate_chapters


SETTINGS = {
    "base_url": "https://api.mangadex.org",
    "catalog_limit": 2,
    "chapter_limit": 10,
    "timeout_seconds": 1,
}


class PipelineTest(unittest.TestCase):
    @patch("src.ingest.time.sleep")
    @patch("src.ingest._request_json")
    def test_fixed_cohort_is_completed_once_and_requested_in_batches(self, request_json, _sleep):
        ids = [f"manga-{index}" for index in range(101)]

        def response(url, params, timeout):
            if url.endswith("/manga") and "/statistics/" not in url:
                selected = [value for name, value in params if name == "ids[]"]
                if not selected:
                    offset = int(dict(params)["offset"])
                    selected = ids[offset:offset + 100]
                return {"data": [{"id": item} for item in reversed(selected)]}
            if "/statistics/" in url:
                return {"statistics": {value: {"follows": 1} for name, value in params if name == "manga[]"}}
            return {"data": [], "total": 0}

        request_json.side_effect = response
        settings = {**SETTINGS, "catalog_limit": 101, "tracked_manga_ids": ids[:100]}
        first = fetch_mangadex(settings)
        frozen = [item["id"] for item in first["catalog"]["data"]]
        self.assertEqual(set(frozen), set(ids))
        catalog_calls = [call for call in request_json.call_args_list if call.args[0].endswith("/manga") and "/statistics/" not in call.args[0]]
        self.assertEqual(len(catalog_calls), 4)
        self.assertEqual([dict(call.args[1])["offset"] for call in catalog_calls[:2]], ["0", "100"])
        for call in catalog_calls[2:]:
            self.assertLessEqual(sum(name == "ids[]" for name, _ in call.args[1]), 100)
            self.assertIn(("originalLanguage[]", "ko"), call.args[1])
        request_json.reset_mock()
        second = fetch_mangadex({**settings, "tracked_manga_ids": frozen})
        self.assertEqual({item["id"] for item in second["catalog"]["data"]}, set(ids))
        for call in request_json.call_args_list:
            if call.args[0].endswith("/manga") and "/statistics/" not in call.args[0]:
                self.assertTrue(any(name == "ids[]" for name, _ in call.args[1]))

    @patch("src.ingest._request_json")
    def test_missing_fixed_catalog_or_statistics_fails_before_raw_writes(self, request_json):
        settings = {**SETTINGS, "catalog_limit": 1, "tracked_manga_ids": ["missing"]}
        for responses in ([{"data": []}], [{"data": [{"id": "missing"}]}, {"statistics": {}}]):
            with self.subTest(responses=responses), tempfile.TemporaryDirectory() as directory:
                request_json.reset_mock()
                request_json.side_effect = responses
                with self.assertRaisesRegex(RuntimeError, "cohort tetap tidak tersedia"):
                    ingest(Path(directory), settings)
                self.assertEqual(list(Path(directory).iterdir()), [])
                self.assertEqual(request_json.call_count, len(responses))

    @patch("src.run_pipeline.transform", return_value={"feature_rows": 0})
    @patch("src.run_pipeline.ingest")
    def test_main_persists_and_reuses_the_completed_cohort(self, fetch, _transform):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "metadata").mkdir()
            config_path = root / "config.json"
            config_path.write_text(json.dumps({"mangadex": {**SETTINGS, "tracked_manga_ids": ["m1"]}, "quality": {}}), encoding="utf-8")
            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps({"payload": {"data": [{"id": "m1"}, {"id": "m2"}]}}), encoding="utf-8")
            fetch.return_value = {"raw_files": {"catalog": str(catalog_path)}}
            with patch("sys.argv", ["pipeline", "--config", str(config_path), "--output-root", str(root)]), patch("sys.stdout", new_callable=StringIO):
                main()
                self.assertEqual(fetch.call_args.args[1]["tracked_manga_ids"], ["m1"])
                main()
                self.assertEqual(fetch.call_args.args[1]["tracked_manga_ids"], ["m1", "m2"])
            cohort_path = root / "metadata" / "tracked_manga_ids.json"
            self.assertEqual(json.loads(cohort_path.read_text(encoding="utf-8")), ["m1", "m2"])

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
            self.assertIn(("includes[]", "manga"), call.args[1])
            self.assertNotIn("includes", {name for name, _value in call.args[1]})
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

    @patch("src.ingest.datetime")
    @patch("src.ingest.fetch_mangadex")
    def test_reruns_preserve_raw_and_update_only_current_daily_snapshot(self, fetch, clock):
        first_time = datetime(2026, 9, 25, 9, tzinfo=timezone.utc)
        current_time = first_time + timedelta(days=1)
        # Even identical clock readings must produce distinct raw files.
        clock.now.side_effect = [first_time, current_time, current_time]
        batches = [deepcopy(OFFLINE_PAYLOADS) for _ in range(3)]
        for batch, follows in zip(batches, [120, 125, 130]):
            batch["statistics"]["statistics"]["demo-manga-1"]["follows"] = follows
        fetch.side_effect = batches

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original_bytes = {}
            run_ids = set()
            for batch in batches:
                ingestion = ingest(root, SETTINGS, offline=True)
                run_ids.add(ingestion["run_id"])
                for name, filename in ingestion["raw_files"].items():
                    path = Path(filename)
                    self.assertNotIn(path, original_bytes)
                    original_bytes[path] = path.read_bytes()
                    raw = json.loads(original_bytes[path])
                    self.assertEqual(raw["run_id"], ingestion["run_id"])
                    self.assertEqual(raw["fetched_at"], ingestion["fetched_at"])
                    self.assertEqual(raw["payload"], batch[name])
                manifest = transform(root, ingestion, {"max_invalid_ratio": 0.05})

            self.assertEqual(len(run_ids), 3)
            self.assertEqual(len(list((root / "raw").rglob("*.json"))), 9)
            for path, content in original_bytes.items():
                self.assertEqual(path.read_bytes(), content)
            with Path(manifest["interim_file"]).open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(
                [(row["snapshot_date"], row["follows"]) for row in rows],
                [("2026-09-25", "120"), ("2026-09-26", "130")],
            )
            self.assertEqual(manifest["chapter_history_rows"], 1)
            self.assertEqual(manifest["feature_rows"], 0)

    @patch("src.ingest.fetch_mangadex", side_effect=RuntimeError("API unavailable"))
    def test_failed_fetch_preserves_existing_data(self, _fetch):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "raw" / "catalog" / "2026-09-25" / "catalog.json"
            marker.parent.mkdir(parents=True)
            marker.write_text('{"existing": true}', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "API unavailable"):
                ingest(root, SETTINGS)
            self.assertEqual(marker.read_text(encoding="utf-8"), '{"existing": true}')
            self.assertEqual([path for path in root.rglob("*") if path.is_file()], [marker])

    def test_committed_live_sample_can_be_preprocessed_repeatedly(self):
        raw_root = Path(__file__).resolve().parents[1] / "data" / "raw"
        paths = {
            name: raw_root / name / "2026-09-26" / f"{name}-sample.json"
            for name in ("catalog", "statistics", "chapters")
        }
        original = {name: path.read_bytes() for name, path in paths.items()}
        catalog = json.loads(original["catalog"])
        ingestion = {
            "catalog_source": catalog["source"],
            "snapshot_date": catalog["fetched_at"][:10],
            "fetched_at": catalog["fetched_at"],
            "raw_files": {name: str(path) for name, path in paths.items()},
        }
        with tempfile.TemporaryDirectory() as directory:
            first = transform(Path(directory), ingestion, {"max_invalid_ratio": 0.05})
            second = transform(Path(directory), ingestion, {"max_invalid_ratio": 0.05})
            self.assertEqual(second["catalog_source"], "mangadex_api")
            self.assertEqual(second["snapshot_rows"], 1)
            self.assertGreater(second["chapter_history_rows"], 0)
            self.assertEqual(second["chapter_history_rows"], first["chapter_history_rows"])
            self.assertEqual(second["processed_sha256"], first["processed_sha256"])
            self.assertEqual(second["rejected_records"], 0)
            self.assertEqual(second["feature_rows"], 0)
            with Path(second["interim_file"]).open(encoding="utf-8", newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["title"], "Save the Earth!")
            self.assertEqual(row["rating_votes"], "")
        self.assertEqual({name: path.read_bytes() for name, path in paths.items()}, original)


if __name__ == "__main__":
    unittest.main()
