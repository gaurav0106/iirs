from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable

from .backends import RunbookStore, TelemetryBackend
from .llm import OpenAIRequestError, ReasoningClient
from .models import (
    AgentRun,
    AlertPayload,
    ConversationTurn,
    Critique,
    CritiqueFinding,
    EvidenceBundle,
    EvidenceItem,
    Hypothesis,
    IIRSState,
    IncidentBrief,
    PlanStep,
    ToolCallRecord,
    ToolResult,
)
from .utils import utc_now


@dataclass(slots=True)
class AgentContext:
    telemetry: TelemetryBackend
    runbooks: RunbookStore
    llm: ReasoningClient | None = None


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


def _is_live_diagnosis_alert(alert: AlertPayload) -> bool:
    return alert.labels.get("mode") == "live-diagnosis"


def _is_live_health_check_alert(alert: AlertPayload) -> bool:
    return alert.labels.get("mode") == "live-health-check"


def _is_live_alert(alert: AlertPayload) -> bool:
    return _is_live_diagnosis_alert(alert) or _is_live_health_check_alert(alert)


def _service_label(service: str | None) -> str:
    if not service:
        return "service"
    labels = {
        "catalogservice": "catalogservice",
        "basketservice": "basketservice",
        "frontend": "frontend",
        "aspire-shop": "Aspire Shop",
        "postgres": "postgres",
        "catalogdb": "catalogdb",
        "catalogdbmanager": "catalogdbmanager",
        "basketcache": "basketcache",
        "rediscommander": "rediscommander",
        "pgadmin": "pgadmin",
    }
    return labels.get(service, service)


def _clamp_confidence(value: object, default: float = 0.5) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, numeric))


def _normalize_root_cause_title(title: str) -> str:
    lowered = title.lower()
    if any(term in lowered for term in ("degraded", "timeout", "latency", "path")):
        return title.strip() or "Unknown root cause"
    unavailable_terms = ("missing", "process missing", "unavailable", "down", "exited", "not running")
    for service in ("catalogservice", "basketservice", "frontend"):
        if service in lowered and any(term in lowered for term in unavailable_terms):
            return f"{_service_label(service)} unavailable"
    postgres_terms = ("postgres", "postgresql")
    redis_terms = ("redis",)
    outage_terms = ("outage", "down", "unavailable", "dependency", "connection refused")
    if any(term in lowered for term in postgres_terms) and any(term in lowered for term in outage_terms):
        return "PostgreSQL dependency outage"
    if any(term in lowered for term in redis_terms) and any(term in lowered for term in outage_terms):
        return "Redis dependency outage"
    return title.strip() or "Unknown root cause"


def _filter_evidence_ids(candidate_ids: object, valid_ids: set[str]) -> list[str]:
    values = candidate_ids if isinstance(candidate_ids, list) else []
    result: list[str] = []
    for value in values:
        text = str(value)
        if text in valid_ids and text not in result:
            result.append(text)
    return result


def _normalize_text_list(values: object, fallback: list[str]) -> list[str]:
    if not isinstance(values, list):
        return fallback
    normalized = [str(value).strip() for value in values if str(value).strip()]
    return normalized or fallback


def _generic_follow_up_checks(service: str | None) -> list[str]:
    label = _service_label(service)
    return [
        f"Confirm whether {label} is running and reachable from the Aspire dashboard.",
        f"Check the first error logs and failed traces tied to {label}.",
        "Review recent config or dependency changes only after basic availability is confirmed.",
    ]


def _generic_safe_actions(service: str | None) -> list[str]:
    label = _service_label(service)
    return [
        f"Inspect {label} health, logs, and recent failed requests without changing state.",
        f"Correlate failing traces with the first upstream or dependency errors affecting {label}.",
        f"Validate recovery by checking fresh error-rate and latency signals for {label}.",
    ]


def _generic_approval_actions(service: str | None) -> list[str]:
    label = _service_label(service)
    return [
        f"Restart or re-enable {label} if it is confirmed unavailable.",
        f"Roll back or fix the most likely config or dependency change if the failure is not a simple outage.",
    ]


_SERVICE_DEPENDENCY_FAMILIES: dict[str, tuple[str, ...]] = {
    "catalogservice": ("postgres",),
    "basketservice": ("redis",),
}
_DEPENDENCY_SIGNAL_PATTERNS: dict[str, tuple[str, ...]] = {
    "postgres": (
        "postgres",
        "postgresql",
        "npgsql",
        "entityframework",
        "retrylimitexceededexception",
        "db.connect",
    ),
    "redis": (
        "redis",
        "stackexchange.redis",
        "redisconnectionexception",
        "redistimeoutexception",
        "basketcache",
        "cache timeout",
    ),
}
_DEPENDENCY_OUTAGE_PATTERNS: dict[str, tuple[str, ...]] = {
    "postgres": (
        "connection refused",
        "retrylimitexceededexception",
        "database unavailable",
        "db.connect",
        "postgres down",
    ),
    "redis": (
        "redisconnectionexception",
        "redistimeoutexception",
        "connection refused",
        "cache timeout",
        "redis down",
    ),
}


def _dependency_label(family: str | None) -> str:
    labels = {
        "postgres": "PostgreSQL",
        "redis": "Redis",
    }
    if family is None:
        return "dependency"
    return labels.get(family, _service_label(family))


def _evidence_text(item: EvidenceItem) -> str:
    parts = [item.summary, str(item.value)]
    for citation in item.citations:
        parts.extend([citation.excerpt, citation.query, citation.source_type, citation.source])
    return " ".join(part for part in parts if part).lower()


def _dependency_signal_family(item: EvidenceItem) -> str | None:
    text = _evidence_text(item)
    for family, patterns in _DEPENDENCY_SIGNAL_PATTERNS.items():
        if any(pattern in text for pattern in patterns):
            return family
    return None


def _dependency_signal_items(
    bundle: EvidenceBundle,
    *,
    service: str | None = None,
    family: str | None = None,
) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for item in [*bundle.logs, *bundle.metrics, *bundle.traces]:
        if service is not None and item.service != service:
            continue
        detected_family = _dependency_signal_family(item)
        if detected_family is None:
            continue
        if family is not None and detected_family != family:
            continue
        items.append(item)
    return items


def _runtime_state_for_item(item: EvidenceItem) -> str:
    state = str(item.metadata.get("state", "")).lower()
    if state:
        return state
    text = f"{item.summary} {item.value}".lower()
    if "missing" in text:
        return "missing"
    if "unhealthy" in text:
        return "unhealthy"
    if "exited" in text:
        return "exited"
    if "restarting" in text:
        return "restarting"
    if "created" in text:
        return "created"
    if "running" in text or text.startswith("up "):
        return "running"
    return "unknown"


def _candidate_services_for_alert(
    alert: AlertPayload,
) -> list[str]:
    if alert.service and alert.service not in {"aspire-shop", "shop", "unknown"}:
        return [alert.service]
    return ["frontend", "catalogservice", "basketservice"]


def _matches_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _unique_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _format_name_list(values: list[str]) -> str:
    labels = [_service_label(value) for value in _unique_preserve(values)]
    if not labels:
        return "the affected resources"
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


def _format_plain_list(values: list[str]) -> str:
    items = [value for value in _unique_preserve(values) if value]
    if not items:
        return "the available evidence"
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _runtime_failure_items(bundle: EvidenceBundle, *, include_support: bool = False) -> list[EvidenceItem]:
    failing_states = {"dead", "restarting", "missing", "exited", "unhealthy", "created", "unknown"}
    failures: list[EvidenceItem] = []
    for item in bundle.runtime_states:
        state = str(item.metadata.get("state", "")).lower()
        role = str(item.metadata.get("role", "")).lower()
        if state not in failing_states:
            continue
        if role == "support" and not include_support:
            continue
        failures.append(item)
    return failures


def _running_runtime_items(bundle: EvidenceBundle, *, include_support: bool = True) -> list[EvidenceItem]:
    running_items: list[EvidenceItem] = []
    for item in bundle.runtime_states:
        role = str(item.metadata.get("role", "")).lower()
        if role == "support" and not include_support:
            continue
        if _runtime_state_for_item(item) == "running":
            running_items.append(item)
    return running_items


def _root_runtime_failures(bundle: EvidenceBundle) -> list[EvidenceItem]:
    root_failures: list[EvidenceItem] = []
    for item in _runtime_failure_items(bundle, include_support=False):
        role = str(item.metadata.get("role", "")).lower()
        state = str(item.metadata.get("state", "")).lower()
        if role == "dependency":
            root_failures.append(item)
            continue
        if role == "service" and state in {"dead", "restarting", "missing", "exited", "created", "unknown"}:
            root_failures.append(item)
    return root_failures


def _scoped_alert(alert: AlertPayload, service: str) -> AlertPayload:
    return AlertPayload(
        incident_id=alert.incident_id,
        summary=alert.summary,
        severity=alert.severity,
        service=service,
        environment=alert.environment,
        started_at=alert.started_at,
        window_minutes=alert.window_minutes,
        scenario=alert.scenario,
        labels=dict(alert.labels),
    )


def _runtime_items_for_family(bundle: EvidenceBundle, family: str) -> list[EvidenceItem]:
    return [
        item
        for item in _root_runtime_failures(bundle)
        if str(item.metadata.get("family", "")).lower() == family.lower()
    ]


def _runtime_items_for_family_all(bundle: EvidenceBundle, family: str) -> list[EvidenceItem]:
    return [
        item
        for item in bundle.runtime_states
        if str(item.metadata.get("family", "")).lower() == family.lower()
    ]


def _runtime_items_for_service(bundle: EvidenceBundle, service: str | None) -> list[EvidenceItem]:
    if not service:
        return []
    lowered_service = service.lower()
    return [
        item
        for item in bundle.runtime_states
        if str(item.metadata.get("resource") or item.service).lower() == lowered_service
    ]


def _runtime_issue_items_for_service(bundle: EvidenceBundle, service: str | None) -> list[EvidenceItem]:
    return [
        item
        for item in _runtime_items_for_service(bundle, service)
        if _runtime_state_for_item(item) != "running"
    ]


def _top_dependency_signal_for_service(
    bundle: EvidenceBundle,
    service: str | None,
) -> tuple[str | None, list[EvidenceItem]]:
    if not service:
        return None, []

    candidate_families = list(_SERVICE_DEPENDENCY_FAMILIES.get(service, ()))
    if not candidate_families:
        candidate_families = []
        for item in [*bundle.logs, *bundle.metrics, *bundle.traces]:
            if item.service != service:
                continue
            family = _dependency_signal_family(item)
            if family and family not in candidate_families:
                candidate_families.append(family)

    best_family: str | None = None
    best_items: list[EvidenceItem] = []
    for family in candidate_families:
        items = _dependency_signal_items(bundle, service=service, family=family)
        if len(items) > len(best_items):
            best_family = family
            best_items = items
    return best_family, best_items


def _dependency_path_title(service: str | None, family: str | None) -> str:
    return f"{_service_label(service)} to {_dependency_label(family)} dependency path degraded"


def _dependency_family_from_title(title: str) -> str | None:
    lowered = title.lower()
    if "postgresql" in lowered or "postgres" in lowered:
        return "postgres"
    if "redis" in lowered:
        return "redis"
    return None


def _runtime_resource_names(items: list[EvidenceItem]) -> list[str]:
    return _unique_preserve([str(item.metadata.get("resource") or item.service) for item in items])


def _runtime_state_lines(bundle: EvidenceBundle, *, limit: int = 6) -> list[str]:
    lines: list[str] = []
    for item in bundle.runtime_states[:limit]:
        resource = str(item.metadata.get("resource") or item.service)
        lines.append(f"- {resource}: {item.value}")
    return lines


def _evidence_channel_labels(bundle: EvidenceBundle) -> list[str]:
    labels: list[str] = []
    if bundle.runtime_states:
        labels.append("runtime state")
    if bundle.logs:
        labels.append("logs")
    if bundle.metrics:
        labels.append("metrics")
    if bundle.traces:
        labels.append("traces")
    if bundle.runbook_hits:
        labels.append("runbook guidance")
    if bundle.change_signals:
        labels.append("change signals")
    return labels


def _non_live_summary(bundle: EvidenceBundle, top_title: str, service: str | None) -> str:
    evidence_channels = _evidence_channel_labels(bundle)
    if evidence_channels:
        return (
            f"Most likely root cause: {top_title}. Evidence spans "
            f"{_format_plain_list(evidence_channels)} for {_service_label(service)}."
        )
    return (
        f"Most likely root cause: {top_title}. Evidence is thin, so confirm the first failing log, "
        "metric, or trace before taking state-changing actions."
    )


def _dependency_outage_supported(
    alert: AlertPayload,
    bundle: EvidenceBundle,
    family: str | None,
    items: list[EvidenceItem],
) -> bool:
    if family is None or not items:
        return False
    if _runtime_items_for_family(bundle, family):
        return True
    evidence_text = f" {alert.summary.lower()} " + " ".join(_evidence_text(item) for item in items)
    score = len(items)
    score += sum(1 for pattern in _DEPENDENCY_OUTAGE_PATTERNS.get(family, ()) if pattern in evidence_text)
    return score >= 4


def _live_summary(bundle: EvidenceBundle, top_title: str, dominant_service: str | None) -> str:
    primary_failures = _root_runtime_failures(bundle)
    support_failures = _runtime_failure_items(bundle, include_support=True)
    downstream_unhealthy = [
        item
        for item in _runtime_failure_items(bundle, include_support=False)
        if item not in primary_failures
    ]
    if top_title == "No clear live fault detected":
        running_resources = _runtime_resource_names(_running_runtime_items(bundle, include_support=True))
        family, family_items = _top_dependency_signal_for_service(bundle, dominant_service)
        if running_resources:
            summary = (
                "Current live health check does not show a clear active fault. "
                f"Local runtime state shows {_format_name_list(running_resources)} running."
            )
        else:
            summary = "Current live health check does not show a clear active fault in the available runtime data."
        if family is not None and family_items:
            summary += (
                f" There is still {_dependency_label(family)}-shaped error noise around "
                f"{_service_label(dominant_service)}, but current runtime state does not prove an active outage."
            )
        return summary
    if top_title == "Multiple service outages in Aspire Shop" and primary_failures:
        down_resources = _format_name_list(_runtime_resource_names(primary_failures))
        support_resources = _runtime_resource_names(
            [
                item
                for item in support_failures
                if str(item.metadata.get("role", "")).lower() == "support"
            ]
        )
        summary = f"Most likely root cause: {top_title}. Local runtime state shows {down_resources} exited or unhealthy."
        if downstream_unhealthy:
            summary += f" Downstream services like {_format_name_list(_runtime_resource_names(downstream_unhealthy))} are also unhealthy."
        if support_resources:
            summary += f" Supporting resources like {_format_name_list(support_resources)} are also unhealthy."
        return summary
    if top_title == "PostgreSQL dependency outage":
        postgres_resources = _runtime_resource_names(_runtime_items_for_family(bundle, "postgres")) or ["postgres"]
        return (
            f"Most likely root cause: {top_title}. Local runtime state shows {_format_name_list(postgres_resources)} "
            "exited or unhealthy, and downstream services are failing around database access."
        )
    if top_title == "Redis dependency outage":
        redis_resources = _runtime_resource_names(_runtime_items_for_family(bundle, "redis")) or ["basketcache"]
        return (
            f"Most likely root cause: {top_title}. Local runtime state shows {_format_name_list(redis_resources)} "
            "exited or unhealthy, and downstream basket traffic is failing."
        )
    if top_title.endswith(" unavailable"):
        service_issues = _runtime_issue_items_for_service(bundle, dominant_service)
        if service_issues:
            affected_resources = _runtime_resource_names(service_issues) or [_service_label(dominant_service)]
            return (
                f"Most likely root cause: {top_title}. Local runtime state shows "
                f"{_format_name_list(affected_resources)} is missing or not healthy, so the service itself is the "
                "closest visible failure point."
            )
    if top_title.endswith("dependency path degraded"):
        family = _dependency_family_from_title(top_title)
        service_label = _service_label(dominant_service)
        dependency_label = _dependency_label(family)
        dependency_runtime = _runtime_items_for_family_all(bundle, family) if family is not None else []
        if dependency_runtime:
            dependency_resources = _runtime_resource_names(dependency_runtime) or [dependency_label]
            return (
                f"Most likely root cause: {top_title}. {service_label} shows direct {dependency_label}-related "
                f"errors while {_format_name_list(dependency_resources)} is still running, so this looks like a "
                "degraded dependency path rather than a hard outage."
            )
        return (
            f"Most likely root cause: {top_title}. {service_label} shows direct {dependency_label}-related "
            "errors, but current runtime state does not prove the dependency is fully down."
        )
    return (
        f"Most likely root cause: {top_title}. Evidence was pulled from live telemetry for "
        f"{_service_label(dominant_service) if dominant_service else 'Aspire Shop'}."
    )


