from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .models import AlertPayload, Citation, EvidenceItem, ToolResult
from .scenarios import EvidenceSeed, ScenarioDefinition


class TelemetryBackend(Protocol):
    def get_error_logs(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult: ...

    def get_latency_metrics(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult: ...

    def get_error_rate_metrics(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult: ...

    def get_failed_traces(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult: ...

    def get_slow_traces(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult: ...

    def get_recent_changes(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult: ...


def _build_evidence(
    alert: AlertPayload,
    seed: EvidenceSeed,
    category: str,
    source_type: str,
    source: str,
    query: str,
) -> EvidenceItem:
    citation = Citation(
        id=f"{seed.id}.citation",
        source_type=source_type,
        source=source,
        query=query,
        observed_at=seed.observed_at,
        excerpt=seed.excerpt,
    )
    return EvidenceItem(
        id=seed.id,
        category=category,
        service=alert.service,
        summary=seed.summary,
        value=seed.value,
        citations=[citation],
        metadata=dict(seed.metadata),
    )


class MockTelemetryBackend:
    def get_error_logs(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult:
        query = f'{{service_name="{alert.service}"}} |= "error"'
        items = [
            _build_evidence(alert, seed, "logs", "loki", "mock-loki", query)
            for seed in scenario.logs
        ]
        return ToolResult(query=query, items=items)

    def get_latency_metrics(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult:
        query = (
            "histogram_quantile(0.95, "
            f'sum(rate(http_server_request_duration_seconds_bucket{{service="{alert.service}"}}[5m])) by (le))'
        )
        items = [
            _build_evidence(alert, seed, "metrics", "prometheus", "mock-prometheus", query)
            for seed in scenario.metrics
            if seed.kind == "latency"
        ]
        return ToolResult(query=query, items=items)

    def get_error_rate_metrics(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult:
        query = (
            f'sum(rate(http_server_requests_total{{service="{alert.service}",status=~"5.."}}[5m]))'
        )
        items = [
            _build_evidence(alert, seed, "metrics", "prometheus", "mock-prometheus", query)
            for seed in scenario.metrics
            if seed.kind == "error_rate"
        ]
        return ToolResult(query=query, items=items)

    def get_failed_traces(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult:
        query = f'service.name="{alert.service}" status.code="error"'
        items = [
            _build_evidence(alert, seed, "traces", "tempo", "mock-tempo", query)
            for seed in scenario.traces
            if seed.kind == "failed_trace"
        ]
        return ToolResult(query=query, items=items)

    def get_slow_traces(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult:
        query = f'service.name="{alert.service}" duration>1s'
        items = [
            _build_evidence(alert, seed, "traces", "tempo", "mock-tempo", query)
            for seed in scenario.traces
            if seed.kind == "slow_trace"
        ]
        return ToolResult(query=query, items=items)

    def get_recent_changes(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult:
        query = f"service={alert.service} within={alert.window_minutes}m"
        items = [
            _build_evidence(alert, seed, "change_signals", "change-log", "mock-change-feed", query)
            for seed in scenario.changes
        ]
        return ToolResult(query=query, items=items)


class RunbookStore:
    def __init__(self, runbooks_dir: Path) -> None:
        self.runbooks_dir = runbooks_dir

    def get_runbook(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult:
        topic_terms = [
            term
            for term in scenario.topic.lower().split()
            if term not in {"troubleshooting", "runbook"}
        ]
        candidates: list[EvidenceItem] = []
        for path in sorted(self.runbooks_dir.glob("*.md")):
            content = path.read_text(encoding="utf-8")
            lowered = content.lower()
            score = sum(term in lowered for term in topic_terms)
            if score == 0:
                continue
            query = f'topic="{scenario.topic}"'
            excerpt = content.splitlines()[0].strip()
            citation = Citation(
                id=f"runbook.{path.stem}.citation",
                source_type="runbook",
                source=str(path),
                query=query,
                observed_at=alert.started_at,
                excerpt=excerpt,
            )
            candidates.append(
                EvidenceItem(
                    id=f"runbook.{path.stem}",
                    category="runbook_hits",
                    service=alert.service,
                    summary=f"Runbook match: {path.stem}",
                    value=f"keyword score {score}",
                    citations=[citation],
                    metadata={"path": str(path)},
                )
            )
        return ToolResult(query=f'topic="{scenario.topic}"', items=candidates[:2])
