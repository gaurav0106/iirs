from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any, TYPE_CHECKING, Protocol

import httpx

from .models import AlertPayload, Citation, EvidenceItem, ToolResult
from .scenarios import EvidenceSeed, ScenarioDefinition

if TYPE_CHECKING:
    from .config import Settings


class TelemetryBackend(Protocol):
    def get_runtime_states(self, alert: AlertPayload, services: list[str] | None = None) -> ToolResult: ...

    def get_runtime_log_tails(self, alert: AlertPayload, runtime_items: list[EvidenceItem]) -> ToolResult: ...

    def get_error_logs(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult: ...

    def get_latency_metrics(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult: ...

    def get_error_rate_metrics(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult: ...

    def get_failed_traces(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult: ...

    def get_slow_traces(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult: ...

    def get_recent_changes(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult: ...


class TelemetryConfigurationError(RuntimeError):
    pass


class TelemetryRequestError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LiveMetricProfile:
    route_matcher: str | None = None
    route_operator: str = "="
    exception_error_pattern: str | None = None
    runtime_exception_pattern: str | None = None
    failed_trace_filter: str | None = None

    def base_selector_parts(self, service: str) -> list[str]:
        parts = [f'exported_job="{service}"']
        if self.route_matcher:
            parts.append(f'http_route{self.route_operator}"{self.route_matcher}"')
        return parts


@dataclass(frozen=True, slots=True)
class RuntimeResourceSpec:
    name: str
    aliases: tuple[str, ...]
    family: str
    role: str


_DEFAULT_METRIC_PROFILE = LiveMetricProfile()
_LIVE_METRIC_PROFILES: dict[tuple[str, str | None], LiveMetricProfile] = {
    ("catalogservice", "postgres_down"): LiveMetricProfile(
        route_matcher="/api/v1/catalog/items/type/all",
        exception_error_pattern="Microsoft\\.EntityFrameworkCore\\.Storage\\.RetryLimitExceededException",
    ),
    ("basketservice", "redis_down"): LiveMetricProfile(
        route_matcher="/BasketApi.Basket/(GetBasketById|UpdateBasket|CheckoutBasket|DeleteBasket)",
        route_operator="=~",
        exception_error_pattern="StackExchange\\.Redis\\..+",
        runtime_exception_pattern="RedisConnectionException|RedisTimeoutException|SocketException",
        failed_trace_filter='name =~ "POST /BasketApi.Basket/.*" && trace:duration > 4s',
    ),
}
_RUNTIME_RESOURCE_SPECS: tuple[RuntimeResourceSpec, ...] = (
    RuntimeResourceSpec(
        name="frontend",
        aliases=("frontend", "aspireshop.frontend", "aspireshop_frontend"),
        family="frontend",
        role="service",
    ),
    RuntimeResourceSpec(
        name="catalogservice",
        aliases=("catalogservice", "aspireshop.catalogservice", "aspireshop_catalogservice"),
        family="catalogservice",
        role="service",
    ),
    RuntimeResourceSpec(
        name="basketservice",
        aliases=("basketservice", "aspireshop.basketservice", "aspireshop_basketservice"),
        family="basketservice",
        role="service",
    ),
    RuntimeResourceSpec(name="postgres", aliases=("postgres",), family="postgres", role="dependency"),
    RuntimeResourceSpec(name="catalogdb", aliases=("catalogdb",), family="postgres", role="dependency"),
    RuntimeResourceSpec(
        name="catalogdbmanager",
        aliases=("catalogdbmanager", "aspireshop.catalogdbmanager", "aspireshop_catalogdbmanager"),
        family="postgres",
        role="support",
    ),
    RuntimeResourceSpec(name="basketcache", aliases=("basketcache",), family="redis", role="dependency"),
    RuntimeResourceSpec(name="rediscommander", aliases=("rediscommander",), family="redis", role="support"),
    RuntimeResourceSpec(name="pgadmin", aliases=("pgadmin",), family="postgres", role="support"),
)
_RUNTIME_SCOPE: dict[str, tuple[str, ...]] = {
    "aspire-shop": tuple(spec.name for spec in _RUNTIME_RESOURCE_SPECS),
    "frontend": ("frontend", "catalogservice", "basketservice", "postgres", "catalogdb", "basketcache"),
    "catalogservice": ("catalogservice", "postgres", "catalogdb", "catalogdbmanager"),
    "basketservice": ("basketservice", "basketcache", "rediscommander"),
}


def _render_label_selector(parts: list[str]) -> str:
    return ",".join(parts)


def _escape_promql_regex(pattern: str) -> str:
    return pattern.replace("\\", "\\\\").replace('"', '\\"')


@dataclass(slots=True)
class QueryTemplates:
    service: str
    scenario: str | None = None

    def error_logs(self) -> str:
        return f'{{service_name="{self.service}"}} |= "error"'

    def latency_metrics(self) -> str:
        profile = self._metric_profile()
        selector = _render_label_selector(profile.base_selector_parts(self.service))
        group_labels = ["le", "exported_job"]
        if profile.route_matcher:
            group_labels.append("http_route")
        return (
            "histogram_quantile(0.95, "
            f"sum(rate(http_server_request_duration_seconds_bucket{{{selector}}}[5m])) "
            f'by ({",".join(group_labels)}))'
        )

    def error_rate_metrics(self) -> str:
        profile = self._metric_profile()

        request_selector_parts = profile.base_selector_parts(self.service)
        request_selector_parts.append('http_response_status_code=~"499|5.."')
        request_group_labels = ["exported_job", "http_response_status_code", "error_type"]
        if profile.route_matcher:
            request_group_labels.insert(1, "http_route")
        request_query = (
            "sum(rate("
            f"http_server_request_duration_seconds_count{{{_render_label_selector(request_selector_parts)}}}[5m]"
            ")) "
            f'by ({",".join(request_group_labels)})'
        )

        exception_queries = [request_query]

        if profile.exception_error_pattern:
            escaped_exception_pattern = _escape_promql_regex(profile.exception_error_pattern)
            exception_queries.append(
                "sum(rate("
                f'aspnetcore_diagnostics_exceptions_total{{exported_job="{self.service}",'
                f'error_type=~"{escaped_exception_pattern}"}}[5m]'
                ")) "
                "by (exported_job,error_type)"
            )

        if profile.runtime_exception_pattern:
            escaped_runtime_pattern = _escape_promql_regex(profile.runtime_exception_pattern)
            exception_queries.append(
                "sum(rate("
                f'dotnet_exceptions_total{{exported_job="{self.service}",'
                f'error_type=~"{escaped_runtime_pattern}"}}[5m]'
                ")) "
                "by (exported_job,error_type)"
            )

        return " or ".join(exception_queries)

    def failed_traces(self) -> str:
        profile = self._metric_profile()
        trace_filter = profile.failed_trace_filter or "status = error"
        return f'{{ resource.service.name = "{self.service}" && {trace_filter} }} with (most_recent=true)'

    def slow_traces(self) -> str:
        return f'{{ resource.service.name = "{self.service}" && trace:duration > 1s }} with (most_recent=true)'

    @classmethod
    def for_alert(cls, alert: AlertPayload) -> "QueryTemplates":
        return cls(service=alert.service, scenario=alert.scenario)

    def _metric_profile(self) -> LiveMetricProfile:
        return _LIVE_METRIC_PROFILES.get(
            (self.service, self.scenario),
            _LIVE_METRIC_PROFILES.get((self.service, None), _DEFAULT_METRIC_PROFILE),
        )


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


def _parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(UTC)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _coerce_timestamp(value: str | int | float | None, fallback: str) -> str:
    if value is None:
        return fallback

    if isinstance(value, str):
        try:
            return _format_datetime(_parse_datetime(value))
        except ValueError:
            try:
                numeric = float(value)
            except ValueError:
                return fallback
    else:
        numeric = float(value)

    if abs(numeric) > 1_000_000_000_000_000:
        numeric /= 1_000_000_000
    elif abs(numeric) > 1_000_000_000_000:
        numeric /= 1_000
    return _format_datetime(datetime.fromtimestamp(numeric, tz=UTC))


def _time_window(alert: AlertPayload) -> tuple[str, str]:
    center = _parse_datetime(alert.started_at)
    delta = timedelta(minutes=alert.window_minutes)
    return _format_datetime(center - delta), _format_datetime(center + delta)


def _time_window_unix_seconds(alert: AlertPayload) -> tuple[str, str]:
    center = _parse_datetime(alert.started_at)
    delta = timedelta(minutes=alert.window_minutes)
    start = int((center - delta).timestamp())
    end = int((center + delta).timestamp())
    return str(start), str(end)


def _prometheus_step(alert: AlertPayload) -> str:
    seconds = max(15, min(60, alert.window_minutes * 2))
    return f"{seconds}s"


def _clip_text(value: str, limit: int = 240) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _unique_nonempty_lines(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        normalized = line.strip()
        if not normalized or normalized in seen:
            continue
        result.append(normalized)
        seen.add(normalized)
    return result


def _interesting_log_lines(lines: list[str], *, limit: int = 3) -> list[str]:
    error_terms = (
        "error",
        "exception",
        "fail",
        "fatal",
        "timeout",
        "timed out",
        "refused",
        "unavailable",
        "panic",
        "crash",
    )
    unique_lines = _unique_nonempty_lines(lines)
    prioritized = [line for line in reversed(unique_lines) if any(term in line.lower() for term in error_terms)]
    if prioritized:
        return prioritized[:limit]
    return list(reversed(unique_lines[-limit:]))


def _format_labels(labels: dict[str, str]) -> str:
    pairs = [f"{key}={value}" for key, value in sorted(labels.items()) if key != "__name__"]
    return ", ".join(pairs)


def _runtime_scope_for_services(services: list[str] | None) -> list[RuntimeResourceSpec]:
    requested = services or ["aspire-shop"]
    wanted: list[str] = []
    for service in requested:
        wanted.extend(_RUNTIME_SCOPE.get(service, (service,)))

    seen: set[str] = set()
    specs: list[RuntimeResourceSpec] = []
    for spec in _RUNTIME_RESOURCE_SPECS:
        if spec.name in wanted and spec.name not in seen:
            specs.append(spec)
            seen.add(spec.name)
    return specs


def _resource_name_matches(container_name: str, spec: RuntimeResourceSpec) -> bool:
    lowered = container_name.lower()
    for alias in spec.aliases:
        pattern = rf"(^|[-_.]){re.escape(alias)}($|[-_.])"
        if re.search(pattern, lowered):
            return True
    return False


def _process_name_matches(command_text: str, spec: RuntimeResourceSpec) -> bool:
    lowered = command_text.lower()
    normalized = re.sub(r"[^a-z0-9]+", "", lowered)
    for alias in spec.aliases:
        alias_lower = alias.lower()
        if alias_lower in lowered:
            return True
        alias_normalized = re.sub(r"[^a-z0-9]+", "", alias_lower)
        if alias_normalized and alias_normalized in normalized:
            return True
    return False


def _runtime_state_bucket(status_text: str) -> str:
    lowered = status_text.lower()
    if lowered.startswith("up"):
        return "unhealthy" if "unhealthy" in lowered else "running"
    if lowered.startswith("exited"):
        return "exited"
    if lowered.startswith("restarting"):
        return "restarting"
    if lowered.startswith("created"):
        return "created"
    if "dead" in lowered:
        return "dead"
    return "unknown"


def _process_state_bucket(status_text: str) -> str:
    lowered = status_text.strip().lower()
    if not lowered:
        return "unknown"
    if lowered.startswith("z"):
        return "dead"
    if lowered.startswith("t"):
        return "unknown"
    return "running"


class MockTelemetryBackend:
    def get_runtime_states(self, alert: AlertPayload, services: list[str] | None = None) -> ToolResult:
        requested = ",".join(services or [alert.service])
        return ToolResult(query=f"runtime:{requested}", items=[])

    def get_runtime_log_tails(self, alert: AlertPayload, runtime_items: list[EvidenceItem]) -> ToolResult:
        resources = ",".join(str(item.metadata.get("resource") or item.service) for item in runtime_items)
        return ToolResult(query=f"runtime-log-tails:{resources}", items=[])

    def get_error_logs(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult:
        query = QueryTemplates.for_alert(alert).error_logs()
        items = [
            _build_evidence(alert, seed, "logs", "loki", "mock-loki", query)
            for seed in scenario.logs
        ]
        return ToolResult(query=query, items=items)

    def get_latency_metrics(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult:
        query = QueryTemplates.for_alert(alert).latency_metrics()
        items = [
            _build_evidence(alert, seed, "metrics", "prometheus", "mock-prometheus", query)
            for seed in scenario.metrics
            if seed.kind == "latency"
        ]
        return ToolResult(query=query, items=items)

    def get_error_rate_metrics(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult:
        query = QueryTemplates.for_alert(alert).error_rate_metrics()
        items = [
            _build_evidence(alert, seed, "metrics", "prometheus", "mock-prometheus", query)
            for seed in scenario.metrics
            if seed.kind == "error_rate"
        ]
        return ToolResult(query=query, items=items)

    def get_failed_traces(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult:
        query = QueryTemplates.for_alert(alert).failed_traces()
        items = [
            _build_evidence(alert, seed, "traces", "tempo", "mock-tempo", query)
            for seed in scenario.traces
            if seed.kind == "failed_trace"
        ]
        return ToolResult(query=query, items=items)

    def get_slow_traces(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult:
        query = QueryTemplates.for_alert(alert).slow_traces()
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


class PLTHttpTelemetryBackend:
    def __init__(
        self,
        *,
        prometheus_base_url: str,
        loki_base_url: str,
        tempo_base_url: str,
        timeout_seconds: float = 10.0,
        verify_tls: bool = True,
        tenant_id: str | None = None,
        docker_command: str = "docker",
        client: httpx.Client | None = None,
    ) -> None:
        self.prometheus_base_url = prometheus_base_url.rstrip("/")
        self.loki_base_url = loki_base_url.rstrip("/")
        self.tempo_base_url = tempo_base_url.rstrip("/")
        self.tenant_id = tenant_id
        self.docker_command = docker_command
        self.client = client or httpx.Client(timeout=timeout_seconds, verify=verify_tls, follow_redirects=True)

    def get_runtime_states(self, alert: AlertPayload, services: list[str] | None = None) -> ToolResult:
        specs = _runtime_scope_for_services(services or [alert.service])
        query = (
            f"{self.docker_command} ps -a --format '{{{{.Names}}}}\\t{{{{.Status}}}}\\t{{{{.Image}}}}'; "
            "ps -eo pid=,stat=,args="
        )
        if not specs:
            return ToolResult(query=query, items=[])

        try:
            completed = subprocess.run(
                [*shlex.split(self.docker_command), "ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"],
                capture_output=True,
                text=True,
                check=True,
            )
            docker_lines = completed.stdout.splitlines()
        except (OSError, subprocess.CalledProcessError):
            docker_lines = []

        try:
            process_listing = subprocess.run(
                ["ps", "-eo", "pid=,stat=,args="],
                capture_output=True,
                text=True,
                check=True,
            )
            process_lines = process_listing.stdout.splitlines()
        except (OSError, subprocess.CalledProcessError):
            process_lines = []

        containers: list[tuple[str, str, str]] = []
        for raw_line in docker_lines:
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            containers.append((parts[0], parts[1], parts[2]))

        processes: list[tuple[str, str, str]] = []
        for raw_line in process_lines:
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split(None, 2)
            if len(parts) != 3:
                continue
            processes.append((parts[0], parts[1], parts[2]))

        items: list[EvidenceItem] = []
        for spec in specs:
            match = next((container for container in containers if _resource_name_matches(container[0], spec)), None)
            if match is not None:
                name, status_text, image = match
                state = _runtime_state_bucket(status_text)
                items.append(
                    EvidenceItem(
                        id=f"runtime.{spec.name}",
                        category="runtime_states",
                        service=spec.name,
                        summary=f"Runtime state for {spec.name}: {state}",
                        value=status_text,
                        citations=[
                            Citation(
                                id=f"runtime.{spec.name}.citation",
                                source_type="runtime",
                                source="docker ps -a",
                                query=query,
                                observed_at=alert.started_at,
                                excerpt=f"container={name}; status={status_text}; image={image}",
                            )
                        ],
                        metadata={
                            "resource": spec.name,
                            "family": spec.family,
                            "role": spec.role,
                            "state": state,
                            "container_name": name,
                            "image": image,
                        },
                    )
                )
                continue

            process_match = next((process for process in processes if _process_name_matches(process[2], spec)), None)
            if process_match is not None:
                pid, status_text, command_text = process_match
                state = _process_state_bucket(status_text)
                items.append(
                    EvidenceItem(
                        id=f"runtime.{spec.name}",
                        category="runtime_states",
                        service=spec.name,
                        summary=f"Runtime state for {spec.name}: {state}",
                        value=f"pid={pid}; stat={status_text}",
                        citations=[
                            Citation(
                                id=f"runtime.{spec.name}.citation",
                                source_type="runtime",
                                source="ps -eo",
                                query=query,
                                observed_at=alert.started_at,
                                excerpt=f"pid={pid}; stat={status_text}; args={_clip_text(command_text)}",
                            )
                        ],
                        metadata={
                            "resource": spec.name,
                            "family": spec.family,
                            "role": spec.role,
                            "state": state,
                            "pid": pid,
                            "process_args": command_text,
                        },
                    )
                )
                continue

            if spec.role != "service":
                continue

            items.append(
                EvidenceItem(
                    id=f"runtime.{spec.name}",
                    category="runtime_states",
                    service=spec.name,
                    summary=f"Runtime state for {spec.name}: missing",
                    value="Process not observed in docker or local process list",
                    citations=[
                        Citation(
                            id=f"runtime.{spec.name}.citation",
                            source_type="runtime",
                            source="docker ps -a / ps -eo",
                            query=query,
                            observed_at=alert.started_at,
                            excerpt="resource not observed in local process or container listings",
                        )
                    ],
                    metadata={
                        "resource": spec.name,
                        "family": spec.family,
                        "role": spec.role,
                        "state": "missing",
                    },
                )
            )

        severity_order = {
            "dead": 0,
            "restarting": 1,
            "missing": 2,
            "exited": 3,
            "unhealthy": 4,
            "created": 5,
            "unknown": 6,
            "running": 7,
        }
        items.sort(key=lambda item: (severity_order.get(str(item.metadata.get("state")), 99), str(item.metadata.get("resource"))))
        return ToolResult(query=query, items=items)

    def get_runtime_log_tails(self, alert: AlertPayload, runtime_items: list[EvidenceItem]) -> ToolResult:
        targeted_items = [
            item
            for item in runtime_items
            if str(item.metadata.get("state", "")).lower() != "running"
            and str(item.metadata.get("role", "")).lower() in {"service", "dependency"}
        ]
        resources = [
            str(item.metadata.get("resource") or item.service)
            for item in targeted_items
        ]
        if not resources:
            return ToolResult(query="runtime-log-tails:none", items=[])

        items: list[EvidenceItem] = []
        seen_resources: set[str] = set()
        for runtime_item in targeted_items:
            resource = str(runtime_item.metadata.get("resource") or runtime_item.service)
            if resource in seen_resources:
                continue
            seen_resources.add(resource)

            container_name = str(runtime_item.metadata.get("container_name") or "").strip()
            if container_name:
                items.extend(self._docker_tail_items(alert, runtime_item, container_name))
                continue

            if str(runtime_item.metadata.get("role", "")).lower() == "service":
                items.extend(self._loki_tail_items(alert, runtime_item))

        return ToolResult(query=f"runtime-log-tails:{','.join(resources)}", items=items[:12])

    def get_error_logs(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult:
        query = QueryTemplates.for_alert(alert).error_logs()
        start, end = _time_window(alert)
        payload = self._request(
            "loki",
            f"{self.loki_base_url}/loki/api/v1/query_range",
            params={"query": query, "start": start, "end": end, "limit": 10, "direction": "backward"},
        )
        streams = payload.get("result", [])
        items: list[EvidenceItem] = []
        line_number = 0
        for stream in streams:
            labels = stream.get("stream", {})
            for raw_ts, line in stream.get("values", []):
                line_number += 1
                if line_number > 5:
                    break
                observed_at = _coerce_timestamp(raw_ts, alert.started_at)
                label_text = _format_labels(labels)
                excerpt = _clip_text(str(line))
                items.append(
                    EvidenceItem(
                        id=f"log.live.error.{line_number}",
                        category="logs",
                        service=alert.service,
                        summary=f"Loki error log for {alert.service}" + (f" ({label_text})" if label_text else ""),
                        value=excerpt,
                        citations=[
                            Citation(
                                id=f"log.live.error.{line_number}.citation",
                                source_type="loki",
                                source=f"{self.loki_base_url}/loki/api/v1/query_range",
                                query=query,
                                observed_at=observed_at,
                                excerpt=excerpt,
                            )
                        ],
                        metadata={"labels": labels},
                    )
                )
            if line_number > 5:
                break
        return ToolResult(query=query, items=items)

    def get_latency_metrics(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult:
        query = QueryTemplates.for_alert(alert).latency_metrics()
        return self._prometheus_items(
            alert,
            query,
            self._query_prometheus(query, alert),
            id_prefix="metric.live.latency",
            summary_prefix=f"Prometheus latency signal for {alert.service}",
        )

    def get_error_rate_metrics(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult:
        query = QueryTemplates.for_alert(alert).error_rate_metrics()
        return self._prometheus_items(
            alert,
            query,
            self._query_prometheus(query, alert),
            id_prefix="metric.live.error_rate",
            summary_prefix=f"Prometheus error-rate signal for {alert.service}",
        )

    def get_failed_traces(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult:
        query = QueryTemplates.for_alert(alert).failed_traces()
        return self._tempo_items(
            alert,
            query,
            self._search_tempo(query, alert),
            id_prefix="trace.live.failed",
            summary_prefix=f"Tempo failed trace match for {alert.service}",
        )

    def get_slow_traces(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult:
        query = QueryTemplates.for_alert(alert).slow_traces()
        return self._tempo_items(
            alert,
            query,
            self._search_tempo(query, alert),
            id_prefix="trace.live.slow",
            summary_prefix=f"Tempo slow trace match for {alert.service}",
        )

    def get_recent_changes(self, alert: AlertPayload, scenario: ScenarioDefinition) -> ToolResult:
        query = f"service={alert.service} within={alert.window_minutes}m"
        items = [
            _build_evidence(alert, seed, "change_signals", "change-log", "static-change-feed", query)
            for seed in scenario.changes
        ]
        return ToolResult(query=query, items=items)

    def _headers(self) -> dict[str, str]:
        if not self.tenant_id:
            return {}
        return {"X-Scope-OrgID": self.tenant_id}

    def _request(self, backend_name: str, url: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.client.get(url, params=params, headers=self._headers())
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TelemetryRequestError(f"{backend_name} query failed: {exc}") from exc

        payload = response.json()
        status = payload.get("status")
        if status not in {None, "success"}:
            raise TelemetryRequestError(f"{backend_name} query returned status={status!r}: {payload}")
        return payload.get("data", payload)

    def _query_prometheus(self, query: str, alert: AlertPayload) -> list[dict[str, Any]]:
        start, end = _time_window(alert)
        payload = self._request(
            "prometheus",
            f"{self.prometheus_base_url}/api/v1/query_range",
            params={"query": query, "start": start, "end": end, "step": _prometheus_step(alert)},
        )
        return payload.get("result", [])

    def _search_tempo(self, query: str, alert: AlertPayload) -> list[dict[str, Any]]:
        start, end = _time_window_unix_seconds(alert)
        payload = self._request(
            "tempo",
            f"{self.tempo_base_url}/api/search",
            params={"q": query, "start": start, "end": end, "limit": 5},
        )
        candidates = [
            payload.get("traces"),
            payload.get("results"),
            payload.get("result"),
            payload.get("data", {}).get("traces") if isinstance(payload.get("data"), dict) else None,
        ]
        for candidate in candidates:
            if isinstance(candidate, list):
                return candidate
        return []

    def _prometheus_items(
        self,
        alert: AlertPayload,
        query: str,
        series_list: list[dict[str, Any]],
        *,
        id_prefix: str,
        summary_prefix: str,
    ) -> ToolResult:
        items: list[EvidenceItem] = []
        for index, series in enumerate(series_list[:3], start=1):
            metric = series.get("metric", {})
            observed_at, latest_value = self._latest_prometheus_sample(series, alert.started_at)
            label_text = _format_labels(metric)
            excerpt = f"latest={latest_value}" + (f"; labels={label_text}" if label_text else "")
            items.append(
                EvidenceItem(
                    id=f"{id_prefix}.{index}",
                    category="metrics",
                    service=alert.service,
                    summary=summary_prefix + (f" ({label_text})" if label_text else ""),
                    value=str(latest_value),
                    citations=[
                        Citation(
                            id=f"{id_prefix}.{index}.citation",
                            source_type="prometheus",
                            source=f"{self.prometheus_base_url}/api/v1/query_range",
                            query=query,
                            observed_at=observed_at,
                            excerpt=excerpt,
                        )
                    ],
                    metadata={"metric": metric},
                )
            )
        return ToolResult(query=query, items=items)

    def _latest_prometheus_sample(self, series: dict[str, Any], fallback: str) -> tuple[str, str]:
        if "values" in series and series["values"]:
            timestamp, value = series["values"][-1]
            return _coerce_timestamp(timestamp, fallback), str(value)
        if "value" in series and series["value"]:
            timestamp, value = series["value"]
            return _coerce_timestamp(timestamp, fallback), str(value)
        return fallback, "unknown"

    def _tempo_items(
        self,
        alert: AlertPayload,
        query: str,
        traces: list[dict[str, Any]],
        *,
        id_prefix: str,
        summary_prefix: str,
    ) -> ToolResult:
        items: list[EvidenceItem] = []
        for index, trace in enumerate(traces[:3], start=1):
            trace_id = (
                trace.get("traceID")
                or trace.get("traceId")
                or trace.get("trace_id")
                or f"unknown-{index}"
            )
            root_service = trace.get("rootServiceName") or trace.get("rootService") or alert.service
            root_name = trace.get("rootTraceName") or trace.get("rootName") or trace.get("spanName") or "trace match"
            duration = (
                trace.get("durationMs")
                or trace.get("durationNanos")
                or trace.get("duration")
                or "unknown"
            )
            observed_at = _coerce_timestamp(
                trace.get("startTimeUnixNano") or trace.get("startTimeUnixMs") or trace.get("startTime"),
                alert.started_at,
            )
            excerpt = f"trace_id={trace_id}; root={root_name}; service={root_service}; duration={duration}"
            items.append(
                EvidenceItem(
                    id=f"{id_prefix}.{index}",
                    category="traces",
                    service=alert.service,
                    summary=summary_prefix + f" ({root_name})",
                    value=str(duration),
                    citations=[
                        Citation(
                            id=f"{id_prefix}.{index}.citation",
                            source_type="tempo",
                            source=f"{self.tempo_base_url}/api/search",
                            query=query,
                            observed_at=observed_at,
                            excerpt=excerpt,
                        )
                    ],
                    metadata={"trace_id": trace_id, "root_service": root_service},
                )
            )
        return ToolResult(query=query, items=items)

    def _docker_tail_items(
        self,
        alert: AlertPayload,
        runtime_item: EvidenceItem,
        container_name: str,
    ) -> list[EvidenceItem]:
        resource = str(runtime_item.metadata.get("resource") or runtime_item.service)
        query = f"{self.docker_command} logs --tail 25 {container_name}"
        try:
            completed = subprocess.run(
                [*shlex.split(self.docker_command), "logs", "--tail", "25", container_name],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return []

        combined = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
        selected_lines = _interesting_log_lines(combined.splitlines(), limit=3)
        items: list[EvidenceItem] = []
        for index, line in enumerate(selected_lines, start=1):
            excerpt = _clip_text(line)
            items.append(
                EvidenceItem(
                    id=f"log.runtime.tail.{resource}.docker.{index}",
                    category="logs",
                    service=resource,
                    summary=f"Recent runtime log tail for {resource}",
                    value=excerpt,
                    citations=[
                        Citation(
                            id=f"log.runtime.tail.{resource}.docker.{index}.citation",
                            source_type="docker-logs",
                            source=f"docker logs {container_name}",
                            query=query,
                            observed_at=alert.started_at,
                            excerpt=excerpt,
                        )
                    ],
                    metadata={
                        "resource": resource,
                        "origin": "docker-logs",
                        "container_name": container_name,
                    },
                )
            )
        return items

    def _loki_tail_items(self, alert: AlertPayload, runtime_item: EvidenceItem) -> list[EvidenceItem]:
        resource = str(runtime_item.metadata.get("resource") or runtime_item.service)
        query = f'{{service_name="{resource}"}}'
        start, end = _time_window(alert)
        payload = self._request(
            "loki",
            f"{self.loki_base_url}/loki/api/v1/query_range",
            params={"query": query, "start": start, "end": end, "limit": 20, "direction": "backward"},
        )
        streams = payload.get("result", [])
        collected: list[tuple[str, str]] = []
        for stream in streams:
            for raw_ts, line in stream.get("values", []):
                collected.append((_coerce_timestamp(raw_ts, alert.started_at), str(line)))

        selected_lines = _interesting_log_lines([line for _, line in collected], limit=3)
        items: list[EvidenceItem] = []
        for index, line in enumerate(selected_lines, start=1):
            observed_at = next((timestamp for timestamp, text in collected if text.strip() == line.strip()), alert.started_at)
            excerpt = _clip_text(line)
            items.append(
                EvidenceItem(
                    id=f"log.runtime.tail.{resource}.loki.{index}",
                    category="logs",
                    service=resource,
                    summary=f"Recent runtime log tail for {resource}",
                    value=excerpt,
                    citations=[
                        Citation(
                            id=f"log.runtime.tail.{resource}.loki.{index}.citation",
                            source_type="loki",
                            source=f"{self.loki_base_url}/loki/api/v1/query_range",
                            query=query,
                            observed_at=observed_at,
                            excerpt=excerpt,
                        )
                    ],
                    metadata={"resource": resource, "origin": "loki-tail"},
                )
            )
        return items


def build_telemetry_backend(settings: "Settings", *, client: httpx.Client | None = None) -> TelemetryBackend:
    backend_name = settings.telemetry_backend.strip().lower()
    if backend_name == "mock":
        return MockTelemetryBackend()
    if backend_name != "plt":
        raise TelemetryConfigurationError(
            f"Unsupported IIRS_TELEMETRY_BACKEND={settings.telemetry_backend!r}. Use 'mock' or 'plt'."
        )

    missing = [
        name
        for name, value in {
            "IIRS_PROMETHEUS_URL": settings.prometheus_base_url,
            "IIRS_LOKI_URL": settings.loki_base_url,
            "IIRS_TEMPO_URL": settings.tempo_base_url,
        }.items()
        if not value
    ]
    if missing:
        if settings.allow_backend_fallback:
            return MockTelemetryBackend()
        raise TelemetryConfigurationError(
            "PLT backend selected but missing required environment variables: " + ", ".join(missing)
        )

    return PLTHttpTelemetryBackend(
        prometheus_base_url=settings.prometheus_base_url or "",
        loki_base_url=settings.loki_base_url or "",
        tempo_base_url=settings.tempo_base_url or "",
        timeout_seconds=settings.http_timeout_seconds,
        verify_tls=settings.verify_tls,
        tenant_id=settings.tenant_id,
        docker_command=settings.docker_command,
        client=client,
    )
