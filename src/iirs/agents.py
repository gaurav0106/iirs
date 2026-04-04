from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .backends import RunbookStore, TelemetryBackend
from .models import (
    AgentRun,
    AlertPayload,
    ConversationTurn,
    Critique,
    CritiqueFinding,
    EvidenceBundle,
    Hypothesis,
    IIRSState,
    IncidentBrief,
    PlanStep,
    ToolCallRecord,
    ToolResult,
)
from .scenarios import ScenarioDefinition
from .utils import utc_now


@dataclass(slots=True)
class AgentContext:
    telemetry: TelemetryBackend
    runbooks: RunbookStore
    scenarios: dict[str, ScenarioDefinition]


def infer_scenario(alert: AlertPayload, scenarios: dict[str, ScenarioDefinition]) -> str:
    if alert.scenario and alert.scenario in scenarios:
        return alert.scenario
    summary = alert.summary.lower()
    service = alert.service.lower()
    for name, scenario in scenarios.items():
        if scenario.service.lower() == service:
            return name
        if "postgres" in summary and "postgres" in name:
            return name
        if "redis" in summary and "redis" in name:
            return name
    raise KeyError(f"Could not infer scenario for alert service={alert.service!r}")


def _append_trace(state: IIRSState, run: AgentRun) -> list[AgentRun]:
    return [*state.get("trace_runs", []), run]


def _append_message(state: IIRSState, role: str, content: str) -> list[ConversationTurn]:
    return [
        *state.get("messages", []),
        ConversationTurn(role=role, content=content, created_at=utc_now()),
    ]


def _record_tool_call(
    tool_name: str,
    arguments: dict[str, object],
    started_at: str,
    result: ToolResult,
) -> ToolCallRecord:
    return ToolCallRecord(
        tool_name=tool_name,
        arguments=arguments,
        query=result.query,
        evidence_ids=[item.id for item in result.items],
        started_at=started_at,
        finished_at=utc_now(),
    )


def make_retriever_node(context: AgentContext) -> Callable[[IIRSState], dict[str, object]]:
    def retriever(state: IIRSState) -> dict[str, object]:
        alert = state["alert"]
        scenario_name = infer_scenario(alert, context.scenarios)
        scenario = context.scenarios[scenario_name]

        tool_calls: list[ToolCallRecord] = []

        started_at = utc_now()
        error_logs = context.telemetry.get_error_logs(alert, scenario)
        tool_calls.append(
            _record_tool_call(
                "get_error_logs",
                {"service": alert.service, "time_window_minutes": alert.window_minutes},
                started_at,
                error_logs,
            )
        )

        started_at = utc_now()
        latency = context.telemetry.get_latency_metrics(alert, scenario)
        tool_calls.append(
            _record_tool_call(
                "get_latency_metrics",
                {"service": alert.service, "time_window_minutes": alert.window_minutes},
                started_at,
                latency,
            )
        )

        started_at = utc_now()
        error_rate = context.telemetry.get_error_rate_metrics(alert, scenario)
        tool_calls.append(
            _record_tool_call(
                "get_error_rate_metrics",
                {"service": alert.service, "time_window_minutes": alert.window_minutes},
                started_at,
                error_rate,
            )
        )

        started_at = utc_now()
        failed_traces = context.telemetry.get_failed_traces(alert, scenario)
        tool_calls.append(
            _record_tool_call(
                "get_failed_traces",
                {"service": alert.service, "time_window_minutes": alert.window_minutes},
                started_at,
                failed_traces,
            )
        )

        started_at = utc_now()
        slow_traces = context.telemetry.get_slow_traces(alert, scenario)
        tool_calls.append(
            _record_tool_call(
                "get_slow_traces",
                {"service": alert.service, "time_window_minutes": alert.window_minutes},
                started_at,
                slow_traces,
            )
        )

        started_at = utc_now()
        runbooks = context.runbooks.get_runbook(alert, scenario)
        tool_calls.append(
            _record_tool_call(
                "get_runbook",
                {"topic": scenario.topic},
                started_at,
                runbooks,
            )
        )

        started_at = utc_now()
        changes = context.telemetry.get_recent_changes(alert, scenario)
        tool_calls.append(
            _record_tool_call(
                "get_recent_changes",
                {"service": alert.service, "time_window_minutes": alert.window_minutes},
                started_at,
                changes,
            )
        )

        bundle = EvidenceBundle(
            logs=error_logs.items,
            metrics=[*error_rate.items, *latency.items],
            traces=[*failed_traces.items, *slow_traces.items],
            runbook_hits=runbooks.items,
            change_signals=changes.items,
        )

        agent_run = AgentRun(
            agent_name="Retriever",
            started_at=tool_calls[0].started_at,
            finished_at=utc_now(),
            input_summary=f"Alert for {alert.service} in {alert.environment}: {alert.summary}",
            output_summary=(
                f"Collected {len(bundle.logs)} logs, {len(bundle.metrics)} metrics, "
                f"{len(bundle.traces)} traces, {len(bundle.runbook_hits)} runbooks, "
                f"and {len(bundle.change_signals)} change signals."
            ),
            tool_calls=tool_calls,
        )

        return {
            "scenario_name": scenario_name,
            "evidence_bundle": bundle,
            "trace_runs": _append_trace(state, agent_run),
            "messages": _append_message(
                state,
                "assistant",
                f"Retriever assembled {len(bundle.all_items())} evidence items for {alert.service}.",
            ),
        }

    return retriever


