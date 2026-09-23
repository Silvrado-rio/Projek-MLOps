"""Fetch live Korean-origin manga metadata from MangaDex."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


USER_AGENT = "UB-MLOps-Coursework/2.0"

OFFLINE_PAYLOADS = {
    "catalog": {
        "data": [
            {
                "id": "demo-manga-1",
                "attributes": {
                    "title": {"en": "Demo Manhwa"},
                    "originalLanguage": "ko",
                    "status": "ongoing",
                    "year": 2024,
                    "tags": [{"attributes": {"name": {"en": "Action"}}}],
                    "contentRating": "safe",
                },
            }
        ]
    },
    "statistics": {
        "statistics": {
            "demo-manga-1": {
                "rating": {"average": 8.2, "bayesian": 7.8, "distribution": {"8": 10, "9": 5}},
                "follows": 120,
                "comments": {"repliesCount": 7},
            }
        }
    },
    "chapters": {
        "data": [
            {
                "id": "demo-chapter-en",
                "attributes": {
                    "volume": "1",
                    "chapter": "10",
                    "translatedLanguage": "en",
                    "publishAt": "2024-01-01T00:00:00+00:00",
                    "readableAt": "2024-01-01T00:00:00+00:00",
                    "pages": 20,
                },
                "relationships": [{"id": "demo-manga-1", "type": "manga"}],
            },
            {
                "id": "demo-chapter-id",
                "attributes": {
                    "volume": "1",
                    "chapter": "10",
                    "translatedLanguage": "id",
                    "publishAt": "2024-01-02T00:00:00+00:00",
                    "readableAt": "2024-01-02T00:00:00+00:00",
                    "pages": 20,
                },
                "relationships": [{"id": "demo-manga-1", "type": "manga"}],
            },
        ]
    },
}


def _request_json(url: str, params: list[tuple[str, str]], timeout: int) -> dict:
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": USER_AGENT},
    )
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            if payload.get("result") not in (None, "ok"):
                raise ValueError(f"respons API tidak sukses: {payload.get('result')}")
            return payload
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace").strip()
            message = f"HTTP {error.code} dari {url}: {details or error.reason}"
            if error.code != 429:
                raise RuntimeError(message) from error
            last_error = RuntimeError(message)
            print(f"[WARN] Percobaan MangaDex {attempt}/3 gagal: {message}")
            if attempt < 3:
                retry_after = error.headers.get("Retry-After")
                time.sleep(int(retry_after) if retry_after and retry_after.isdigit() else 2 ** (attempt - 1))
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            last_error = error
            print(f"[WARN] Percobaan MangaDex {attempt}/3 gagal: {error}")
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))
    raise RuntimeError("MangaDex API tidak dapat diakses setelah tiga percobaan") from last_error


def fetch_mangadex(settings: dict, offline: bool = False) -> dict[str, dict]:
    if offline:
        return OFFLINE_PAYLOADS

    base_url = settings["base_url"].rstrip("/")
    timeout = settings["timeout_seconds"]
    catalog = _request_json(
        f"{base_url}/manga",
        [
            ("limit", str(settings["catalog_limit"])),
            ("originalLanguage[]", "ko"),
            ("order[updatedAt]", "desc"),
            ("contentRating[]", "safe"),
            ("contentRating[]", "suggestive"),
        ],
        timeout,
    )
    manga_ids = [item["id"] for item in catalog.get("data", [])]
    if not manga_ids:
        raise RuntimeError("Katalog manhwa MangaDex kosong")

    statistics = _request_json(
        f"{base_url}/statistics/manga",
        [("manga[]", manga_id) for manga_id in manga_ids],
        timeout,
    )
    chapter_params = [
        ("limit", str(settings["chapter_limit"])),
        ("order[publishAt]", "desc"),
        ("includes", "manga"),
        ("contentRating[]", "safe"),
        ("contentRating[]", "suggestive"),
    ]
    chapter_pages = []
    chapter_data = []
    matched_total = 0
    for index, manga_id in enumerate(manga_ids):
        page = _request_json(
            f"{base_url}/chapter",
            chapter_params + [("manga", manga_id)],
            timeout,
        )
        chapter_pages.append(page)
        chapter_data.extend(page.get("data", []))
        matched_total += int(page.get("total", 0))
        if index < len(manga_ids) - 1:
            time.sleep(0.25)
    chapters = {
        "result": "ok",
        "data": chapter_data,
        "total": len(chapter_data),
        "matched_total": matched_total,
        "pages": chapter_pages,
    }
    return {"catalog": catalog, "statistics": statistics, "chapters": chapters}


def ingest(output_root: Path, settings: dict, offline: bool = False) -> dict:
    fetched_at = datetime.now(timezone.utc)
    payloads = fetch_mangadex(settings, offline)
    source = "offline_fixture" if offline else "mangadex_api"
    paths = {}
    filenames = {"catalog": "catalog.json", "statistics": "statistics.json", "chapters": "chapters.json"}

    for name, payload in payloads.items():
        raw_dir = output_root / "raw" / name / fetched_at.date().isoformat()
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = raw_dir / filenames[name]
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"fetched_at": fetched_at.isoformat(), "source": source, "payload": payload},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
        paths[name] = str(path)

    return {
        "catalog_source": source,
        "snapshot_date": fetched_at.date().isoformat(),
        "fetched_at": fetched_at.isoformat(),
        "raw_files": paths,
    }
