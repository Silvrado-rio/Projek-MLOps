"""HTTP inference using a model downloaded from the Compose MLflow service."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI, HTTPException
import mlflow
import mlflow.pyfunc
import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field


FEATURES = [
    'follows', 'bayesian_rating', 'replies_count', 'follows_delta_1d',
    'follows_delta_7d', 'follows_growth_7d', 'replies_delta_7d',
    'rating_delta_7d', 'chapter_count_7d', 'days_since_update',
    'release_interval_mean_30d',
]


class FeatureRow(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    follows: float = Field(ge=0)
    bayesian_rating: float = Field(ge=0, le=10)
    replies_count: float = Field(ge=0)
    follows_delta_1d: float | None = None
    follows_delta_7d: float
    follows_growth_7d: float
    replies_delta_7d: float
    rating_delta_7d: float
    chapter_count_7d: float = Field(ge=0)
    days_since_update: float | None = Field(default=None, ge=0)
    release_interval_mean_30d: float | None = Field(default=None, ge=0)


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    instances: list[FeatureRow] = Field(min_length=1, max_length=100)


@asynccontextmanager
async def lifespan(app):
    tracking = os.environ['MLFLOW_TRACKING_URI']
    name = os.environ.get('MODEL_NAME', 'manhwa-growth-ridge-compose')
    alias = os.environ.get('MODEL_ALIAS', 'demo')
    expected_hash = os.environ['MODEL_SHA256']
    mlflow.set_tracking_uri(tracking)
    version = mlflow.MlflowClient().get_model_version_by_alias(name, alias)
    if version.tags.get('model_sha256') != expected_hash:
        raise ValueError('MLflow model checksum tag does not match the pinned model')
    model_uri = f'models:/{name}/{version.version}'
    with TemporaryDirectory(prefix='manhwa-model-') as directory:
        local_path = Path(mlflow.artifacts.download_artifacts(artifact_uri=model_uri, dst_path=directory))
        digest = hashlib.sha256((local_path / 'model.pkl').read_bytes()).hexdigest()
        if digest != expected_hash:
            raise ValueError('Downloaded model checksum does not match the pinned model')
        app.state.model = mlflow.pyfunc.load_model(str(local_path))
        app.state.metadata = {
            'tracking_uri': tracking, 'model_uri': model_uri, 'model_alias': alias,
            'model_version': str(version.version), 'artifact_uri': version.source,
            'model_sha256': digest, 'loaded_at': datetime.now(timezone.utc).isoformat(),
            'simulation_only': True, 'deployment_ready': False,
        }
        logging.getLogger('uvicorn.error').info('model_loaded_from_mlflow %s', json.dumps(app.state.metadata))
        yield


app = FastAPI(title='Manhwa Growth API - LK09', lifespan=lifespan)


@app.get('/health')
def health():
    return {'status': 'ok', **app.state.metadata}


@app.post('/predict')
def predict(request: PredictRequest):
    frame = pd.DataFrame([row.model_dump() for row in request.instances], columns=FEATURES).astype(float)
    predictions = np.asarray(app.state.model.predict(frame), dtype=float)
    if predictions.shape != (len(frame),) or not np.isfinite(predictions).all():
        raise HTTPException(status_code=500, detail='Model returned invalid predictions')
    return {'predictions': predictions.tolist(), 'target': 'target_growth_next_7d',
            'model_version': app.state.metadata['model_version'], 'simulation_only': True}