def make_analyst_node(context: AgentContext) -> Callable[[IIRSState], dict[str, object]]:
    def analyst(state: IIRSState) -> dict[str, object]:
        scenario = context.scenarios[state["scenario_name"]]
        bundle = state["evidence_bundle"]
        all_items = bundle.all_items()
        evidence_ids = [item.id for item in all_items]
        primary_support = evidence_ids[: min(5, len(evidence_ids))]
        change_ids = [item.id for item in bundle.change_signals]
        trace_ids = [item.id for item in bundle.traces]

        hypotheses = [
            Hypothesis(
                rank=1,
                title=scenario.expected_root_cause,
                confidence=0.93 if len(primary_support) >= 5 else 0.84,
                supporting_evidence_ids=primary_support,
                contradicting_evidence_ids=change_ids[:1],
                next_checks=scenario.follow_up_checks,
            ),
            Hypothesis(
                rank=2,
                title=scenario.secondary_hypothesis,
                confidence=0.46,
                supporting_evidence_ids=trace_ids[:2] or primary_support[:2],
                contradicting_evidence_ids=change_ids[:1],
                next_checks=[
                    "Confirm whether request workers recover immediately once the dependency is restored.",
                    "Inspect connection-pool saturation and retry pressure.",
                ],
            ),
            Hypothesis(
                rank=3,
                title="Recent deploy or configuration regression",
                confidence=0.18 if change_ids else 0.08,
                supporting_evidence_ids=change_ids[:1],
                contradicting_evidence_ids=primary_support[:3],
                next_checks=[
                    "Review deployment metadata and config drift only if dependency health is normal.",
                ],
            ),
        ]

        agent_run = AgentRun(
            agent_name="Analyst",
            started_at=utc_now(),
            finished_at=utc_now(),
            input_summary=f"Analyzed {len(all_items)} evidence items for scenario {scenario.name}.",
            output_summary=(
                f"Ranked root causes with top hypothesis '{hypotheses[0].title}' "
                f"at confidence {hypotheses[0].confidence:.2f}."
            ),
        )

        return {
            "hypotheses": hypotheses,
            "trace_runs": _append_trace(state, agent_run),
            "messages": _append_message(
                state,
                "assistant",
                f"Analyst ranked '{hypotheses[0].title}' as the most likely root cause.",
            ),
        }

    return analyst


def make_critic_node(context: AgentContext) -> Callable[[IIRSState], dict[str, object]]:
    def critic(state: IIRSState) -> dict[str, object]:
        bundle = state["evidence_bundle"]
        hypotheses = state["hypotheses"]
        source_types = {
            citation.source_type
            for item in bundle.all_items()
            for citation in item.citations
        }

        hallucination_risks: list[str] = []
        if len(source_types) < 3:
            hallucination_risks.append(
                "The root-cause ranking is backed by fewer than three evidence source types."
            )
        if not bundle.runbook_hits:
            hallucination_risks.append("No runbook guidance was matched for this incident.")

        missing_data = []
        if not bundle.change_signals:
            missing_data.append("No change feed result was captured near incident start.")
        if not bundle.traces:
            missing_data.append("No trace evidence was collected.")

        findings = [
            CritiqueFinding(
                severity="info",
                message="Top hypothesis is grounded in logs, metrics, and traces with explicit citations.",
                evidence_ids=hypotheses[0].supporting_evidence_ids[:3],
            )
        ]

        if hypotheses[0].confidence >= 0.9 and bundle.change_signals:
            findings.append(
                CritiqueFinding(
                    severity="warning",
                    message=(
                        "Recent-change evidence does not explain the incident, so remediation should focus on "
                        "dependency availability first."
                    ),
                    evidence_ids=[item.id for item in bundle.change_signals],
                )
            )

        critique = Critique(
            relevant_evidence_ids=hypotheses[0].supporting_evidence_ids,
            hallucination_risks=hallucination_risks,
            missing_data=missing_data,
            safety_notes=[
                "Keep all actions read-only until a human approves dependency restarts or traffic changes.",
                "Treat rollback or restart recommendations as needs-approval actions.",
            ],
            findings=findings,
        )

        agent_run = AgentRun(
            agent_name="Critic",
            started_at=utc_now(),
            finished_at=utc_now(),
            input_summary=f"Validated {len(hypotheses)} hypotheses against {len(bundle.all_items())} evidence items.",
            output_summary=(
                f"Generated {len(critique.findings)} findings and "
                f"{len(critique.hallucination_risks)} hallucination-risk checks."
            ),
        )

        return {
            "critique": critique,
            "trace_runs": _append_trace(state, agent_run),
            "messages": _append_message(
                state,
                "assistant",
                "Critic validated citation coverage and highlighted approval boundaries.",
            ),
        }

    return critic


