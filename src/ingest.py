"""Extract MangaDex metadata and create explicitly synthetic comment batches."""

from __future__ import annotations

import json
import random
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


COMMENTS = {
    "positive": [
        "Chapter ini seru banget, alurnya makin bagus.",
        "Art dan pertarungannya keren, tidak sabar lanjut.",
        "Perkembangan karakternya terasa memuaskan.",
        "Akhir chapter-nya bagus dan bikin penasaran.",
    ],
    "neutral": [
        "Chapter berikutnya rilis kapan?",
        "Bagian ini menjelaskan latar belakang karakter utama.",
        "Saya baru selesai membaca chapter ini.",
        "Alurnya mulai masuk ke konflik baru.",
    ],
    "negative": [
        "Chapter ini terasa terlalu lambat dan bertele-tele.",
        "Keputusan karakter utamanya tidak masuk akal.",
        "Art-nya menurun dibandingkan chapter sebelumnya.",
        "Akhir chapter ini mengecewakan.",
    ],
}

FALLBACK_CHAPTERS = [
    {"manga_id": "demo-001", "chapter_id": "demo-001-31", "chapter_number": "31"},
    {"manga_id": "demo-002", "chapter_id": "demo-002-18", "chapter_number": "18"},
    {"manga_id": "demo-003", "chapter_id": "demo-003-44", "chapter_number": "44"},
    {"manga_id": "demo-004", "chapter_id": "demo-004-12", "chapter_number": "12"},
    {"manga_id": "demo-005", "chapter_id": "demo-005-27", "chapter_number": "27"},
]


def _normalize_chapters(payload: dict) -> list[dict]:
    chapters = []
    for item in payload.get("data", []):
        manga = next(
            (relation for relation in item.get("relationships", []) if relation.get("type") == "manga"),
            {},
        )
        attributes = item.get("attributes", {})
        chapters.append(
            {
                "manga_id": manga.get("id", "unknown"),
                "chapter_id": item["id"],
                "chapter_number": attributes.get("chapter") or "N/A",
                "translated_language": attributes.get("translatedLanguage", "id"),
                "publish_at": attributes.get("publishAt"),
            }
        )
    return chapters


def fetch_chapters(settings: dict, offline: bool = False) -> tuple[list[dict], str, dict]:
    if offline:
        return FALLBACK_CHAPTERS, "fallback_catalog", {"data": FALLBACK_CHAPTERS}

    params = urllib.parse.urlencode(
        [
            ("limit", str(settings["limit"])),
            ("translatedLanguage[]", settings["translated_language"]),
            ("includes[]", "manga"),
            ("order[publishAt]", "desc"),
            ("contentRating[]", "safe"),
            ("contentRating[]", "suggestive"),
        ]
    )
    request = urllib.request.Request(
        f"{settings['endpoint']}?{params}",
        headers={"User-Agent": "UB-MLOps-Coursework/1.0"},
    )
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=settings["timeout_seconds"]) as response:
                payload = json.load(response)
            chapters = _normalize_chapters(payload)
            if chapters:
                return chapters, "mangadex_api", payload
        except (OSError, TimeoutError, ValueError, KeyError) as error:
            print(f"[WARN] Percobaan MangaDex {attempt}/3 gagal: {error}")
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))
    print("[WARN] Memakai katalog fallback setelah tiga percobaan.")
    return FALLBACK_CHAPTERS, "fallback_catalog", {"data": FALLBACK_CHAPTERS}


def _weights(batch_number: int, drift_starts_on_batch: int) -> list[float]:
    return [0.62, 0.25, 0.13] if batch_number < drift_starts_on_batch else [0.34, 0.25, 0.41]


def ingest(
    output_root: Path,
    mangadex: dict,
    simulation: dict,
    offline: bool = False,
) -> dict:
    now = datetime.now(timezone.utc)
    chapters, catalog_source, payload = fetch_chapters(mangadex, offline)

    metadata_dir = output_root / "raw" / "mangadex" / now.date().isoformat()
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (metadata_dir / "chapters.json").write_text(
        json.dumps(
            {"fetched_at": now.isoformat(), "source": catalog_source, "payload": payload},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    rng = random.Random(simulation["seed"])
    labels = list(COMMENTS)
    batches = simulation["batches"]
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=batches - 1)
    written_files = []

    for offset in range(batches):
        batch_number = offset + 1
        batch_time = start + timedelta(days=offset)
        batch_dir = output_root / "raw" / "comments" / batch_time.date().isoformat()
        batch_dir.mkdir(parents=True, exist_ok=True)
        batch_path = batch_dir / f"batch_{batch_number:03d}.jsonl"
        with batch_path.open("w", encoding="utf-8") as stream:
            for index in range(simulation["comments_per_batch"]):
                chapter = rng.choice(chapters)
                label = rng.choices(
                    labels,
                    weights=_weights(batch_number, simulation["drift_starts_on_batch"]),
                    k=1,
                )[0]
                seconds_available = 86_399
                if batch_time.date() == now.date():
                    seconds_available = max(0, int((now - batch_time).total_seconds()))
                event = {
                    "event_id": f"{batch_time:%Y%m%d}-{index:04d}",
                    "batch_id": batch_time.date().isoformat(),
                    "source": "synthetic_comment_simulation",
                    "synthetic": True,
                    "user_id": f"user_{rng.randint(1, 120):03d}",
                    **chapter,
                    "comment_text": rng.choice(COMMENTS[label]),
                    "sentiment_label": label,
                    "event_time": (batch_time + timedelta(seconds=rng.randint(0, seconds_available))).isoformat(),
                    "ingested_at": now.isoformat(),
                }
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        written_files.append(str(batch_path))

    return {
        "catalog_source": catalog_source,
        "metadata_file": str(metadata_dir / "chapters.json"),
        "comment_files": written_files,
        "total_events": batches * simulation["comments_per_batch"],
        "comments_are_synthetic": True,
    }
