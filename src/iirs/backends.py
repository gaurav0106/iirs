from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TYPE_CHECKING, Protocol

import httpx

from .models import AlertPayload, Citation, EvidenceItem, ToolResult
from .scenarios import EvidenceSeed, ScenarioDefinition

if TYPE_CHECKING:
    from .config import Settings


class TelemetryBackend(Protocol):
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


@dataclass(slots=True)
class QueryTemplates:
    service: str

    def error_logs(self) -> str:
        return f'{{service_name="{self.service}"}} |= "error"'

    def latency_metrics(self) -> str:
        return (
            "histogram_quantile(0.95, "
            f'sum(rate(http_server_request_duration_seconds_bucket{{service_name="{self.service}"}}[5m])) by (le))'
        )

    def error_rate_metrics(self) -> str:
        return (
            "sum(rate("
            f'http_server_request_duration_seconds_count{{service_name="{self.service}",http_response_status_code=~"5.."}}'
            "[5m]))"
        )

    def failed_traces(self) -> str:
        return f'{{ resource.service.name = "{self.service}" && status = error }} with (most_recent=true)'

    def slow_traces(self) -> str:
        return f'{{ resource.service.name = "{self.service}" && trace:duration > 1s }} with (most_recent=true)'

    @classmethod
    def for_alert(cls, alert: AlertPayload) -> "QueryTemplates":
        return cls(service=alert.service)


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


def _prometheus_step(alert: AlertPayload) -> str:
    seconds = max(15, min(60, alert.window_minutes * 2))
    return f"{seconds}s"


def _clip_text(value: str, limit: int = 240) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _format_labels(labels: dict[str, str]) -> str:
    pairs = [f"{key}={value}" for key, value in sorted(labels.items()) if key != "__name__"]
    return ", ".join(pairs)


class MockTelemetryBackend:
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
        client: httpx.Client | None = None,
    ) -> None:
        self.prometheus_base_url = prometheus_base_url.rstrip("/")
        self.loki_base_url = loki_base_url.rstrip("/")
        self.tempo_base_url = tempo_base_url.rstrip("/")
        self.tenant_id = tenant_id
        self.client = client or httpx.Client(timeout=timeout_seconds, verify=verify_tls, follow_redirects=True)

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
        start, end = _time_window(alert)
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
        client=client,
    )
