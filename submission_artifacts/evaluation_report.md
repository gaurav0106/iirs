# IIRS Evaluation Report

**Date:** 2026-04-11
**System:** Intelligent Incident Response System (IIRS)
**Author:** Gaurav Yadav

---

## 1. Evaluation overview

IIRS is evaluated along two axes: **quantitative accuracy** (does the system identify the correct root cause and produce structurally valid incident briefs?) and **qualitative review** (is the reasoning evidence-grounded, safety-aware, and operationally useful?).

The evaluation harness (`src/iirs/evaluation.py`) runs the full four-agent pipeline against ground-truth labels and scores each run on 11 checks: root cause accuracy (top-1 and top-3), 5 evidence coverage checks, action keyword and type matching, and 6 qualitative review criteria.

---

## 2. Quantitative results

### 2.1 Deterministic mode (mock telemetry, 5 runs per scenario)

| Metric | postgres_down | redis_down | Overall |
|--------|--------------|------------|---------|
| Runs | 5 | 5 | 10 |
| Top-1 accuracy | 5/5 (100%) | 5/5 (100%) | 10/10 (100%) |
| Top-3 accuracy | 5/5 (100%) | 5/5 (100%) | 10/10 (100%) |
| Fully passing runs | 5/5 | 5/5 | 10/10 |
| Qualitative score | 30/30 (100%) | 30/30 (100%) | 60/60 (100%) |

**Top hypotheses produced (consistent across all runs):**

postgres_down:
1. PostgreSQL dependency outage (confidence 0.93)
2. Catalogservice connection pool exhaustion caused by database unavailability (confidence 0.46)
3. Recent deploy or configuration regression (confidence 0.18)

redis_down:
1. Redis dependency outage (confidence 0.93)
2. Basketservice thread starvation caused by repeated Redis retry loops (confidence 0.46)
3. Recent deploy or configuration regression (confidence 0.18)

### 2.2 LLM-backed mode (gpt-5-mini, 1 run per scenario)

| Metric | postgres_down | redis_down | Overall |
|--------|--------------|------------|---------|
| Runs | 1 | 1 | 2 |
| Top-1 accuracy | 1/1 (100%) | 1/1 (100%) | 2/2 (100%) |
| Qualitative score | 5/6 (83%) | 4/6 (67%) | 9/12 (75%) |
| Action keyword match | 0/2 | 0/2 | 0/4 |
| Safety boundary explicit | Yes | No | 50% |

The LLM correctly identifies root causes but generates action plans with different phrasing than the ground-truth keywords, and sometimes omits the explicit `needs-approval` action type. This is expected: the model produces more natural language at the cost of structural predictability.

### 2.3 Deterministic vs LLM comparison

| Metric | Deterministic | LLM-backed |
|--------|--------------|------------|
| Top-1 root cause accuracy | 100% | 100% |
| Top-3 root cause accuracy | 100% | 100% |
| Qualitative review score | 100% | 75% |
| Action keyword match rate | 100% | 0% |
| Safety boundary enforcement | 100% | 50% |
| Reproducibility | Identical across runs | Varies per run |

**Interpretation:** Deterministic mode is the reliable evaluation baseline -- it proves the pipeline architecture, evidence flow, and handoff contracts work correctly. LLM mode adds the ability to handle novel incidents and produce more nuanced reasoning, at the cost of less predictable output structure.

---

## 3. Qualitative review criteria

Each run is scored against 6 qualitative checks. All 60 checks pass in deterministic mode.

| # | Check | What it validates |
|---|-------|-------------------|
| 1 | Top hypothesis cites >= 3 evidence items | Diagnosis is not based on a single signal |
| 2 | Evidence spans >= 3 source types | Multi-signal correlation (logs + metrics + traces) |
| 3 | Critic catches >= 1 material caveat | The critique stage adds value beyond rubber-stamping |
| 4 | Safety boundary is explicit | Auto-safe and needs-approval actions are separated |
| 5 | Every action is evidence-grounded | No recommended action lacks a citation |
| 6 | Open questions preserve critic's data gaps | Unresolved issues flow through to the operator |

**Why these checks matter:** They measure whether the multi-agent architecture produces better incident briefs than a single-agent summarizer would. A monolithic prompt could match top-1 accuracy, but it would not naturally produce evidence-linked citations, explicit safety boundaries, or internally contested hypotheses. The qualitative checks validate that the four-agent separation provides structural benefits.

---

## 4. Evidence coverage

