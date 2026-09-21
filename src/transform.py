"""Transform cleaned comments into chapter-level sentiment features."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from .validate import validate_and_clean


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def transform(output_root: Path, ingestion: dict) -> dict:
    clean, rejected, report = validate_and_clean(ingestion["comment_files"])
    if not clean:
        raise RuntimeError("Tidak ada record valid; periksa quality report dan data raw.")
    interim_path = output_root / "interim" / "comments_clean.csv"
    clean_fields = sorted({key for event in clean for key in event})
    _write_csv(interim_path, clean, clean_fields)

    quarantine_path = output_root / "quarantine" / "rejected.jsonl"
    quarantine_path.parent.mkdir(parents=True, exist_ok=True)
    quarantine_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rejected),
        encoding="utf-8",
    )

    grouped: dict[tuple[str, str, str], dict[str, int]] = defaultdict(
        lambda: {"positive": 0, "neutral": 0, "negative": 0}
    )
    for event in clean:
        grouped[(event["manga_id"], event["chapter_id"], event["batch_id"])][
            event["sentiment_label"]
        ] += 1

    latest_batch = max(date.fromisoformat(key[2]) for key in grouped)
    previous_score: dict[tuple[str, str], float] = {}
    features = []
    for (manga_id, chapter_id, batch_id), counts in sorted(grouped.items(), key=lambda item: item[0][2]):
        total = sum(counts.values())
        score = (counts["positive"] - counts["negative"]) / total
        chapter_key = (manga_id, chapter_id)
        old_score = previous_score.get(chapter_key, score)
        previous_score[chapter_key] = score
        age_days = (latest_batch - date.fromisoformat(batch_id)).days
        features.append(
            {
                "manga_id": manga_id,
                "chapter_id": chapter_id,
                "batch_id": batch_id,
                "comment_count": total,
                "positive_ratio": round(counts["positive"] / total, 4),
                "neutral_ratio": round(counts["neutral"] / total, 4),
                "negative_ratio": round(counts["negative"] / total, 4),
                "sentiment_score": round(score, 4),
                "trend_delta": round(score - old_score, 4),
                "recency_weight": round(1 / (1 + age_days), 4),
            }
        )

    processed_path = output_root / "processed" / "chapter_sentiment_features.csv"
    feature_fields = list(features[0])
    _write_csv(processed_path, features, feature_fields)

    metadata_dir = output_root / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (metadata_dir / "quality_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    checksum = hashlib.sha256(processed_path.read_bytes()).hexdigest()
    manifest = {
        **ingestion,
        **report,
        "interim_file": str(interim_path),
        "processed_file": str(processed_path),
        "processed_sha256": checksum,
        "feature_rows": len(features),
    }
    (metadata_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest
