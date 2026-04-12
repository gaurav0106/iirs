from __future__ import annotations

import json
from pathlib import Path

from iirs.models import AlertPayload, Citation, EvidenceItem, ToolResult


ROOT = Path(__file__).resolve().parents[1]


def load_alert_fixture(name: str) -> AlertPayload:
    path = ROOT / "fixtures" / "alerts" / f"{name}.json"
    with path.open(encoding="utf-8") as handle:
        return AlertPayload.from_mapping(json.load(handle))


class NoopTelemetryBackend:
    def get_error_logs(self, alert):
        return ToolResult(query=f"logs:{alert.service}", items=[])

    def get_latency_metrics(self, alert):
        return ToolResult(query=f"latency:{alert.service}", items=[])

    def get_error_rate_metrics(self, alert):
        return ToolResult(query=f"errors:{alert.service}", items=[])

    def get_failed_traces(self, alert):
        return ToolResult(query=f"failed-traces:{alert.service}", items=[])

    def get_slow_traces(self, alert):
        return ToolResult(query=f"slow-traces:{alert.service}", items=[])

    def get_recent_changes(self, alert):
        return ToolResult(query=f"changes:{alert.service}", items=[])

    def get_runtime_states(self, alert, services=None):
        requested = services or [alert.service]
        return ToolResult(query=f"runtime:{','.join(requested)}", items=[])

    def get_runtime_log_tails(self, alert, runtime_items):
        return ToolResult(query="runtime-log-tails:none", items=[])


