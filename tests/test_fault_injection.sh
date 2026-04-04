#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT_DIR/scripts/inject_aspire_fault.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

FAKE_DOCKER_LOG="$TMP_DIR/docker.log"
FAKE_DOCKER_NAMES="$TMP_DIR/names.txt"

cat >"$TMP_DIR/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

case "$1" in
  ps)
    shift
    if [ "${1:-}" = "-a" ] && [ "${2:-}" = "--format" ]; then
      cat "$FAKE_DOCKER_NAMES"
      exit 0
    fi
    if [ "${1:-}" = "-a" ] && [ "${2:-}" = "--filter" ]; then
      echo "status:${3:-}" >>"$FAKE_DOCKER_LOG"
      exit 0
    fi
    ;;
  stop|start|restart)
    echo "$1:$2" >>"$FAKE_DOCKER_LOG"
    exit 0
    ;;
esac

echo "unexpected fake docker invocation: $*" >&2
exit 1
EOF

chmod +x "$TMP_DIR/docker"

export IIRS_DOCKER_CMD="$TMP_DIR/docker"
export FAKE_DOCKER_LOG
export FAKE_DOCKER_NAMES

cat >"$FAKE_DOCKER_NAMES" <<'EOF'
aspire-postgres-123
aspire-basketcache-456
aspire-frontend-789
EOF

postgres_discovery="$("$SCRIPT" discover)"
[[ "$postgres_discovery" == *"candidate=aspire-postgres-123"* ]]
[[ "$postgres_discovery" == *"candidate=aspire-basketcache-456"* ]]

"$SCRIPT" stop postgres
"$SCRIPT" start redis
"$SCRIPT" status postgres

grep -q '^stop:aspire-postgres-123$' "$FAKE_DOCKER_LOG"
grep -q '^start:aspire-basketcache-456$' "$FAKE_DOCKER_LOG"
grep -q '^status:name=\^aspire-postgres-123\$$' "$FAKE_DOCKER_LOG"

cat >"$FAKE_DOCKER_NAMES" <<'EOF'
ambiguous-postgres-a
ambiguous-postgres-b
EOF

if "$SCRIPT" stop postgres >/dev/null 2>&1; then
  echo "expected ambiguous postgres resolution to fail" >&2
  exit 1
fi

export IIRS_ASPIRE_POSTGRES_CONTAINER="manual-postgres"
"$SCRIPT" restart postgres
grep -q '^restart:manual-postgres$' "$FAKE_DOCKER_LOG"
