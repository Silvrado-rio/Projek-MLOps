"""Import the existing LK-07 model into the isolated Compose registry once."""
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

import mlflow


BUNDLE = Path('/app/models/serving')
MODEL_NAME = 'manhwa-growth-ridge-compose'


def main():
    for attempt in range(90):
        try:
            with urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=2):
                break
        except (OSError, urllib.error.URLError):
            time.sleep(1)
    else:
        raise RuntimeError('MLflow did not become ready within 90 attempts')

    provenance = json.loads((BUNDLE / 'provenance.json').read_text())
    digest = hashlib.sha256((BUNDLE / 'model/model.pkl').read_bytes()).hexdigest()
    if digest != provenance['model_sha256']:
        raise ValueError('The bundled model checksum does not match its provenance')
    client = mlflow.MlflowClient()
    registered = client.search_registered_models(filter_string=f"name = '{MODEL_NAME}'")
    if registered and 'demo' in registered[0].aliases:
        existing = client.get_model_version_by_alias(MODEL_NAME, 'demo')
        if existing.tags.get('model_sha256') != digest:
            raise ValueError('Existing demo alias refers to a different model; review before replacing it')
        print(json.dumps({'event': 'model_reused', 'version': existing.version,
                          'model_sha256': digest}), flush=True)
        return

    mlflow.set_experiment('lk09-compose-import')
    with mlflow.start_run(run_name='import-lk07-version-2') as run:
        mlflow.set_tags({'simulation_only': 'true', 'deployment_ready': 'false'})
        mlflow.log_dict(provenance, 'provenance.json')
        # Upload the original model unchanged; no training or promotion occurs here.
        client.log_artifacts(run.info.run_id, str(BUNDLE / 'model'), artifact_path='model')
        version = mlflow.register_model(f'runs:/{run.info.run_id}/model', MODEL_NAME)
    for key in ['model_sha256', 'source_model_name', 'source_model_version', 'source_run_id']:
        client.set_model_version_tag(MODEL_NAME, version.version, key, str(provenance[key]))
    client.set_model_version_tag(MODEL_NAME, version.version, 'simulation_only', 'true')
    client.set_model_version_tag(MODEL_NAME, version.version, 'deployment_ready', 'false')
    client.set_registered_model_alias(MODEL_NAME, 'demo', version.version)
    print(json.dumps({'event': 'model_imported', 'version': version.version,
                      'model_sha256': digest}), flush=True)


if __name__ == '__main__':
    main()
