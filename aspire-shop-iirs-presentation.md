# IIRS Capstone Presentation Outline

## Audience and objective

- **Audience:** capstone evaluators who understand distributed systems but have not read the codebase
- **Grading criteria (non-negotiable coverage):**
  1. Use >= 3 agents per project
  2. Log agent reasoning traces
  3. Include quantitative + qualitative evaluation
  4. Deliver a demo dashboard + report
- **Time limit:** 10-12 minutes main deck, plus backup slides for Q&A
- **Tone:** technical, concise, honest about tradeoffs
- **One-line thesis:** "Aspire Shop gives me realistic failures, the observability stack gives me evidence, and the multi-agent pipeline turns that evidence into a safer, traceable incident brief."

---

## Slide-by-slide outline

### Slide 1 -- IIRS turns microservice failures into traceable incident briefs

**Key takeaway:** this is a multi-agent incident-response system, not a chatbot wrapper

**Content:**
- IIRS runs a four-agent pipeline: Retriever -> Analyst -> Critic -> Planner
- Input: alert payloads, mock scenarios, live chat prompts, or broad health checks
- Output: an Incident Brief with ranked causes, evidence citations, and safety-bounded actions
- Every run produces a JSON trace so the reasoning is inspectable

**Visual:** one-slide architecture overview -- Aspire Shop on the left, observability stack in the middle, four agents on the right, incident brief as output

**Speaker notes:** open by naming the four grading criteria and saying this talk will hit each one. Set expectations: the first two slides cover the testbed and telemetry, the rest are all agents, traces, and evaluation.

**Time:** 1 minute

---

### Slide 2 -- Aspire Shop and the observability stack provide a realistic testbed

**Key takeaway:** Aspire Shop is a real microservice graph with real telemetry, not a toy

**Content:**
- Service graph: frontend -> catalogservice -> PostgreSQL, frontend -> basketservice -> Redis
- Aspire provides orchestration, service discovery, and health checks
- OTLP telemetry from all services -> OTel Collector -> Loki (logs), Prometheus (metrics), Tempo (traces)
- A thin local patch makes the environment reliable on Linux without changing the dependency structure

**Visual:** two-part diagram: (1) service graph with arrows, (2) telemetry flow from services through collector to three backends

**Speaker notes:** keep this fast. The point is grounding -- the rest of the deck needs the audience to know what these services are and where the telemetry goes. Do not linger on OTLP details; say "logs go to Loki, metrics to Prometheus, traces to Tempo" and move on.

**Time:** 1.5 minutes

---

### Slide 3 -- Four specialized agents with explicit contracts (criterion 1)

**Key takeaway:** IIRS is not one monolithic prompt -- it is a staged pipeline where each agent has a narrow role

**Content:**
- `IIRSPipeline` builds an `AgentContext` with telemetry backends, runbooks, and optional LLM client
- Fixed sequence: Retriever -> Analyst -> Critic -> Planner
- Orchestrated via LangGraph (graph-based) or a fallback linear runner
- Each agent appends structured results to shared `IIRSState`
- Shared state is the contract: `AlertPayload` -> `EvidenceBundle` -> `Hypothesis[]` -> `Critique` -> `IncidentBrief`

**Visual:** pipeline flow diagram with state fields added at each stage:
```
Alert -> [Retriever: EvidenceBundle] -> [Analyst: Hypothesis[]] -> [Critic: Critique] -> [Planner: IncidentBrief]
```

**Speaker notes:** this is the "why multi-agent?" slide. The handoff structure makes each stage independently testable, auditable, and replaceable. A single agent could guess a root cause; four agents produce evidence-linked, internally-contested, safety-bounded briefs.

**Time:** 1.5 minutes

---

### Slide 4 -- Retriever grounds the run in evidence before any reasoning happens

**Key takeaway:** the first stage is tool-driven evidence collection, not model speculation

**Content:**
- Retriever makes 8 tool calls per run: error_logs, latency_metrics, error_rate_metrics, failed_traces, slow_traces, runbook, recent_changes, runtime_states
- For live alerts, it expands candidate services instead of assuming a single broken component
- Runtime state checks both Docker containers and local processes
- Unhealthy or missing services trigger targeted runtime log tails
- Execution mode: always `[tooling]` -- no LLM reasoning at this stage

**Visual:** evidence bundle diagram with 6 category icons (logs, metrics, traces, runtime, runbook, changes) and counts from a real run

**Speaker notes:** concrete example: in `postgres_down`, Retriever collects 2 Loki error logs, 2 Prometheus metrics, 2 Tempo traces, 1 runbook match, and 1 change signal. That bundle is what the Analyst reasons over.

**Time:** 1.5 minutes

---

### Slide 5 -- Analyst and Critic turn evidence into contested diagnoses

**Key takeaway:** diagnosis quality comes from evidence-aware ranking plus an internal quality check

