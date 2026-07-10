const QUESTIONS = [
  {
    id: "q01",
    category: "Core",
    difficulty: "Basic",
    question: "What is IIRS in one crisp sentence?",
    answer: [
      "IIRS is a local incident-response assistant that takes an alert or plain-English incident report, gathers observability evidence, ranks probable root causes, and produces an evidence-cited Incident Brief.",
      "The current implementation is not a full autonomous remediation platform. It is a triage assistant for faster, safer diagnosis."
    ],
    say: "Lead with: evidence-grounded triage assistant for on-call engineers.",
    trap: "Do not call it a production-ready self-healing system. That overclaims the current repo."
  },
  {
    id: "q02",
    category: "Core",
    difficulty: "Intermediate",
    question: "What problem does the project solve?",
    answer: [
      "During incidents, engineers waste time jumping between logs, metrics, traces, runbooks, and recent-change context. IIRS compresses that manual correlation into a structured pipeline.",
      "The expected benefit is faster time-to-triage, better documentation, and lower risk of acting on unsupported guesses."
    ],
    say: "Frame it as reducing cognitive load under incident pressure.",
    trap: "Do not claim it guarantees final root cause. It ranks hypotheses from available evidence."
  },
  {
    id: "q03",
    category: "Architecture",
    difficulty: "Basic",
    question: "What are the four agents and what does each one own?",
    answer: [
      "Retriever gathers evidence from telemetry and runbooks. Analyst turns the evidence into ranked root-cause hypotheses. Critic challenges those hypotheses for missing evidence, hallucination risk, and safety. Planner converts the surviving diagnosis into an Incident Brief with action steps.",
      "That division keeps collection, reasoning, validation, and planning separate."
    ],
    say: "Retriever collects, Analyst ranks, Critic validates, Planner operationalizes.",
    trap: "Do not blur Critic and Planner. Critic checks quality; Planner writes the response plan."
  },
  {
    id: "q04",
    category: "Architecture",
    difficulty: "Intermediate",
    question: "Why use multiple agents instead of a single prompt?",
    answer: [
      "A single prompt can produce a plausible answer, but it mixes evidence collection, diagnosis, validation, and action planning in one opaque step.",
      "The multi-agent design creates explicit handoffs: evidence first, hypotheses second, critique third, plan last. That makes traces easier to audit and evals easier to write."
    ],
    say: "The key reason is separation of concerns, not agent hype.",
    trap: "Avoid saying multi-agent is always better. Here it is useful because each stage has a different contract."
  },
  {
    id: "q05",
    category: "Architecture",
    difficulty: "Intermediate",
    question: "Why is the pipeline linear instead of dynamic or looping?",
    answer: [
      "For the capstone MVP, linear flow is easier to test, explain, and audit. Every run has the same stage order: Retriever -> Analyst -> Critic -> Planner.",
      "Loops can be useful later for active investigation, but they add non-termination risk and make evaluation harder. The Critic gives one validation pass without an open-ended loop."
    ],
    say: "Predictability and evalability were more valuable than dynamic exploration for this slice.",
    trap: "If asked about future work, admit looping could be added once budget limits and stop conditions are defined."
  },
  {
    id: "q06",
    category: "Architecture",
    difficulty: "Intermediate",
    question: "What is the role of LangGraph?",
    answer: [
      "LangGraph models the pipeline as a StateGraph with nodes for the four agents and edges for the fixed sequence.",
      "The repo also has a LinearGraphRunner fallback, so the system still runs if LangGraph is unavailable. That means LangGraph improves orchestration structure, but the business logic is not trapped inside it."
    ],
    say: "LangGraph is preferred orchestration; the fallback proves the pipeline contract is independent.",
    trap: "Do not imply the current graph is complex. It is intentionally simple and linear."
  },
  {
    id: "q07",
    category: "Architecture",
    difficulty: "Intermediate",
    question: "What is stored in IIRSState?",
    answer: [
      "IIRSState carries the alert, EvidenceBundle, hypotheses, critique, triage plan, Incident Brief, conversation messages, agent trace runs, and final trace path.",
      "This shared state is the contract between agents. Each stage adds structured fields instead of passing free-form text only."
    ],
    say: "State is the audit spine of the system.",
    trap: "Do not describe it as just chat history. It is the full incident record."
  },
  {
    id: "q08",
    category: "Agents",
    difficulty: "Intermediate",
    question: "Why is the Retriever deterministic?",
    answer: [
      "The Retriever's job is evidence collection, not interpretation. It calls known backends with parameterized queries for logs, metrics, traces, runbooks, recent changes, runtime state, and runtime log tails.",
      "Making this stage deterministic keeps the evidence foundation reproducible. If an LLM hallucinated a query, downstream reasoning would start from a weak or empty bundle."
    ],
    say: "Keep probabilistic reasoning above a deterministic evidence layer.",
    trap: "Do not apologize for this. It is a deliberate safety tradeoff."
  },
  {
    id: "q09",
    category: "Agents",
    difficulty: "Intermediate",
    question: "What evidence does the Retriever collect?",
    answer: [
      "It collects error logs, latency metrics, error-rate metrics, failed traces, slow traces, runbook hits, recent-change signals, runtime states, and targeted runtime log tails for unhealthy resources.",
      "In live broad diagnosis, it expands candidate services across frontend, catalogservice, and basketservice instead of assuming only one service is affected."
    ],
    say: "Logs, metrics, traces, runbooks, changes, and runtime state.",
    trap: "The current change feed is limited. Say 'recent-change signals' rather than pretending there is a full deployment database."
  },
  {
    id: "q10",
    category: "Agents",
    difficulty: "Intermediate",
    question: "What is an EvidenceBundle?",
    answer: [
      "EvidenceBundle is the structured collection of all evidence items gathered before reasoning. It separates runtime_states, logs, metrics, traces, runbook_hits, and change_signals.",
      "Each EvidenceItem has an ID, category, service, summary, value, citations, and metadata. Later agents cite these IDs so claims remain traceable."
    ],
    say: "EvidenceBundle is where raw observability becomes citeable incident context.",
    trap: "Do not say the LLM reads Grafana directly. It reads the curated bundle."
  },
  {
    id: "q11",
    category: "Agents",
    difficulty: "Advanced",
    question: "How does the Analyst rank root causes?",
    answer: [
      "The Analyst receives the alert and EvidenceBundle, then returns up to three hypotheses with title, confidence, supporting evidence IDs, contradicting evidence IDs, and next checks.",
      "The prompt prefers direct runtime-state or dependency failures over vague downstream symptoms, and normalizes common titles like PostgreSQL dependency outage, Redis dependency outage, Multiple service outages in Aspire Shop, and No clear live fault detected."
    ],
    say: "Ranking is evidence-first and conservative, not free-form speculation.",
    trap: "Confidence is not calibrated probability yet. It is a relative confidence score from the current evidence and prompt."
  },
  {
    id: "q12",
    category: "Agents",
    difficulty: "Advanced",
    question: "How should you explain confidence scores?",
    answer: [
      "A confidence score reflects how strongly the available evidence supports a hypothesis relative to alternatives. Direct runtime proof plus matching logs, metrics, or traces justifies higher confidence.",
      "It is not yet statistically calibrated against a large labeled incident dataset. A production version should calibrate these scores using historical incidents."
    ],
    say: "Use 'confidence ranking' rather than 'ground-truth probability'.",
    trap: "A Data Science HOD may challenge calibration. Admit it and name calibration as future work."
  },
  {
    id: "q13",
    category: "Agents",
    difficulty: "Intermediate",
    question: "What does the Critic add after the Analyst?",
    answer: [
      "The Critic checks whether hypotheses are backed by relevant evidence, calls out hallucination risk, lists missing data, and makes safety notes explicit.",
      "It is a quality-control stage. It does not collect new data in the current design; it validates the reasoning against the same evidence bundle."
    ],
    say: "Critic reduces overconfidence and unsupported causal leaps.",
    trap: "Do not say Critic magically proves the diagnosis. It flags weaknesses; it cannot create missing evidence."
  },
  {
    id: "q14",
    category: "Agents",
    difficulty: "Intermediate",
    question: "What does the Planner produce?",
    answer: [
      "Planner produces the final Incident Brief: title, summary, ranked root causes, recommended actions, open questions, and an evidence snapshot.",
      "Recommended actions are split into auto-safe read-only checks and needs-approval remediation steps such as restart, failover, rollback, reconfiguration, or traffic changes."
    ],
    say: "Diagnosis becomes an operator-ready brief with safety boundaries.",
    trap: "Do not claim the Planner executes remediation today. It recommends and gates."
  },
  {
    id: "q15",
    category: "Safety",
    difficulty: "Intermediate",
    question: "What is the difference between auto-safe and needs-approval actions?",
    answer: [
      "Auto-safe actions are read-only or validation steps: inspect health, check logs, correlate traces, validate recovery signals.",
      "Needs-approval actions change state: restart, fail over, roll back, reconfigure, redeploy, or change traffic. The eval suite explicitly checks that mutating actions are approval-gated."
    ],
    say: "Read-only checks are auto-safe; state-changing actions require a human.",
    trap: "If an answer says restart is auto-safe, correct it. The current code marks restarts as needs-approval."
  },
  {
    id: "q16",
    category: "Safety",
    difficulty: "Advanced",
    question: "What prevents hallucinated root causes?",
    answer: [
      "There are four defenses: deterministic retrieval, evidence IDs that the Analyst must cite, Critic validation against the same evidence, and Planner instructions not to introduce a new diagnosis.",
      "The OpenAI wrapper also requires structured JSON output for Analyst, Critic, and Planner. Invalid structured output or timeout raises an error instead of silently falling back to a weak answer."
    ],
    say: "The core defense is citation pressure plus explicit validation.",
    trap: "Do not say hallucinations are impossible. Say the design reduces and exposes them."
  },
  {
    id: "q17",
    category: "Safety",
    difficulty: "Advanced",
    question: "What happens if the LLM times out or returns invalid JSON?",
    answer: [
      "The OpenAI client raises OpenAIRequestError. The system does not silently accept malformed model output.",
      "That is safer for incidents because an explicit failure is less dangerous than a confident but unsupported diagnosis."
    ],
    say: "Fail visible beats fail misleading.",
    trap: "Do not promise availability of the model-backed path without API key and network."
  },
  {
    id: "q18",
    category: "Safety",
    difficulty: "Intermediate",
    question: "Why not automatically execute remediations?",
    answer: [
      "The current project is proving evidence-grounded diagnosis and safe triage, not autonomous actuation. Giving an LLM write access to infrastructure before trust and eval coverage exist would be risky.",
      "A future Executor agent could run whitelisted auto-safe commands, but state-changing actions should still require explicit approval."
    ],
    say: "The trust boundary is intentional: assist first, automate later.",
    trap: "Do not let 'auto-safe' sound like 'auto-execute'. In this repo it means safe recommendation category."
  },
  {
    id: "q19",
    category: "Observability",
    difficulty: "Basic",
    question: "What is PLT?",
    answer: [
      "PLT means Prometheus, Loki, and Tempo. Prometheus provides metrics, Loki provides logs, and Tempo provides distributed traces.",
      "Together they cover the main observability signals IIRS needs for incident triage."
    ],
    say: "Metrics tell what changed, logs explain errors, traces show request path causality.",
    trap: "Do not say PLT is a standard acronym everywhere. It is the shorthand used in this project."
  },
  {
    id: "q20",
    category: "Observability",
    difficulty: "Intermediate",
    question: "What is the OpenTelemetry Collector doing?",
    answer: [
      "Aspire Shop emits telemetry using OTLP to the OpenTelemetry Collector. The Collector centralizes telemetry intake and forwards data to the local observability stack.",
      "This decouples the application from backend-specific wiring and makes the architecture closer to production observability practice."
    ],
    say: "The Collector is the vendor-neutral telemetry gateway.",
    trap: "Do not imply the Collector does root-cause analysis. It only moves and processes telemetry."
  },
  {
    id: "q21",
    category: "Observability",
    difficulty: "Intermediate",
    question: "What is Aspire Shop and why use it?",
    answer: [
      "Aspire Shop is a .NET microservices e-commerce demo used as the system under observation. It gives realistic dependencies without the scope of a full production system.",
      "The important services are frontend, catalogservice, basketservice, PostgreSQL/catalogdb, and Redis/basketcache. Catalog depends on PostgreSQL; basket and checkout depend on Redis."
    ],
    say: "Realistic enough to show distributed failure modes, bounded enough for a capstone demo.",
    trap: "Do not call it the team's business product. It is the monitored testbed."
  },
  {
    id: "q22",
    category: "Observability",
    difficulty: "Intermediate",
    question: "How does fault injection work?",
    answer: [
      "The repo includes scripts/inject_aspire_fault.sh to stop and start PostgreSQL or Redis containers. Stopping PostgreSQL simulates catalog database failure; stopping Redis simulates cart/cache failure.",
      "After injecting a fault, the user exercises Aspire Shop to generate failed requests, then IIRS retrieves evidence from the PLT stack or fixture profiles."
    ],
    say: "Fault injection gives reproducible incidents for demo and validation.",
    trap: "Do not say it covers every production failure class. Current live profiles focus on PostgreSQL and Redis."
  },
  {
    id: "q23",
    category: "Observability",
    difficulty: "Advanced",
    question: "How does IIRS handle conflicting signals, such as service up but errors present?",
    answer: [
      "It treats conflict as diagnostic signal. If runtime state says a service is running but logs and traces show dependency-specific failures, the top hypothesis can become a dependency path degraded rather than a hard outage.",
      "The Critic should then surface the mixed evidence and avoid overclaiming that the dependency is fully down."
    ],
    say: "Running process does not equal healthy dependency path.",
    trap: "Do not flatten all errors into 'service down'. That is exactly the mistake the design tries to avoid."
  },
  {
    id: "q24",
    category: "Observability",
    difficulty: "Advanced",
    question: "What happens if a telemetry backend is unavailable?",
    answer: [
      "The PLT backend has explicit configuration and request errors. The safer behavior is to fail visibly when required telemetry cannot be reached rather than produce a confident brief from missing evidence.",
      "The offline iirs eval command uses its own fixed evaluation telemetry backend. An alert JSON file by itself contains alert metadata, not replacement logs, metrics, or traces, so a normal CLI incident run still needs the configured PLT backend."
    ],
    say: "Partial evidence should reduce confidence or stop the run, not create fake certainty.",
    trap: "Do not call an alert fixture an offline telemetry bundle. The current offline evidence path belongs to iirs eval."
  },
  {
    id: "q25",
    category: "LLM",
    difficulty: "Intermediate",
    question: "Why use OpenAI instead of a local model?",
    answer: [
      "OpenAI was chosen for reliable structured output, strong reasoning on text-heavy incident context, and fast capstone iteration.",
      "The repo isolates model calls behind ReasoningClient and supports an OpenAI-compatible base URL, so another provider could be swapped in later."
    ],
    say: "Provider choice is pragmatic; architecture keeps the boundary narrow.",
    trap: "Do not make a vendor-lock-in argument you cannot defend. Point to src/iirs/llm.py as the boundary."
  },
  {
    id: "q26",
    category: "LLM",
    difficulty: "Advanced",
    question: "Which stages use the LLM?",
    answer: [
      "Retriever is tooling-only. Analyst, Critic, Planner, and follow-up answers can use OpenAI when configured.",
      "There are deterministic paths when OpenAI is disabled, and the Analyst is forced deterministic for live health checks or concrete runtime outages. A model timeout is not a fallback trigger: model-backed runs fail visibly instead of silently changing modes."
    ],
    say: "LLM for reasoning and language; deterministic code for collection and obvious runtime cases.",
    trap: "Do not say the whole pipeline is LLM-based or that model errors silently fall back. Both claims are false in the current code."
  },
  {
    id: "q27",
    category: "LLM",
    difficulty: "Intermediate",
    question: "What does IIRS_OPENAI_REASONING_EFFORT=low mean?",
    answer: [
      "It configures supported OpenAI reasoning models to spend a smaller reasoning budget. That reduces latency and cost for the local demo path.",
      "For high-severity production use, you might raise effort, but only after measuring quality, latency, and cost tradeoffs."
    ],
    say: "Low is a demo default, not a claim that all incidents need minimal reasoning.",
    trap: "Do not talk about hidden chain-of-thought. Discuss operational tradeoffs: latency, cost, quality."
  },
  {
    id: "q28",
    category: "LLM",
    difficulty: "Advanced",
    question: "How do follow-up questions work?",
    answer: [
      "After a run, Chainlit keeps the last incident state in session. Follow-up detection routes short questions like 'why?' or explicit questions about evidence, root cause, confidence, or plan to pipeline.follow_up().",
      "The follow-up model receives the current incident context, recent messages, hypotheses, critique, brief, evidence bundle, and trace summaries. It answers without rerunning the full pipeline."
    ],
    say: "Follow-ups are contextual reads over saved state.",
    trap: "Do not claim follow-ups fetch fresh telemetry. They answer from the last state unless a new incident run starts."
  },
  {
    id: "q29",
    category: "Evaluation",
    difficulty: "Intermediate",
    question: "What does iirs eval test?",
    answer: [
      "It runs offline regression suites. The routing suite checks message classification. The pipeline suite checks fixture and eval-telemetry incidents for top diagnosis, evidence citations, trace completeness, trace writing, auto-safe actions, and approval boundaries.",
      "The deterministic eval run currently passes 2/2 suites, 9/9 cases, and 46/46 checks."
    ],
    say: "iirs eval turns prompt and routing changes into regression tests.",
    trap: "The published 46/46 result is the deterministic baseline. Do not present it as model accuracy."
  },
  {
    id: "q30",
    category: "Evaluation",
    difficulty: "Intermediate",
    question: "What is the difference between iirs eval and iirs verify-live?",
    answer: [
      "iirs eval tests reasoning behavior on reproducible offline cases. It is fast, deterministic when OpenAI is disabled, and good for catching prompt, routing, and safety regressions.",
      "iirs verify-live checks whether the live Prometheus, Loki, and Tempo stack contains expected signals after a real fault is injected. It validates telemetry availability, not reasoning quality."
    ],
    say: "Eval checks thinking; verify-live checks wiring.",
    trap: "Do not merge the two. They answer different reliability questions."
  },
  {
    id: "q31",
    category: "Evaluation",
    difficulty: "Advanced",
    question: "How would you evaluate this as a data science system?",
    answer: [
      "Define labeled incident cases with ground-truth root cause and acceptable evidence. Measure top-1 accuracy, top-3 recall, evidence citation precision, unsafe-action violation rate, time-to-triage, and answer helpfulness from human review.",
      "Also track calibration: when the system says 0.8 confidence, incidents in that bucket should be correct about 80 percent of the time over a large corpus."
    ],
    say: "Go beyond pass/fail. Name ranking, citation, safety, latency, and calibration metrics.",
    trap: "Current corpus is small. Do not pretend it is statistically sufficient."
  },
  {
    id: "q32",
    category: "Evaluation",
    difficulty: "Advanced",
    question: "What baselines would you compare against?",
    answer: [
      "Compare against manual on-call triage, a single-agent LLM prompt, deterministic rules only, and maybe a retrieval-plus-summary pipeline without Critic.",
      "Useful measurements are time to identify the top root cause, correctness of the ranked diagnosis, number of unsupported claims, and safety-boundary violations."
    ],
    say: "The strongest baseline is single-agent LLM plus the same evidence bundle.",
    trap: "Do not compare against an empty baseline only. A professor will ask why the four-agent design matters."
  },
  {
    id: "q33",
    category: "Evaluation",
    difficulty: "Advanced",
    question: "How do you know the system is doing causal diagnosis and not just correlation?",
    answer: [
      "Today it is mostly evidence-supported hypothesis ranking, not full causal inference. It uses service dependencies, runtime state, traces, and temporal evidence to prefer plausible causes over symptoms.",
      "A stronger production version would add an explicit dependency graph, intervention data from fault injection, counterfactual checks, and historical incident labels to separate cause from correlation."
    ],
    say: "Be honest: it is causal triage, not formal causal discovery.",
    trap: "Do not overclaim causality. The HOD may press this hard."
  },
  {
    id: "q34",
    category: "Evaluation",
    difficulty: "Intermediate",
    question: "What does trace completeness mean in the eval suite?",
    answer: [
      "The pipeline eval expects four AgentRun records, one for each stage. It also checks that a trace JSON file is written.",
      "This matters because auditability is part of the project claim: a user should be able to inspect what each stage did after the run."
    ],
    say: "If trace completeness fails, the system may still answer, but it loses auditability.",
    trap: "Do not treat traces as a cosmetic feature. They are central evidence for evaluation and postmortem review."
  },
  {
    id: "q35",
    category: "Demo",
    difficulty: "Intermediate",
    question: "What is the safest demo path?",
    answer: [
      "Start with the saved model-backed result captures and run deterministic iirs eval. Those two artifacts demonstrate the result shape and the reproducible regression baseline without depending on live infrastructure.",
      "Then show Chainlit or a CLI incident run only after PLT and Aspire Shop are healthy. An alert fixture supplies alert metadata, but the normal run still retrieves telemetry from the configured backend."
    ],
    say: "Saved proof first, deterministic eval second, live stack only when already verified.",
    trap: "Do not call iirs run with an alert file a fully offline fallback. It still needs telemetry."
  },
  {
    id: "q36",
    category: "Demo",
    difficulty: "Intermediate",
    question: "What should you show if asked for proof that the system is not just a chatbot?",
    answer: [
      "Show the trace text: Retriever [tooling] with concrete tool calls, Analyst with a top hypothesis, Critic with safety checks, and Planner with action categories.",
      "Also show the JSON trace file under traces/ and the eval report proving the same cases are regression-tested."
    ],
    say: "The strongest proof is tool calls, evidence IDs, traces, and evals.",
    trap: "A polished chat answer alone is weak evidence. Always anchor it to trace artifacts."
  },
  {
    id: "q37",
    category: "Demo",
    difficulty: "Intermediate",
    question: "What commands should you know cold?",
    answer: [
      "Use ./scripts/run_demo_stack.sh start for the full demo stack, iirs run --alert-file fixtures/alerts/postgres_down.json --show-trace for fixture CLI, iirs eval for regression eval, iirs verify-live --profile postgres_down for live signal validation, and ./scripts/inject_aspire_fault.sh stop postgres or stop redis for fault injection.",
      "Also know iirs llm-check for the OpenAI path and IIRS_USE_OPENAI_AGENTS=false for deterministic local runs."
    ],
    say: "Know the happy path and the fallback path.",
    trap: "Do not debug command syntax live from memory. Keep the commands visible."
  },
  {
    id: "q38",
    category: "Limitations",
    difficulty: "Intermediate",
    question: "What are the current limitations?",
    answer: [
      "The eval corpus is small, mainly PostgreSQL and Redis incidents plus routing cases. Aspire Shop is fetched from upstream rather than fully vendored. Retriever is deterministic and follows fixed query logic. There is no true service dependency graph yet.",
      "The system is reactive: it investigates when given an alert or prompt; it is not a continuous anomaly detector."
    ],
    say: "Own the limits clearly. That makes the rest more credible.",
    trap: "Do not call limitations 'minor'. They are real scope boundaries."
  },
  {
    id: "q39",
    category: "Limitations",
    difficulty: "Advanced",
    question: "What would you build next for production readiness?",
    answer: [
      "First expand the labeled incident corpus: more services, cascading failures, partial degradations, noisy telemetry, and false alarms. Then add a real service dependency graph and richer change feeds from CI/CD.",
      "After that, add calibrated confidence, human review workflows, stronger auth/audit controls, and only then consider a tightly constrained Executor for approved actions."
    ],
    say: "More data and stronger evaluation come before more autonomy.",
    trap: "Do not jump straight to auto-remediation. That is the wrong next step for safety."
  },
  {
    id: "q40",
    category: "Limitations",
    difficulty: "Advanced",
    question: "If the professor asks whether this is Data Science or just software engineering, what do you say?",
    answer: [
      "The implementation is software-heavy because incident response needs reliable systems integration. The data science part is the evidence-grounded ranking problem: retrieving multi-modal telemetry, scoring root-cause hypotheses, evaluating ranked outputs, measuring citation quality, and calibrating confidence.",
      "The honest answer is that the current capstone has the scaffold for DS evaluation, and the next step is expanding the labeled incident dataset so accuracy, calibration, and ablation studies become statistically meaningful."
    ],
    say: "Software builds the pipeline; data science validates the ranking and confidence behavior.",
    trap: "Do not pretend the current nine eval cases are enough for a research-grade DS conclusion."
  },
  {
    id: "q41",
    category: "Architecture",
    difficulty: "Advanced",
    question: "How would a true service dependency graph improve IIRS?",
    answer: [
      "A dependency graph would make upstream/downstream reasoning explicit. Instead of relying on fixed service families and text patterns, IIRS could trace impact paths like frontend -> catalogservice -> PostgreSQL or frontend -> basketservice -> Redis.",
      "That would help distinguish root causes from symptoms, rank cascading failures better, and decide which telemetry to fetch next."
    ],
    say: "A dependency graph would turn implicit topology into a first-class feature.",
    trap: "Do not claim the current repo has a full graph. It has scoped service/dependency rules, not a general graph model."
  },
  {
    id: "q42",
    category: "Evaluation",
    difficulty: "Advanced",
    question: "How would you prove the Critic stage is actually useful?",
    answer: [
      "Run an ablation study: same incidents, same evidence, with and without Critic. Compare unsupported-claim rate, unsafe-action violations, confidence overstatement, and final top-1/top-3 diagnosis quality.",
      "The Critic is justified only if it measurably reduces hallucination or unsafe recommendations without hurting correct diagnoses too much."
    ],
    say: "Use ablation, not intuition, to defend the extra agent.",
    trap: "Do not say Critic is useful just because it sounds rigorous."
  },
  {
    id: "q43",
    category: "Evaluation",
    difficulty: "Advanced",
    question: "How would you evaluate evidence citation quality?",
    answer: [
      "Measure citation precision and coverage. Precision asks whether cited evidence really supports the claim. Coverage asks whether the answer cites enough of the decisive evidence, not just one convenient log line.",
      "A human rubric can label each citation as supporting, contradicting, irrelevant, or stale. Over time this can become a supervised eval set."
    ],
    say: "Citation presence is not enough; citation relevance has to be scored.",
    trap: "Do not equate having evidence IDs with being correct. Bad citations can still exist."
  },
  {
    id: "q44",
    category: "Evaluation",
    difficulty: "Advanced",
    question: "How would you calibrate the confidence scores?",
    answer: [
      "Collect many labeled incidents, bucket predictions by confidence, and compare predicted confidence with empirical correctness. Reliability diagrams, expected calibration error, and Brier score would expose overconfidence.",
      "If the model says 0.8 confidence, roughly 80 percent of similar cases should be correct. The current project does not yet have enough labeled cases for that."
    ],
    say: "Confidence needs calibration data; today it is a useful ranking signal, not a calibrated probability.",
    trap: "Do not defend confidence scores as statistically calibrated without a dataset."
  },
  {
    id: "q45",
    category: "Observability",
    difficulty: "Advanced",
    question: "Why do you need traces if you already have logs and metrics?",
    answer: [
      "Metrics show aggregate symptoms and logs show local errors, but traces connect one user request across service boundaries. That matters when frontend errors are caused by catalogservice waiting on PostgreSQL or basketservice waiting on Redis.",
      "Traces help establish the failure path, not just the fact that something failed."
    ],
    say: "Metrics say where pain appears; traces show the request path that produced it.",
    trap: "Do not say traces are always available or complete. Missing traces are a real caveat the Critic should surface."
  },
  {
    id: "q46",
    category: "Safety",
    difficulty: "Advanced",
    question: "If you added an Executor agent, what controls would it need?",
    answer: [
      "It would need a strict allowlist, dry-run mode, typed action schemas, blast-radius limits, approval workflow, audit logs, and rollback awareness.",
      "It should execute only narrow read-only or pre-approved low-risk actions at first. Anything touching restarts, failover, rollback, config, data, or traffic should remain human-approved."
    ],
    say: "Executor comes after trust, not before it.",
    trap: "Do not propose broad shell access or arbitrary Kubernetes commands. That is unsafe."
  },
  {
    id: "q47",
    category: "LLM",
    difficulty: "Advanced",
    question: "What are the limits of structured JSON output?",
    answer: [
      "Structured output guarantees shape, not truth. A valid JSON hypothesis can still be wrong, overconfident, or cite weak evidence.",
      "That is why IIRS also validates evidence IDs, uses a Critic stage, gates actions, and evaluates behavior with fixtures and rubrics."
    ],
    say: "Schema compliance is necessary plumbing, not correctness.",
    trap: "Do not present JSON schemas as a hallucination cure."
  },
  {
    id: "q48",
    category: "Architecture",
    difficulty: "Intermediate",
    question: "Why does runtime state get so much priority in live diagnosis?",
    answer: [
      "Runtime state is often the clearest live signal for hard outages: a missing, exited, unhealthy, or restarting container is stronger evidence than a stale log line.",
      "The code deliberately forces deterministic live analysis for concrete runtime outages like PostgreSQL down, Redis down, multiple outages, or a service unavailable."
    ],
    say: "Fresh runtime state prevents stale telemetry from dominating the diagnosis.",
    trap: "Do not ignore logs and traces. Runtime state is prioritized, not used alone."
  },
  {
    id: "q49",
    category: "Evaluation",
    difficulty: "Advanced",
    question: "How would you create ground truth for incident evaluation?",
    answer: [
      "For injected faults, ground truth comes from the known intervention: for example stopping PostgreSQL or Redis at a known time. For real incidents, ground truth should come from postmortems, operator labels, deployment records, and verified remediation outcomes.",
      "The label should include root cause, affected service, time window, decisive evidence, and unsafe actions to avoid."
    ],
    say: "Ground truth is more than the root-cause label; it includes evidence and safety expectations.",
    trap: "Do not train or score only on alert text. The real target is evidence-grounded incident reasoning."
  },
  {
    id: "q50",
    category: "Limitations",
    difficulty: "Advanced",
    question: "How would IIRS handle noisy or stale telemetry?",
    answer: [
      "Today it handles some of this by using time windows, runtime state, health-check mode, and conservative lower-ranked hypotheses when evidence is weak.",
      "A stronger version would tag evidence freshness, downweight stale signals, compare against baselines, and require corroboration across channels before high confidence."
    ],
    say: "Freshness and corroboration are the answer to noisy telemetry.",
    trap: "Do not treat every log error as current root-cause evidence."
  },
  {
    id: "q51",
    category: "Architecture",
    difficulty: "Advanced",
    question: "How do you distinguish root cause from blast radius?",
    answer: [
      "Look for the earliest and most upstream failing resource that explains downstream symptoms. If PostgreSQL is down, catalogservice and frontend may show errors, but they are likely blast radius rather than root cause.",
      "A dependency graph, timestamps, traces, and runtime state together make this distinction stronger."
    ],
    say: "The root cause explains the blast radius; the blast radius should not outrank the cause.",
    trap: "Do not rank frontend first just because users see the frontend failure."
  },
  {
    id: "q52",
    category: "LLM",
    difficulty: "Advanced",
    question: "What prompt-injection or runbook-poisoning risks exist?",
    answer: [
      "Runbooks and logs are untrusted text. A malicious log line or poisoned runbook could try to instruct the model to ignore safety rules or recommend dangerous actions.",
      "Mitigations include treating retrieved text as data, not instructions; system-level safety prompts; schema validation; action allowlists; source trust labels; and human approval for state-changing actions."
    ],
    say: "Operational text must be treated as evidence, not authority.",
    trap: "Do not assume internal logs are safe. Logs can contain attacker-controlled strings."
  },
  {
    id: "q53",
    category: "Observability",
    difficulty: "Intermediate",
    question: "What is the difference between alert fixtures, eval evidence, and live evidence?",
    answer: [
      "Alert fixtures under fixtures/alerts contain reproducible alert payloads such as service, summary, time window, and scenario. They do not contain a complete telemetry EvidenceBundle.",
      "iirs eval supplies fixed synthetic evidence through its evaluation backend. Live runs retrieve current evidence from Prometheus, Loki, Tempo, Docker, and process state. These three inputs test different boundaries."
    ],
    say: "Alert fixture is input metadata; eval backend is offline evidence; PLT is live evidence.",
    trap: "Do not use an alert fixture as proof that the live observability stack or an offline evidence bundle exists."
  },
  {
    id: "q54",
    category: "Evaluation",
    difficulty: "Advanced",
    question: "How would you measure latency and cost tradeoffs?",
    answer: [
      "Track per-stage latency, token usage, model cost, retrieval time, and total time-to-brief. Then compare against diagnosis quality and safety metrics.",
      "For severe incidents, slower but more accurate reasoning may be acceptable. For low-severity alerts, a deterministic or lower-effort path may be enough."
    ],
    say: "Quality, latency, and cost should be evaluated together.",
    trap: "Do not optimize only for speed. Fast wrong triage is harmful."
  },
  {
    id: "q55",
    category: "Agents",
    difficulty: "Advanced",
    question: "What if deterministic retrieval misses the important evidence?",
    answer: [
      "That is the main tradeoff of a deterministic Retriever. It is reproducible but limited to known query templates and service scopes.",
      "Future work could add a bounded query-planning layer: propose extra read-only queries, validate them against an allowlist, cap query budget, and log every query for audit."
    ],
    say: "The right next step is bounded adaptive retrieval, not unconstrained LLM querying.",
    trap: "Do not say deterministic retrieval is perfect. It is reliable but incomplete."
  },
  {
    id: "q56",
    category: "Safety",
    difficulty: "Advanced",
    question: "Why can rollback be dangerous during an incident?",
    answer: [
      "Rollback can discard important fixes, conflict with database migrations, worsen compatibility, or shift traffic onto unhealthy dependencies. In distributed systems, it is not automatically safe.",
      "That is why IIRS classifies rollback as needs-approval and recommends read-only validation before state-changing remediation."
    ],
    say: "Rollback is a mutating action with blast radius, not a harmless button.",
    trap: "Do not list rollback under auto-safe just because it is common incident practice."
  },
  {
    id: "q57",
    category: "Evaluation",
    difficulty: "Advanced",
    question: "Which ranking metrics fit root-cause hypothesis evaluation?",
    answer: [
      "Top-1 accuracy is easy to explain, but top-k recall, mean reciprocal rank, and NDCG are better when multiple plausible hypotheses exist.",
      "If the correct cause is rank 2 with strong evidence, that is not as bad as missing it entirely. Ranking metrics capture that nuance."
    ],
    say: "Use ranking metrics because the system outputs ranked hypotheses, not a single class label.",
    trap: "Do not reduce the evaluation to plain accuracy if the output is top-N."
  },
  {
    id: "q58",
    category: "Demo",
    difficulty: "Intermediate",
    question: "What is your fallback if the live demo stack fails during the presentation?",
    answer: [
      "Use the saved model-backed healthy and failure captures, open their matching JSON trace artifacts if asked, and run deterministic iirs eval to show the 2/2 suites, 9/9 cases, and 46/46 checks.",
      "That still demonstrates the core claims without pretending the alert JSON is offline telemetry. Do not spend the presentation debugging Docker or Aspire startup."
    ],
    say: "Saved model proof plus deterministic eval is the fallback.",
    trap: "Do not run a normal incident command after PLT has failed; it still needs the configured telemetry backend."
  }
];

