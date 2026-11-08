"""Apply LK-01 thresholds to an independent test, then conditionally stage a model."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

import mlflow
import pandas as pd

from train import HORIZON_DAYS, TARGET, scores


def evaluate(summary: dict, predictions: pd.DataFrame) -> dict:
    """Fail closed on missing metrics, leakage, or a failed LK-01 threshold."""
    split = summary["split"]
    if split.get("independent_test_set") is not True:
        raise ValueError("LK-08 requires a test set unused for hyperparameter selection.")
    train_end, val_start, val_end, test_start, test_end = (
        pd.Timestamp(split[key]) for key in ("train_end", "validation_start", "validation_end", "test_start", "test_end")
    )
    horizon = pd.Timedelta(days=HORIZON_DAYS)
    if not (train_end + horizon <= val_start <= val_end < test_start <= test_end):
        raise ValueError("Training, validation, and test dates overlap or training labels leak.")
    if val_end + horizon > test_start or test_end + horizon > pd.Timestamp(summary["dataset"]["snapshot_date"]):
        raise ValueError("Validation or test labels have not matured at the prediction boundary.")
    dates = pd.to_datetime(predictions["snapshot_date"], format="%Y-%m-%d", errors="raise")
    if (len(predictions) != split["test_rows"] or len(predictions) < 2
            or predictions[["snapshot_date", "manga_id"]].isna().any().any()
            or predictions.duplicated(["snapshot_date", "manga_id"]).any()
            or dates.min() != test_start or dates.max() != test_end):
        raise ValueError("Test prediction keys or dates disagree with the training summary.")
    predictions = predictions.assign(snapshot_date=dates)
    if not all(math.isfinite(float(v)) for k in [TARGET, "prediction", "momentum", "follows"] for v in predictions[k]):
        raise ValueError("Test labels, predictions, and baselines must be finite.")
    model = scores(predictions, predictions["prediction"].to_numpy())
    momentum = scores(predictions, predictions["momentum"].to_numpy())
    popularity = scores(predictions, predictions["follows"].to_numpy())
    has_ranking = all("ndcg_at_10" in item for item in (model, momentum, popularity))
    checks = {
        "mae_at_least_10_percent_better": momentum["mae"] > 0 and model["mae"] <= 0.9 * momentum["mae"],
        "ndcg_beats_momentum": has_ranking and model["ndcg_at_10"] > momentum["ndcg_at_10"],
        "ndcg_beats_popularity": has_ranking and model["ndcg_at_10"] > popularity["ndcg_at_10"],
    }
    return {
        "passed": all(checks.values()), "checks": checks,
        "thresholds": {"maximum_mae_ratio_to_momentum": 0.9, "ndcg_comparison": "strictly_greater_than_both_baselines"},
        "metrics": {"model": model, "momentum": momentum,
                    "popularity": {k: v for k, v in popularity.items() if k == "ndcg_at_10"}},
        "split": split, "dataset": summary["dataset"], "training_code": summary["training_code"],
        "selected_model": summary["best"], "deployment_ready": False,
    }


def stage(summary: dict, evaluation: dict, model_name: str, client) -> dict:
    if evaluation.get("passed") is not True or not all(evaluation["checks"].values()):
        raise ValueError("Model rejected by LK-01; Registry must not be updated.")
    version = mlflow.register_model(summary["best"]["model_uri"], model_name)
    client.transition_model_version_stage(model_name, version.version, "Staging", archive_existing_versions=True)
    if client.get_model_version(model_name, version.version).current_stage != "Staging":
        raise RuntimeError("MLflow did not confirm the Staging transition.")
    for key, value in {"lifecycle_stage": "Staging", "deployment_ready": "false", "lk01_gate": "passed",
                       "data_revision": summary["dataset"]["data_revision"],
                       "code_revision": summary["training_code"]["git_revision"]}.items():
        client.set_model_version_tag(model_name, version.version, key, value)
    client.set_registered_model_alias(model_name, "staging", version.version)
    return {"name": model_name, "version": str(version.version), "stage": "Staging",
            "model_uri": f"models:/{model_name}/{version.version}"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["evaluate", "register"])
    parser.add_argument("--output-dir", type=Path, default=Path("output/automation"))
    parser.add_argument("--tracking-uri", default=os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5001"))
    parser.add_argument("--model-name", default="manhwa-growth-ridge-automation")
    args = parser.parse_args()
    summary_path = args.output_dir / "best_model.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    result = evaluate(summary, pd.read_csv(args.output_dir / "test_predictions.csv"))
    result["summary_sha256"] = hashlib.sha256(summary_path.read_bytes()).hexdigest()
    evaluation_path = args.output_dir / "evaluation.json"
    mlflow.set_tracking_uri(args.tracking_uri)
    client = mlflow.MlflowClient()
    if args.command == "register":
        if json.loads(evaluation_path.read_text(encoding="utf-8")) != result:
            raise ValueError("Evaluation changed after validation; registration stopped.")
        result["registry"] = stage(summary, result, args.model_name, client)
        client.log_dict(summary["best"]["run_id"], result, "staging_registration.json")
        (args.output_dir / "registry.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    else:
        evaluation_path.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
        client.log_dict(summary["best"]["run_id"], result, "evaluation.json")
        client.set_tag(summary["best"]["run_id"], "lk01_gate", "passed" if result["passed"] else "rejected")
        if output := os.environ.get("GITHUB_OUTPUT"):
            with open(output, "a", encoding="utf-8") as stream:
                stream.write(f"passed={str(result['passed']).lower()}\n")
        status = "PASSED - eligible for Staging" if result["passed"] else "REJECTED - Staging blocked"
        lines = [f"## LK-08 evaluation: {status}", "", "| Check | Result |", "|---|---|"]
        lines.extend(f"| {key} | {'PASS' if value else 'FAIL'} |" for key, value in result["checks"].items())
        lines.extend(["", f"Data revision: `{result['dataset']['data_revision']}`",
                      f"Code revision: `{result['training_code']['git_revision']}`",
                      "", "A successful evaluation job means the checks completed; the verdict above controls registration."])
        if output := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(output, "a", encoding="utf-8") as stream:
                stream.write("\n".join(lines) + "\n")
        print(status)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
