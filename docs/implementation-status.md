# Implementation Status

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
11. Automated PostgreSQL and Redis fault-injection helpers for the Aspire Shop demo.
12. Ground-truth labels plus a quantitative evaluation harness for Top-1 and Top-3 scoring.
13. OpenAI-backed Analyst, Critic, and follow-up responses with local-only key loading.

## Still open from the issue

1. Aspire Shop is still fetched from the upstream sample repository rather than being vendored or fully automated as part of this repo.
2. The live PostgreSQL and Redis fault scenarios are not validated automatically against expected telemetry signatures yet.
3. Retriever and Planner remain deterministic; only Analyst, Critic, and follow-up answers use OpenAI today.
4. Qualitative evaluation workflows for evidence quality and Critic catch rate are not implemented yet.
5. Demo automation, Codespaces, and the final report remain open.

## Recommended next step

The next practical milestone is adding live telemetry signature checks plus qualitative review scoring so the end-to-end demo is validated, not just measured.
