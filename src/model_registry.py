"""Version, stage, and verify regression models in a simulated serving registry."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

import mlflow
import mlflow.pyfunc
import mlflow.sklearn
from mlflow.models import infer_signature
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
import yaml

from train import FEATURES, ROOT, TARGET, git, load_dataset, scores, temporal_split

RECORD = ROOT / "models" / "registry" / "active_model.yaml"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save(record: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(yaml.safe_dump(record, sort_keys=False, allow_unicode=True), encoding="utf-8")
    temporary.replace(path)


def advance_stage(record: dict, stage: str, client) -> dict:
    name, version = record["model_name"], record["selected_version"]
    previous = client.get_model_version(name, version).current_stage
    if (previous, stage) not in {("None", "Staging"), ("Staging", "Production")}:
        raise ValueError(f"Expected None -> Staging -> Production; received {previous} -> {stage}")
    if record["stage"] != previous:
        raise ValueError("Metadata and Registry disagree; reconcile them before promotion")
    client.transition_model_version_stage(name, version, stage, archive_existing_versions=True)
    if client.get_model_version(name, version).current_stage != stage:
        raise RuntimeError("Registry did not confirm the requested stage")
    client.set_model_version_tag(name, version, "lifecycle_stage", stage)
    if stage == "Production":
        client.set_registered_model_alias(name, "production", version)
    record["stage"] = stage
    record["active_version"] = version if stage == "Production" else None
    record["transition_history"].append({"from": previous, "to": stage, "version": version, "at": now()})
    return record


def production_version(record: dict, client) -> str:
    if record["stage"] != "Production":
        raise ValueError("Inference requires metadata in Production stage")
    production = [v for v in client.search_model_versions(f"name='{record['model_name']}'")
                  if v.current_stage == "Production"]
    if len(production) != 1 or str(production[0].version) != str(record["active_version"]):
        raise ValueError("Expected exactly the Production version recorded by DVC metadata")
    if str(record["active_version"]) != str(record["selected_version"]):
        raise ValueError("Active version must be the selected comparison winner")
    return str(production[0].version)


def register(args, client) -> dict:
    if args.record.exists() or client.search_model_versions(f"name='{args.model_name}'"):
        raise ValueError("Use a new model name and metadata path to preserve an existing lifecycle")
    frame, provenance = load_dataset(args.data_rev)
    training, validation, purged = temporal_split(frame, args.validation_start)
    old_name, old_version = args.baseline_uri.removeprefix("models:/").rsplit("/", 1)
    previous = client.get_model_version(old_name, old_version)
    baseline_run = client.get_run(previous.run_id)
    old_model = mlflow.sklearn.load_model(args.baseline_uri)
    new_model = make_pipeline(
        SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True),
        StandardScaler(), Ridge(alpha=args.alpha, solver="svd"),
    )
    new_model.fit(training[FEATURES], training[TARGET])
    mlflow.set_experiment(args.experiment)
    versions = []
    for label, model in [("previous-best", old_model), ("updated", new_model)]:
        with mlflow.start_run(run_name=label) as run:
            predicted = model.predict(validation[FEATURES])
            metrics = scores(validation, predicted)
            alpha = float(model.named_steps["ridge"].alpha)
            training_revision = baseline_run.data.params["data_revision"] if label == "previous-best" else provenance["data_revision"]
            training_sha = baseline_run.data.params["processed_sha256"] if label == "previous-best" else provenance["processed_sha256"]
            for key, value in {"alpha": alpha, "training_data_revision": training_revision,
                               "evaluation_data_revision": provenance["data_revision"],
                               "evaluation_sha256": provenance["processed_sha256"],
                               "validation_rows": len(validation), "validation_start": str(validation.snapshot_date.min().date()),
                               "code_revision": git("rev-parse", "HEAD")}.items():
                mlflow.log_param(key, value)
            for key, value in metrics.items():
                mlflow.log_metric(f"validation_{key}", value)
            mlflow.set_tags({"simulation_only": "true", "deployment_ready": "false"})
            mlflow.log_dict({"features": FEATURES, "predictions": predicted.tolist(),
                             "keys": validation[["snapshot_date", "manga_id"]].astype(str).to_dict("records")},
                            "validation_predictions.json")
            if label == "previous-best":
                source = args.baseline_uri
                model_run_id = previous.run_id
            else:
                info = mlflow.sklearn.log_model(model, name="model", input_example=training[FEATURES].head(3),
                                               signature=infer_signature(training[FEATURES], model.predict(training[FEATURES])))
                source = info.model_uri
                model_run_id = run.info.run_id
            registered = mlflow.register_model(source, args.model_name)
            for key, value in {"simulation_only": "true", "deployment_ready": "false",
                               "lifecycle_stage": "None",
                               "data_revision": training_revision,
                               "evaluation_data_revision": provenance["data_revision"]}.items():
                client.set_model_version_tag(args.model_name, registered.version, key, value)
            versions.append({"version": str(registered.version), "role": label, "alpha": alpha,
                             "training_data_revision": training_revision, "training_processed_sha256": training_sha,
                             "training_code_revision": baseline_run.data.params["code_revision"] if label == "previous-best" else git("rev-parse", "HEAD"),
                             "source_uri": source, "model_run_id": model_run_id,
                             "evaluation_run_id": run.info.run_id, "metrics": metrics})
    selected = min(versions, key=lambda row: row["metrics"]["mae"])
    baseline = scores(validation, validation["momentum"].to_numpy())
    record = {
        "schema_version": 1, "created_at": now(), "model_name": args.model_name,
        "simulation_only": True, "deployment_ready": False,
        "selection_metric": "validation_mae", "selected_version": selected["version"],
        "active_version": None, "stage": "None",
        "production_uri": f"models:/{args.model_name}/Production",
        "versions": versions, "evaluation_data": provenance,
        "split": {"train_rows_updated_model": len(training), "validation_rows": len(validation), "purged_rows": purged,
                  "validation_start": str(validation.snapshot_date.min().date()),
                  "validation_end": str(validation.snapshot_date.max().date()), "independent_test_set": False},
        "momentum_baseline": baseline, "features": FEATURES, "target": TARGET,
        "code_revision": git("rev-parse", "HEAD"),
        "registry_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "transition_history": [],
        "limitation": "Validation selects the version; no independent test. Updated model changes both data and alpha. Production is an inference simulation.",
    }
    save(record, args.record)
    return record


def verify(args, record, client) -> dict:
    version = production_version(record, client)
    frame, provenance = load_dataset(record["evaluation_data"]["data_revision"])
    if provenance["processed_sha256"] != record["evaluation_data"]["processed_sha256"]:
        raise ValueError("Evaluation data checksum differs from model metadata")
    _, validation, _ = temporal_split(frame, record["split"]["validation_start"])
    inputs = validation[FEATURES].head(5)
    model = mlflow.pyfunc.load_model(record["production_uri"])
    predicted = np.asarray(model.predict(inputs), dtype=float)
    selected = next(v for v in record["versions"] if v["version"] == version)
    logged = json.loads(Path(client.download_artifacts(selected["evaluation_run_id"], "validation_predictions.json")).read_text(encoding="utf-8"))
    expected = np.asarray(logged["predictions"][:len(inputs)], dtype=float)
    if predicted.shape != expected.shape or not np.isfinite(predicted).all():
        raise ValueError("Inference must return one finite prediction per input row")
    np.testing.assert_allclose(predicted, expected, rtol=1e-10, atol=1e-12)
    result = {"verified_at": now(), "model_uri": record["production_uri"], "version": version,
              "samples": len(predicted), "predictions": predicted.tolist(), "matches_logged_predictions": True,
              "input_columns": FEATURES, "simulation_only": True}
    record["inference_verification"] = result
    save(record, args.record)
    client.log_dict(selected["evaluation_run_id"], result, "production_inference_verification.json")
    return result


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracking-uri", default=os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000"))
    parser.add_argument("--record", type=Path, default=RECORD)
    commands = parser.add_subparsers(dest="command", required=True)
    registration = commands.add_parser("register")
    registration.add_argument("--baseline-uri", default="models:/manhwa-growth-ridge/2")
    registration.add_argument("--model-name", default="manhwa-growth-ridge-serving")
    registration.add_argument("--data-rev", default="origin/data-snapshots")
    registration.add_argument("--validation-start")
    registration.add_argument("--alpha", type=float, default=20.0)
    registration.add_argument("--experiment", default="manhwa-model-lifecycle")
    promotion = commands.add_parser("promote")
    promotion.add_argument("--stage", choices=["Staging", "Production"], required=True)
    commands.add_parser("verify")
    args = parser.parse_args()
    if args.command == "register" and (not np.isfinite(args.alpha) or args.alpha <= 0):
        parser.error("Alpha must be finite and positive")
    mlflow.set_tracking_uri(args.tracking_uri)
    client = mlflow.MlflowClient()
    if args.command == "register":
        result = register(args, client)
    else:
        record = yaml.safe_load(args.record.read_text(encoding="utf-8"))
        if args.command == "promote":
            result = advance_stage(record, args.stage, client)
            save(result, args.record)
        else:
            result = verify(args, record, client)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
