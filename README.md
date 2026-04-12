# IIRS

IIRS is a local incident-response assistant prototype for issue [#1](https://github.com/gaurav0106/iirs/issues/1). It runs a `Retriever -> Analyst -> Critic -> Planner` pipeline over sample alert fixtures or live telemetry and produces an Incident Brief with ranked root causes, evidence citations, and recommended actions.

## Current scope

Implemented:

- shared incident state and per-incident JSON traces
- LangGraph-first linear pipeline with a local fallback runner
- CLI and Chainlit entrypoints
- sample alert fixtures for PostgreSQL and Redis incidents
- live Prometheus, Loki, and Tempo telemetry adapters
- local observability stack assets and Aspire Shop bootstrap helpers
- Docker-based PostgreSQL and Redis fault injection helpers
- automated live telemetry signature validation profiles for PostgreSQL and Redis faults
- OpenAI-backed Analyst, Critic, Planner, and follow-up responses when a local key is present

Still open:

- Aspire Shop is still fetched from the upstream sample repo instead of being vendored here
- Retriever remains deterministic
- final demo/report polish is not implemented yet

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
IIRS_OPENAI_TIMEOUT_SECONDS=60
IIRS_EMBEDDING_MODEL=text-embedding-3-small
```

Notes:

- `gpt-5-mini` is the best default from the currently supported models for this project
- if a key is present, Analyst, Critic, Planner, and follow-up answers use OpenAI automatically
- set `IIRS_USE_OPENAI_AGENTS=false` to force deterministic behavior
- model-enabled runs fail cleanly on timeout or invalid structured output instead of falling back to a weaker deterministic answer
- use `iirs llm-check` to verify the OpenAI path before testing incidents
- if model calls are slow in your environment, raise `IIRS_OPENAI_TIMEOUT_SECONDS` to `90` or higher
- Retriever remains deterministic today

## Fastest path: alert-fixture end-to-end

Run the sample alert fixtures:

```bash
iirs run --alert-file fixtures/alerts/postgres_down.json --show-trace
iirs run --alert-file fixtures/alerts/redis_down.json --show-trace
```

Or create a one-off live-style alert from a summary:

```bash
iirs run --summary "catalogservice is timing out and PostgreSQL looks down" --service catalogservice --show-trace
iirs run --summary "basketservice cannot reach Redis and cart calls are failing" --service basketservice --show-trace
```

What you should see:

- an Incident Brief in the terminal
- ranked root causes
- actions split into `auto-safe` and `needs-approval`
- a trace path under `traces/`

When you pass `--show-trace`, each stage is labeled as `[tooling]`, `[model]`, or `[deterministic]` so you can tell whether the run actually used the LLM.

### Real LLM smoke check

Verify the configured model path first:

```bash
iirs llm-check
```

Then ask a follow-up question against a sample alert from the CLI:

```bash
iirs ask --alert-file fixtures/alerts/postgres_down.json "How sure are we?"
iirs ask --alert-file fixtures/alerts/postgres_down.json "Did a deploy cause this?"
iirs ask --alert-file fixtures/alerts/redis_down.json "What should I do first?"
```

If the model is unavailable, these commands should fail cleanly instead of silently falling back to a weaker answer.

### Chainlit

```bash
source .venv/bin/activate
chainlit run chainlit_app.py --port 8000
```

Useful prompt styles:

1. dependency-shaped incidents: `catalogservice is timing out and PostgreSQL looks down`
2. cache/cart incidents: `basketservice cannot reach Redis and cart calls are failing`
3. broad live diagnosis: `what broke in aspire shop right now?`
4. broad health checks: `is everything healthy or broken right now?` or `can you check the health of aspireshop?`
5. user-facing page issues: `the aspire shop page is not loading at all`
6. follow-ups after a run: `why?`, `show me more`, `then what?`, `is it healthy?`
7. a full alert JSON payload

Notes:

- plain-English text is treated as a new incident prompt instead of being routed through hidden demo shortcuts
- broad health-check prompts use a safer `live-health-check` mode that prefers `No clear live fault detected` when runtime state is green
- short follow-ups are resolved against the current incident state instead of starting a new run
- if a model-backed stage fails, Chainlit stops that run and shows the model error instead of silently falling back

### Live signature validation

Run the live signature validator after reproducing a real local fault:

```bash
iirs verify-live --profile postgres_down
iirs verify-live --profile redis_down
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

This clones the upstream sample into `.external/aspire-samples` by default and
auto-applies the local patch in [`patches/aspire-shop-local-e2e.patch`](/home/gaurav/code-ubuntu/personal/capstone/patches/aspire-shop-local-e2e.patch).

### 3. Run Aspire Shop against the local collector

```bash
cd .external/aspire-samples/samples/aspire-shop
export IIRS_OTLP_ENDPOINT=http://127.0.0.1:4317
export IIRS_OTLP_PROTOCOL=grpc
aspire run
```

Alternative:

```bash
dotnet run --project AspireShop.AppHost
```

If you fetched Aspire Shop some other way, rerun `./scripts/bootstrap_aspire_shop.sh`
or apply [`patches/aspire-shop-local-e2e.patch`](/home/gaurav/code-ubuntu/personal/capstone/patches/aspire-shop-local-e2e.patch)
manually before starting it.

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
iirs verify-live --profile postgres_down
iirs verify-live --profile redis_down
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
rg -n '"used_langgraph"|"incident_id"|"source_type"' traces/*.json
```

If you want the pipeline to stay fully deterministic even with `.env.local` present:

```bash
export IIRS_USE_OPENAI_AGENTS=false
```

If OpenAI-backed runs are too slow, keep `IIRS_OPENAI_REASONING_EFFORT=low`, prefer CLI runs over Chainlit first, and raise `IIRS_OPENAI_TIMEOUT_SECONDS` if planner-style health checks time out.

## Configuration reference

General:

- `IIRS_TRACE_DIR`: trace output directory, default `traces`
- `IIRS_PREFER_LANGGRAPH`: `true` by default

Telemetry:

- `IIRS_TELEMETRY_BACKEND`: `plt`
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
- `IIRS_OPENAI_TIMEOUT_SECONDS`
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
- `fixtures/live_signatures/`: live validation profiles
- `scripts/`: observability, Aspire Shop, and fault-injection helpers
- `tests/`: unit and integration coverage
- `docs/implementation-status.md`: implementation status against issue #1

## Additional references

- Issue: [#1](https://github.com/gaurav0106/iirs/issues/1)
- Status doc: [`docs/implementation-status.md`](docs/implementation-status.md)