const CATEGORY_ORDER = ["All", "Core", "Architecture", "Agents", "Safety", "Observability", "LLM", "Evaluation", "Demo", "Limitations"];
const DIFFICULTY_ORDER = ["All", "Basic", "Intermediate", "Advanced"];
const STORAGE_KEY = "iirs-viva-reviewed";

function loadReviewed() {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    return new Set(Array.isArray(value) ? value : []);
  } catch {
    return new Set();
  }
}

const state = {
  category: "All",
  difficulty: "All",
  query: "",
  view: "browse",
  practiceIndex: 0,
  reviewed: loadReviewed()
};

const els = {
  totalCount: document.querySelector("#totalCount"),
  reviewedCount: document.querySelector("#reviewedCount"),
  searchInput: document.querySelector("#searchInput"),
  categoryFilters: document.querySelector("#categoryFilters"),
  difficultyFilters: document.querySelector("#difficultyFilters"),
  expandAll: document.querySelector("#expandAll"),
  collapseAll: document.querySelector("#collapseAll"),
  resetReviewed: document.querySelector("#resetReviewed"),
  resultHeading: document.querySelector("#resultHeading"),
  resultMeta: document.querySelector("#resultMeta"),
  browseView: document.querySelector("#browseView"),
  practiceView: document.querySelector("#practiceView"),
  practiceControls: document.querySelector("#practiceControls"),
  previousQuestion: document.querySelector("#previousQuestion"),
  nextQuestion: document.querySelector("#nextQuestion"),
  randomQuestion: document.querySelector("#randomQuestion"),
  practiceProgress: document.querySelector("#practiceProgress"),
  questionGrid: document.querySelector("#questionGrid"),
  template: document.querySelector("#questionTemplate")
};

