#!/bin/bash
# Build the offline laptop images for Intel/AMD and Apple Silicon and push them to GitHub Container Registry.
# Usage: deploy/offline/build_push.sh [tag]     (default tag: offline; the git commit is pushed as a tag too)
# Needs: docker buildx, and `gh auth token` (or GHCR_TOKEN) with write:packages for the AtelicAI org.
# Laptops pull with a read-only token (read:packages); see deploy/offline/README.md.
set -euo pipefail
cd "$(dirname "$0")/../.."
REGISTRY=${REGISTRY:-ghcr.io/atelicai}
TAG=${1:-offline}
SHA=$(git rev-parse --short HEAD)
PLATFORMS=${PLATFORMS:-linux/amd64,linux/arm64}

if [[ -n "$(git status --porcelain -- engine backend frontend data deploy/offline)" ]]; then
  echo "Uncommitted changes in the app; commit them first so the image matches a commit." >&2
  exit 1
fi
# Every agent step the offline story uses must be recorded, or it fails on the laptop.
for f in INC-001.investigate INC-001.plan INC-003.investigate INC-003.plan INC-001.verify; do
  [[ -f engine/data/recorded_runs/$f.json ]] || { echo "Missing recording engine/data/recorded_runs/$f.json" >&2; exit 1; }
done
python3 scripts/cache_basemap.py

# Docker Desktop's proxy sometimes times out on ghcr.io: try the login a few times.
for attempt in 1 2 3 4 5; do
  echo "${GHCR_TOKEN:-$(gh auth token)}" | docker login ghcr.io -u "${GHCR_USER:-$(gh api user -q .login)}" --password-stdin && break
  [[ $attempt == 5 ]] && exit 1; sleep 5
done
docker buildx inspect atelic-multi >/dev/null 2>&1 || docker buildx create --name atelic-multi --driver docker-container >/dev/null
build() {  # name, context, dockerfile
  docker buildx build --builder atelic-multi --platform "$PLATFORMS" --push \
    --label org.opencontainers.image.revision="$SHA" \
    -t "$REGISTRY/dell-demo-$1:$TAG" -t "$REGISTRY/dell-demo-$1:$SHA" -f "$3" "$2"
}
build engine . deploy/offline/engine.Dockerfile
build backend . deploy/offline/backend.Dockerfile
build web frontend frontend/Dockerfile
echo "Pushed $REGISTRY/dell-demo-{engine,backend,web}:$TAG ($SHA)"
