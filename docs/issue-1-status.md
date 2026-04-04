# Issue #1 implementation status

This repository started with only the capstone proposal PDF. The current slice implements the minimum working software skeleton behind the issue.

## Implemented in this slice

1. Shared incident state schema with alert, evidence bundle, hypotheses, critique, triage plan, incident brief, messages, and traces.
2. Linear `Retriever -> Analyst -> Critic -> Planner` orchestration with a LangGraph-first runner and a local fallback runner for environments where dependencies are not installed yet.
3. Parameterized query templates for logs, latency metrics, error-rate metrics, failed traces, slow traces, runbooks, and recent changes.
4. Real Prometheus, Loki, and Tempo HTTP adapters that can replace the mock telemetry backend through environment-based configuration.
5. Structured JSON reasoning traces written one file per incident.
6. CLI entrypoint for reproducible incident runs.
7. Chainlit entrypoint that displays agent steps and supports follow-up questions over the last incident state.
8. Two deterministic fault scenarios: `postgres_down` and `redis_down`.
9. Local observability stack assets for Prometheus, Loki, Tempo, and the OpenTelemetry Collector.
10. Helper scripts and docs for bootstrapping the upstream Aspire Shop sample against that stack.

## Still open from the issue

1. Aspire Shop is still fetched from the upstream sample repository rather than being vendored or automated as part of this repo.
2. Fault injection for PostgreSQL and Redis outages is not automated yet.
3. The agent reasoning is deterministic rather than backed by OpenAI models.
4. Quantitative Top-1 and Top-3 evaluation runs are not implemented yet.
5. Qualitative evaluation workflows, demo automation, Codespaces, and the final report remain open.

## Recommended next step

The next practical milestone is automating Aspire Shop startup plus PostgreSQL and Redis fault injection so the live telemetry path becomes a reproducible end-to-end demo.