**Content:**
- **Analyst** produces 3 ranked hypotheses with confidence scores and supporting/contradicting evidence IDs
  - Deterministic mode: heuristic evidence-shape rules
  - LLM mode: OpenAI Responses API with strict JSON schema output
  - Titles are intentionally constrained: "X dependency outage" vs "service-to-X path degraded"
- **Critic** reviews the hypotheses against the same evidence bundle
  - Emits findings, hallucination risks, missing data, and safety notes
  - Challenges unsupported leaps before the planner acts
  - Keeps approval boundaries visible

**Visual:** small table: evidence pattern -> diagnosis title -> critic challenge

**Speaker notes:** give the concrete example: if Redis is up but basketservice logs show Redis errors, the Analyst says "basketservice to Redis dependency path degraded", not "Redis dependency outage". The Critic would flag if the Analyst overclaimed.

**Time:** 1.5 minutes

---

### Slide 6 -- Planner produces an operator-facing incident brief with safety boundaries

**Key takeaway:** the system's output is not raw diagnosis -- it is an actionable triage plan

**Content:**
- Planner outputs: `brief_title`, `brief_summary`, ranked root causes, ordered action steps, open questions, evidence snapshot
- Actions split into `auto-safe` (read-only checks) and `needs-approval` (restarts, rollbacks, traffic moves)
- Example actions from postgres_down:
  - [auto-safe] Inspect PostgreSQL health, restart history, and readiness probes
  - [auto-safe] Correlate failing traces with first DB connection-refused log lines
  - [needs-approval] Restart or fail over PostgreSQL if the database remains unavailable
- Open questions carry forward the Critic's unresolved data gaps

**Visual:** sample incident brief card with two columns: diagnosis (left) and actions with safety labels (right)

**Speaker notes:** this is where the system becomes useful to an operator. The diagnosis alone is not enough; the user needs next actions with explicit safety boundaries.

**Time:** 1 minute

---

### Slide 7 -- Every agent run is traced and inspectable (criterion 2)

**Key takeaway:** reasoning traces are not an afterthought -- they are a first-class output

**Content:**
- Every pipeline run writes a JSON trace to `traces/`
- Each `AgentRun` record: agent_name, started_at, finished_at, execution_mode, output_summary, tool_calls[]
- Each `ToolCallRecord`: tool_name, arguments, query, evidence_ids, timestamps
- Example trace excerpt:
  ```
  Retriever [tooling]: 8 tool calls -> 2 logs, 2 metrics, 2 traces, 1 runbook, 1 change
  Analyst [deterministic]: top hypothesis "PostgreSQL dependency outage" at 0.93
  Critic [deterministic]: 2 findings, 0 hallucination risks
  Planner [deterministic]: 5 triage actions, final brief produced
  ```
- Traces enable replay, debugging, and evaluation without re-running the pipeline

**Visual:** formatted trace snippet or trace timeline diagram showing 4 agent boxes with tool calls branching from Retriever

**Speaker notes:** the trace format is the same whether the pipeline runs in deterministic or LLM mode. The `execution_mode` field makes it clear which agents used model reasoning and which used heuristics.

**Time:** 1 minute

---

### Slide 8 -- Quantitative and qualitative evaluation against ground truth (criterion 3)

**Key takeaway:** the system is evaluated with measurable metrics, not just "it looks right"

**Content:**

**Quantitative results (deterministic mode, 5 runs x 2 scenarios):**

| Metric | Result |
|--------|--------|
| Top-1 root cause accuracy | 10/10 (100%) |
| Top-3 root cause accuracy | 10/10 (100%) |
| Evidence coverage | All required evidence collected |
| Action keyword match | 100% |
| Fully passing runs | 10/10 |

**Qualitative rubric (6 checks per run, 60 total):**

| Check | Pass rate |
|-------|-----------|
| Top hypothesis cites >= 3 evidence items | 100% |
| Evidence spans >= 3 source types (logs, metrics, traces) | 100% |
| Critic catches >= 1 material caveat | 100% |
| Safety boundary between auto-safe and needs-approval is explicit | 100% |
| Every action is evidence-grounded | 100% |
| Open questions preserve critic's unresolved data gaps | 100% |

**LLM-backed comparison:** 100% top-1 accuracy but 75% qualitative score (action phrasing varies, safety boundaries less consistent)

**Visual:** the two tables above, side by side

**Speaker notes:** the qualitative checks are what justify the multi-agent architecture. A single-agent summarizer could match top-1 accuracy, but it would not naturally produce evidence-linked citations, explicit safety boundaries, or internally contested hypotheses. These checks validate that agent separation provides structural benefits beyond raw accuracy.

**Time:** 1.5 minutes

---

### Slide 9 -- Chainlit and CLI provide the demo interface (criterion 4)