function saveReviewed() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...state.reviewed]));
  } catch {
    // Review tracking remains usable for the current page session.
  }
  els.reviewedCount.textContent = state.reviewed.size;
}

function normalize(value) {
  return value.toLowerCase().trim();
}

function questionHaystack(item) {
  return [
    item.category,
    item.difficulty,
    item.question,
    ...item.answer,
    item.say,
    item.trap
  ].join(" ").toLowerCase();
}

function filteredQuestions() {
  const query = normalize(state.query);
  return QUESTIONS.filter((item) => {
    const categoryMatches = state.category === "All" || item.category === state.category;
    const difficultyMatches = state.difficulty === "All" || item.difficulty === state.difficulty;
    const queryMatches = !query || questionHaystack(item).includes(query);
    return categoryMatches && difficultyMatches && queryMatches;
  });
}

function makeFilterButton(label, current, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.className = label === current ? "is-active" : "";
  button.setAttribute("aria-pressed", String(label === current));
  button.addEventListener("click", onClick);
  return button;
}

function renderFilters() {
  els.categoryFilters.replaceChildren(
    ...CATEGORY_ORDER.map((category) =>
      makeFilterButton(category, state.category, () => {
        state.category = category;
        state.practiceIndex = 0;
        render();
      })
    )
  );

  els.difficultyFilters.replaceChildren(
    ...DIFFICULTY_ORDER.map((difficulty) =>
      makeFilterButton(difficulty, state.difficulty, () => {
        state.difficulty = difficulty;
        state.practiceIndex = 0;
        render();
      })
    )
  );
}

