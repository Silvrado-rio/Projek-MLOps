"""Check every running MLflow replica using its published HTTP endpoint."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import subprocess
import urllib.request


ROOT = Path(__file__).resolve().parents[1]


def compose(*args):
    return subprocess.check_output(['docker', 'compose', *args], cwd=ROOT, text=True)


def check_prediction(url, payload, expected):
    request = urllib.request.Request(url + '/invocations', data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    values = result['predictions']
    if len(values) != len(expected) or not all(
            math.isfinite(a) and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-10)
            for a, b in zip(values, expected)):
        raise AssertionError(f'Predictions differ from the original model: {result}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replicas', type=int, default=3)
    args = parser.parse_args()
    print('$ docker compose ps\n' + compose('ps'))
    ids = compose('ps', '-q', '--all', 'api-service').splitlines()
    if len(ids) != args.replicas:
        raise AssertionError(f'Expected {args.replicas} replicas; received {len(ids)}')
    containers = json.loads(subprocess.check_output(['docker', 'inspect', *ids], text=True))
    if not all(c['State']['Running'] and c['State']['Health']['Status'] == 'healthy' for c in containers):
        raise AssertionError('Not all replicas are running and healthy')
    payload = json.loads((ROOT / 'configs/mlflow-invocations.json').read_text())
    expected = json.loads((ROOT / 'models/serving/expected-predictions.json').read_text())['predictions']
    digest = json.loads((ROOT / 'models/serving/provenance.json').read_text())['model_sha256']
    requirements_digest = hashlib.sha256((ROOT / 'models/serving/model/requirements.txt').read_bytes()).hexdigest()
    tracking = 'http://' + compose('port', 'mlflow-server', '5000').strip()
    with urllib.request.urlopen(tracking + '/api/2.0/mlflow/registered-models/alias?name=manhwa-growth-ridge-compose&alias=production', timeout=10) as response:
        version = json.load(response)['model_version']
    tags = {tag['key']: tag['value'] for tag in version['tags']}
    if version['current_stage'] != 'Production' or tags.get('model_sha256') != digest or tags.get('requirements_sha256') != requirements_digest:
        raise AssertionError('Production registry version does not match the packaged model and requirements')
    print(f"Registry: {version['name']} version {version['version']} | stage Production")
    urls = []
    for index in range(1, args.replicas + 1):
        address = compose('port', '--index', str(index), 'api-service', '8080').strip()
        url = 'http://' + address
        with urllib.request.urlopen(url + '/ping', timeout=10) as response:
            assert response.status == 200
        result = check_prediction(url, payload, expected)
        check = "import hashlib,json; print(json.dumps({name:hashlib.sha256(open('/opt/ml/model/'+name,'rb').read()).hexdigest() for name in ['model.pkl','requirements.txt']}))"
        actual = compose('exec', '-T', '--index', str(index), 'api-service', 'python', '-c', check).strip()
        hashes = json.loads(actual)
        if hashes['model.pkl'] != digest or hashes['requirements.txt'] != requirements_digest:
            raise AssertionError(f'Replica {index} model or environment checksum mismatch')
        print(json.dumps({'replica': index, 'url': url, 'production_version': version['version'], 'model_sha256': hashes['model.pkl'], **result}))
        urls.append(url)
    # Functional concurrent clients, not a throughput benchmark.
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(check_prediction, url, payload, expected) for url in urls for _ in range(4)]
        for future in futures:
            future.result()
    print(f'PASS: {args.replicas} healthy replicas; {len(futures)} concurrent-scenario requests matched the reference')


if __name__ == '__main__':
    main()
