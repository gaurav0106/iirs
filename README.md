# IIRS

IIRS is an incident-response assistant scaffold for issue [#1](https://github.com/gaurav0106/iirs/issues/1). This repository now contains a runnable foundation slice instead of just the project proposal.

The current implementation focuses on the software skeleton the issue calls for:

- a shared incident state model
- a linear 4-stage pipeline: `Retriever -> Analyst -> Critic -> Planner`
- deterministic mock telemetry for the two required fault scenarios: `postgres_down` and `redis_down`
- structured evidence with citations
- JSON reasoning traces written per incident
- a CLI entrypoint
- a Chainlit app entrypoint for interactive demo flows

What is not in this slice yet:

- live Aspire Shop / PLT stack integration
- real OpenAI-backed agent prompting
- automated quantitative and qualitative evaluation runs

## Quickstart

1. Create a virtual environment.
2. Install the package in editable mode:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

3. Run the deterministic CLI demo:

```bash
iirs run --scenario postgres_down
iirs run --scenario redis_down --format json
```

4. Run the test suite:

```bash
python3 -m unittest discover -s tests
```

5. Start Chainlit:

```bash
chainlit run chainlit_app.py -h
```

Then send `postgres_down`, `redis_down`, or paste an alert JSON payload.

## End-to-end test flows

There are currently two realistic ways to test IIRS end to end:

1. Mock end-to-end: uses the built-in PostgreSQL and Redis incident scenarios. This is the path that works immediately from this repo.
2. Live PLT end-to-end: uses real Prometheus, Loki, and Tempo endpoints if you already have them running elsewhere.

### Mock end-to-end with CLI

This is the fastest full pipeline check.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

iirs run --scenario postgres_down --show-trace
iirs run --scenario redis_down --show-trace
```

What you should see:

- an Incident Brief printed to the terminal
- ranked root causes
- recommended actions split into `auto-safe` and `needs-approval`
- a trace file path under `traces/`

You can also run the same flow from the fixture alerts:

```bash
iirs run --alert-file fixtures/alerts/postgres_down.json --show-trace
iirs run --alert-file fixtures/alerts/redis_down.json --show-trace
```

### Mock end-to-end with Chainlit

Use this if you want the full chat flow plus follow-up questions.

Terminal 1:

```bash
source .venv/bin/activate
chainlit run chainlit_app.py -h
```

Then in the browser:

1. Send `postgres_down` or `redis_down`.
2. Wait for the Incident Brief to appear.
3. Ask follow-up questions such as:
   - `What is the root cause?`
   - `What evidence supports it?`
   - `What should I do next?`

What you should see:

- Chainlit steps for `Retriever`, `Analyst`, `Critic`, and `Planner`
- tool steps for logs, metrics, traces, runbooks, and change signals
- the final Incident Brief
- a trace path message pointing to the saved JSON trace

### Live PLT end-to-end

Use this only if Prometheus, Loki, and Tempo are already reachable. This repo does not provision that stack yet.

Set the live backend:

```bash
export IIRS_TELEMETRY_BACKEND=plt
export IIRS_PROMETHEUS_URL=http://localhost:9090
export IIRS_LOKI_URL=http://localhost:3100
export IIRS_TEMPO_URL=http://localhost:3200
```

Optional for Grafana multi-tenant setups or local TLS exceptions:

```bash
export IIRS_TENANT_ID=tenant-a
export IIRS_HTTP_TIMEOUT_SECONDS=15
export IIRS_VERIFY_TLS=false
```

Then run either the CLI or Chainlit:

```bash
iirs run --alert-file fixtures/alerts/postgres_down.json --show-trace
```

Or:

```bash
chainlit run chainlit_app.py -h
```

Then paste a fixture alert JSON payload or send an alert that matches your running services.

What to verify in live mode:

- the run completes without `TelemetryConfigurationError`
- the trace file contains `"used_langgraph": true`
- the Retriever step contains live-derived evidence IDs like `log.live.*`, `metric.live.*`, or `trace.live.*`
- the citations reference real API sources such as `/api/v1/query_range`, `/loki/api/v1/query_range`, and `/api/search`

### Validation commands

After any end-to-end run, you can inspect the trace artifacts directly:

```bash
ls traces
rg -n '"used_langgraph"|"scenario_name"|"source_type"' traces/*.json
```

You can also rerun the automated checks:

```bash
python3 -m unittest discover -s tests
```

## Project layout

- `src/iirs/`: application code
- `runbooks/`: static troubleshooting documents used by the Retriever
- `fixtures/alerts/`: sample alert payloads
- `tests/`: unit and integration coverage for the mock pipeline
- `docs/issue-1-status.md`: what this slice covers and what is still open against issue #1
- `docs/live-backend.md`: details for the real PLT adapter mode

## Configuration

Environment variables:

- `IIRS_TRACE_DIR`: override the trace directory, default `traces`
- `IIRS_PREFER_LANGGRAPH`: `true` by default. If `false`, the code uses an internal linear fallback runner that keeps tests working without LangGraph installed.
- `IIRS_TELEMETRY_BACKEND`: `mock` or `plt`
- `IIRS_PROMETHEUS_URL`, `IIRS_LOKI_URL`, `IIRS_TEMPO_URL`: required when using `plt`

## Current behavior

The pipeline is intentionally deterministic so the capstone can be developed and tested locally before the real observability stack and live models are connected. The abstractions are already shaped around the issue requirements, so the next iteration can replace the mock backend and deterministic reasoning without rewriting the core flow.

Current limitation for true end-to-end demos: this repo still does not stand up Aspire Shop or the PLT stack itself. The mock scenarios are the reliable e2e path today; the live path works only when those backends already exist.
