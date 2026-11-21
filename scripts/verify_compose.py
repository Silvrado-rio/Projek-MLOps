"""Verify real service discovery, remote model loading and inference in Compose."""
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def compose(*args, input=None):
    return subprocess.check_output(['docker', 'compose', '-f', 'docker-compose.lk09.yaml', *args], cwd=ROOT,
                                   input=input, text=True)


def main():
    print('$ docker compose -f docker-compose.lk09.yaml ps')
    print(compose('ps'))
    request = json.loads((ROOT / 'configs/predict-example.json').read_text())
    code = '''
import json,socket,urllib.request,urllib.error
print('mlflow-server resolves to:',socket.gethostbyname('mlflow-server'))
with urllib.request.urlopen('http://mlflow-server:5000/health',timeout=10) as r:
    print('MLflow health:',r.status,r.read().decode())
with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=10) as r:
    health=json.load(r)
print('Remote model loaded:',json.dumps(health,sort_keys=True))
assert health['tracking_uri']=='http://mlflow-server:5000'
request=urllib.request.Request('http://127.0.0.1:8000/predict',
    data=PAYLOAD.encode(),headers={'Content-Type':'application/json'})
with urllib.request.urlopen(request,timeout=20) as r:
    prediction=json.load(r)
print('Prediction:',json.dumps(prediction,sort_keys=True))
assert len(prediction['predictions'])==len(EXPECTED)
assert all(abs(a-b)<1e-10 for a,b in zip(prediction['predictions'],EXPECTED))
request=urllib.request.Request('http://127.0.0.1:8000/predict',
    data=b'{"instances": []}',headers={'Content-Type':'application/json'})
try:
    urllib.request.urlopen(request,timeout=10)
except urllib.error.HTTPError as e:
    assert e.code==422
    print('Invalid input rejected:',e.code)
else:
    raise AssertionError('Invalid request was accepted')
print('PASS: service discovery, MLflow connection, model checksum and predictions')
'''
    expected = json.loads((ROOT / 'models/serving/expected-predictions.json').read_text())['predictions']
    program = f'PAYLOAD={json.dumps(request)!r}\nEXPECTED={expected!r}\n' + code
    print('$ docker compose -f docker-compose.lk09.yaml exec -T api-service python -')
    print(compose('exec', '-T', 'api-service', 'python', '-', input=program))
    print('API mounts:', compose('exec', '-T', 'api-service', 'sh', '-c',
          'test ! -d /app/models/serving && echo "No bundled model or shared MLflow volume in API container"'))


if __name__ == '__main__':
    main()
