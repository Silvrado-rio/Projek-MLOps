"""Normalize and validate MangaDex catalog, statistics, and chapter records."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def _payload(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))["payload"]


def _title(attributes: dict) -> str:
    titles = attributes.get("title") or {}
    return titles.get("en") or titles.get("ko-ro") or next(iter(titles.values()), "Tanpa judul")


def _rating_votes(distribution: dict | None) -> int | None:
    if distribution is None:
        return None
    return sum(int(value) for value in distribution.values())


def deduplicate_chapters(records: list[dict]) -> list[dict]:
    earliest: dict[tuple[str, str, str], dict] = {}
    for record in records:
        chapter_key = record["chapter_number"] or record["chapter_id"]
        key = (record["manga_id"], record["volume"] or "", chapter_key)
        current = earliest.get(key)
        if current is None or record["publish_at"] < current["publish_at"]:
            earliest[key] = record
    return sorted(earliest.values(), key=lambda row: (row["manga_id"], row["publish_at"], row["chapter_id"]))


def validate_snapshot(ingestion: dict, max_invalid_ratio: float) -> tuple[list[dict], list[dict], list[dict], dict]:
    catalog = _payload(ingestion["raw_files"]["catalog"])
    statistics = _payload(ingestion["raw_files"]["statistics"]).get("statistics", {})
    chapter_payload = _payload(ingestion["raw_files"]["chapters"])
    valid_manga = []
    rejected = []

    for item in catalog.get("data", []):
        try:
            attributes = item["attributes"]
            manga_id = item["id"]
            if attributes.get("originalLanguage") != "ko":
                raise ValueError("original_language bukan ko")
            stats = statistics.get(manga_id)
            if not stats:
                raise ValueError("statistik manga tidak tersedia")
            rating = stats.get("rating") or {}
            comments = stats.get("comments") or {}
            follows = int(stats.get("follows", 0))
            replies = int(comments.get("repliesCount", 0))
            if follows < 0 or replies < 0:
                raise ValueError("nilai hitungan negatif")
            valid_manga.append(
                {
                    "snapshot_date": ingestion["snapshot_date"],
                    "fetched_at": ingestion["fetched_at"],
                    "manga_id": manga_id,
                    "title": _title(attributes),
                    "original_language": "ko",
                    "status": attributes.get("status"),
                    "year": attributes.get("year"),
                    "tags": "|".join(
                        tag.get("attributes", {}).get("name", {}).get("en", "")
                        for tag in attributes.get("tags", [])
                    ),
                    "content_rating": attributes.get("contentRating"),
                    "follows": follows,
                    "average_rating": rating.get("average"),
                    "bayesian_rating": rating.get("bayesian"),
                    "rating_votes": _rating_votes(rating.get("distribution")),
                    "replies_count": replies,
                }
            )
        except (KeyError, TypeError, ValueError) as error:
            rejected.append({"kind": "manga", "id": item.get("id"), "reason": str(error)})

    chapters = []
    known_ids = {row["manga_id"] for row in valid_manga}
    for item in chapter_payload.get("data", []):
        try:
            attributes = item["attributes"]
            manga_id = next(
                relation["id"]
                for relation in item.get("relationships", [])
                if relation.get("type") == "manga"
            )
            if manga_id not in known_ids:
                continue
            publish_at = attributes["publishAt"]
            datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
            chapters.append(
                {
                    "manga_id": manga_id,
                    "chapter_id": item["id"],
                    "volume": attributes.get("volume"),
                    "chapter_number": attributes.get("chapter"),
                    "translated_language": attributes.get("translatedLanguage"),
                    "publish_at": publish_at,
                    "readable_at": attributes.get("readableAt"),
                    "pages": attributes.get("pages"),
                }
            )
        except (KeyError, StopIteration, TypeError, ValueError) as error:
            rejected.append({"kind": "chapter", "id": item.get("id"), "reason": str(error)})

    unique_chapters = deduplicate_chapters(chapters)
    input_records = len(catalog.get("data", [])) + len(chapter_payload.get("data", []))
    invalid_ratio = len(rejected) / input_records if input_records else 1.0
    report = {
        "input_records": input_records,
        "valid_manga": len(valid_manga),
        "valid_unique_chapters": len(unique_chapters),
        "rejected_records": len(rejected),
        "duplicate_chapters": len(chapters) - len(unique_chapters),
        "invalid_ratio": round(invalid_ratio, 4),
    }
    if not valid_manga or invalid_ratio > max_invalid_ratio:
        raise RuntimeError(f"Quality gate gagal: {report}")
    return valid_manga, unique_chapters, rejected, report
