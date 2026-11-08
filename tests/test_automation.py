"""Regression checks for temporal isolation and the conditional Staging boundary."""
from copy import deepcopy
from types import SimpleNamespace
import tempfile
import subprocess
import sys
import json
import unittest
from unittest.mock import Mock, patch

from tests.test_training import train
if train is not None:
    from src import automation


@unittest.skipIf(train is None, "Install requirements-ci.txt for automation checks")
class AutomationTest(unittest.TestCase):
    def inputs(self):
        summary = {
            "split": {"independent_test_set": True, "train_end": "2026-10-18",
                      "validation_start": "2026-10-25", "validation_end": "2026-10-25",
                      "test_start": "2026-11-01", "test_end": "2026-11-01", "test_rows": 3},
            "dataset": {"snapshot_date": "2026-11-08", "data_revision": "abc"},
            "training_code": {"git_revision": "def"},
            "best": {"model_uri": "models:/selected", "run_id": "selected"},
        }
        predictions = train.pd.DataFrame({
            "snapshot_date": ["2026-11-01"] * 3, "manga_id": ["a", "b", "c"],
            train.TARGET: [0.1, 0.2, 0.3], "prediction": [0.1, 0.2, 0.3],
            "momentum": [0.3, 0.2, 0.1], "follows": [30, 20, 10],
        })
        return summary, predictions

    def test_three_way_split_purges_future_labels(self):
        frame = train.pd.DataFrame({
            "snapshot_date": train.pd.to_datetime(["2026-10-18"] * 2 + ["2026-10-19"] * 2 + ["2026-10-25"] * 2 + ["2026-10-26"] * 2 + ["2026-11-01"] * 2),
            train.TARGET: [0.1, 0.2] * 5,
        })
        training, validation, test, purged = train.automation_split(frame)
        self.assertEqual((len(training), len(validation), len(test), purged), (2, 2, 2, 4))
        self.assertEqual(validation.snapshot_date.max() + train.pd.Timedelta(days=7), test.snapshot_date.min())

    def test_gate_accepts_only_if_all_three_lk01_checks_pass(self):
        summary, predictions = self.inputs()
        result = automation.evaluate(summary, predictions)
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["checks"]), 3)
        for column in ("momentum", "follows"):
            changed = predictions.copy()
            changed[column] = changed[train.TARGET]
            self.assertFalse(automation.evaluate(summary, changed)["passed"])
        predictions[train.TARGET] = 0.0
        self.assertFalse(automation.evaluate(summary, predictions)["passed"])

    def test_evaluation_rejects_leakage_stale_labels_and_bad_predictions(self):
        summary, predictions = self.inputs()
        for key, value in [("independent_test_set", False), ("train_end", "2026-10-19"),
                           ("validation_end", "2026-10-26"), ("test_rows", 4)]:
            changed = deepcopy(summary)
            changed["split"][key] = value
            with self.assertRaises(ValueError):
                automation.evaluate(changed, predictions)
        changed = deepcopy(summary)
        changed["dataset"]["snapshot_date"] = "2026-11-07"
        with self.assertRaises(ValueError):
            automation.evaluate(changed, predictions)
        for value in (float("nan"), float("inf")):
            changed_predictions = predictions.copy()
            changed_predictions.loc[0, "prediction"] = value
            with self.assertRaises(ValueError):
                automation.evaluate(summary, changed_predictions)
        with self.assertRaises(ValueError):
            automation.evaluate(summary, predictions.iloc[[0, 0, 1]])

    @patch("src.automation.mlflow.register_model")
    def test_registry_is_untouched_on_rejection_and_confirms_staging_on_acceptance(self, register):
        summary, predictions = self.inputs()
        result = automation.evaluate(summary, predictions)
        client = Mock()
        with self.assertRaises(ValueError):
            automation.stage(summary, {**result, "passed": False}, "example", client)
        register.assert_not_called()
        client.transition_model_version_stage.assert_not_called()
        register.return_value = SimpleNamespace(version="3")
        client.get_model_version.return_value = SimpleNamespace(current_stage="Staging")
        record = automation.stage(summary, result, "example", client)
        self.assertEqual(record["stage"], "Staging")
        client.transition_model_version_stage.assert_called_once_with("example", "3", "Staging", archive_existing_versions=True)
        client.get_model_version.return_value.current_stage = "None"
        with self.assertRaises(RuntimeError):
            automation.stage(summary, result, "example", client)

    def test_real_mlflow_registry_persists_staging_and_loads_model(self):
        # Synthetic fixture tests the accepted path; it is not project performance evidence.
        summary, predictions = self.inputs()
        evaluation = automation.evaluate(summary, predictions)
        script = """
import json, sys
from pathlib import Path
import mlflow, mlflow.sklearn
import numpy as np
from sklearn.linear_model import Ridge
from src.automation import stage
root = Path(sys.argv[1])
summary, evaluation = json.loads(sys.stdin.read())
mlflow.set_tracking_uri(f"sqlite:///{(root / 'mlflow.db').as_posix()}")
experiment = mlflow.create_experiment("lk08-integration", artifact_location=(root / "artifacts").as_uri())
model = Ridge(alpha=1).fit([[0.0], [1.0], [2.0]], [0.1, 0.2, 0.3])
with mlflow.start_run(experiment_id=experiment):
    info = mlflow.sklearn.log_model(model, name="model", input_example=np.array([[1.0]]))
summary["best"]["model_uri"] = info.model_uri
record = stage(summary, evaluation, "lk08-integration", mlflow.MlflowClient())
assert mlflow.MlflowClient().get_model_version(record["name"], record["version"]).current_stage == "Staging"
restored = mlflow.sklearn.load_model(f"models:/{record['name']}/Staging")
np.testing.assert_allclose(restored.predict([[1.0]]), model.predict([[1.0]]))
"""
        # Process exit releases SQLite handles before temporary cleanup on Windows.
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-c", script, directory],
                                    input=json.dumps([summary, evaluation]), text=True,
                                    encoding="utf-8", capture_output=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
