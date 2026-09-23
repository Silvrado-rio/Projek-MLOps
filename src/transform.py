"""Build auditable daily snapshots and temporal manhwa trend features."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .validate import deduplicate_chapters, validate_snapshot


SNAPSHOT_FIELDS = [
    "snapshot_date", "fetched_at", "manga_id", "title", "original_language", "status",
    "year", "tags", "content_rating", "follows", "average_rating", "bayesian_rating",
    "rating_votes", "replies_count", "latest_chapter_id", "latest_volume",
    "latest_chapter_number", "latest_translated_language", "latest_publish_at", "latest_pages",
]
CHAPTER_FIELDS = [
    "manga_id", "chapter_id", "volume", "chapter_number", "translated_language",
    "publish_at", "readable_at", "pages",
]
FEATURE_FIELDS = [
    "snapshot_date", "manga_id", "title", "follows", "average_rating", "bayesian_rating",
    "rating_votes", "replies_count", "follows_delta_1d", "follows_delta_7d",
    "follows_growth_7d", "replies_delta_7d", "rating_delta_7d", "chapter_count_7d",
    "days_since_update", "release_interval_mean_30d", "target_growth_next_7d",
]


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _number(value: str | int | float | None) -> float | None:
    return None if value in (None, "") else float(value)


def _attach_latest_chapter(manga_rows: list[dict], chapters: list[dict], fetched_at: str) -> None:
    cutoff = _parse_datetime(fetched_at)
    by_manga: dict[str, list[dict]] = defaultdict(list)
    for chapter in chapters:
        if _parse_datetime(chapter["publish_at"]) <= cutoff:
            by_manga[chapter["manga_id"]].append(chapter)

    for row in manga_rows:
        available = by_manga.get(row["manga_id"], [])
        latest = max(available, key=lambda item: _parse_datetime(item["publish_at"]), default={})
        row.update(
            {
                "latest_chapter_id": latest.get("chapter_id"),
                "latest_volume": latest.get("volume"),
                "latest_chapter_number": latest.get("chapter_number"),
                "latest_translated_language": latest.get("translated_language"),
                "latest_publish_at": latest.get("publish_at"),
                "latest_pages": latest.get("pages"),
            }
        )


def _merge_snapshots(existing: list[dict], current: list[dict]) -> list[dict]:
    merged = {(row["snapshot_date"], row["manga_id"]): row for row in existing}
    merged.update({(row["snapshot_date"], row["manga_id"]): row for row in current})
    return [merged[key] for key in sorted(merged)]


def _merge_chapters(existing: list[dict], current: list[dict]) -> list[dict]:
    normalized = [
        {field: (row.get(field) or None) for field in CHAPTER_FIELDS}
        for row in existing + current
    ]
    return deduplicate_chapters(normalized)


def _chapter_features(chapters: list[dict], manga_id: str, snapshot_day: date) -> tuple[int, int | str, float | str]:
    released = sorted(
        _parse_datetime(row["publish_at"]).date()
        for row in chapters
        if row["manga_id"] == manga_id and _parse_datetime(row["publish_at"]).date() <= snapshot_day
    )
    if not released:
        return 0, "", ""
    recent = [day for day in released if snapshot_day - timedelta(days=6) <= day <= snapshot_day]
    window = [day for day in released if snapshot_day - timedelta(days=29) <= day <= snapshot_day]
    intervals = [(right - left).days for left, right in zip(window, window[1:])]
    mean_interval = round(sum(intervals) / len(intervals), 4) if intervals else ""
    return len(recent), (snapshot_day - released[-1]).days, mean_interval


def _build_features(snapshots: list[dict], chapters: list[dict]) -> list[dict]:
    by_manga: dict[str, dict[date, dict]] = defaultdict(dict)
    for row in snapshots:
        by_manga[row["manga_id"]][date.fromisoformat(row["snapshot_date"])] = row

    features = []
    for manga_id, timeline in by_manga.items():
        for snapshot_day, row in sorted(timeline.items()):
            previous_day = timeline.get(snapshot_day - timedelta(days=1))
            previous_week = timeline.get(snapshot_day - timedelta(days=7))
            future_week = timeline.get(snapshot_day + timedelta(days=7))
            if previous_week is None or future_week is None:
                continue
            follows = int(row["follows"])
            follows_7d = follows - int(previous_week["follows"])
            previous_follows = int(previous_week["follows"])
            chapter_count, days_since_update, release_interval = _chapter_features(
                chapters, manga_id, snapshot_day
            )
            rating = _number(row.get("bayesian_rating"))
            previous_rating = _number(previous_week.get("bayesian_rating"))
            features.append(
                {
                    "snapshot_date": snapshot_day.isoformat(),
                    "manga_id": manga_id,
                    "title": row["title"],
                    "follows": follows,
                    "average_rating": row.get("average_rating"),
                    "bayesian_rating": row.get("bayesian_rating"),
                    "rating_votes": row["rating_votes"],
                    "replies_count": row["replies_count"],
                    "follows_delta_1d": follows - int(previous_day["follows"]) if previous_day else "",
                    "follows_delta_7d": follows_7d,
                    "follows_growth_7d": round(follows_7d / max(previous_follows, 1), 6),
                    "replies_delta_7d": int(row["replies_count"]) - int(previous_week["replies_count"]),
                    "rating_delta_7d": round(rating - previous_rating, 6) if rating is not None and previous_rating is not None else "",
                    "chapter_count_7d": chapter_count,
                    "days_since_update": days_since_update,
                    "release_interval_mean_30d": release_interval,
                    "target_growth_next_7d": round(
                        math.log1p(int(future_week["follows"])) - math.log1p(follows), 6
                    ),
                }
            )
    return features


def transform(output_root: Path, ingestion: dict, quality: dict) -> dict:
    manga_rows, chapters, rejected, report = validate_snapshot(
        ingestion, quality["max_invalid_ratio"]
    )
    _attach_latest_chapter(manga_rows, chapters, ingestion["fetched_at"])

    interim_path = output_root / "interim" / "manga_daily_snapshots.csv"
    chapter_path = output_root / "interim" / "chapter_history.csv"
    snapshots = _merge_snapshots(_read_csv(interim_path), manga_rows)
    chapter_history = _merge_chapters(_read_csv(chapter_path), chapters)
    _write_csv(interim_path, snapshots, SNAPSHOT_FIELDS)
    _write_csv(chapter_path, chapter_history, CHAPTER_FIELDS)

    processed_path = output_root / "processed" / "manga_trend_features.csv"
    features = _build_features(snapshots, chapter_history)
    _write_csv(processed_path, features, FEATURE_FIELDS)

    metadata_dir = output_root / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    quarantine_path = output_root / "quarantine" / f"rejected-{ingestion['snapshot_date']}.jsonl"
    quarantine_path.parent.mkdir(parents=True, exist_ok=True)
    quarantine_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rejected),
        encoding="utf-8",
    )
    (metadata_dir / "quality_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = {
        "schema_version": "2.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **ingestion,
        **report,
        "snapshot_rows": len(snapshots),
        "chapter_history_rows": len(chapter_history),
        "feature_rows": len(features),
        "interim_file": str(interim_path),
        "chapter_history_file": str(chapter_path),
        "processed_file": str(processed_path),
        "processed_sha256": hashlib.sha256(processed_path.read_bytes()).hexdigest(),
    }
    (metadata_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest
