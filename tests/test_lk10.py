"""Checks for simulation promotion and the native MLflow response contract."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.verify_lk10 import check_prediction

try:
    from src.bootstrap_model import ensure_production, MODEL_NAME
except ModuleNotFoundError:
    ensure_production = None


@unittest.skipIf(ensure_production is None, 'Install MLflow for registry checks')
class ProductionSimulationTest(unittest.TestCase):
    def test_native_scoring_server_can_initialize_with_pinned_dependencies(self):
        import mlflow.pyfunc
        from mlflow.pyfunc.scoring_server import init
        model = mlflow.pyfunc.load_model(str(Path(__file__).resolve().parents[1] / 'models/serving/model'))
        app = init(model)
        self.assertIn('/invocations', [route.path for route in app.routes])

    def test_promotes_in_order_and_reuses_existing_production(self):
        current = SimpleNamespace(current_stage='None')
        client = Mock()
        client.get_model_version.return_value = current
        client.transition_model_version_stage.side_effect = lambda name, version, stage, **kw: setattr(current, 'current_stage', stage)
        with redirect_stdout(io.StringIO()):
            ensure_production(client, '1')
            ensure_production(client, '1')
        stages = [call.args[2] for call in client.transition_model_version_stage.call_args_list]
        self.assertEqual(stages, ['Staging', 'Production'])
        client.set_registered_model_alias.assert_called_with(MODEL_NAME, 'production', '1')

    def test_unconfirmed_or_archived_stage_cannot_be_reported_as_production(self):
        client = Mock()
        client.get_model_version.return_value = SimpleNamespace(current_stage='Archived')
        with self.assertRaises(ValueError):
            ensure_production(client, '1')
        client.set_registered_model_alias.assert_not_called()
        client.get_model_version.return_value = SimpleNamespace(current_stage='None')
        with self.assertRaises(RuntimeError):
            ensure_production(client, '1')
        client.set_registered_model_alias.assert_not_called()


class MLflowResponseTest(unittest.TestCase):
    def response(self, values):
        return io.BytesIO(json.dumps({'predictions': values}).encode())

    def test_native_predictions_match_with_float_tolerance(self):
        with patch('urllib.request.urlopen', return_value=self.response([0.10000000001])):
            result = check_prediction('http://localhost:12345', {'dataframe_split': {}}, [0.1])
        self.assertEqual(len(result['predictions']), 1)

    def test_missing_wrong_and_nonfinite_predictions_fail(self):
        for values in [[], [0.2], [float('nan')], [float('inf')]]:
            with self.subTest(values=values), patch('urllib.request.urlopen', return_value=self.response(values)):
                with self.assertRaises(AssertionError):
                    check_prediction('http://localhost:12345', {'dataframe_split': {}}, [0.1])


if __name__ == '__main__':
    unittest.main()
