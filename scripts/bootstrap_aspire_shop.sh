#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${1:-$ROOT_DIR/.external/aspire-samples}"
PATCH_FILE="$ROOT_DIR/patches/aspire-shop-local-e2e.patch"

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

if [ ! -f "$PATCH_FILE" ]; then
  echo "Required patch is missing: $PATCH_FILE" >&2
  exit 1
fi

if git -C "$TARGET_DIR" apply --check "$PATCH_FILE" >/dev/null 2>&1; then
  git -C "$TARGET_DIR" apply "$PATCH_FILE"
  PATCH_STATUS="applied"
elif git -C "$TARGET_DIR" apply --reverse --check "$PATCH_FILE" >/dev/null 2>&1; then
  PATCH_STATUS="already applied"
else
  echo "Aspire Shop patch could not be applied cleanly." >&2
  echo "The upstream sample may have changed; inspect $PATCH_FILE against $SAMPLE_DIR." >&2
  exit 1
fi

cat <<EOF
Aspire Shop sample prepared at:
  $SAMPLE_DIR

Local patch status:
  $PATCH_STATUS ($PATCH_FILE)

Next steps:
  1. Start the local observability stack:
       cd "$ROOT_DIR"
       ./scripts/run_observability_stack.sh up

  2. Run Aspire Shop with OTLP pointed at the local collector:
       cd "$SAMPLE_DIR"
       export IIRS_OTLP_ENDPOINT=http://127.0.0.1:4317
       export IIRS_OTLP_PROTOCOL=grpc
       aspire run

     Or, if you do not use the Aspire CLI:
       dotnet run --project AspireShop.AppHost

  3. Verify telemetry:
       curl -s http://localhost:9090/api/v1/targets | jq .
       curl -s -G http://localhost:3100/loki/api/v1/query --data-urlencode 'query={service_name="catalogservice"}'
       curl -s -G http://localhost:3200/api/search --data-urlencode 'q={ resource.service.name = "catalogservice" }'

See README.md for the full local stack walkthrough.
EOF