def _live_safe_actions(bundle: EvidenceBundle, top_title: str, dominant_service: str | None) -> list[str]:
    primary_failures = _root_runtime_failures(bundle)
    focus_resources = _runtime_resource_names(primary_failures)
    if top_title == "No clear live fault detected":
        running_resources = _runtime_resource_names(_running_runtime_items(bundle, include_support=True))
        resource_list = _format_name_list(running_resources) if running_resources else "the current Aspire resources"
        return [
            f"Use the Aspire dashboard to confirm {resource_list} stay healthy while you reproduce the symptom.",
            "Prefer fresh user-path probes, logs, and traces over older noisy telemetry before declaring a new incident.",
            "If the problem recurs, capture the first failing request and compare it with runtime state to find the first concrete failing resource.",
        ]
    if top_title == "Multiple service outages in Aspire Shop" and focus_resources:
        resource_list = _format_name_list(focus_resources)
        return [
            f"Confirm in Docker or the Aspire dashboard that {resource_list} are still exited or unhealthy.",
            f"Correlate downstream frontend and service failures against the first missing resource among {resource_list}.",
            f"After recovery, verify fresh request success, error-rate, and latency signals for the affected app paths.",
        ]
    if top_title == "PostgreSQL dependency outage":
        postgres_resources = _runtime_resource_names(_runtime_items_for_family(bundle, "postgres")) or ["postgres"]
        resource_list = _format_name_list(postgres_resources)
        return [
            f"Inspect runtime state and startup logs for {resource_list} without changing state.",
            "Confirm catalogservice failures line up with the first PostgreSQL connection errors in logs and traces.",
            "After recovery, verify catalog requests and database-backed frontend calls succeed again.",
        ]
    if top_title == "Redis dependency outage":
        redis_resources = _runtime_resource_names(_runtime_items_for_family(bundle, "redis")) or ["basketcache"]
        resource_list = _format_name_list(redis_resources)
        return [
            f"Inspect runtime state and startup logs for {resource_list} without changing state.",
            "Confirm basketservice failures line up with the first Redis connection or timeout errors.",
            "After recovery, verify basket and checkout requests succeed again.",
        ]
    if top_title.endswith("dependency path degraded"):
        family = _dependency_family_from_title(top_title)
        service_label = _service_label(dominant_service)
        dependency_label = _dependency_label(family)
        dependency_runtime = _runtime_items_for_family_all(bundle, family) if family is not None else []
        dependency_resources = _runtime_resource_names(dependency_runtime) or [dependency_label]
        return [
            f"Inspect {service_label} error logs and failed traces around the first {dependency_label}-related timeout or retry.",
            f"Check reachability, latency, secrets, and connection settings on the {service_label} to {dependency_label} path, even if {_format_name_list(dependency_resources)} is still running.",
            f"Validate recovery with fresh {service_label} requests and confirm error-rate and latency return to baseline.",
        ]
    return _generic_safe_actions(dominant_service)


def _live_approval_actions(bundle: EvidenceBundle, top_title: str, dominant_service: str | None) -> list[str]:
    primary_failures = _root_runtime_failures(bundle)
    focus_resources = _runtime_resource_names(primary_failures)
    if top_title == "No clear live fault detected":
        return [
            "Avoid restarts, rollbacks, or failover changes until a fresh failing resource is confirmed.",
            "Only approve state-changing remediation if the issue reproduces with current runtime evidence.",
        ]
    if top_title.endswith("dependency path degraded"):
        family = _dependency_family_from_title(top_title)
        service_label = _service_label(dominant_service)
        dependency_label = _dependency_label(family)
        return [
            f"Restart or reconfigure {service_label} or the {dependency_label} dependency only if the path stays broken after read-only checks.",
            "Roll back the most recent connectivity, secret, or routing change if the dependency remains reachable but requests still fail.",
        ]
    if focus_resources:
        resource_list = _format_name_list(focus_resources)
        return [
            f"Restart or re-enable {resource_list} if they were intentionally disabled or are confirmed down.",
            "Escalate to config or deploy rollback only if the failed resources stay unhealthy after being brought back.",
        ]
    return _generic_approval_actions(dominant_service)


def _non_live_safe_actions(top_title: str, dominant_service: str | None) -> list[str]:
    family = _dependency_family_from_title(top_title)
    if family == "postgres":
        return [
            "Inspect PostgreSQL health, logs, and recent failed connections without changing state.",
            "Correlate catalogservice failures with the first PostgreSQL timeout or connection-refused evidence.",
            "Validate recovery by checking fresh catalog request latency and error-rate after PostgreSQL is healthy again.",
        ]
    if family == "redis":
        return [
            "Inspect Redis health, logs, and recent cache failures without changing state.",
            "Correlate basketservice failures with the first Redis timeout or connection-refused evidence.",
            "Validate recovery by checking fresh basket and checkout request latency after Redis is healthy again.",
        ]
    return _generic_safe_actions(dominant_service)


def _non_live_approval_actions(top_title: str, dominant_service: str | None) -> list[str]:
    family = _dependency_family_from_title(top_title)
    if family == "postgres":
        return [
            "Restart or fail over PostgreSQL only if read-only checks confirm the database is still unavailable.",
            "Roll back the most likely database connectivity or secret change if PostgreSQL is reachable but requests still fail.",
        ]
    if family == "redis":
        return [
            "Restart or fail over Redis only if read-only checks confirm the cache is still unavailable.",
            "Roll back the most likely cache connectivity or secret change if Redis is reachable but requests still fail.",
        ]
    return _generic_approval_actions(dominant_service)


def _evidence_items_for_ids(bundle: EvidenceBundle, evidence_ids: list[str]) -> list[EvidenceItem]:
    evidence_lookup = bundle.by_id()
    items: list[EvidenceItem] = []
    for evidence_id in evidence_ids:
        item = evidence_lookup.get(evidence_id)
        if item is not None:
            items.append(item)
    return items


def _format_evidence_lines(items: list[EvidenceItem], *, limit: int = 3, include_query: bool = False) -> list[str]:
    lines: list[str] = []
    for item in items[:limit]:
        citation = item.citations[0]
        line = f"- {item.id}: {item.summary} ({citation.source_type}: {citation.source})"
        if include_query and citation.query:
            line += f" | query `{citation.query}`"
        lines.append(line)
    return lines


def _retrieve_for_alert(
    context: AgentContext,
    alert: AlertPayload,
) -> tuple[list[ToolCallRecord], EvidenceBundle]:
    tool_calls: list[ToolCallRecord] = []

    started_at = utc_now()
    error_logs = context.telemetry.get_error_logs(alert)
    tool_calls.append(
        _record_tool_call(
            "get_error_logs",
            {"service": alert.service, "time_window_minutes": alert.window_minutes},
            started_at,
            error_logs,
        )
    )

    started_at = utc_now()
    latency = context.telemetry.get_latency_metrics(alert)
    tool_calls.append(
        _record_tool_call(
            "get_latency_metrics",
            {"service": alert.service, "time_window_minutes": alert.window_minutes},
            started_at,
            latency,
        )
    )

    started_at = utc_now()
    error_rate = context.telemetry.get_error_rate_metrics(alert)
    tool_calls.append(
        _record_tool_call(
            "get_error_rate_metrics",
            {"service": alert.service, "time_window_minutes": alert.window_minutes},
            started_at,
            error_rate,
        )
    )

    started_at = utc_now()
    failed_traces = context.telemetry.get_failed_traces(alert)
    tool_calls.append(
        _record_tool_call(
            "get_failed_traces",
            {"service": alert.service, "time_window_minutes": alert.window_minutes},
            started_at,
            failed_traces,
        )
    )

    started_at = utc_now()
    slow_traces = context.telemetry.get_slow_traces(alert)
    tool_calls.append(
        _record_tool_call(
            "get_slow_traces",
            {"service": alert.service, "time_window_minutes": alert.window_minutes},
            started_at,
            slow_traces,
        )
    )

    started_at = utc_now()
    runbooks = context.runbooks.get_runbook(alert)
    tool_calls.append(
        _record_tool_call(
            "get_runbook",
            {"service": alert.service, "summary": alert.summary},
            started_at,
            runbooks,
        )
    )

    started_at = utc_now()
    changes = context.telemetry.get_recent_changes(alert)
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
    return tool_calls, bundle


def _dominant_service(bundle: EvidenceBundle) -> str | None:
    failure_services = [item.service for item in _root_runtime_failures(bundle)]
    if failure_services:
        return Counter(failure_services).most_common(1)[0][0]
    dependency_signal_services = [
        item.service
        for item in [*bundle.logs, *bundle.metrics, *bundle.traces]
        if _dependency_signal_family(item) is not None
    ]
    if dependency_signal_services:
        return Counter(dependency_signal_services).most_common(1)[0][0]
    services = [item.service for item in bundle.all_items() if item.service and item.service != "aspire-shop"]
    if not services:
        return None
    return Counter(services).most_common(1)[0][0]


def _live_evidence_text(bundle: EvidenceBundle) -> str:
    parts: list[str] = []
    for item in bundle.all_items():
        parts.append(item.summary)
        parts.append(str(item.value))
        for citation in item.citations:
            parts.append(citation.excerpt)
    return " ".join(parts).lower()


def _deterministic_live_hypotheses(state: IIRSState) -> list[Hypothesis]:
    alert = state["alert"]
    bundle = state["evidence_bundle"]
    all_items = bundle.all_items()
    dominant_service = _dominant_service(bundle)
    health_check = _is_live_health_check_alert(alert)
    broad_live_request = alert.service in {"aspire-shop", "shop", "unknown"}
    runtime_issues = _runtime_failure_items(bundle, include_support=False)
    runtime_failures = _root_runtime_failures(bundle)
    runtime_support_ids = [item.id for item in runtime_failures[:4]]
    healthy_runtime_ids = [item.id for item in _running_runtime_items(bundle, include_support=True)[:4]]
    support_ids = runtime_support_ids or [item.id for item in all_items[:5]]
    trace_ids = [item.id for item in bundle.traces[:2]]
    change_ids = [item.id for item in bundle.change_signals[:1]]
    failing_families = _unique_preserve(
        [str(item.metadata.get("family") or item.service) for item in runtime_failures]
    )
    postgres_failures = _runtime_items_for_family(bundle, "postgres")
    redis_failures = _runtime_items_for_family(bundle, "redis")
    service_runtime_issues = _runtime_issue_items_for_service(bundle, dominant_service)
    hard_service_runtime_issues = [
        item
        for item in service_runtime_issues
        if _runtime_state_for_item(item) in {"dead", "restarting", "missing", "exited", "created", "unknown"}
    ]
    top_dependency_family, top_dependency_items = _top_dependency_signal_for_service(bundle, dominant_service)
    dependency_signal_ids = [item.id for item in top_dependency_items[:4]]

    if health_check and not runtime_issues:
        top_title = "No clear live fault detected"
        confidence = 0.82 if len(healthy_runtime_ids) >= 3 else (0.7 if healthy_runtime_ids else 0.42)
        support_ids = healthy_runtime_ids or support_ids
        if all_items:
            secondary_title = "Transient issue or stale telemetry from earlier faults"
            secondary_support_ids = [item.id for item in all_items[:2]]
        else:
            secondary_title = "Transient issue or insufficient live telemetry"
            secondary_support_ids = []
    elif len(failing_families) >= 2 and len(runtime_failures) >= 2:
        top_title = "Multiple service outages in Aspire Shop"
        confidence = 0.95 if len(runtime_failures) >= 3 else 0.86
        support_ids = runtime_support_ids
        if postgres_failures:
            secondary_title = "PostgreSQL dependency outage"
        elif redis_failures:
            secondary_title = "Redis dependency outage"
        else:
            secondary_title = f"{_service_label(dominant_service)} unavailable" if dominant_service else "Service unavailable"
        secondary_support_ids = trace_ids or support_ids[:2]
    elif postgres_failures:
        top_title = "PostgreSQL dependency outage"
        confidence = 0.88 if len(runtime_support_ids) >= 1 and len(dependency_signal_ids) >= 2 else 0.78
        support_ids = _unique_preserve([
            *[item.id for item in postgres_failures[:2]],
            *dependency_signal_ids,
            *runtime_support_ids,
        ])[:4] or support_ids
        secondary_title = f"{_service_label(dominant_service)} unavailable" if dominant_service else "Service unavailable"
        secondary_support_ids = [item.id for item in service_runtime_issues[:2]] or trace_ids or support_ids[:2]
    elif redis_failures:
        top_title = "Redis dependency outage"
        confidence = 0.88 if len(runtime_support_ids) >= 1 and len(dependency_signal_ids) >= 2 else 0.78
        support_ids = _unique_preserve([
            *[item.id for item in redis_failures[:2]],
            *dependency_signal_ids,
            *runtime_support_ids,
        ])[:4] or support_ids
        secondary_title = f"{_service_label(dominant_service)} unavailable" if dominant_service else "Service unavailable"
        secondary_support_ids = [item.id for item in service_runtime_issues[:2]] or trace_ids or support_ids[:2]
    elif hard_service_runtime_issues:
        label = _service_label(dominant_service)
        top_title = f"{label} unavailable"
        confidence = 0.87 if len(hard_service_runtime_issues) >= 1 else 0.74
        support_ids = _unique_preserve([
            *[item.id for item in hard_service_runtime_issues[:2]],
            *dependency_signal_ids,
        ])[:4] or support_ids
        secondary_title = (
            _dependency_path_title(dominant_service, top_dependency_family)
            if top_dependency_family is not None and top_dependency_items
            else f"Upstream dependency issue affecting {label}"
        )
        secondary_support_ids = dependency_signal_ids[:2] or trace_ids or support_ids[:2]
    elif top_dependency_family is not None and top_dependency_items:
        top_title = _dependency_path_title(dominant_service, top_dependency_family)
        confidence = 0.84 if service_runtime_issues else (0.79 if len(top_dependency_items) >= 3 else 0.71)
        support_ids = _unique_preserve([
            *dependency_signal_ids,
            *[item.id for item in service_runtime_issues[:2]],
        ])[:4] or support_ids
        secondary_title = (
            f"{_service_label(dominant_service)} unavailable"
            if service_runtime_issues
            else f"{_dependency_label(top_dependency_family)} dependency outage"
        )
        secondary_support_ids = [item.id for item in service_runtime_issues[:2]] or trace_ids or support_ids[:2]
    elif broad_live_request and not runtime_issues:
        top_title = "No clear live fault detected"
        confidence = 0.76 if healthy_runtime_ids else 0.42
        support_ids = healthy_runtime_ids or support_ids[:3]
        secondary_title = (
            "Transient issue or stale telemetry from earlier faults"
            if all_items
            else "Transient issue or insufficient live telemetry"
        )
        secondary_support_ids = [item.id for item in all_items[:2]]
    elif support_ids:
        label = _service_label(dominant_service)
        top_title = f"{label} unavailable"
        confidence = 0.7 if len(support_ids) >= 2 else 0.58
        secondary_title = f"Upstream dependency issue affecting {label}"
        secondary_support_ids = trace_ids or support_ids[:2]
    else:
        top_title = "No clear live fault detected"
        confidence = 0.24
        secondary_title = "Transient issue or insufficient live telemetry"
        secondary_support_ids = []

    next_checks = _generic_follow_up_checks(dominant_service)
    return [
        Hypothesis(
            rank=1,
            title=top_title,
            confidence=confidence,
            supporting_evidence_ids=support_ids,
            contradicting_evidence_ids=change_ids,
            next_checks=next_checks,
        ),
        Hypothesis(
            rank=2,
            title=secondary_title,
            confidence=max(0.1, confidence - 0.28),
            supporting_evidence_ids=secondary_support_ids,
            contradicting_evidence_ids=change_ids,
            next_checks=next_checks,
        ),
        Hypothesis(
            rank=3,
            title="Recent deploy or configuration regression",
            confidence=0.18 if change_ids else 0.08,
            supporting_evidence_ids=change_ids,
            contradicting_evidence_ids=support_ids[:3],
            next_checks=[
                "Inspect deployment or config drift only after validating basic service and dependency availability.",
            ],
        ),
    ]


