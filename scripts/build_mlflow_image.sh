#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up -d --build --wait --wait-timeout 180 mlflow-server

# Reuse the installed MLflow environment; Docker builds through the host socket.
plugin_mount=()
for directory in /usr/libexec/docker/cli-plugins /usr/lib/docker/cli-plugins /usr/local/lib/docker/cli-plugins; do
  if [ -x "$directory/docker-buildx" ]; then
    plugin_mount=(-v "$directory:/usr/local/lib/docker/cli-plugins:ro")
    break
  fi
done
docker compose run --rm --no-deps \
  -e MLFLOW_TRACKING_URI=http://mlflow-server:5000 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$(command -v docker):/usr/local/bin/docker:ro" \
  "${plugin_mount[@]}" --entrypoint mlflow mlflow-server \
  models build-docker \
  --model-uri models:/manhwa-growth-ridge-compose/Production \
  --name manhwa-growth-mlflow:lk10 --env-manager local
