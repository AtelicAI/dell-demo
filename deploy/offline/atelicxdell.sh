#!/bin/bash
# Atelic × Dell methane demo: offline laptop launcher (macOS / Linux). Windows: atelicxdell.ps1.
#   ./atelicxdell.sh            update if online, start, load the data once, open the browser
#   ./atelicxdell.sh stop       stop the demo (data kept)
#   ./atelicxdell.sh reset      wipe this demo's data and start fresh
#   ./atelicxdell.sh status     show the containers
#   ./atelicxdell.sh logs       follow the logs
# Needs Docker Desktop running and ghcr-token.txt (read-only registry token) beside this script.
set -uo pipefail
DEMO_NAME=atelic-dell
HTTP_PORT=8080
REGISTRY=ghcr.io/atelicai
TAG=offline
LOGIN_USER=atelic
LOGIN_PASSWORD=atelic-demo
HERE=$(cd "$(dirname "$0")" && pwd)
STATE="$HOME/.atelic/$DEMO_NAME"
ENGINE="$REGISTRY/dell-demo-engine:$TAG"
mkdir -p "$STATE"
dc() { docker compose --project-directory "$STATE" -f "$STATE/compose.yml" --env-file "$STATE/.env" "$@"; }
say() { printf '\n== %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

docker info >/dev/null 2>&1 || die "Docker is not running. Open Docker Desktop, wait for 'Engine running', then run this again."

if [[ ! -f "$STATE/.env" ]]; then
  r() { openssl rand -hex 24; }
  umask 077
  cat > "$STATE/.env" <<ENV
DEMO_NAME=$DEMO_NAME
HTTP_PORT=$HTTP_PORT
REGISTRY=$REGISTRY
TAG=$TAG
NEO4J_PASSWORD=$(r)
INFLUX_PASSWORD=$(r)
INFLUX_TOKEN=$(r)
POSTGRES_PASSWORD=$(r)
WORKFLOW_RUNTIME_PASSWORD=$(r)
QDRANT_API_KEY=$(r)
ENGINEER_TOKEN=$(openssl rand -hex 32)
SESSION_SECRET=$(openssl rand -hex 32)
LOGIN_USER=$LOGIN_USER
LOGIN_PASSWORD=$LOGIN_PASSWORD
ENV
  umask 022
fi

# Update from the registry when online; otherwise run what is already on this laptop.
update() {
  local token="$HERE/ghcr-token.txt"
  [[ -f "$token" ]] || { echo "No ghcr-token.txt beside the launcher: skipping the update check."; return 1; }
  tr -d '[:space:]' < "$token" | docker login ghcr.io -u atelic-demo --password-stdin >/dev/null 2>&1 \
    || { echo "Registry not reachable (offline?): using the version already on this laptop."; return 1; }
  docker pull -q "$ENGINE" >/dev/null || { echo "Update download failed: using the version already on this laptop."; return 1; }
  docker run --rm --entrypoint cat "$ENGINE" /srv/offline/compose.yml > "$STATE/compose.yml.new" && mv "$STATE/compose.yml.new" "$STATE/compose.yml"
  # A newer launcher takes effect on the next run.
  docker run --rm --entrypoint cat "$ENGINE" /srv/offline/atelicxdell.sh > "$STATE/launcher.new" \
    && ! cmp -s "$STATE/launcher.new" "$0" && cp "$STATE/launcher.new" "$0" && chmod +x "$0" && echo "Launcher updated (used from the next run)."
  rm -f "$STATE/launcher.new"
  dc pull -q
}

start() {
  say "Checking for updates"
  update || true
  [[ -f "$STATE/compose.yml" ]] || die "The demo has never been downloaded. Connect to the internet and run this again."
  say "Starting the demo (first start takes a few minutes)"
  dc up -d --remove-orphans || die "Could not start the containers (see the messages above)."
  local image; image=$(docker image inspect -f '{{.Id}}' "$ENGINE")
  if [[ "$(cat "$STATE/seeded" 2>/dev/null)" != "$image" ]]; then
    say "Loading the demo data (once per version, about 2 minutes)"
    dc --profile seed run --rm seed || die "Loading the data failed (see the messages above). Run this again to retry."
    dc restart engine backend >/dev/null
    echo "$image" > "$STATE/seeded"
  fi
  say "Waiting for the demo to answer"
  local url="http://localhost:$HTTP_PORT"
  for _ in $(seq 1 120); do
    [[ "$(curl -s -o /dev/null -w '%{http_code}' "$url/login.html")" == 200 ]] && break
    sleep 2
  done
  [[ "$(curl -s -o /dev/null -w '%{http_code}' "$url/login.html")" == 200 ]] || die "The demo did not come up. Try: $0 logs"
  say "Ready: $url   (sign in: $LOGIN_USER / $LOGIN_PASSWORD)"
  open "$url" 2>/dev/null || xdg-open "$url" 2>/dev/null || true
}

case "${1:-start}" in
  start) start ;;
  stop) dc down && echo "Stopped. Data is kept; run $0 to start again." ;;
  reset) dc down -v && rm -f "$STATE/seeded" && start ;;
  status) dc ps ;;
  logs) dc logs -f --tail 100 ;;
  *) die "Unknown command '$1'. Use: start, stop, reset, status or logs." ;;
esac