def _deterministic_hypotheses(state: IIRSState) -> list[Hypothesis]:
    if _is_live_alert(state["alert"]):
        return _deterministic_live_hypotheses(state)

    alert = state["alert"]
    bundle = state["evidence_bundle"]
    all_items = bundle.all_items()
    dominant_service = _dominant_service(bundle) or alert.service
    change_ids = [item.id for item in bundle.change_signals]
    default_support = [item.id for item in all_items[:4]]
    next_checks = _generic_follow_up_checks(dominant_service)
    top_dependency_family, top_dependency_items = _top_dependency_signal_for_service(bundle, dominant_service)
    dependency_support = [item.id for item in top_dependency_items[:4]]

    if _dependency_outage_supported(alert, bundle, top_dependency_family, top_dependency_items):
        top_title = f"{_dependency_label(top_dependency_family)} dependency outage"
        confidence = 0.9 if len(top_dependency_items) >= 3 else 0.82
        top_support = dependency_support or default_support
        secondary_title = _dependency_path_title(dominant_service, top_dependency_family)
        secondary_support = dependency_support[:2] or default_support[:2]
    elif top_dependency_family is not None and top_dependency_items:
        top_title = _dependency_path_title(dominant_service, top_dependency_family)
        confidence = 0.78 if len(top_dependency_items) >= 3 else 0.68
        top_support = dependency_support or default_support
        secondary_title = (
            f"{_service_label(dominant_service)} unavailable"
            if dominant_service
            else f"{_dependency_label(top_dependency_family)} dependency issue"
        )
        secondary_support = default_support[:2] or dependency_support[:2]
    elif default_support:
        top_title = f"{_service_label(dominant_service)} unavailable" if dominant_service else "Service unavailable"
        confidence = 0.64 if len(default_support) >= 3 else 0.52
        top_support = default_support
        secondary_title = (
            f"Upstream dependency issue affecting {_service_label(dominant_service)}"
            if dominant_service
            else "Upstream dependency issue"
        )
        secondary_support = default_support[:2]
    else:
        top_title = "No clear fault detected in retrieved evidence"
        confidence = 0.24
        top_support = []
        secondary_title = "Insufficient evidence to rank a concrete alternate hypothesis"
        secondary_support = []

    return [
        Hypothesis(
            rank=1,
            title=top_title,
            confidence=confidence,
            supporting_evidence_ids=top_support,
            contradicting_evidence_ids=change_ids[:1],
            next_checks=next_checks,
        ),
        Hypothesis(
            rank=2,
            title=secondary_title,
            confidence=max(0.1, confidence - 0.28),
            supporting_evidence_ids=secondary_support,
            contradicting_evidence_ids=change_ids[:1],
            next_checks=next_checks,
        ),
        Hypothesis(
            rank=3,
            title="Recent deploy or configuration regression",
            confidence=0.18 if change_ids else 0.08,
            supporting_evidence_ids=change_ids[:1],
            contradicting_evidence_ids=top_support[:3],
            next_checks=[
                "Review deployment metadata and config drift only if dependency health is normal.",
            ],
        ),
    ]


def _llm_hypotheses(
    context: AgentContext,
    state: IIRSState,
) -> list[Hypothesis]:
    if _is_live_health_check_alert(state["alert"]):
        return _deterministic_live_hypotheses(state)
    if context.llm is None:
        return _deterministic_hypotheses(state)

    response = context.llm.analyze_incident(state)
    all_items = state["evidence_bundle"].all_items()
    valid_ids = {item.id for item in all_items}
    default_support = [item.id for item in all_items[:3]]
    default_next_checks = _generic_follow_up_checks(_dominant_service(state["evidence_bundle"]))
    hypotheses: list[Hypothesis] = []
    for index, raw in enumerate(response.get("hypotheses", [])[:3], start=1):
        supporting_ids = _filter_evidence_ids(raw.get("supporting_evidence_ids"), valid_ids) or default_support
        next_checks = _normalize_text_list(raw.get("next_checks"), default_next_checks)[:3]
        hypotheses.append(
            Hypothesis(
                rank=index,
                title=_normalize_root_cause_title(str(raw.get("title", "")).strip()),
                confidence=_clamp_confidence(raw.get("confidence"), default=0.5),
                supporting_evidence_ids=supporting_ids,
                contradicting_evidence_ids=_filter_evidence_ids(raw.get("contradicting_evidence_ids"), valid_ids),
                next_checks=next_checks,
            )
        )

    if not hypotheses:
        raise OpenAIRequestError("OpenAI analyst response did not contain any hypotheses.")
    return hypotheses


def _should_force_deterministic_live_analysis(state: IIRSState) -> bool:
    if not _is_live_alert(state["alert"]):
        return False
    if _is_live_health_check_alert(state["alert"]):
        return True
    deterministic_top = _deterministic_live_hypotheses(state)[0].title
    return deterministic_top in {
        "PostgreSQL dependency outage",
        "Redis dependency outage",
        "Multiple service outages in Aspire Shop",
    } or deterministic_top.endswith(" unavailable")


def _deterministic_critique(state: IIRSState) -> Critique:
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
            message="Top hypothesis is grounded in retrieved evidence with explicit citations.",
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

    return Critique(
        relevant_evidence_ids=hypotheses[0].supporting_evidence_ids,
        hallucination_risks=hallucination_risks,
        missing_data=missing_data,
        safety_notes=[
            "Keep all actions read-only until a human approves dependency restarts or traffic changes.",
            "Treat rollback or restart recommendations as needs-approval actions.",
        ],
        findings=findings,
    )


def _llm_critique(context: AgentContext, state: IIRSState) -> Critique:
    if context.llm is None:
        return _deterministic_critique(state)

    response = context.llm.critique_incident(state)
    valid_ids = {item.id for item in state["evidence_bundle"].all_items()}
    findings: list[CritiqueFinding] = []
    for raw in response.get("findings", []):
        severity = str(raw.get("severity", "info")).strip().lower()
        if severity not in {"info", "warning", "critical"}:
            severity = "info"
        findings.append(
            CritiqueFinding(
                severity=severity,
                message=str(raw.get("message", "")).strip() or "No critique message provided.",
                evidence_ids=_filter_evidence_ids(raw.get("evidence_ids"), valid_ids),
            )
        )

    critique = Critique(
        relevant_evidence_ids=_filter_evidence_ids(response.get("relevant_evidence_ids"), valid_ids),
        hallucination_risks=_normalize_text_list(response.get("hallucination_risks"), []),
        missing_data=_normalize_text_list(response.get("missing_data"), []),
        safety_notes=_normalize_text_list(
            response.get("safety_notes"),
            ["Treat state-changing remediation as needs-approval."],
        ),
        findings=findings,
    )
    if not critique.relevant_evidence_ids and state["hypotheses"]:
        critique.relevant_evidence_ids = state["hypotheses"][0].supporting_evidence_ids
    return critique


def _deterministic_plan(
    state: IIRSState,
) -> tuple[list[PlanStep], IncidentBrief]:
    alert = state["alert"]
    hypotheses = state["hypotheses"]
    critique = state["critique"]
    bundle = state["evidence_bundle"]
    top_evidence = hypotheses[0].supporting_evidence_ids[:3]
    dominant_service = _dominant_service(bundle)

    steps: list[PlanStep] = []
    if _is_live_alert(alert):
        safe_actions = _live_safe_actions(bundle, hypotheses[0].title, dominant_service)
        approval_actions = _live_approval_actions(bundle, hypotheses[0].title, dominant_service)
    else:
        safe_actions = _non_live_safe_actions(hypotheses[0].title, dominant_service)
        approval_actions = _non_live_approval_actions(hypotheses[0].title, dominant_service)
    brief_title = (
        f"Incident Brief: {alert.scenario}"
        if alert.scenario
        else (
            f"Incident Brief: live health check for {_service_label(dominant_service) if dominant_service else 'Aspire Shop'}"
            if _is_live_health_check_alert(alert)
            else (
                "Incident Brief: live diagnosis for Aspire Shop"
                if _is_live_alert(alert) and hypotheses[0].title == "Multiple service outages in Aspire Shop"
                else (
                    f"Incident Brief: live diagnosis for {_service_label(dominant_service) if dominant_service else 'Aspire Shop'}"
                    if _is_live_alert(alert)
                    else f"Incident Brief: investigation for {_service_label(dominant_service) if dominant_service else _service_label(alert.service)}"
                )
            )
        )
    )
    brief_summary = (
        _live_summary(bundle, hypotheses[0].title, dominant_service)
        if _is_live_alert(alert)
        else _non_live_summary(bundle, hypotheses[0].title, dominant_service or alert.service)
    )

    for index, description in enumerate(safe_actions, start=1):
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
    for offset, description in enumerate(approval_actions, start=0):
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
        title=brief_title,
        summary=brief_summary,
        probable_root_causes=hypotheses,
        recommended_actions=steps,
        open_questions=critique.missing_data or ["No unresolved data gaps captured in the current incident state."],
        evidence_snapshot=[item.summary for item in bundle.all_items()[:5]],
    )
    return steps, brief


def _llm_plan(
    context: AgentContext,
    state: IIRSState,
) -> tuple[list[PlanStep], IncidentBrief]:
    if context.llm is None:
        return _deterministic_plan(state)

    response = context.llm.plan_incident(state)
    valid_ids = {item.id for item in state["evidence_bundle"].all_items()}
    top_evidence = state["hypotheses"][0].supporting_evidence_ids[:3]
    steps: list[PlanStep] = []
    for order, raw in enumerate(response.get("steps", [])[:6], start=1):
        description = str(raw.get("description", "")).strip()
        if not description:
            continue
        action_type = str(raw.get("action_type", "auto-safe")).strip()
        if action_type not in {"auto-safe", "needs-approval"}:
            lowered = description.lower()
            if any(term in lowered for term in {"restart", "rollback", "fail over", "failover", "repoint"}):
                action_type = "needs-approval"
            else:
                action_type = "auto-safe"
        evidence_ids = _filter_evidence_ids(raw.get("evidence_ids"), valid_ids) or top_evidence
        steps.append(
            PlanStep(
                order=order,
                description=description,
                action_type=action_type,
                rationale=str(raw.get("rationale", "")).strip() or "Model-generated triage step.",
                evidence_ids=evidence_ids,
            )
        )

    if not steps:
        raise OpenAIRequestError("OpenAI planner response did not contain any plan steps.")

    brief = IncidentBrief(
        title=(
            str(raw_title).strip()
            if (raw_title := response.get("brief_title"))
            else (
                f"Incident Brief: {state['alert'].scenario}"
                if state["alert"].scenario
                else (
                    f"Incident Brief: live diagnosis for {_service_label(_dominant_service(state['evidence_bundle']))}"
                    if _is_live_alert(state["alert"])
                    else f"Incident Brief: investigation for {_service_label(_dominant_service(state['evidence_bundle']) or state['alert'].service)}"
                )
            )
        ),
        summary=str(raw_summary).strip() if (raw_summary := response.get("brief_summary")) else f"Most likely root cause: {state['hypotheses'][0].title}.",
        probable_root_causes=state["hypotheses"],
        recommended_actions=steps,
        open_questions=_normalize_text_list(
            response.get("open_questions"),
            state["critique"].missing_data or ["No unresolved data gaps captured."],
        )[:4],
        evidence_snapshot=_normalize_text_list(
            response.get("evidence_snapshot"),
            [item.summary for item in state["evidence_bundle"].all_items()[:5]],
        )[:5],
    )
    return steps, brief


