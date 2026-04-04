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

## Still open from the issue

1. Aspire Shop and the Prometheus/Loki/Tempo stack are not provisioned by this repository yet.
2. The live PLT adapter expects reachable endpoints, but there is no docker-compose or Aspire wiring here yet.
3. The agent reasoning is deterministic rather than backed by OpenAI models.
4. Quantitative Top-1 and Top-3 evaluation runs are not implemented yet.
5. Qualitative evaluation workflows, demo automation, Codespaces, and the final report remain open.

## Recommended next step

The next practical milestone is standing up Aspire Shop plus the PLT stack locally and validating the live adapter queries against real incident telemetry.