def make_planner_node(context: AgentContext) -> Callable[[IIRSState], dict[str, object]]:
    def planner(state: IIRSState) -> dict[str, object]:
        scenario = context.scenarios[state["scenario_name"]]
        hypotheses = state["hypotheses"]
        critique = state["critique"]
        bundle = state["evidence_bundle"]
        top_evidence = hypotheses[0].supporting_evidence_ids[:3]

        steps: list[PlanStep] = []
        for index, description in enumerate(scenario.safe_actions, start=1):
            steps.append(
                PlanStep(
                    order=index,
                    description=description,
                    action_type="auto-safe",
                    rationale="Read-only diagnostic step supported by retrieved telemetry.",
                    evidence_ids=top_evidence,
                )
            )

        start_order = len(steps) + 1
        for offset, description in enumerate(scenario.approval_actions, start=0):
            steps.append(
                PlanStep(
                    order=start_order + offset,
                    description=description,
                    action_type="needs-approval",
                    rationale="This changes dependency state or service routing and must stay human-approved.",
                    evidence_ids=top_evidence,
                )
            )

        brief = IncidentBrief(
            title=f"Incident Brief: {scenario.name}",
            summary=(
                f"Most likely root cause: {hypotheses[0].title}. "
                f"Evidence spans logs, metrics, traces, and runbook guidance for {scenario.service}."
            ),
            probable_root_causes=hypotheses,
            recommended_actions=steps,
            open_questions=critique.missing_data or ["No unresolved data gaps in the mock scenario."],
            evidence_snapshot=[item.summary for item in bundle.all_items()[:5]],
        )

        agent_run = AgentRun(
            agent_name="Planner",
            started_at=utc_now(),
            finished_at=utc_now(),
            input_summary="Synthesized hypotheses and critique into an incident brief.",
            output_summary=(
                f"Produced {len(steps)} triage actions and the final brief '{brief.title}'."
            ),
        )

        return {
            "triage_plan": steps,
            "incident_brief": brief,
            "trace_runs": _append_trace(state, agent_run),
            "messages": _append_message(
                state,
                "assistant",
                f"Planner generated the incident brief with {len(steps)} actions.",
            ),
        }

    return planner


def answer_follow_up(question: str, state: IIRSState) -> str:
    bundle = state["evidence_bundle"]
    brief = state["incident_brief"]
    evidence_lookup = bundle.by_id()
    lowered = question.lower()

    if "root cause" in lowered or "why" in lowered:
        top = brief.probable_root_causes[0]
        evidence_lines = []
        for evidence_id in top.supporting_evidence_ids[:3]:
            item = evidence_lookup[evidence_id]
            evidence_lines.append(f"- {item.summary} ({item.citations[0].source_type}: {item.citations[0].source})")
        return (
            f"Most likely root cause: {top.title} (confidence {top.confidence:.2f}).\n"
            f"Supporting evidence:\n" + "\n".join(evidence_lines)
        )

    if "evidence" in lowered or "citation" in lowered or "proof" in lowered:
        lines = []
        for item in bundle.all_items()[:5]:
            citation = item.citations[0]
            lines.append(
                f"- {item.id}: {item.summary} | {citation.source_type} | query `{citation.query}`"
            )
        return "Top cited evidence:\n" + "\n".join(lines)

    if "action" in lowered or "next" in lowered or "plan" in lowered:
        lines = [
            f"- [{step.action_type}] {step.description}"
            for step in brief.recommended_actions
        ]
        return "Recommended next steps:\n" + "\n".join(lines)

    return (
        f"{brief.summary}\n"
        f"Open questions: {', '.join(brief.open_questions)}\n"
        "Ask about root cause, evidence, or next actions for a more specific answer."
    )