def make_retriever_node(context: AgentContext) -> Callable[[IIRSState], dict[str, object]]:
    def retriever(state: IIRSState) -> dict[str, object]:
        alert = state["alert"]
        scenario_name = alert.scenario
        candidate_services = _candidate_services_for_alert(alert) if _is_live_alert(alert) else [alert.service]
        if _is_live_alert(alert):
            tool_calls: list[ToolCallRecord] = []
            bundle = EvidenceBundle()
            for service in candidate_services:
                service_alert = _scoped_alert(alert, service)
                service_tool_calls, service_bundle = _retrieve_for_alert(context, service_alert)
                tool_calls.extend(service_tool_calls)
                bundle.logs.extend(service_bundle.logs)
                bundle.metrics.extend(service_bundle.metrics)
                bundle.traces.extend(service_bundle.traces)
                bundle.runbook_hits.extend(service_bundle.runbook_hits)
                bundle.change_signals.extend(service_bundle.change_signals)
        else:
            tool_calls, bundle = _retrieve_for_alert(context, alert)

        started_at = utc_now()
        runtime_states = context.telemetry.get_runtime_states(
            alert,
            candidate_services if _is_live_alert(alert) else [alert.service],
        )
        tool_calls.append(
            _record_tool_call(
                "get_runtime_states",
                {
                    "service": alert.service,
                    "candidate_services": candidate_services if _is_live_alert(alert) else [alert.service],
                },
                started_at,
                runtime_states,
            )
        )
        bundle.runtime_states.extend(runtime_states.items)

        unhealthy_runtime_items = [
            item
            for item in runtime_states.items
            if _runtime_state_for_item(item) != "running"
            and str(item.metadata.get("role", "")).lower() in {"service", "dependency"}
        ]
        if unhealthy_runtime_items:
            started_at = utc_now()
            runtime_log_tails = context.telemetry.get_runtime_log_tails(alert, unhealthy_runtime_items)
            tool_calls.append(
                _record_tool_call(
                    "get_runtime_log_tails",
                    {
                        "service": alert.service,
                        "resources": [
                            str(item.metadata.get("resource") or item.service)
                            for item in unhealthy_runtime_items
                        ],
                    },
                    started_at,
                    runtime_log_tails,
                )
            )
            bundle.logs.extend(runtime_log_tails.items)

        if _is_live_alert(alert):
            output_summary = (
                f"Live investigation queried {', '.join(candidate_services)} and collected "
                f"{len(bundle.runtime_states)} runtime states, {len(bundle.logs)} logs, {len(bundle.metrics)} metrics, "
                f"{len(bundle.traces)} traces, {len(bundle.runbook_hits)} runbooks, and "
                f"{len(bundle.change_signals)} change signals."
            )
            assistant_message = (
                f"Retriever assembled {len(bundle.all_items())} live evidence items across "
                f"{', '.join(candidate_services)}."
            )
        else:
            output_summary = (
                f"Collected {len(bundle.runtime_states)} runtime states, {len(bundle.logs)} logs, {len(bundle.metrics)} metrics, "
                f"{len(bundle.traces)} traces, {len(bundle.runbook_hits)} runbooks, "
                f"and {len(bundle.change_signals)} change signals."
            )
            assistant_message = f"Retriever assembled {len(bundle.all_items())} evidence items for {alert.service}."

        agent_run = AgentRun(
            agent_name="Retriever",
            started_at=tool_calls[0].started_at,
            finished_at=utc_now(),
            input_summary=f"Alert for {alert.service} in {alert.environment}: {alert.summary}",
            output_summary=output_summary,
            execution_mode="tooling",
            tool_calls=tool_calls,
        )

        return {
            "scenario_name": scenario_name,
            "evidence_bundle": bundle,
            "trace_runs": _append_trace(state, agent_run),
            "messages": _append_message(
                state,
                "assistant",
                assistant_message,
            ),
        }

    return retriever


