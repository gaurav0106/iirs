from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import TYPE_CHECKING

from .models import EvidenceItem, IIRSState
from .utils import read_json, to_jsonable, utc_now

if TYPE_CHECKING:
    from .pipeline import IIRSPipeline


@dataclass(slots=True)
class EvidenceExpectation:
    description: str
    category: str
    source_type: str | None = None
    query_contains: str | None = None
    text_contains: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: dict[str, object]) -> "EvidenceExpectation":
        return cls(
            description=str(payload["description"]),
            category=str(payload["category"]),
            source_type=str(payload["source_type"]) if payload.get("source_type") else None,
            query_contains=str(payload["query_contains"]) if payload.get("query_contains") else None,
            text_contains=[str(item) for item in payload.get("text_contains", [])],
        )

    def matches(self, item: EvidenceItem) -> bool:
        if item.category != self.category:
            return False

        if self.source_type and not any(
            citation.source_type.lower() == self.source_type.lower()
            for citation in item.citations
        ):
            return False

        if self.query_contains and not any(
            self.query_contains.lower() in citation.query.lower()
            for citation in item.citations
        ):
            return False

        if self.text_contains:
            haystack_parts = [item.summary, item.value]
            haystack_parts.extend(citation.excerpt for citation in item.citations)
            haystack = "\n".join(haystack_parts).lower()
            for fragment in self.text_contains:
                if fragment.lower() not in haystack:
                    return False

        return True


@dataclass(slots=True)
class GroundTruthLabel:
    scenario_name: str
    expected_root_cause: str
    acceptable_root_causes: list[str] = field(default_factory=list)
    required_evidence: list[EvidenceExpectation] = field(default_factory=list)
    required_action_keywords: list[str] = field(default_factory=list)
    required_action_types: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: dict[str, object]) -> "GroundTruthLabel":
        return cls(
            scenario_name=str(payload["scenario_name"]),
            expected_root_cause=str(payload["expected_root_cause"]),
            acceptable_root_causes=[str(item) for item in payload.get("acceptable_root_causes", [])],
            required_evidence=[
                EvidenceExpectation.from_mapping(item)
                for item in payload.get("required_evidence", [])
            ],
            required_action_keywords=[str(item) for item in payload.get("required_action_keywords", [])],
            required_action_types=[str(item) for item in payload.get("required_action_types", [])],
            notes=[str(item) for item in payload.get("notes", [])],
        )

    def matches_root_cause(self, candidate: str) -> bool:
        normalized = candidate.strip().lower()
        allowed = {self.expected_root_cause.strip().lower()}
        allowed.update(item.strip().lower() for item in self.acceptable_root_causes)
        return normalized in allowed


@dataclass(slots=True)
class EvidenceCheckResult:
    description: str
    matched_ids: list[str] = field(default_factory=list)

    @property
    def satisfied(self) -> bool:
        return bool(self.matched_ids)


@dataclass(slots=True)
class ScenarioRunEvaluation:
    scenario_name: str
    expected_root_cause: str
    incident_id: str
    trace_path: str
    run_number: int
    top_titles: list[str]
    top1_correct: bool
    top3_correct: bool
    evidence_checks: list[EvidenceCheckResult] = field(default_factory=list)
    matched_action_keywords: list[str] = field(default_factory=list)
    missing_action_keywords: list[str] = field(default_factory=list)
    missing_action_types: list[str] = field(default_factory=list)

    @property
    def missing_evidence_descriptions(self) -> list[str]:
        return [check.description for check in self.evidence_checks if not check.satisfied]

    @property
    def passed(self) -> bool:
        return (
            self.top1_correct
            and self.top3_correct
            and not self.missing_evidence_descriptions
            and not self.missing_action_keywords
            and not self.missing_action_types
        )


@dataclass(slots=True)
class ScenarioEvaluationReport:
    scenario_name: str
    expected_root_cause: str
    runs: list[ScenarioRunEvaluation] = field(default_factory=list)

    @property
    def top1_hits(self) -> int:
        return sum(run.top1_correct for run in self.runs)

    @property
    def top3_hits(self) -> int:
        return sum(run.top3_correct for run in self.runs)

    @property
    def passed_runs(self) -> int:
        return sum(run.passed for run in self.runs)


