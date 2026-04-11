# Submission Runbook

This document is the reproducible path for:

1. local setup
2. mock validation
3. live PostgreSQL and Redis fault E2E validation
4. optional agent runs over the live fault window
5. building the final submission zip

The safest default is to submit a zip that works without an OpenAI key. The OpenAI-backed path is optional.

## What The Agent Actually Does

There are two separate flows in this repo:

- `iirs verify-live`: checks whether Loki, Prometheus, and Tempo expose the expected live fault signals. This validates telemetry collection and live query correctness. It does not use the LLM reasoning path.
- `iirs run`: runs the `Retriever -> Analyst -> Critic -> Planner` pipeline. This is the actual incident-analysis flow. The Retriever is deterministic. Analyst, Critic, Planner, and follow-up answers can use OpenAI when enabled.

Recommended order:

1. reproduce the fault
2. run `verify-live`
3. run `iirs run` against that same live time window if you want the agent output

## Prerequisites

- Python 3.12+
- `git`
- Docker and Docker Compose
- .NET 10 SDK
- `grpcurl`
- optional: OpenAI API key in `.env.local`

## Important: Aspire Shop Local Patch

The live Redis E2E depends on local Aspire Shop changes that are not tracked in the top-level git repo because `.external/` is ignored.

`./scripts/bootstrap_aspire_shop.sh` now applies that patch automatically for the default `.external/aspire-samples` checkout.

If you fetched Aspire Shop some other way, or you already have an older unpatched checkout, do this:

```bash
git -C .external/aspire-samples apply "$PWD/patches/aspire-shop-local-e2e.patch"
```

That patch does two important things:

- forwards Aspire Shop telemetry to the local OTLP collector
- forces local basket service calls to use the plaintext Redis endpoint so `redis_down` is a real induced fault instead of a broken baseline

If you build the final zip from your working tree, include the patched `.external/aspire-samples/samples/aspire-shop/` tree so the live demo sample is present without dragging the entire upstream repository into the submission.

The fastest packaging path after generating the proof artifacts is:

```bash
./scripts/build_submission_zip.sh
```

## One-Time Setup

```bash
cd /home/gaurav/code-ubuntu/personal/capstone

python3 -m venv .venv
source .venv/bin/activate
pip install -e .

mkdir -p submission_artifacts

export IIRS_USE_OPENAI_AGENTS=false
```

If Aspire Shop is not present:

```bash
./scripts/bootstrap_aspire_shop.sh
```

## Save Basic Proof

These commands produce simple artifacts that are useful to include in the zip.

```bash
./.venv/bin/python -m unittest discover -s tests | tee submission_artifacts/unittest.txt
./.venv/bin/iirs eval --runs 2 | tee submission_artifacts/eval.txt
./.venv/bin/iirs run --scenario postgres_down --show-trace | tee submission_artifacts/mock_postgres.txt
./.venv/bin/iirs run --scenario redis_down --show-trace | tee submission_artifacts/mock_redis.txt
```

The evaluation output now includes both quantitative accuracy and a qualitative review score for evidence grounding, critic caution, and action-plan traceability.

If the live rehearsal reproduces the fault but one telemetry check stays flaky, the best fallback is to show the saved live rehearsal output and then switch immediately to:

```bash
IIRS_USE_OPENAI_AGENTS=false ./.venv/bin/iirs run --scenario postgres_down --show-trace
```

That preserves the multi-agent demo even when the live environment is being annoying.

If OpenAI-backed runs time out while waiting for model output, that is usually a model latency issue rather than a bad key. Raise the timeout before the demo if needed:

```bash
export IIRS_OPENAI_TIMEOUT_SECONDS=90
```

Model-enabled runs now fail cleanly on timeout or invalid structured output instead of silently falling back to deterministic answers.

## Start The Live Stack

Use two terminals.

### Terminal 1

