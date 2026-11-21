#!/bin/sh
set -eu
mlflow server \
  --host 0.0.0.0 --port 5000 --workers 1 \
  --backend-store-uri sqlite:////mlflow/mlflow.db \
  --serve-artifacts --artifacts-destination /mlflow/artifacts \
  --allowed-hosts 'mlflow-server:5000,localhost:5000,127.0.0.1:5000,localhost:5001,127.0.0.1:5001' &
server_pid=$!
trap 'kill -TERM "$server_pid" 2>/dev/null || true; wait "$server_pid" || true' TERM INT
if ! python -m src.bootstrap_model; then
  kill -TERM "$server_pid" 2>/dev/null || true
  exit 1
fi
wait "$server_pid"
