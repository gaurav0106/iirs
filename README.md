# IIRS

IIRS is a local incident-response assistant prototype for issue [#1](https://github.com/gaurav0106/iirs/issues/1). It runs a `Retriever -> Analyst -> Critic -> Planner` pipeline over mock or live telemetry and produces an Incident Brief with ranked root causes, evidence citations, and recommended actions.

## Current scope

Implemented:

- shared incident state and per-incident JSON traces
- LangGraph-first linear pipeline with a local fallback runner
- CLI and Chainlit entrypoints
- mock fault scenarios for `postgres_down` and `redis_down`
- live Prometheus, Loki, and Tempo telemetry adapters
- local observability stack assets and Aspire Shop bootstrap helpers
- Docker-based PostgreSQL and Redis fault injection helpers
- quantitative evaluation harness with ground-truth labels
- automated live telemetry signature validation for the Aspire Shop fault scenarios
- OpenAI-backed Analyst, Critic, and follow-up responses when a local key is present

Still open:

- Aspire Shop is still fetched from the upstream sample repo instead of being vendored here
- Retriever and Planner are still deterministic
- qualitative review scoring and final demo/report polish are not implemented yet

## Prerequisites

- Python `3.12+`
- `git`
- for the live stack path: Docker, Docker Compose, `.NET 10 SDK`, and either the Aspire CLI or `dotnet run`
- for the model-backed path: a local OpenAI API key in `.env.local`

## Setup

Create a virtual environment and install the package:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Local OpenAI config

If `.env.local` exists, IIRS loads it automatically. `.env` and `.env.local` are gitignored.

Recommended `.env.local`:

```dotenv
OPENAI_API_KEY=sk-...
IIRS_AGENT_MODEL=gpt-5-mini
IIRS_OPENAI_REASONING_EFFORT=low
IIRS_EMBEDDING_MODEL=text-embedding-3-small
```

Notes:

- `gpt-5-mini` is the best default from the currently supported models for this project
- if a key is present, Analyst, Critic, and follow-up answers use OpenAI automatically
- set `IIRS_USE_OPENAI_AGENTS=false` to force deterministic behavior
- Retriever and Planner remain deterministic today

## Fastest path: mock end-to-end

Run the built-in scenarios:

```bash
iirs run --scenario postgres_down --show-trace
iirs run --scenario redis_down --show-trace
```

Or run from alert fixtures:

```bash
iirs run --alert-file fixtures/alerts/postgres_down.json --show-trace
iirs run --alert-file fixtures/alerts/redis_down.json --show-trace
```

What you should see:

- an Incident Brief in the terminal
- ranked root causes
- actions split into `auto-safe` and `needs-approval`
- a trace path under `traces/`

### Chainlit

```bash
source .venv/bin/activate
chainlit run chainlit_app.py -h
```

Then send:

1. `postgres_down` or `redis_down`
2. a follow-up like `What is the root cause?`
3. or a full alert JSON payload

### Evaluation

Run the quantitative harness:

```bash
iirs eval --runs 3
```

Useful variants:

```bash
iirs eval --scenario postgres_down --runs 5
iirs eval --runs 3 --format json
```

The evaluation harness checks:

- Top-1 root-cause accuracy
- Top-3 root-cause accuracy
- required evidence-source coverage
- required action-type and action-keyword coverage

Ground-truth labels live in `fixtures/ground_truth/`.

Run the live signature validator after reproducing a real local fault:

```bash
iirs verify-live --scenario postgres_down
iirs verify-live --scenario redis_down
```

Useful variants:

```bash
iirs verify-live --started-at 2026-04-06T12:00:00Z --window-minutes 20
iirs verify-live --format json
```

The live signature validator checks that the PLT backend can retrieve the expected Loki, Prometheus, and Tempo signals for the active fault window.

## Live local stack

This is the full local path with Aspire Shop plus Prometheus, Loki, Tempo, and the OTel Collector.

### 1. Start the observability stack

```bash
./scripts/run_observability_stack.sh up
./scripts/run_observability_stack.sh ps
```

Ports:

- Prometheus: `http://localhost:9090`
- Loki: `http://localhost:3100`
- Tempo: `http://localhost:3200`
- OTel Collector OTLP gRPC: `localhost:4317`
- OTel Collector OTLP HTTP: `localhost:4318`
- OTel Collector Prometheus exporter: `localhost:9464`

### 2. Fetch Aspire Shop

```bash
./scripts/bootstrap_aspire_shop.sh
```

This clones the upstream sample into `.external/aspire-samples` by default.

### 3. Run Aspire Shop against the local collector

```bash
cd .external/aspire-samples/samples/aspire-shop
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
aspire run
```

Alternative:

```bash
dotnet run --project AspireShop.AppHost
```

The sample exposes these key resources:

- `frontend`
- `catalogservice`
- `catalogdbmanager`
- `basketservice`
- PostgreSQL resource `postgres` with database `catalogdb`
- Redis resource `basketcache`

### 4. Generate baseline traffic

Open the shop frontend from the Aspire output and:

1. browse the catalog
2. add items to the basket
3. refresh and repeat

### 5. Verify telemetry landed

Prometheus:

```bash
curl -s http://localhost:9090/api/v1/targets
curl -s -G http://localhost:9090/api/v1/query --data-urlencode 'query=up'
```

Loki:

```bash
curl -s -G http://localhost:3100/loki/api/v1/query --data-urlencode 'query={service_name="catalogservice"}'
curl -s -G http://localhost:3100/loki/api/v1/query --data-urlencode 'query={service_name="basketservice"}'
```

Tempo:

```bash
curl -s -G http://localhost:3200/api/search --data-urlencode 'q={ resource.service.name = "catalogservice" }'
curl -s -G http://localhost:3200/api/search --data-urlencode 'q={ resource.service.name = "basketservice" }'
```

### 6. Enable the live IIRS backend

```bash
export IIRS_TELEMETRY_BACKEND=plt
export IIRS_PROMETHEUS_URL=http://localhost:9090
export IIRS_LOKI_URL=http://localhost:3100
export IIRS_TEMPO_URL=http://localhost:3200
```

Optional:

```bash
export IIRS_TENANT_ID=tenant-a
export IIRS_HTTP_TIMEOUT_SECONDS=15
export IIRS_VERIFY_TLS=false
```

Run IIRS:

```bash
iirs run --alert-file fixtures/alerts/postgres_down.json --show-trace
```

Or use Chainlit:

```bash
chainlit run chainlit_app.py -h
```

Live-mode checks:

- the run completes without `TelemetryConfigurationError`
- the trace contains `"used_langgraph": true`
- Retriever evidence IDs look like `log.live.*`, `metric.live.*`, or `trace.live.*`
- citations point at `/api/v1/query_range`, `/loki/api/v1/query_range`, and `/api/search`

Validate the live fault signatures directly:

```bash
iirs verify-live --scenario postgres_down
iirs verify-live --scenario redis_down
```

## Fault injection

Use the helper:

```bash
./scripts/inject_aspire_fault.sh discover
./scripts/inject_aspire_fault.sh stop postgres
./scripts/inject_aspire_fault.sh start postgres
./scripts/inject_aspire_fault.sh stop redis
./scripts/inject_aspire_fault.sh start redis
```

Aliases:

- PostgreSQL: `postgres`, `db`, `catalogdb`
- Redis: `redis`, `basketcache`, `cache`

Discovery behavior:

- PostgreSQL matches container names containing `postgres` or `catalogdb`
- Redis matches container names containing `basketcache` or `redis`
- ambiguous matches fail fast and require an override

Explicit overrides:

```bash
export IIRS_ASPIRE_POSTGRES_CONTAINER=<exact-container-name>
export IIRS_ASPIRE_REDIS_CONTAINER=<exact-container-name>
```

Typical workflow:

1. start the observability stack
2. run Aspire Shop with OTLP pointed at the collector
3. generate baseline traffic
4. run `./scripts/inject_aspire_fault.sh discover`
5. stop PostgreSQL or Redis
6. generate more traffic and verify the failure signals
7. run IIRS against the matching alert fixture
8. recover with `start postgres` or `start redis`

The script-level harness is:

```bash
bash tests/test_fault_injection.sh
```

## Validation and troubleshooting

Run the test suite:

```bash
./.venv/bin/python -m unittest discover -s tests
```

Inspect trace artifacts:

```bash
ls traces
rg -n '"used_langgraph"|"scenario_name"|"source_type"' traces/*.json
```

If you want the pipeline to stay fully deterministic even with `.env.local` present:

```bash
export IIRS_USE_OPENAI_AGENTS=false
```

If OpenAI-backed runs are too slow, keep `IIRS_OPENAI_REASONING_EFFORT=low` and prefer CLI runs over Chainlit first.

## Configuration reference

General:

- `IIRS_TRACE_DIR`: trace output directory, default `traces`
- `IIRS_PREFER_LANGGRAPH`: `true` by default

Telemetry:

- `IIRS_TELEMETRY_BACKEND`: `mock` or `plt`
- `IIRS_PROMETHEUS_URL`
- `IIRS_LOKI_URL`
- `IIRS_TEMPO_URL`
- `IIRS_TENANT_ID`
- `IIRS_HTTP_TIMEOUT_SECONDS`
- `IIRS_VERIFY_TLS`

OpenAI:

- `OPENAI_API_KEY` or `IIRS_OPENAI_API_KEY`
- `IIRS_USE_OPENAI_AGENTS`
- `IIRS_OPENAI_BASE_URL`
- `IIRS_OPENAI_REASONING_EFFORT`
- `IIRS_AGENT_MODEL`
- `IIRS_EMBEDDING_MODEL`

Fault injection:

- `IIRS_ASPIRE_POSTGRES_CONTAINER`
- `IIRS_ASPIRE_REDIS_CONTAINER`
- `IIRS_DOCKER_CMD`

## Repository layout

- `src/iirs/`: application code
- `infra/observability/`: Prometheus, Loki, Tempo, and OTel Collector config
- `runbooks/`: static troubleshooting docs used by Retriever
- `fixtures/alerts/`: sample alert payloads
- `fixtures/ground_truth/`: evaluation labels
- `scripts/`: observability, Aspire Shop, and fault-injection helpers
- `tests/`: unit and integration coverage
- `docs/implementation-status.md`: implementation status against issue #1

## Additional references

- Issue: [#1](https://github.com/gaurav0106/iirs/issues/1)
- Status doc: [`docs/implementation-status.md`](docs/implementation-status.md)
