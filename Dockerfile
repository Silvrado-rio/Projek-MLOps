FROM python:3.12-slim AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MLFLOW_ENABLE_ARTIFACTS_PROGRESS_BAR=false
COPY requirements-serving.txt .
RUN pip install -r requirements-serving.txt
COPY models/serving/model/requirements.txt /app/requirements-model.txt
RUN pip install -r /app/requirements-model.txt
COPY src/__init__.py src/__init__.py

FROM runtime AS tracking
COPY src/bootstrap_model.py src/bootstrap_model.py
COPY docker/start-tracking.sh /app/start-tracking.sh
COPY models/serving /app/models/serving
RUN chmod +x /app/start-tracking.sh
CMD ["/bin/sh", "/app/start-tracking.sh"]

FROM runtime AS api
COPY src/api.py src/api.py
CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