def make_analyst_node(context: AgentContext) -> Callable[[IIRSState], dict[str, object]]:
    def analyst(state: IIRSState) -> dict[str, object]:
        all_items = state["evidence_bundle"].all_items()
        if context.llm is None or _should_force_deterministic_live_analysis(state):
            hypotheses = _deterministic_hypotheses(state)
            execution_mode = "deterministic"
        else:
            hypotheses = _llm_hypotheses(context, state)
            execution_mode = "model"

        agent_run = AgentRun(
            agent_name="Analyst",
            started_at=utc_now(),
            finished_at=utc_now(),
            input_summary=(
                f"Analyzed {len(all_items)} evidence items for {state['alert'].scenario}."
                if state["alert"].scenario
                else (
                    f"Analyzed {len(all_items)} evidence items for a live multi-service investigation."
                    if _is_live_alert(state["alert"])
                    else f"Analyzed {len(all_items)} evidence items for {_service_label(state['alert'].service)}."
                )
            ),
            output_summary=(
                f"Ranked root causes with top hypothesis '{hypotheses[0].title}' "
                f"at confidence {hypotheses[0].confidence:.2f}."
            ),
            execution_mode=execution_mode,
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
        if context.llm is None:
            critique = _deterministic_critique(state)
            execution_mode = "deterministic"
        else:
            critique = _llm_critique(context, state)
            execution_mode = "model"

        agent_run = AgentRun(
            agent_name="Critic",
            started_at=utc_now(),
            finished_at=utc_now(),
            input_summary=f"Validated {len(hypotheses)} hypotheses against {len(bundle.all_items())} evidence items.",
            output_summary=(
                f"Generated {len(critique.findings)} findings and "
                f"{len(critique.hallucination_risks)} hallucination-risk checks."
            ),
            execution_mode=execution_mode,
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
        if context.llm is None:
            steps, brief = _deterministic_plan(state)
            execution_mode = "deterministic"
        else:
            steps, brief = _llm_plan(context, state)
            execution_mode = "model"

        agent_run = AgentRun(
            agent_name="Planner",
            started_at=utc_now(),
            finished_at=utc_now(),
            input_summary="Synthesized hypotheses and critique into an incident brief.",
            output_summary=f"Produced {len(steps)} triage actions and the final brief '{brief.title}'.",
            execution_mode=execution_mode,
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


def _expand_follow_up_question(question: str, state: IIRSState) -> str:
    normalized = " ".join(question.strip().lower().split())
    if not normalized:
        return question

    top = state["incident_brief"].probable_root_causes[0]
    rewrites = {
        "why": f"Why is {top.title} the current top hypothesis and what evidence supports it?",
        "why?": f"Why is {top.title} the current top hypothesis and what evidence supports it?",
        "how so": f"Why is {top.title} the current top hypothesis and what evidence supports it?",
        "how so?": f"Why is {top.title} the current top hypothesis and what evidence supports it?",
        "why is that": f"Why is {top.title} the current top hypothesis and what evidence supports it?",
        "why is that?": f"Why is {top.title} the current top hypothesis and what evidence supports it?",
        "are you sure": "How sure are we about the current diagnosis?",
        "are you sure?": "How sure are we about the current diagnosis?",
        "show me more": "Show me the strongest cited evidence.",
        "show me more.": "Show me the strongest cited evidence.",
        "more": "Show me the strongest cited evidence.",
        "more?": "Show me the strongest cited evidence.",
        "proof?": "Show me the strongest cited evidence.",
        "what supports that": "Show me the strongest cited evidence.",
        "what supports that?": "Show me the strongest cited evidence.",
        "other one": "Compare the top hypothesis with the main alternate hypothesis.",
        "other one?": "Compare the top hypothesis with the main alternate hypothesis.",
        "the other one": "Compare the top hypothesis with the main alternate hypothesis.",
        "the other one?": "Compare the top hypothesis with the main alternate hypothesis.",
        "and then": "What should I do first and what should I do next?",
        "and then?": "What should I do first and what should I do next?",
        "then what": "What should I do first and what should I do next?",
        "then what?": "What should I do first and what should I do next?",
        "what next": "What should I do first and what should I do next?",
        "what next?": "What should I do first and what should I do next?",
        "next": "What should I do first and what should I do next?",
        "next?": "What should I do first and what should I do next?",
        "healthy?": "Summarize whether the system currently looks healthy or broken using the latest runtime state.",
        "broken?": "Summarize whether the system currently looks healthy or broken using the latest runtime state.",
        "healthy or broken?": "Summarize whether the system currently looks healthy or broken using the latest runtime state.",
    }
    return rewrites.get(normalized, question)


def answer_follow_up(question: str, state: IIRSState, llm: ReasoningClient | None = None) -> str:
    effective_question = _expand_follow_up_question(question, state)
    if llm is not None:
        llm_question = (
            question
            if effective_question == question
            else (
                f"Original user follow-up: {question}\n"
                f"Resolved follow-up intent: {effective_question}"
            )
        )
        return llm.answer_follow_up(llm_question, state)
    fallback_prefix = ""

    bundle = state["evidence_bundle"]
    brief = state["incident_brief"]
    critique = state["critique"]
    alert = state["alert"]
    hypotheses = brief.probable_root_causes
    top = hypotheses[0]
    alternate = hypotheses[1] if len(hypotheses) > 1 else None
    lowered = effective_question.lower()

    if _matches_any(lowered, ("what happened", "summary", "recap", "plain english", "plain-english")):
        biggest_gap = critique.missing_data[0] if critique.missing_data else "No material data gap is currently blocking the main diagnosis."
        return fallback_prefix + (
            f"Current read: {brief.summary}\n"
            f"Affected service: {alert.service} in {alert.environment}.\n"
            f"Biggest open question: {biggest_gap}"
        )

    if lowered.startswith("why") or _matches_any(lowered, ("root cause", "what caused")):
        evidence_lines = _format_evidence_lines(_evidence_items_for_ids(bundle, top.supporting_evidence_ids), limit=3)
        return fallback_prefix + (
            f"Most likely root cause: {top.title} (confidence {top.confidence:.2f}).\n"
            f"Supporting evidence:\n" + "\n".join(evidence_lines)
        )

    if _matches_any(lowered, ("how sure", "confidence", "certain", "sure", "uncertain", "how likely")):
        lines = [
            f"Top hypothesis: {top.title} at confidence {top.confidence:.2f}.",
        ]
        if alternate is not None:
            lines.append(
                f"Main alternate: {alternate.title} at confidence {alternate.confidence:.2f}."
            )
        if critique.missing_data:
            lines.append(
                "Confidence is capped by these gaps: " + "; ".join(critique.missing_data[:2])
            )
        else:
            lines.append("There is no major unresolved data gap in the current incident state.")
        return fallback_prefix + "\n".join(lines)

    if _matches_any(lowered, ("compare", "alternative", "other hypothesis", "second guess", "runner up")):
        if alternate is None:
            return fallback_prefix + f"There is no meaningful alternate hypothesis in the current brief. The case points to {top.title}."
        return fallback_prefix + (
            f"Top hypothesis: {top.title} ({top.confidence:.2f}).\n"
            f"Main alternate: {alternate.title} ({alternate.confidence:.2f}).\n"
            f"The difference is that the top hypothesis has stronger direct evidence from {', '.join(top.supporting_evidence_ids[:2])}."
        )

    if _matches_any(lowered, ("evidence", "citation", "proof")):
        relevant_ids = critique.relevant_evidence_ids or top.supporting_evidence_ids
        lines = _format_evidence_lines(_evidence_items_for_ids(bundle, relevant_ids), limit=5, include_query=True)
        return fallback_prefix + "Strongest cited evidence:\n" + "\n".join(lines)

    if _matches_any(lowered, ("log", "logs")):
        lines = _format_evidence_lines(bundle.logs, limit=3, include_query=True)
        return fallback_prefix + ("Top log signals:\n" + "\n".join(lines) if lines else "I do not have log evidence in the current incident state.")

    if _matches_any(lowered, ("health", "dashboard", "container", "containers", "resource state", "runtime state")):
        lines = _runtime_state_lines(bundle)
        current_read = (
            "Current read: no clear active fault in the latest runtime state."
            if top.title == "No clear live fault detected"
            else f"Current read: {top.title} remains the leading issue."
        )
        if not lines:
            return fallback_prefix + current_read + "\nI do not have local runtime-state evidence in the current incident state."
        return fallback_prefix + current_read + "\nCurrent runtime state:\n" + "\n".join(lines)

    if _matches_any(lowered, ("metric", "metrics", "latency", "error rate", "5xx")):
        lines = _format_evidence_lines(bundle.metrics, limit=3, include_query=True)
        return fallback_prefix + ("Top metric signals:\n" + "\n".join(lines) if lines else "I do not have metric evidence in the current incident state.")

    if _matches_any(lowered, ("trace", "traces", "span")):
        lines = _format_evidence_lines(bundle.traces, limit=3, include_query=True)
        return fallback_prefix + ("Top trace signals:\n" + "\n".join(lines) if lines else "I do not have trace evidence in the current incident state.")

    if _matches_any(lowered, ("runbook", "playbook")):
        lines = _format_evidence_lines(bundle.runbook_hits, limit=3, include_query=True)
        return fallback_prefix + ("Relevant runbook guidance:\n" + "\n".join(lines) if lines else "I do not have matched runbook guidance in the current incident state.")

    if _matches_any(lowered, ("deploy", "change", "regression", "config", "configuration")):
        if not bundle.change_signals:
            return fallback_prefix + "I do not have any change evidence in the current incident state."
        lines = _format_evidence_lines(bundle.change_signals, limit=3, include_query=True)
        return fallback_prefix + "Current change evidence:\n" + "\n".join(lines)

    if _matches_any(lowered, ("missing", "unknown", "open question", "what else do we need", "what are we missing")):
        if critique.missing_data:
            return fallback_prefix + "Missing data that would tighten the diagnosis:\n" + "\n".join(
                f"- {item}" for item in critique.missing_data
            )
        return fallback_prefix + "No material missing-data item is captured in the current critique."

    if _matches_any(lowered, ("risk", "risky", "caveat", "concern", "safe", "danger", "blast radius")):
        notes = [*critique.safety_notes, *critique.hallucination_risks]
        if notes:
            return fallback_prefix + "Main risks and caveats:\n" + "\n".join(f"- {item}" for item in notes[:5])
        return fallback_prefix + "I do not see a major caveat beyond the normal approval boundary for state-changing actions."

    if _matches_any(lowered, ("approval", "restart", "rollback", "fail over", "failover", "can we", "should we")):
        approval_steps = [step for step in brief.recommended_actions if step.action_type == "needs-approval"]
        if approval_steps:
            return fallback_prefix + (
                "State-changing actions stay human-approved here.\n"
                + "\n".join(f"- {step.description}" for step in approval_steps[:3])
            )
        return fallback_prefix + "There is no state-changing remediation step in the current brief."

    if _matches_any(lowered, ("action", "next", "plan", "what do i do", "what should i do", "first", "priorit")):
        if _matches_any(lowered, ("first", "priorit", "start")):
            prioritized = sorted(
                brief.recommended_actions,
                key=lambda step: (step.action_type != "auto-safe", step.order),
            )
            lines = [
                f"- [{step.action_type}] {step.description}"
                for step in prioritized[:2]
            ]
            return fallback_prefix + "Start here:\n" + "\n".join(lines)
        lines = [
            f"- [{step.action_type}] {step.description}"
            for step in brief.recommended_actions
        ]
        return fallback_prefix + "Recommended next steps:\n" + "\n".join(lines)

    if _matches_any(lowered, ("when", "started", "start time")):
        return fallback_prefix + f"The incident window in state starts at {alert.started_at} for service {alert.service}."

    if _matches_any(lowered, ("which service", "affected service", "blast radius", "who is affected")):
        return fallback_prefix + f"The current incident state is centered on {alert.service} in {alert.environment}."

    if _matches_any(lowered, ("who owns", "owner", "team")):
        return fallback_prefix + (
            f"I do not have ownership metadata in the incident state. What I do have is evidence centered on {alert.service} "
            f"and the current leading diagnosis of {top.title}."
        )

    return fallback_prefix + (
        f"{brief.summary}\n"
        f"Known unknowns: {', '.join(brief.open_questions)}\n"
        "I can answer directly about confidence, evidence, changes, risks, or what to do first."
    )
