#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/infra/observability/docker-compose.yml"

usage() {
  cat <<EOF
Usage: $(basename "$0") {up|down|restart|logs|ps|config}
EOF
}

cmd="${1:-}"

case "$cmd" in
  up)
    docker compose -f "$COMPOSE_FILE" up -d
    ;;
  down)
    docker compose -f "$COMPOSE_FILE" down -v
    ;;
  restart)
    docker compose -f "$COMPOSE_FILE" down -v
    docker compose -f "$COMPOSE_FILE" up -d
    ;;
  logs)
    docker compose -f "$COMPOSE_FILE" logs -f "${2:-}"
    ;;
  ps)
    docker compose -f "$COMPOSE_FILE" ps
    ;;
  config)
    docker compose -f "$COMPOSE_FILE" config
    ;;
  *)
    usage
    exit 1
    ;;
esac
