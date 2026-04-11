from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict


@dataclass(slots=True)
class AlertPayload:
    incident_id: str
    summary: str
    severity: str
    service: str
    environment: str
    started_at: str
    window_minutes: int = 15
    scenario: str | None = None
    labels: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "AlertPayload":
        return cls(
            incident_id=str(payload["incident_id"]),
            summary=str(payload["summary"]),
            severity=str(payload["severity"]),
            service=str(payload["service"]),
            environment=str(payload["environment"]),
            started_at=str(payload["started_at"]),
            window_minutes=int(payload.get("window_minutes", 15)),
            scenario=payload.get("scenario"),
            labels={str(key): str(value) for key, value in payload.get("labels", {}).items()},
        )


@dataclass(slots=True)
class Citation:
    id: str
    source_type: str
    source: str
    query: str
    observed_at: str
    excerpt: str


@dataclass(slots=True)
class EvidenceItem:
    id: str
    category: str
    service: str
    summary: str
    value: str
    citations: list[Citation]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EvidenceBundle:
    runtime_states: list[EvidenceItem] = field(default_factory=list)
    logs: list[EvidenceItem] = field(default_factory=list)
    metrics: list[EvidenceItem] = field(default_factory=list)
    traces: list[EvidenceItem] = field(default_factory=list)
    runbook_hits: list[EvidenceItem] = field(default_factory=list)
    change_signals: list[EvidenceItem] = field(default_factory=list)

    def all_items(self) -> list[EvidenceItem]:
        return [
            *self.runtime_states,
            *self.logs,
            *self.metrics,
            *self.traces,
            *self.runbook_hits,
            *self.change_signals,
        ]

    def by_id(self) -> dict[str, EvidenceItem]:
        return {item.id: item for item in self.all_items()}


@dataclass(slots=True)
class ToolResult:
    query: str
    items: list[EvidenceItem]


@dataclass(slots=True)
class Hypothesis:
    rank: int
    title: str
    confidence: float
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]
    next_checks: list[str]


@dataclass(slots=True)
class CritiqueFinding:
    severity: str
    message: str
    evidence_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Critique:
    relevant_evidence_ids: list[str]
    hallucination_risks: list[str]
    missing_data: list[str]
    safety_notes: list[str]
    findings: list[CritiqueFinding] = field(default_factory=list)


@dataclass(slots=True)
class PlanStep:
    order: int
    description: str
    action_type: str
    rationale: str
    evidence_ids: list[str]


@dataclass(slots=True)
class IncidentBrief:
    title: str
    summary: str
    probable_root_causes: list[Hypothesis]
    recommended_actions: list[PlanStep]
    open_questions: list[str]
    evidence_snapshot: list[str]


@dataclass(slots=True)
class ConversationTurn:
    role: str
    content: str
    created_at: str


@dataclass(slots=True)
class ToolCallRecord:
    tool_name: str
    arguments: dict[str, Any]
    query: str
    evidence_ids: list[str]
    started_at: str
    finished_at: str


@dataclass(slots=True)
class AgentRun:
    agent_name: str
    started_at: str
    finished_at: str
    input_summary: str
    output_summary: str
    execution_mode: str = "deterministic"
    tool_calls: list[ToolCallRecord] = field(default_factory=list)


class IIRSState(TypedDict, total=False):
    alert: AlertPayload
    scenario_name: str
    evidence_bundle: EvidenceBundle
    hypotheses: list[Hypothesis]
    critique: Critique
    triage_plan: list[PlanStep]
    incident_brief: IncidentBrief
    messages: list[ConversationTurn]
    trace_runs: list[AgentRun]
    trace_path: str