function answerMarkup(paragraphs) {
  return paragraphs.map((paragraph) => `<p>${paragraph}</p>`).join("");
}

function renderCards(items) {
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No matching questions. Clear a filter or search for a broader term.";
    els.questionGrid.replaceChildren(empty);
    return;
  }

  const cards = items.map((item) => {
    const node = els.template.content.firstElementChild.cloneNode(true);
    const meta = node.querySelector(".card-meta");
    const title = node.querySelector("h3");
    const answer = node.querySelector(".answer");
    const say = node.querySelector(".say-block");
    const trap = node.querySelector(".trap-block");
    const reviewed = node.querySelector(".review-toggle");

    meta.textContent = `Q${item.id.slice(1)} / ${item.category} / ${item.difficulty}`;
    title.textContent = item.question;
    answer.innerHTML = answerMarkup(item.answer);
    say.innerHTML = `<strong>Say this</strong>${item.say}`;
    trap.innerHTML = `<strong>Watch out</strong>${item.trap}`;

    const isReviewed = state.reviewed.has(item.id);
    reviewed.textContent = isReviewed ? "Reviewed" : "Mark reviewed";
    reviewed.classList.toggle("is-reviewed", isReviewed);
    reviewed.addEventListener("click", () => {
      if (state.reviewed.has(item.id)) {
        state.reviewed.delete(item.id);
      } else {
        state.reviewed.add(item.id);
      }
      saveReviewed();
      render();
    });

    return node;
  });

  els.questionGrid.replaceChildren(...cards);
}

