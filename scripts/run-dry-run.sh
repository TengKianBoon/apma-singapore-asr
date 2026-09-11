#!/usr/bin/env bash
set -euo pipefail

IMAGE_TAG="apma-v5:dev"

echo "Building Docker image: ${IMAGE_TAG}"
docker build -t "${IMAGE_TAG}" .

echo "Running Docker container (dry-run)..."
docker run --rm -e DRY_RUN=true -v "$(pwd)/jobs:/app/jobs" "${IMAGE_TAG}"

echo "Done."
