# IIRS Multi-Agent Submission Summary

## What This Project Is

IIRS is a local incident-response assistant centered on a four-stage agent pipeline:

1. `Retriever`: gathers logs, metrics, traces, runbook hits, and recent changes.
2. `Analyst`: ranks likely root causes from the retrieved evidence.
3. `Critic`: checks whether the analysis is grounded, conservative, and safe.
4. `Planner`: turns the reviewed analysis into an incident brief and triage plan.

The app surface is not the main contribution. The core contribution is the agent pipeline, shared state, reasoning traces, and evaluation workflow.

## Why This Counts As A Multi-Agent System

The system is not one monolithic prompt. Each agent has a separate role, consumes a different slice of state, and leaves structured output for the next stage:

- `Retriever` produces the evidence bundle and tool-call trace.
- `Analyst` produces ranked hypotheses with cited evidence IDs.
- `Critic` challenges the analyst output, flags risks, and preserves safety boundaries.
- `Planner` converts the reviewed state into operator-facing actions split into `auto-safe` and `needs-approval`.

This separation matters because it makes the reasoning traceable, lets the Critic act as a check on overconfident analysis, and keeps remediation planning downstream of evidence review instead of mixing everything into one opaque response.

## What To Show During Evaluation

Use the CLI first. It is the cleanest proof of the multi-agent system.

Recommended sequence:

1. `./.venv/bin/python -m unittest discover -s tests`
2. `IIRS_USE_OPENAI_AGENTS=false ./.venv/bin/iirs eval --runs 2`
3. `IIRS_USE_OPENAI_AGENTS=false ./.venv/bin/iirs run --scenario postgres_down --show-trace`
4. `IIRS_USE_OPENAI_AGENTS=false ./.venv/bin/iirs run --scenario redis_down --show-trace`

What the evaluator should notice:

- the explicit `Retriever -> Analyst -> Critic -> Planner` flow
- per-agent trace output instead of one undifferentiated answer
- evidence-linked hypotheses and actions
- safety split between read-only diagnostics and approval-required changes
- quantitative accuracy plus qualitative review scoring

## Strongest Submission Story

The safest default is deterministic mode with no API key dependency:

- tests pass locally
- built-in scenarios are reproducible
- evaluation reports are stable
- trace files are generated automatically

The OpenAI-backed path is optional extra credit:

- Analyst, Critic, Planner, and follow-up answers can use OpenAI when enabled
- model-enabled runs fail cleanly on timeout or invalid structured output instead of silently falling back

## Current Honest Limits

- `Retriever` is still deterministic and tool-driven, not model-driven
- the Aspire Shop sample is still external rather than fully vendored
- the live local demo is strongest on `postgres_down`; `redis_down` is usable but less battle-tested in the current docs

## Demo Fallback If Live Telemetry Gets Annoying

If the live stack is up but one telemetry check is flaky on the day, do not waste the demo on infrastructure debugging.

Fallback sequence:

1. State that the live fault reproduction succeeded but one backend check is noisy.
2. Show the saved live rehearsal output from `submission_artifacts/rehearsal_agent_live_postgres.txt`.
3. Then run the deterministic scenario live:
   `IIRS_USE_OPENAI_AGENTS=false ./.venv/bin/iirs run --scenario postgres_down --show-trace`

That still demonstrates the actual multi-agent system clearly:

- separate Retriever, Analyst, Critic, and Planner stages
- traceable evidence collection
- ranked hypotheses
- critique and safety boundaries
- action planning