function renderResultText(items) {
  const parts = [];
  if (state.category !== "All") parts.push(state.category);
  if (state.difficulty !== "All") parts.push(state.difficulty);
  const heading = parts.length ? parts.join(" / ") : "All Questions";
  els.resultHeading.textContent = heading;
  els.resultMeta.textContent = state.view === "practice"
    ? `${items.length} questions in the current practice set`
    : `${items.length} of ${QUESTIONS.length} questions shown`;
}

function render() {
  renderFilters();
  const items = filteredQuestions();
  renderResultText(items);
  state.practiceIndex = Math.min(state.practiceIndex, Math.max(items.length - 1, 0));
  const visibleItems = state.view === "practice" && items.length
    ? [items[state.practiceIndex]]
    : items;
  renderCards(visibleItems);
  els.questionGrid.classList.toggle("is-practice", state.view === "practice");
  els.browseView.setAttribute("aria-selected", String(state.view === "browse"));
  els.practiceView.setAttribute("aria-selected", String(state.view === "practice"));
  els.practiceControls.hidden = state.view !== "practice";
  els.practiceProgress.textContent = items.length ? `${state.practiceIndex + 1} / ${items.length}` : "0 / 0";
  els.previousQuestion.disabled = !items.length || state.practiceIndex === 0;
  els.nextQuestion.disabled = !items.length || state.practiceIndex === items.length - 1;
  els.randomQuestion.disabled = items.length < 2;
  els.totalCount.textContent = QUESTIONS.length;
  els.reviewedCount.textContent = state.reviewed.size;
}

