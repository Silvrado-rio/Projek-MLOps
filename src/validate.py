"""Validate and clean raw synthetic comment events."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path


REQUIRED = {
    "event_id",
    "batch_id",
    "synthetic",
    "manga_id",
    "chapter_id",
    "comment_text",
    "sentiment_label",
    "event_time",
    "ingested_at",
}
ALLOWED_SENTIMENTS = {"positive", "neutral", "negative"}


def _clean_text(text: str) -> str:
    text = re.sub(r"https?://\S+", "<URL>", text)
    return " ".join(text.split())


def validate_and_clean(paths: list[str]) -> tuple[list[dict], list[dict], dict]:
    valid_by_id: dict[str, dict] = {}
    rejected = []
    duplicate_count = 0

    for path in paths:
        with Path(path).open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                try:
                    event = json.loads(line)
                    missing = REQUIRED - event.keys()
                    event_time = datetime.fromisoformat(event["event_time"])
                    ingested_at = datetime.fromisoformat(event["ingested_at"])
                    if missing:
                        raise ValueError(f"field hilang: {sorted(missing)}")
                    if event["synthetic"] is not True:
                        raise ValueError("synthetic harus true")
                    if event["sentiment_label"] not in ALLOWED_SENTIMENTS:
                        raise ValueError("sentiment tidak dikenal")
                    if not str(event["comment_text"]).strip():
                        raise ValueError("komentar kosong")
                    if event_time > ingested_at + timedelta(minutes=5):
                        raise ValueError("event_time melewati ingested_at")
                    event["clean_text"] = _clean_text(event["comment_text"])
                    if event["event_id"] in valid_by_id:
                        duplicate_count += 1
                    valid_by_id[event["event_id"]] = event
                except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                    rejected.append({"file": path, "line": line_number, "reason": str(error)})

    valid = sorted(valid_by_id.values(), key=lambda event: (event["batch_id"], event["event_id"]))
    report = {
        "input_records": len(valid) + len(rejected) + duplicate_count,
        "valid_records": len(valid),
        "rejected_records": len(rejected),
        "duplicate_records": duplicate_count,
    }
    return valid, rejected, report
