"""Check the real packaged model and the HTTP input/output contract."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

try:
    import mlflow.pyfunc
    import numpy as np
    from pydantic import ValidationError
    from src import api
except ModuleNotFoundError:
    api = None


@unittest.skipIf(api is None, 'Install requirements-serving.txt for serving checks')
class ServingTest(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]

    def test_original_model_checksum_and_predictions(self):
        bundle = self.root / 'models/serving'
        provenance = json.loads((bundle / 'provenance.json').read_text())
        digest = hashlib.sha256((bundle / 'model/model.pkl').read_bytes()).hexdigest()
        self.assertEqual(digest, provenance['model_sha256'])
        request = api.PredictRequest.model_validate_json((self.root / 'configs/predict-example.json').read_text())
        model = mlflow.pyfunc.load_model(str(bundle / 'model'))
        with patch.object(api.app.state, 'model', model, create=True), patch.object(
                api.app.state, 'metadata', {'model_version': '1'}, create=True):
            result = api.predict(request)
        expected = json.loads((bundle / 'expected-predictions.json').read_text())
        np.testing.assert_allclose(result['predictions'], expected['predictions'], rtol=1e-10, atol=1e-12)

    def test_invalid_requests_are_rejected_and_missing_features_can_be_imputed(self):
        sample = json.loads((self.root / 'configs/predict-example.json').read_text())['instances'][0]
        for change in [{'follows': -1}, {'follows': float('inf')}, {'unknown': 1}]:
            with self.assertRaises(ValidationError):
                api.PredictRequest(instances=[{**sample, **change}])
        with self.assertRaises(ValidationError):
            api.PredictRequest(instances=[])
        request = api.PredictRequest(instances=[{**sample, 'days_since_update': None}])
        self.assertIsNone(request.instances[0].days_since_update)

    def test_nonfinite_model_output_is_not_returned_to_client(self):
        sample = json.loads((self.root / 'configs/predict-example.json').read_text())
        with patch.object(api.app.state, 'model', Mock(predict=Mock(return_value=[float('nan')]*3)), create=True):
            with self.assertRaises(api.HTTPException) as error:
                api.predict(api.PredictRequest.model_validate(sample))
        self.assertEqual(error.exception.status_code, 500)


if __name__ == '__main__':
    unittest.main()
