#!/usr/bin/env bash
set -euo pipefail

DOCKER_CMD="${IIRS_DOCKER_CMD:-docker}"

usage() {
  cat <<EOF
Usage:
  $(basename "$0") discover
  $(basename "$0") status <postgres|redis>
  $(basename "$0") stop <postgres|redis>
  $(basename "$0") start <postgres|redis>
  $(basename "$0") restart <postgres|redis>

Environment overrides:
  IIRS_ASPIRE_POSTGRES_CONTAINER   Explicit PostgreSQL container name
  IIRS_ASPIRE_REDIS_CONTAINER      Explicit Redis container name
  IIRS_DOCKER_CMD                  Override docker binary for testing
EOF
}

filter_matches() {
  local pattern="$1"
  if command -v rg >/dev/null 2>&1; then
    rg -i "$pattern" || true
  else
    grep -Ei "$pattern" || true
  fi
}

list_container_names() {
  "$DOCKER_CMD" ps -a --format '{{.Names}}'
}

canonical_resource() {
  case "${1:-}" in
    postgres|db|catalogdb)
      echo "postgres"
      ;;
    redis|basketcache|cache)
      echo "redis"
      ;;
    *)
      echo "unknown"
      ;;
  esac
}

resource_pattern() {
  case "$1" in
    postgres)
      echo 'postgres|catalogdb'
      ;;
    redis)
      echo 'basketcache|redis'
      ;;
    *)
      return 1
      ;;
  esac
}

resource_override() {
  case "$1" in
    postgres)
      echo "${IIRS_ASPIRE_POSTGRES_CONTAINER:-}"
      ;;
    redis)
      echo "${IIRS_ASPIRE_REDIS_CONTAINER:-}"
      ;;
    *)
      return 1
      ;;
  esac
}

resolve_candidates() {
  local resource="$1"
  local pattern
  pattern="$(resource_pattern "$resource")"
  list_container_names | filter_matches "$pattern"
}

resolve_container() {
  local resource="$1"
  local override
  override="$(resource_override "$resource")"
  if [ -n "$override" ]; then
    echo "$override"
    return 0
  fi

  mapfile -t candidates < <(resolve_candidates "$resource")

  if [ "${#candidates[@]}" -eq 1 ]; then
    echo "${candidates[0]}"
    return 0
  fi

  if [ "${#candidates[@]}" -eq 0 ]; then
    echo "No container match found for resource '$resource'." >&2
  else
    echo "Multiple container matches found for resource '$resource':" >&2
    printf '  %s\n' "${candidates[@]}" >&2
  fi

  echo "Set $( [ "$resource" = "postgres" ] && echo IIRS_ASPIRE_POSTGRES_CONTAINER || echo IIRS_ASPIRE_REDIS_CONTAINER ) to disambiguate." >&2
  return 1
}

print_discovery() {
  local resource="$1"
  local override
  override="$(resource_override "$resource")"
  echo "$resource:"
  if [ -n "$override" ]; then
    echo "  override=$override"
  fi
  mapfile -t candidates < <(resolve_candidates "$resource")
  if [ "${#candidates[@]}" -eq 0 ]; then
    echo "  candidates=(none)"
    return 0
  fi
  printf '  candidate=%s\n' "${candidates[@]}"
}

run_action() {
  local verb="$1"
  local resource="$2"
  local container
  container="$(resolve_container "$resource")"

  case "$verb" in
    stop|start|restart)
      "$DOCKER_CMD" "$verb" "$container"
      ;;
    status)
      "$DOCKER_CMD" ps -a --filter "name=^${container}$"
      ;;
    *)
      echo "Unsupported action: $verb" >&2
      return 1
      ;;
  esac
}

main() {
  local cmd="${1:-}"
  local raw_resource="${2:-}"
  local resource

  case "$cmd" in
    discover)
      print_discovery postgres
      print_discovery redis
      ;;
    stop|start|restart|status)
      resource="$(canonical_resource "$raw_resource")"
      if [ "$resource" = "unknown" ]; then
        usage >&2
        exit 1
      fi
      run_action "$cmd" "$resource"
      ;;
    *)
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
