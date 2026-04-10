#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT_PATH="${1:-$ROOT_DIR/submission_artifacts/iirs-capstone-submission-$STAMP.zip}"

mkdir -p "$(dirname "$OUTPUT_PATH")"

cd "$ROOT_DIR"

include_paths=(
  README.md
  pyproject.toml
  chainlit.md
  chainlit_app.py
  src
  tests
  fixtures
  runbooks
  scripts
  infra
  docs
  patches
  submission_artifacts
  .env.example
)

if [[ -d ".external/aspire-samples/samples/aspire-shop" ]]; then
  include_paths+=(".external/aspire-samples/samples/aspire-shop")
else
  echo "warning: .external/aspire-samples/samples/aspire-shop is missing; the zip will not contain the live demo sample" >&2
fi

zip -rq "$OUTPUT_PATH" "${include_paths[@]}" \
  -x '*.pyc' \
  -x '*/__pycache__/*' \
  -x '.venv/*' \
  -x '.git/*' \
  -x '*/.git/*' \
  -x '.mypy_cache/*' \
  -x '.pytest_cache/*' \
  -x '.ruff_cache/*' \
  -x '.playwright/*' \
  -x '.playwright-cli/*' \
  -x '.claude/*' \
  -x '.codex/*' \
  -x '*/bin/*' \
  -x '*/obj/*' \
  -x '*/node_modules/*' \
  -x 'submission_artifacts/*.zip' \
  -x 'traces/*'

echo "Created submission zip: $OUTPUT_PATH"