Each scenario requires 5 evidence categories from ground-truth labels:

**postgres_down:**
- Loki error logs for catalogservice (matched: `log.pg.connection_refused`, `log.pg.retry_exhausted`)
- Prometheus latency metrics (matched: `metric.pg.latency`, `metric.pg.error_rate`)
- Prometheus exception metrics (matched: `metric.pg.error_rate`)
- Tempo failed traces (matched: `trace.pg.checkout_failure`)
- PostgreSQL runbook guidance (matched: `runbook.postgresql-troubleshooting`)

**redis_down:**
- Loki error logs for basketservice (matched: `log.redis.connection_refused`, `log.redis.timeout`)
- Prometheus latency metrics (matched: `metric.redis.latency`, `metric.redis.error_rate`)
- Prometheus exception metrics (matched: `metric.redis.error_rate`)
- Tempo failed traces (matched: `trace.redis.cart_failure`)
- Redis runbook guidance (matched: `runbook.redis-troubleshooting`)

The Retriever collects evidence from 4 distinct telemetry sources (Loki, Prometheus, Tempo, runbooks) plus change signals and runtime state. All required evidence checks pass.

---

## 5. Agent reasoning traces

Every pipeline run produces a JSON trace file in `traces/` with full per-agent records:

```
Agent: Retriever [tooling]
  Tool calls: 8 (error_logs, latency_metrics, error_rate_metrics, 
                  failed_traces, slow_traces, runbook, recent_changes, runtime_states)
  Output: "Collected 2 logs, 2 metrics, 2 traces, 1 runbook, 1 change signal"

Agent: Analyst [deterministic]
  Output: "Ranked root causes with top hypothesis 'PostgreSQL dependency outage' at 0.93"

Agent: Critic [deterministic]
  Output: "Generated 2 findings and 0 hallucination-risk checks"

Agent: Planner [deterministic]
  Output: "Produced 5 triage actions and the final brief"
```

Each `AgentRun` record includes: `agent_name`, `started_at`, `finished_at`, `execution_mode` (tooling/deterministic/model), `input_summary`, `output_summary`, and `tool_calls[]`. Each `ToolCallRecord` includes: `tool_name`, `arguments`, `query`, `evidence_ids`, `started_at`, `finished_at`.

---

## 6. Unit test coverage

62 tests across 10 test files cover the pipeline, individual agents, LLM integration, backends, evaluation harness, CLI, Chainlit UI, and live signature validation.

| Test file | Scope |
|-----------|-------|
| `test_pipeline.py` | Full pipeline scenarios, state handoffs, trace persistence |
| `test_backends.py` | Mock and PLT backend query construction and response parsing |
| `test_llm.py` | OpenAI Responses API integration, timeout handling, JSON schema |
| `test_evaluation.py` | Evaluation harness scoring logic |
| `test_chainlit_app.py` | Chat routing, scenario inference, follow-ups |
| `test_cli.py` | CLI command parsing and execution paths |
| `test_config.py` | Settings loading and environment variable handling |
| `test_live_signatures.py` | Live telemetry validation against expected signal profiles |
| `test_chainlit_e2e_matrix.py` | End-to-end Chainlit interaction matrix |
| `test_openai_agents.py` | Agent-specific LLM contract tests |

---

## 7. Limitations and future work

**Current limitations:**
- Only 2 scenarios (postgres_down, redis_down) have ground-truth labels
- Retriever uses deterministic tool-call logic, not model-driven evidence search
- LLM mode action plans do not consistently match ground-truth keyword phrasing
- Topology reasoning is evidence-shape-aware, not based on a true dependency graph
- Live demos can be affected by telemetry ingestion latency and model API timeouts

**Future work:**
- Add scenarios for frontend overload, cascading failures, and configuration drift
- Build a true service dependency graph for topology-aware reasoning
- Improve LLM prompt engineering for structured action plan output
- Add a Streamlit dashboard for visual pipeline traces and evaluation summaries

---

## 8. Reproduction

```bash
# Deterministic evaluation (no API key needed)
IIRS_USE_OPENAI_AGENTS=false python -m iirs.cli eval --runs 5 --format markdown

# LLM-backed evaluation (requires OPENAI_API_KEY)
IIRS_OPENAI_TIMEOUT_SECONDS=120 python -m iirs.cli eval --runs 1 --format markdown

# Run unit tests
python -m pytest tests/ -q

# Inspect a trace file
cat traces/postgres_down-eval-001.json | python -m json.tool
```