```bash
cd /home/gaurav/code-ubuntu/personal/capstone

docker compose -f infra/observability/docker-compose.yml up -d

dotnet build .external/aspire-samples/samples/aspire-shop/AspireShop.AppHost/AspireShop.AppHost.csproj --no-restore

export SSL_CERT_DIR="$HOME/.aspnet/dev-certs/trust:/usr/lib/ssl/certs"
export IIRS_OTLP_ENDPOINT=http://127.0.0.1:4317
export IIRS_OTLP_PROTOCOL=grpc

dotnet run --project .external/aspire-samples/samples/aspire-shop/AspireShop.AppHost/AspireShop.AppHost.csproj --no-build
```

Leave this terminal running.

### Terminal 2

```bash
cd /home/gaurav/code-ubuntu/personal/capstone
source .venv/bin/activate

export IIRS_TELEMETRY_BACKEND=plt
export IIRS_PROMETHEUS_URL=http://127.0.0.1:9090
export IIRS_LOKI_URL=http://127.0.0.1:3100
export IIRS_TEMPO_URL=http://127.0.0.1:3200
```

## Live PostgreSQL E2E

### 1. Generate Baseline Catalog Traffic

```bash
curl -k 'https://127.0.0.1:7241/api/v1/catalog/items/type/all?pageSize=4'
```

### 2. Inject The Fault

```bash
started_at_pg=$(date -u +%Y-%m-%dT%H:%M:%SZ)
./scripts/inject_aspire_fault.sh stop postgres
echo "$started_at_pg" | tee submission_artifacts/postgres_started_at.txt
```

### 3. Generate Failing Traffic

```bash
for i in $(seq 1 4); do
  curl --max-time 15 -k -s -o /dev/null -w '%{http_code}\n' \
    'https://127.0.0.1:7241/api/v1/catalog/items/type/all?pageSize=4'
done | tee submission_artifacts/postgres_fault_traffic.txt
```

### 4. Verify Live Signals

```bash
./.venv/bin/iirs verify-live --scenario postgres_down --started-at "$started_at_pg" --window-minutes 10 \
  | tee submission_artifacts/verify_live_postgres.txt
```

If the live validator misses a check immediately after traffic generation, wait 15-20 seconds and rerun the same command with the same `started_at_pg`.

### 5. Optional: Run The Agent On The Live Postgres Window

```bash
cat > submission_artifacts/live_postgres_alert.json <<EOF
{
  "incident_id": "postgres-live-demo",
  "summary": "catalogservice is timing out because PostgreSQL is unavailable.",
  "severity": "critical",
  "service": "catalogservice",
  "environment": "local-dev",
  "started_at": "$started_at_pg",
  "window_minutes": 10,
  "scenario": "postgres_down",
  "labels": { "source": "live-e2e", "scenario": "postgres_down" }
}
EOF

./.venv/bin/iirs run --alert-file submission_artifacts/live_postgres_alert.json --show-trace \
  | tee submission_artifacts/agent_live_postgres.txt
```

### 6. Recover PostgreSQL

```bash
./scripts/inject_aspire_fault.sh start postgres
```

## Live Redis E2E

### 1. Identify The Real Redis Container

`discover` also sees Redis Commander, so disambiguate the actual cache container:

```bash
export IIRS_ASPIRE_REDIS_CONTAINER=$(docker ps --format '{{.Names}}' | rg '^basketcache-' -m1)
echo "$IIRS_ASPIRE_REDIS_CONTAINER" | tee submission_artifacts/redis_container.txt
```

### 2. Baseline Smoke Check

```bash
grpcurl -plaintext \
  -d '{"id":"smoke-user"}' \
  -import-path .external/aspire-samples/samples/aspire-shop/AspireShop.BasketService/Protos \
  -proto basket.proto \
  localhost:5309 \
  BasketApi.Basket/GetBasketById | tee submission_artifacts/redis_baseline_smoke.txt
```

### 3. Inject The Fault

```bash
started_at_redis=$(date -u +%Y-%m-%dT%H:%M:%SZ)
./scripts/inject_aspire_fault.sh stop redis
echo "$started_at_redis" | tee submission_artifacts/redis_started_at.txt
```

### 4. Generate Failing Basket Traffic

