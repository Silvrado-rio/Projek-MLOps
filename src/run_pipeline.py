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
    ingestion = ingest(args.output_root, config["mangadex"], offline=args.offline)
    manifest = transform(args.output_root, ingestion, config["quality"])
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
