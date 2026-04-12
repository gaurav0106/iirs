# Implementation Status

This repository started with only the capstone proposal PDF. The current slice implements the minimum working software skeleton behind the issue.

## Implemented in this slice

1. Shared incident state schema with alert, evidence bundle, hypotheses, critique, triage plan, incident brief, messages, and traces.
2. Linear `Retriever -> Analyst -> Critic -> Planner` orchestration with a LangGraph-first runner and a local fallback runner for environments where dependencies are not installed yet.
3. Parameterized query templates for logs, latency metrics, error-rate metrics, failed traces, slow traces, runbooks, and recent changes.
4. Real Prometheus, Loki, and Tempo HTTP adapters wired through the PLT telemetry backend.
5. Structured JSON reasoning traces written one file per incident.
6. CLI entrypoint for reproducible incident runs.
7. Chainlit entrypoint that displays agent steps and supports follow-up questions over the last incident state.
8. Reproducible alert fixtures for PostgreSQL and Redis incidents.
9. Local observability stack assets for Prometheus, Loki, Tempo, and the OpenTelemetry Collector.
10. Helper scripts and docs for bootstrapping the upstream Aspire Shop sample against that stack.
11. Automated PostgreSQL and Redis fault-injection helpers for the Aspire Shop demo.
12. First-class rubric-based evaluation via `iirs eval` for routing and fixture-backed pipeline regressions.
13. Reproducible alert-fixture runs plus live-signature profile validation for demo and regression coverage.
14. Prompt, routing, and ranking regression tests for evidence grounding, critic caution, safety boundaries, and live health-check behavior.
15. OpenAI-backed Analyst, Critic, Planner, and follow-up responses with local-only key loading.
16. Live telemetry signature validation for PostgreSQL and Redis fault profiles.

## Still open from the issue

1. Aspire Shop is still fetched from the upstream sample repository rather than being vendored or fully automated as part of this repo.
2. Retriever remains deterministic; Analyst, Critic, Planner, and follow-up answers can use OpenAI when enabled.
3. The new eval layer covers routing and fixture-backed pipeline regressions first; the live corpus and scoring rubrics still need to expand over time.

## Recommended next step

The next practical milestone is expanding the new evaluation layer with more live-like incident cases and richer scoring rubrics while keeping live-signature checks separate from reasoning-quality evaluation.