els.searchInput.addEventListener("input", (event) => {
  state.query = event.target.value;
  state.practiceIndex = 0;
  render();
});

els.browseView.addEventListener("click", () => {
  state.view = "browse";
  render();
});

els.practiceView.addEventListener("click", () => {
  state.view = "practice";
  state.practiceIndex = 0;
  render();
});

els.previousQuestion.addEventListener("click", () => {
  state.practiceIndex = Math.max(0, state.practiceIndex - 1);
  render();
});

els.nextQuestion.addEventListener("click", () => {
  const count = filteredQuestions().length;
  state.practiceIndex = Math.min(Math.max(count - 1, 0), state.practiceIndex + 1);
  render();
});

els.randomQuestion.addEventListener("click", () => {
  const count = filteredQuestions().length;
  if (count < 2) return;
  let nextIndex = state.practiceIndex;
  while (nextIndex === state.practiceIndex) {
    nextIndex = Math.floor(Math.random() * count);
  }
  state.practiceIndex = nextIndex;
  render();
});

els.expandAll.addEventListener("click", () => {
  document.querySelectorAll(".qa-card details").forEach((details) => {
    details.open = true;
  });
});

els.collapseAll.addEventListener("click", () => {
  document.querySelectorAll(".qa-card details").forEach((details) => {
    details.open = false;
  });
});

els.resetReviewed.addEventListener("click", () => {
  state.reviewed.clear();
  saveReviewed();
  render();
});

document.addEventListener("keydown", (event) => {
  if (state.view !== "practice" || event.target instanceof HTMLInputElement) return;
  if (event.key === "ArrowLeft" && !els.previousQuestion.disabled) {
    state.practiceIndex -= 1;
    render();
  }
  if (event.key === "ArrowRight" && !els.nextQuestion.disabled) {
    state.practiceIndex += 1;
    render();
  }
});

render();
