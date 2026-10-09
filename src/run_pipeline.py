"""Run the complete manhwa trend data pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .ingest import ingest
from .transform import transform


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pipeline tren aktivitas komunitas dan pembaruan manhwa MangaDex."
    )
    parser.add_argument("--config", type=Path, default=Path("configs/pipeline.json"))
    parser.add_argument("--output-root", type=Path, default=Path("data"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="Gunakan fixture lokal untuk pengujian.")
    mode.add_argument(
        "--require-live-api",
        action="store_true",
        help="Gunakan API live; pipeline tetap gagal bila API tidak dapat diakses.",
    )
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    cohort_path = args.output_root / "metadata" / "tracked_manga_ids.json"
    if not args.offline and cohort_path.exists():
        cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
        if len(cohort) != config["mangadex"]["catalog_limit"]:
            raise ValueError("Ukuran cohort tersimpan berbeda dari catalog_limit")
        config["mangadex"]["tracked_manga_ids"] = cohort
    ingestion = ingest(args.output_root, config["mangadex"], offline=args.offline)
    manifest = transform(args.output_root, ingestion, config["quality"])
    if not args.offline and config["mangadex"].get("tracked_manga_ids"):
        catalog = json.loads(Path(ingestion["raw_files"]["catalog"]).read_text(encoding="utf-8"))
        cohort_path.write_text(
            json.dumps([item["id"] for item in catalog["payload"]["data"]], indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