@dataclass(slots=True)
class EvaluationReport:
    generated_at: str
    telemetry_backend: str
    scenario_reports: list[ScenarioEvaluationReport] = field(default_factory=list)

    @property
    def total_runs(self) -> int:
        return sum(len(report.runs) for report in self.scenario_reports)

    @property
    def top1_hits(self) -> int:
        return sum(report.top1_hits for report in self.scenario_reports)

    @property
    def top3_hits(self) -> int:
        return sum(report.top3_hits for report in self.scenario_reports)

    @property
    def passed_runs(self) -> int:
        return sum(report.passed_runs for report in self.scenario_reports)

    @property
    def top1_accuracy(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return self.top1_hits / self.total_runs

    @property
    def top3_accuracy(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return self.top3_hits / self.total_runs

    @property
    def passed(self) -> bool:
        return self.total_runs > 0 and self.passed_runs == self.total_runs


def load_ground_truth_labels(directory: Path) -> dict[str, GroundTruthLabel]:
    labels: dict[str, GroundTruthLabel] = {}
    for path in sorted(directory.glob("*.json")):
        label = GroundTruthLabel.from_mapping(read_json(path))
        labels[label.scenario_name] = label
    if not labels:
        raise FileNotFoundError(f"No ground-truth labels found in {directory}")
    return labels


def evaluate_state(state: IIRSState, label: GroundTruthLabel, run_number: int) -> ScenarioRunEvaluation:
    brief = state["incident_brief"]
    bundle = state["evidence_bundle"]
    top_titles = [hypothesis.title for hypothesis in brief.probable_root_causes]
    top1_correct = bool(top_titles) and label.matches_root_cause(top_titles[0])
    top3_correct = any(label.matches_root_cause(title) for title in top_titles[:3])

    evidence_checks: list[EvidenceCheckResult] = []
    for expectation in label.required_evidence:
        matched_ids = [
            item.id
            for item in bundle.all_items()
            if expectation.matches(item)
        ]
        evidence_checks.append(
            EvidenceCheckResult(description=expectation.description, matched_ids=matched_ids)
        )

    action_descriptions = [step.description for step in brief.recommended_actions]
    action_types = {step.action_type for step in brief.recommended_actions}
    matched_action_keywords = [
        keyword
        for keyword in label.required_action_keywords
        if any(keyword.lower() in description.lower() for description in action_descriptions)
    ]
    missing_action_keywords = [
        keyword
        for keyword in label.required_action_keywords
        if keyword not in matched_action_keywords
    ]
    missing_action_types = [
        action_type
        for action_type in label.required_action_types
        if action_type not in action_types
    ]

    return ScenarioRunEvaluation(
        scenario_name=label.scenario_name,
        expected_root_cause=label.expected_root_cause,
        incident_id=state["alert"].incident_id,
        trace_path=state["trace_path"],
        run_number=run_number,
        top_titles=top_titles,
        top1_correct=top1_correct,
        top3_correct=top3_correct,
        evidence_checks=evidence_checks,
        matched_action_keywords=matched_action_keywords,
        missing_action_keywords=missing_action_keywords,
        missing_action_types=missing_action_types,
    )


class EvaluationHarness:
    def __init__(self, pipeline: "IIRSPipeline", labels: dict[str, GroundTruthLabel]) -> None:
        self.pipeline = pipeline
        self.labels = labels

    @classmethod
    def from_directory(cls, pipeline: "IIRSPipeline", directory: Path) -> "EvaluationHarness":
        return cls(pipeline, load_ground_truth_labels(directory))

    def evaluate_scenarios(
        self,
        scenario_names: list[str],
        *,
        runs_per_scenario: int = 3,
    ) -> EvaluationReport:
        reports: list[ScenarioEvaluationReport] = []
        for scenario_name in scenario_names:
            if scenario_name not in self.labels:
                raise KeyError(f"No ground-truth label found for scenario {scenario_name!r}")

            label = self.labels[scenario_name]
            runs: list[ScenarioRunEvaluation] = []
            for run_number in range(1, runs_per_scenario + 1):
                alert = self.pipeline.build_alert_for_scenario(scenario_name)
                alert.incident_id = f"{scenario_name}-eval-{run_number:03d}"
                alert.started_at = utc_now()
                state = self.pipeline.run(alert)
                runs.append(evaluate_state(state, label, run_number))
            reports.append(
                ScenarioEvaluationReport(
                    scenario_name=scenario_name,
                    expected_root_cause=label.expected_root_cause,
                    runs=runs,
                )
            )

        return EvaluationReport(
            generated_at=utc_now(),
            telemetry_backend=self.pipeline.settings.telemetry_backend,
            scenario_reports=reports,
        )


def render_evaluation_markdown(report: EvaluationReport) -> str:
    lines = [
        "# IIRS Evaluation",
        "",
        f"- Generated at: `{report.generated_at}`",
        f"- Telemetry backend: `{report.telemetry_backend}`",
        f"- Total runs: `{report.total_runs}`",
        f"- Top-1 accuracy: `{report.top1_hits}/{report.total_runs}` ({report.top1_accuracy:.0%})",
        f"- Top-3 accuracy: `{report.top3_hits}/{report.total_runs}` ({report.top3_accuracy:.0%})",
        f"- Fully passing runs: `{report.passed_runs}/{report.total_runs}`",
    ]

    for scenario_report in report.scenario_reports:
        lines.extend(
            [
                "",
                f"## {scenario_report.scenario_name}",
                f"Expected root cause: `{scenario_report.expected_root_cause}`",
            ]
        )
        for run in scenario_report.runs:
            lines.append(
                f"- Run {run.run_number}: "
                f"{'PASS' if run.passed else 'FAIL'} | "
                f"top1=`{run.top_titles[0] if run.top_titles else 'n/a'}` | "
                f"trace=`{run.trace_path}`"
            )
            if run.missing_evidence_descriptions:
                lines.append("  Missing evidence checks: " + "; ".join(run.missing_evidence_descriptions))
            if run.missing_action_keywords:
                lines.append("  Missing action keywords: " + "; ".join(run.missing_action_keywords))
            if run.missing_action_types:
                lines.append("  Missing action types: " + ", ".join(run.missing_action_types))

    return "\n".join(lines)


def render_evaluation_json(report: EvaluationReport) -> str:
    payload = {
        "generated_at": report.generated_at,
        "telemetry_backend": report.telemetry_backend,
        "total_runs": report.total_runs,
        "top1_hits": report.top1_hits,
        "top3_hits": report.top3_hits,
        "passed_runs": report.passed_runs,
        "top1_accuracy": report.top1_accuracy,
        "top3_accuracy": report.top3_accuracy,
        "passed": report.passed,
        "scenario_reports": report.scenario_reports,
    }
    return json.dumps(to_jsonable(payload), indent=2)