```bash
proto_dir=.external/aspire-samples/samples/aspire-shop/AspireShop.BasketService/Protos
update_payload='{"buyerId":"fault-user","items":[{"id":"item-1","productId":1,"unitPrice":{"units":"12","nanos":0},"oldUnitPrice":{"units":"15","nanos":0},"quantity":1}]}'

for method in GetBasketById DeleteBasket UpdateBasket CheckoutBasket GetBasketById DeleteBasket UpdateBasket CheckoutBasket; do
  case "$method" in
    GetBasketById) payload='{"id":"fault-user"}' ;;
    DeleteBasket|CheckoutBasket) payload='{"buyerId":"fault-user"}' ;;
    UpdateBasket) payload="$update_payload" ;;
  esac

  timeout 20s grpcurl -plaintext \
    -d "$payload" \
    -import-path "$proto_dir" \
    -proto basket.proto \
    localhost:5309 \
    "BasketApi.Basket/$method" || true

  sleep 1
done | tee submission_artifacts/redis_fault_traffic.txt
```

### 5. Verify Live Signals

```bash
./.venv/bin/iirs verify-live --scenario redis_down --started-at "$started_at_redis" --window-minutes 10 \
  | tee submission_artifacts/verify_live_redis.txt
```

### 6. Optional: Run The Agent On The Live Redis Window

```bash
cat > submission_artifacts/live_redis_alert.json <<EOF
{
  "incident_id": "redis-live-demo",
  "summary": "basketservice is timing out because Redis is unavailable.",
  "severity": "critical",
  "service": "basketservice",
  "environment": "local-dev",
  "started_at": "$started_at_redis",
  "window_minutes": 10,
  "scenario": "redis_down",
  "labels": { "source": "live-e2e", "scenario": "redis_down" }
}
EOF

./.venv/bin/iirs run --alert-file submission_artifacts/live_redis_alert.json --show-trace \
  | tee submission_artifacts/agent_live_redis.txt
```

### 7. Recover Redis

```bash
./scripts/inject_aspire_fault.sh start redis

grpcurl -plaintext \
  -d '{"id":"fault-user"}' \
  -import-path "$proto_dir" \
  -proto basket.proto \
  localhost:5309 \
  BasketApi.Basket/GetBasketById | tee submission_artifacts/redis_recovery_smoke.txt
```

## Build The Final Zip

The final zip should include:

- this repo
- `submission_artifacts/`
- `traces/`
- `.external/aspire-samples/`

Do not include:

- `.git/`
- `.venv/`
- `.env`
- `.env.local`
- API keys
- cache directories

From the parent directory:

```bash
cd /home/gaurav/code-ubuntu/personal

zip -r capstone_submission.zip capstone \
  -x 'capstone/.git/*' \
     'capstone/.venv/*' \
     'capstone/.env' \
     'capstone/.env.local' \
     'capstone/.env.*.local' \
     'capstone/.pytest_cache/*' \
     'capstone/__pycache__/*' \
     'capstone/.codex/*' \
     'capstone/.claude/*' \
     'capstone/.playwright/*' \
     'capstone/.playwright-cli/*' \
     'capstone/.external/aspire-samples/.git/*' \
     '*/bin/*' \
     '*/obj/*'
```

## Final Sanity Check

Before submitting:

1. run `unzip -l /home/gaurav/code-ubuntu/personal/capstone_submission.zip | less`
2. confirm the zip contains:
   - `capstone/src/`
   - `capstone/tests/`
   - `capstone/traces/`
   - `capstone/submission_artifacts/`
   - `capstone/.external/aspire-samples/samples/aspire-shop/AspireShop.AppHost/AppHost.cs`
3. confirm the zip does not contain:
   - `.venv`
   - `.git`
   - `.env.local`
   - any API key

## OpenAI Notes

The default submission path should not depend on OpenAI.

If you want model-backed Analyst and Critic behavior locally:

```bash
cat > .env.local <<EOF
OPENAI_API_KEY=sk-...
IIRS_AGENT_MODEL=gpt-5-mini
IIRS_OPENAI_REASONING_EFFORT=low
EOF
```

Then rerun `iirs run --alert-file ...`.

`verify-live` does not use the OpenAI reasoning path.