**Key takeaway:** the chat UI and CLI make the pipeline visible and interactive for demos

**Content:**
- **Chainlit** accepts scenario shortcuts, natural language, broad health checks, and alert JSON
  - Displays Retriever -> Analyst -> Critic -> Planner handoffs in sequence
  - Follow-up questions ("why?", "show me more", "then what?") resolve against current state
  - Each chat run writes a unique trace file
- **CLI** supports: `run`, `ask`, `eval`, `verify-live`, `llm-check`
  - `--show-trace` flag prints the agent trace summary inline
  - `eval` command runs the full evaluation harness and outputs markdown or JSON reports
- 62 unit tests + evaluation harness + live signature validation

**Visual:** screenshot of Chainlit showing staged agent outputs, or terminal output of `iirs run --scenario postgres_down --show-trace`

**Speaker notes:** demo one of these live if time permits. The Chainlit UI is valuable because it shows stage-by-stage reasoning instead of hiding the pipeline behind one answer. The CLI eval output is what produced the numbers on the previous slide.

**Time:** 1 minute

---

### Slide 10 -- Strengths, limitations, and future work

**Key takeaway:** the project is credible because it has explicit traces, reproducible evaluation, and clear boundaries on what it does not solve

**Content:**

| Strengths | Current limits |
|-----------|---------------|
| 4 specialized agents with explicit contracts | Only 2 scenarios with ground-truth labels |
| Full reasoning traces per incident | Retriever is deterministic, not model-driven |
| Quantitative + qualitative evaluation harness | Topology reasoning is partial (no dependency graph) |
| Safety boundaries on all recommended actions | LLM mode less structurally predictable |
| Reproducible local demo with real telemetry | Live demos subject to telemetry/model latency |

**Future work:**
- More scenarios (frontend overload, cascading failures, config drift)
- True service dependency graph for topology-aware reasoning
- Streamlit dashboard for visual pipeline traces and evaluation summaries
- Model-driven retriever with grounding constraints

**Speaker notes:** end on credibility, not hype. The system is strongest as a traceable incident-analysis assistant, not as a fully autonomous fixer. The multi-agent separation is the core contribution.

**Time:** 0.5 minutes

---

## Pacing summary

| Slides | Topic | Time |
|--------|-------|------|
| 1 | Hook + thesis | 1 min |
| 2 | Testbed + telemetry | 1.5 min |
| 3-6 | Agents deep dive (the core) | 5.5 min |
| 7 | Reasoning traces | 1 min |
| 8 | Evaluation results | 1.5 min |
| 9 | Demo surface | 1 min |
| 10 | Limitations + future | 0.5 min |
| **Total** | | **12 min** |

**Where to slow down:** Slides 3-6 and 8 -- this is where the grading criteria are directly addressed.
**Where to speed up:** Slide 2 -- the testbed supports the story, it is not the story.
**Best live demo tie-in:** after slide 9, show one Chainlit or CLI run so the architecture and visible behavior line up.
**If under 10 minutes:** merge slides 5 and 6 into one (Analyst+Critic+Planner as a single "reasoning" slide).

---

## Grading criteria coverage map

| Criterion | Primary slide | Supporting slides |
|-----------|--------------|-------------------|
| >= 3 agents | Slide 3 | Slides 4, 5, 6 |
| Log agent reasoning traces | Slide 7 | Slide 9 (CLI --show-trace) |
| Quantitative + qualitative evaluation | Slide 8 | Slide 10 (limitations context) |
| Demo dashboard + report | Slide 9 | Slide 8 (eval report), Slide 7 (trace output) |

---

## Backup / appendix slides

### Appendix A -- OTEL collector routing detail
- `logs -> otlphttp/loki -> Loki:3100`
- `metrics -> prometheus exporter -> Prometheus:9090`
- `traces -> otlp/tempo -> Tempo:3200`
- Collector config: `infra/observability/otel-collector/config.yaml`

### Appendix B -- Evidence source detail
- Loki: `{service_name="X"} |= "error"`
- Prometheus: `histogram_quantile(0.95, ...)` and `sum(rate(http_server_request_duration_seconds_count{...status=~"5.."}[2m]))`
- Tempo: `{status = error}` and `{trace:duration > 1s}`
- Runtime: `docker ps` + `ps aux` for host processes
- Runbooks: keyword-scored markdown files from `runbooks/`

### Appendix C -- LLM contract details
- Analyst, Critic, Planner use OpenAI Responses API with strict JSON schema output
- Follow-up answers use plain text
- Model timeouts and malformed structured output fail cleanly (no silent fallback)
- Deterministic behavior always available when OpenAI is disabled

### Appendix D -- Full evaluation report
- Reference: `submission_artifacts/evaluation_report.md`
- JSON data: `submission_artifacts/eval_deterministic_5runs.json`
