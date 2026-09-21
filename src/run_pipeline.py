"""Run the complete LK-03 data pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .ingest import ingest
from .transform import transform


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline data komentar chapter manhwa.")
    parser.add_argument("--config", type=Path, default=Path("configs/pipeline.json"))
    parser.add_argument("--output-root", type=Path, default=Path("data"))
    parser.add_argument("--batches", type=int)
    parser.add_argument("--comments-per-batch", type=int)
    parser.add_argument("--offline", action="store_true", help="Gunakan katalog fallback tanpa API.")
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    simulation = config["simulation"]
    if args.batches is not None:
        simulation["batches"] = args.batches
    if args.comments_per_batch is not None:
        simulation["comments_per_batch"] = args.comments_per_batch
    if simulation["batches"] < 1 or simulation["comments_per_batch"] < 1:
        parser.error("batches dan comments-per-batch harus lebih dari nol")

    ingestion = ingest(args.output_root, config["mangadex"], simulation, args.offline)
    manifest = transform(args.output_root, ingestion)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
