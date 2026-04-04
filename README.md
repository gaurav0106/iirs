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

## Project layout

- `src/iirs/`: application code
- `runbooks/`: static troubleshooting documents used by the Retriever
- `fixtures/alerts/`: sample alert payloads
- `tests/`: unit and integration coverage for the mock pipeline
- `docs/issue-1-status.md`: what this slice covers and what is still open against issue #1

## Configuration

Environment variables:

- `IIRS_TRACE_DIR`: override the trace directory, default `traces`
- `IIRS_PREFER_LANGGRAPH`: `true` by default. If `false`, the code uses an internal linear fallback runner that keeps tests working without LangGraph installed.

## Current behavior

The pipeline is intentionally deterministic so the capstone can be developed and tested locally before the real observability stack and live models are connected. The abstractions are already shaped around the issue requirements, so the next iteration can replace the mock backend and deterministic reasoning without rewriting the core flow.