class StaticScenarioTelemetryBackend(NoopTelemetryBackend):
    def _item(
        self,
        *,
        item_id: str,
        category: str,
        service: str,
        summary: str,
        value: str,
        source_type: str,
        excerpt: str,
        metadata: dict[str, str] | None = None,
    ) -> EvidenceItem:
        return EvidenceItem(
            id=item_id,
            category=category,
            service=service,
            summary=summary,
            value=value,
            citations=[
                Citation(
                    id=f"{item_id}.citation",
                    source_type=source_type,
                    source="static-test-telemetry",
                    query=f"service={service}",
                    observed_at="2026-04-11T00:00:00Z",
                    excerpt=excerpt,
                )
            ],
            metadata=metadata or {},
        )

    def _is_postgres_alert(self, alert) -> bool:
        return alert.service == "catalogservice" or alert.scenario == "postgres_down"

    def _is_redis_alert(self, alert) -> bool:
        return alert.service == "basketservice" or alert.scenario == "redis_down"

    def get_error_logs(self, alert):
        if self._is_postgres_alert(alert):
            items = [
                self._item(
                    item_id="log.pg.connection_refused",
                    category="logs",
                    service="catalogservice",
                    summary="catalogservice failed to connect to PostgreSQL",
                    value="connection refused",
                    source_type="loki",
                    excerpt="NpgsqlException: connection refused to postgres",
                )
            ]
        elif self._is_redis_alert(alert):
            items = [
                self._item(
                    item_id="log.redis.connection_failed",
                    category="logs",
                    service="basketservice",
                    summary="basketservice failed to connect to Redis",
                    value="connection refused",
                    source_type="loki",
                    excerpt="RedisConnectionException: failed to connect to basketcache",
                )
            ]
        else:
            items = []
        return ToolResult(query=f"logs:{alert.service}", items=items)

    def get_latency_metrics(self, alert):
        if self._is_postgres_alert(alert):
            items = [
                self._item(
                    item_id="metric.pg.latency",
                    category="metrics",
                    service="catalogservice",
                    summary="catalogservice latency spiked during database retries",
                    value="4.2s",
                    source_type="prometheus",
                    excerpt="catalogservice p95 latency rose above 4 seconds",
                )
            ]
        elif self._is_redis_alert(alert):
            items = [
                self._item(
                    item_id="metric.redis.latency",
                    category="metrics",
                    service="basketservice",
                    summary="basketservice latency spiked during Redis timeouts",
                    value="3.9s",
                    source_type="prometheus",
                    excerpt="basketservice p95 latency rose above 3 seconds",
                )
            ]
        else:
            items = []
        return ToolResult(query=f"latency:{alert.service}", items=items)

    def get_error_rate_metrics(self, alert):
        if self._is_postgres_alert(alert):
            items = [
                self._item(
                    item_id="metric.pg.error_rate",
                    category="metrics",
                    service="catalogservice",
                    summary="catalogservice 5xx rate increased because PostgreSQL retries failed",
                    value="0.8 req/s",
                    source_type="prometheus",
                    excerpt="RetryLimitExceededException from PostgreSQL dependency",
                )
            ]
        elif self._is_redis_alert(alert):
            items = [
                self._item(
                    item_id="metric.redis.error_rate",
                    category="metrics",
                    service="basketservice",
                    summary="basketservice 5xx rate increased because Redis lookups failed",
                    value="0.7 req/s",
                    source_type="prometheus",
                    excerpt="RedisConnectionException and RedisTimeoutException surged",
                )
            ]
        else:
            items = []
        return ToolResult(query=f"errors:{alert.service}", items=items)

    def get_failed_traces(self, alert):
        if self._is_postgres_alert(alert):
            items = [
                self._item(
                    item_id="trace.pg.checkout_failure",
                    category="traces",
                    service="catalogservice",
                    summary="catalogservice trace failed while opening a PostgreSQL connection",
                    value="error",
                    source_type="tempo",
                    excerpt="db.connect span failed with connection refused to postgres",
                )
            ]
        elif self._is_redis_alert(alert):
            items = [
                self._item(
                    item_id="trace.redis.checkout_failure",
                    category="traces",
                    service="basketservice",
                    summary="basketservice trace failed while contacting Redis",
                    value="error",
                    source_type="tempo",
                    excerpt="cache.get span failed with Redis timeout",
                )
            ]
        else:
            items = []
        return ToolResult(query=f"failed-traces:{alert.service}", items=items)

    def get_slow_traces(self, alert):
        if self._is_postgres_alert(alert):
            items = [
                self._item(
                    item_id="trace.pg.slow_checkout",
                    category="traces",
                    service="catalogservice",
                    summary="catalogservice trace slowed down before the PostgreSQL failure",
                    value="6.1s",
                    source_type="tempo",
                    excerpt="db.connect span retried for 6 seconds",
                )
            ]
        elif self._is_redis_alert(alert):
            items = [
                self._item(
                    item_id="trace.redis.slow_checkout",
                    category="traces",
                    service="basketservice",
                    summary="basketservice trace slowed down before the Redis timeout",
                    value="5.4s",
                    source_type="tempo",
                    excerpt="cache.get span retried for 5 seconds",
                )
            ]
        else:
            items = []
        return ToolResult(query=f"slow-traces:{alert.service}", items=items)

    def get_recent_changes(self, alert):
        if self._is_postgres_alert(alert):
            items = [
                self._item(
                    item_id="change.pg.none",
                    category="change_signals",
                    service="catalogservice",
                    summary="No recent deploy or config change explains the PostgreSQL outage",
                    value="no change detected",
                    source_type="git",
                    excerpt="no deploys or config edits near incident start",
                )
            ]
        elif self._is_redis_alert(alert):
            items = [
                self._item(
                    item_id="change.redis.none",
                    category="change_signals",
                    service="basketservice",
                    summary="No recent deploy or config change explains the Redis outage",
                    value="no change detected",
                    source_type="git",
                    excerpt="no deploys or config edits near incident start",
                )
            ]
        else:
            items = []
        return ToolResult(query=f"changes:{alert.service}", items=items)

    def get_runtime_states(self, alert, services=None):
        items: list[EvidenceItem] = []
        if self._is_postgres_alert(alert):
            items.extend(
                [
                    self._item(
                        item_id="runtime.catalogservice",
                        category="runtime_states",
                        service="catalogservice",
                        summary="Runtime state for catalogservice: running",
                        value="Up 8 minutes",
                        source_type="runtime",
                        excerpt="container=aspire-catalogservice-1; status=Up 8 minutes",
                        metadata={
                            "resource": "catalogservice",
                            "family": "catalogservice",
                            "role": "service",
                            "state": "running",
                        },
                    ),
                    self._item(
                        item_id="runtime.postgres",
                        category="runtime_states",
                        service="postgres",
                        summary="Runtime state for postgres: exited",
                        value="Exited (1) 1 minute ago",
                        source_type="runtime",
                        excerpt="container=aspire-postgres-1; status=Exited (1) 1 minute ago",
                        metadata={
                            "resource": "postgres",
                            "family": "postgres",
                            "role": "dependency",
                            "state": "exited",
                            "container_name": "aspire-postgres-1",
                        },
                    ),
                ]
            )
        elif self._is_redis_alert(alert):
            items.extend(
                [
                    self._item(
                        item_id="runtime.basketservice",
                        category="runtime_states",
                        service="basketservice",
                        summary="Runtime state for basketservice: running",
                        value="Up 8 minutes",
                        source_type="runtime",
                        excerpt="container=aspire-basketservice-1; status=Up 8 minutes",
                        metadata={
                            "resource": "basketservice",
                            "family": "basketservice",
                            "role": "service",
                            "state": "running",
                        },
                    ),
                    self._item(
                        item_id="runtime.basketcache",
                        category="runtime_states",
                        service="basketcache",
                        summary="Runtime state for basketcache: exited",
                        value="Exited (1) 1 minute ago",
                        source_type="runtime",
                        excerpt="container=aspire-basketcache-1; status=Exited (1) 1 minute ago",
                        metadata={
                            "resource": "basketcache",
                            "family": "redis",
                            "role": "dependency",
                            "state": "exited",
                            "container_name": "aspire-basketcache-1",
                        },
                    ),
                ]
            )
        requested = services or [alert.service]
        return ToolResult(
            query=f"runtime:{','.join(requested)}",
            items=[
                item
                for item in items
                if item.service in requested or str(item.metadata.get("role", "")).lower() == "dependency"
            ],
        )

    def get_runtime_log_tails(self, alert, runtime_items):
        items = []
        for item in runtime_items:
            resource = str(item.metadata.get("resource") or item.service)
            if resource == "postgres":
                items.append(
                    self._item(
                        item_id="log.runtime.tail.postgres",
                        category="logs",
                        service="postgres",
                        summary="Recent PostgreSQL container logs show connection failures",
                        value="database system is shutting down",
                        source_type="runtime",
                        excerpt="postgres container exited after connection refused errors",
                    )
                )
            elif resource == "basketcache":
                items.append(
                    self._item(
                        item_id="log.runtime.tail.basketcache",
                        category="logs",
                        service="basketcache",
                        summary="Recent Redis container logs show failed startup",
                        value="Ready to accept connections then exited",
                        source_type="runtime",
                        excerpt="basketcache container exited after socket failures",
                    )
                )
        return ToolResult(query="runtime-log-tails", items=items)
