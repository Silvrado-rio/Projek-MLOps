"""Compare Ridge regressors on one DVC revision using a purged temporal holdout."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import subprocess

import dvc.api
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mlflow
import mlflow.sklearn
from mlflow.models import infer_signature
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, ndcg_score, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parent
TARGET = "target_growth_next_7d"
HORIZON_DAYS = 7
FEATURES = [
    "follows", "bayesian_rating", "replies_count", "follows_delta_1d",
    "follows_delta_7d", "follows_growth_7d", "replies_delta_7d",
    "rating_delta_7d", "chapter_count_7d", "days_since_update",
    "release_interval_mean_30d",
]


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def load_dataset(revision: str) -> tuple[pd.DataFrame, dict]:
    """Read the selected DVC commit, independent of the working-tree data pointers."""
    revision = git("rev-parse", "--verify", f"{revision}^{{commit}}")

    def read(path: str) -> bytes:
        with dvc.api.open(path, repo=str(ROOT), rev=revision, mode="rb") as stream:
            return stream.read()

    payload = read("data/processed/manga_trend_features.csv")
    manifest = json.loads(read("data/metadata/manifest.json"))
    digest = hashlib.sha256(payload).hexdigest()
    if manifest.get("catalog_source") != "mangadex_api":
        raise ValueError("Training requires live MangaDex data, not offline fixtures.")
    if manifest.get("processed_sha256") != digest:
        raise ValueError("Processed CSV does not match the selected version's manifest.")
    frame = pd.read_csv(io.BytesIO(payload))
    required = ["snapshot_date", "manga_id", "title", TARGET, *FEATURES]
    if missing := set(required) - set(frame.columns):
        raise ValueError(f"Missing training columns: {sorted(missing)}")
    if frame.empty or len(frame) != manifest["feature_rows"]:
        raise ValueError("Processed dataset is empty or disagrees with its manifest.")
    frame["snapshot_date"] = pd.to_datetime(frame["snapshot_date"], format="%Y-%m-%d", errors="raise")
    if frame[["snapshot_date", "manga_id"]].isna().any().any() or frame.duplicated(["snapshot_date", "manga_id"]).any():
        raise ValueError("Snapshot dates and IDs must be populated and unique.")
    for column in [*FEATURES, TARGET]:
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype(float)
    if not np.isfinite(frame[TARGET]).all() or np.isinf(frame[FEATURES].to_numpy()).any():
        raise ValueError("Targets must be finite; features may be missing but not infinite.")
    label_dates = frame["snapshot_date"] + pd.Timedelta(days=HORIZON_DAYS)
    if (label_dates > pd.Timestamp(manifest["snapshot_date"])).any():
        raise ValueError("The dataset contains labels that have not matured yet.")
    previous_follows = frame["follows"] - frame["follows_delta_7d"]
    if (previous_follows < 0).any() or previous_follows.isna().any() or frame["follows"].isna().any():
        raise ValueError("Momentum requires valid current and previous-week follows.")
    frame["momentum"] = np.log1p(frame["follows"]) - np.log1p(previous_follows)
    metadata = {
        "data_revision": revision, "processed_sha256": digest,
        "processed_dvc_pointer": git("show", f"{revision}:data/processed.dvc"),
        "snapshot_date": manifest["snapshot_date"], "source": manifest["catalog_source"],
        "total_rows": len(frame), "unique_manga_ids": frame["manga_id"].nunique(),
    }
    return frame.sort_values(["snapshot_date", "manga_id"]).reset_index(drop=True), metadata


def temporal_split(frame: pd.DataFrame, validation_start: str | None = None):
    """Only train on outcomes already observed when validation prediction begins.

    A label ending on validation_start is usable after that day's snapshot arrives.
    Rows between the training boundary and validation are explicitly purged.
    """
    cutoff = pd.Timestamp(validation_start) if validation_start else frame["snapshot_date"].max()
    validation = frame.loc[frame["snapshot_date"] >= cutoff].copy()
    training = frame.loc[frame["snapshot_date"] + pd.Timedelta(days=HORIZON_DAYS) <= cutoff].copy()
    purged = len(frame) - len(training) - len(validation)
    if len(training) < 2 or len(validation) < 2 or training[TARGET].nunique() < 2:
        raise ValueError("Insufficient mature temporal data: collect more days or select a later validation start.")
    if validation.empty or training["snapshot_date"].max() >= validation["snapshot_date"].min():
        raise ValueError("Training and validation dates must not overlap.")
    return training, validation, purged


def scores(frame: pd.DataFrame, predictions: np.ndarray) -> dict[str, float]:
    """NDCG uses nonnegative log-growth relevance and is averaged over dates."""
    ranked = frame.assign(prediction=np.asarray(predictions))
    daily_scores = []
    for _, group in ranked.groupby("snapshot_date"):
        if len(group) >= 2 and group[TARGET].clip(lower=0).max() > 0:
            daily_scores.append(ndcg_score(
                [group[TARGET].clip(lower=0).to_numpy()],
                [group["prediction"].to_numpy()], k=10,
            ))
    result = {
        "mae": float(mean_absolute_error(frame[TARGET], predictions)),
        "rmse": float(math.sqrt(mean_squared_error(frame[TARGET], predictions))),
    }
    if frame[TARGET].nunique() > 1:
        result["r2"] = float(r2_score(frame[TARGET], predictions))
    if daily_scores:
        result["ndcg_at_10"] = float(np.mean(daily_scores))
    return result


def run_experiments(args: argparse.Namespace) -> dict:
    mlflow.set_tracking_uri(args.tracking_uri)
    mlflow.set_experiment(args.experiment)
    frame, provenance = load_dataset(args.data_rev)
    training, validation, purged = temporal_split(frame, args.validation_start)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    baseline = scores(validation, validation["momentum"].to_numpy())
    popularity = scores(validation, validation["follows"].to_numpy())
    split = {
        "train_rows": len(training), "validation_rows": len(validation), "purged_rows": purged,
        "train_start": str(training["snapshot_date"].min().date()),
        "train_end": str(training["snapshot_date"].max().date()),
        "validation_start": str(validation["snapshot_date"].min().date()),
        "validation_end": str(validation["snapshot_date"].max().date()),
        "train_labels_available_by": str((training["snapshot_date"].max() + pd.Timedelta(days=7)).date()),
        "train_all_missing_features": training[FEATURES].columns[training[FEATURES].isna().all()].tolist(),
        "missing_values": frame[FEATURES].isna().sum().astype(int).to_dict(),
        "independent_test_set": False,
    }
    common = {
        **provenance, **{k: v for k, v in split.items() if isinstance(v, (str, int, bool))},
        "algorithm": "Ridge", "horizon_days": HORIZON_DAYS, "solver": "svd",
        "imputation": "train_median_with_missing_indicators", "scaling": "train_standard_scaler",
        "selection_metric": "validation_mae", "feature_count": len(FEATURES),
        "code_revision": git("rev-parse", "HEAD"),
        "train_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    pointer = common.pop("processed_dvc_pointer")
    results = []
    for alpha in args.alphas:
        with mlflow.start_run(run_name=f"ridge-alpha-{alpha:g}") as run:
            for key, value in {**common, "alpha": alpha}.items():
                mlflow.log_param(key, value)
            mlflow.set_tags({"evaluation_status": "exploratory_temporal_holdout", "deployment_ready": "false"})
            model = make_pipeline(
                SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True),
                StandardScaler(), Ridge(alpha=alpha, solver="svd"),
            )
            model.fit(training[FEATURES], training[TARGET])
            predicted = model.predict(validation[FEATURES])
            metrics = scores(validation, predicted)
            for key, value in metrics.items():
                mlflow.log_metric(f"validation_{key}", value)
            for key, value in baseline.items():
                mlflow.log_metric(f"momentum_{key}", value)
            if "ndcg_at_10" in popularity:
                mlflow.log_metric("popularity_ndcg_at_10", popularity["ndcg_at_10"])
            improvement = 1 - metrics["mae"] / baseline["mae"] if baseline["mae"] > 0 else None
            if improvement is not None:
                mlflow.log_metric("mae_improvement_vs_momentum", improvement)
            model_info = mlflow.sklearn.log_model(
                model, name="model", signature=infer_signature(training[FEATURES], model.predict(training[FEATURES])),
                input_example=training[FEATURES].head(3),
            )
            predictions = validation[["snapshot_date", "manga_id", "title", TARGET, "momentum", "follows"]].copy()
            predictions["prediction"] = predicted
            prediction_path = args.output_dir / f"predictions-alpha-{alpha:g}.csv"
            predictions.to_csv(prediction_path, index=False)
            mlflow.log_artifact(str(prediction_path))
            mlflow.log_dict({**provenance, "split": split, "features": FEATURES}, "data_and_split.json")
            mlflow.log_text(pointer, "processed.dvc")
            mlflow.log_artifact(str(Path(__file__).resolve()), artifact_path="code")
            mlflow.log_artifact(str(ROOT / "requirements-train.txt"), artifact_path="code")
            results.append({"alpha": alpha, "run_id": run.info.run_id, "model_uri": model_info.model_uri, **metrics})
    best = min(results, key=lambda row: row["mae"])
    gate = (
        baseline["mae"] > 0 and best["mae"] <= 0.9 * baseline["mae"]
        and best.get("ndcg_at_10", -1) > baseline.get("ndcg_at_10", 1)
        and best.get("ndcg_at_10", -1) > popularity.get("ndcg_at_10", 1)
    )
    summary = {
        "experiment": args.experiment, "dataset": provenance, "split": split,
        "target": TARGET, "features": FEATURES, "runs": results,
        "momentum_baseline": baseline, "popularity_baseline": popularity,
        "best": best, "selection_metric": "validation_mae",
        "meets_initial_metric_gate": bool(gate), "deployment_ready": False,
        "limitation": "Small temporal validation set reused for parameter selection; no independent test set. Registry entry is an exploratory candidate.",
    }
    if args.register_best:
        version = mlflow.register_model(best["model_uri"], args.model_name)
        client = mlflow.MlflowClient()
        client.set_registered_model_alias(args.model_name, "candidate", version.version)
        client.set_model_version_tag(args.model_name, version.version, "deployment_ready", "false")
        client.set_model_version_tag(args.model_name, version.version, "data_revision", provenance["data_revision"])
        summary["registry"] = {"name": args.model_name, "version": version.version, "alias": "candidate"}
    comparison = pd.DataFrame(results)
    comparison.to_csv(args.output_dir / "comparison.csv", index=False)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), layout="constrained")
    labels = [f"alpha={item['alpha']:g}" for item in results]
    for axis, key in zip(axes, ["mae", "rmse"]):
        axis.bar(labels, comparison[key], color="#2563eb")
        axis.axhline(baseline[key], color="#dc2626", linestyle="--", label="Momentum baseline")
        axis.set(title=f"Validation {key.upper()}", ylabel=key.upper())
        axis.legend()
    chart = args.output_dir / "metric_comparison.png"
    fig.savefig(chart, dpi=180)
    plt.close(fig)
    result_path = args.output_dir / "best_model.json"
    result_path.write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    mlflow.MlflowClient().log_artifact(best["run_id"], str(chart))
    mlflow.MlflowClient().log_artifact(best["run_id"], str(result_path))
    print(json.dumps(summary, indent=2, allow_nan=False))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-rev", default="origin/data-snapshots", help="DVC Git revision; resolved once before all runs.")
    parser.add_argument("--alphas", nargs="+", type=float, default=[0.1, 1.0, 10.0])
    parser.add_argument("--validation-start", help="First validation date, YYYY-MM-DD; defaults to latest feature date.")
    parser.add_argument("--tracking-uri", default=os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000"))
    parser.add_argument("--experiment", default="manhwa-growth-regression")
    parser.add_argument("--output-dir", type=Path, default=Path("output/training"))
    parser.add_argument("--register-best", action="store_true", help="Register the lowest-validation-MAE Ridge as candidate.")
    parser.add_argument("--model-name", default="manhwa-growth-ridge")
    args = parser.parse_args()
    if len(set(args.alphas)) < 3 or any(not math.isfinite(a) or a <= 0 for a in args.alphas):
        parser.error("Supply at least three distinct, finite positive alpha values.")
    run_experiments(args)


if __name__ == "__main__":
    main()
