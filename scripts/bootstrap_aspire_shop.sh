#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${1:-$ROOT_DIR/.external/aspire-samples}"

if ! command -v git >/dev/null 2>&1; then
  echo "git is required." >&2
  exit 1
fi

if [ ! -d "$TARGET_DIR/.git" ]; then
  git clone --depth 1 https://github.com/dotnet/aspire-samples.git "$TARGET_DIR"
else
  echo "Aspire samples already present at $TARGET_DIR"
fi

SAMPLE_DIR="$TARGET_DIR/samples/aspire-shop"

cat <<EOF
Aspire Shop sample prepared at:
  $SAMPLE_DIR

Next steps:
  1. Start the local observability stack:
       cd "$ROOT_DIR"
       ./scripts/run_observability_stack.sh up

  2. Run Aspire Shop with OTLP pointed at the local collector:
       cd "$SAMPLE_DIR"
       export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
       export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
       aspire run

     Or, if you do not use the Aspire CLI:
       dotnet run --project AspireShop.AppHost

  3. Verify telemetry:
       curl -s http://localhost:9090/api/v1/targets | jq .
       curl -s -G http://localhost:3100/loki/api/v1/query --data-urlencode 'query={service_name="catalogservice"}'
       curl -s -G http://localhost:3200/api/search --data-urlencode 'q={ resource.service.name = "catalogservice" }'

See docs/aspire-shop-local-stack.md for the full walkthrough.
EOF
