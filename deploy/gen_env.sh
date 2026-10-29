#!/bin/bash
# Generate a fresh .env with random secrets. Usage: deploy/gen_env.sh <out-file> <site-address> <demo-password>
# NVIDIA_API_KEY is read from the environment (never echoed).
set -euo pipefail
out=$1; site=$2; pass=$3
r() { openssl rand -hex 24; }
hash=$(docker run --rm caddy:2.8.4-alpine caddy hash-password --plaintext "$pass" | sed 's/\$/$$/g')  # $ escaped for compose
umask 077
cat > "$out" <<ENV
NEO4J_PASSWORD=$(r)
INFLUX_PASSWORD=$(r)
INFLUX_TOKEN=$(r)
POSTGRES_PASSWORD=$(r)
WORKFLOW_RUNTIME_PASSWORD=$(r)
QDRANT_API_KEY=$(r)
ENGINEER_TOKEN=$(openssl rand -hex 32)
ENGINEER_ID=demo-engineer
NVIDIA_API_KEY=${NVIDIA_API_KEY:-}
LLM_MODEL=nvidia/nemotron-3-ultra-550b-a55b
SITE_ADDRESS=$site
HTTP_PORT=8080
BASIC_AUTH_USER=atelic
BASIC_AUTH_HASH=$hash
DEPLOYMENT_LABEL=AWS EC2 eu-west-1 (Docker Compose)
ENV
echo "Wrote $out"
