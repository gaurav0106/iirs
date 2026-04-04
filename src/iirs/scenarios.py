from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import AlertPayload


@dataclass(slots=True)
class EvidenceSeed:
    id: str
    kind: str
    summary: str
    value: str
    excerpt: str
    observed_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScenarioDefinition:
    name: str
    service: str
    topic: str
    summary: str
    expected_root_cause: str
    secondary_hypothesis: str
    follow_up_checks: list[str]
    safe_actions: list[str]
    approval_actions: list[str]
    logs: list[EvidenceSeed]
    metrics: list[EvidenceSeed]
    traces: list[EvidenceSeed]
    changes: list[EvidenceSeed]


def get_builtin_scenarios() -> dict[str, ScenarioDefinition]:
    return {
        "postgres_down": ScenarioDefinition(
            name="postgres_down",
            service="checkoutservice",
            topic="postgresql troubleshooting",
            summary="checkoutservice is returning 5xx responses after losing PostgreSQL connectivity.",
            expected_root_cause="PostgreSQL dependency outage",
            secondary_hypothesis="Checkoutservice connection pool exhaustion caused by database unavailability",
            follow_up_checks=[
                "Confirm PostgreSQL container or pod health in the infrastructure layer.",
                "Verify whether connection failures started at the same time as the HTTP 5xx spike.",
                "Check whether any recent configuration change altered the database endpoint or credentials.",
            ],
            safe_actions=[
                "Inspect PostgreSQL health, restart history, and readiness probes without changing state.",
                "Correlate failing traces with the first DB connection-refused log lines.",
                "Validate recovery after mitigation by checking 5xx rate and latency for checkoutservice.",
            ],
            approval_actions=[
                "Restart or fail over PostgreSQL if the database remains unavailable.",
                "Roll back any database connectivity configuration change if a recent change is implicated.",
            ],
            logs=[
                EvidenceSeed(
                    id="log.pg.connection_refused",
                    kind="error_log",
                    summary="checkoutservice failed to connect to PostgreSQL",
                    value="4 connection-refused events in 2m",
                    excerpt="Npgsql.NpgsqlException: Connection refused 10.0.2.15:5432 while handling POST /checkout",
                    observed_at="2026-04-04T09:15:00Z",
                ),
                EvidenceSeed(
                    id="log.pg.retry_exhausted",
                    kind="error_log",
                    summary="checkoutservice exhausted retry budget while acquiring a DB connection",
                    value="12 retries exhausted in 5m",
                    excerpt="RetryPolicy exhausted for dependency postgres after 12 attempts in checkout workflow",
                    observed_at="2026-04-04T09:16:00Z",
                ),
            ],
            metrics=[
                EvidenceSeed(
                    id="metric.pg.error_rate",
                    kind="error_rate",
                    summary="checkoutservice 5xx rate spiked sharply",
                    value="5xx rate 0.81 req/s over 5m",
                    excerpt="checkoutservice status=500 requests increased 9.4x from baseline",
                    observed_at="2026-04-04T09:17:00Z",
                ),
                EvidenceSeed(
                    id="metric.pg.latency",
                    kind="latency",
                    summary="checkoutservice p95 latency regressed during database outage",
                    value="p95 latency 4.2s over 5m",
                    excerpt="p95 request latency rose from 320ms to 4.2s while waiting on DB connection timeouts",
                    observed_at="2026-04-04T09:17:30Z",
                ),
            ],
            traces=[
                EvidenceSeed(
                    id="trace.pg.checkout_failure",
                    kind="failed_trace",
                    summary="checkout trace failed on database connect span",
                    value="73% of sampled traces ended with status=error",
                    excerpt="Span db.connect failed with ECONNREFUSED and the parent checkout pipeline aborted",
                    observed_at="2026-04-04T09:18:00Z",
                ),
                EvidenceSeed(
                    id="trace.pg.slow_checkout",
                    kind="slow_trace",
                    summary="slow traces are dominated by DB timeout waits",
                    value="max duration 6.8s",
                    excerpt="Most of the trace duration is spent in db.connect retries before the request fails",
                    observed_at="2026-04-04T09:18:30Z",
                ),
            ],
            changes=[
                EvidenceSeed(
                    id="change.pg.none",
                    kind="recent_change",
                    summary="No relevant deploy landed near incident start",
                    value="last deploy 3h earlier",
                    excerpt="The last checkoutservice deploy completed three hours before the incident started.",
                    observed_at="2026-04-04T09:14:00Z",
                ),
            ],
        ),
        "redis_down": ScenarioDefinition(
            name="redis_down",
            service="cartservice",
            topic="redis troubleshooting",
            summary="cartservice is timing out because Redis is unavailable.",
            expected_root_cause="Redis dependency outage",
            secondary_hypothesis="Cartservice thread starvation caused by repeated Redis retry loops",
            follow_up_checks=[
                "Confirm Redis container or pod health and whether port 6379 is accepting connections.",
                "Compare cache timeout traces with the first Redis connection errors.",
                "Check for any configuration or network policy changes affecting the cache endpoint.",
            ],
            safe_actions=[
                "Inspect Redis health, memory pressure, and restart history without modifying state.",
                "Correlate cartservice timeout traces with Redis connection errors.",
                "Validate cartservice latency and error-rate recovery after mitigation.",
            ],
            approval_actions=[
                "Restart or fail over Redis if it is confirmed down.",
                "Repoint cartservice to a healthy cache endpoint if configuration drift is confirmed.",
            ],
            logs=[
                EvidenceSeed(
                    id="log.redis.connection_refused",
                    kind="error_log",
                    summary="cartservice failed to connect to Redis",
                    value="18 connection-refused events in 3m",
                    excerpt="StackExchange.Redis.RedisConnectionException: No connection is active/available to service this operation",
                    observed_at="2026-04-04T10:05:00Z",
                ),
                EvidenceSeed(
                    id="log.redis.timeout",
                    kind="error_log",
                    summary="cartservice requests timed out while waiting for cache responses",
                    value="27 timeout logs in 5m",
                    excerpt="Timeout awaiting response from redis:6379 during cart lookup",
                    observed_at="2026-04-04T10:05:40Z",
                ),
            ],
            metrics=[
                EvidenceSeed(
                    id="metric.redis.error_rate",
                    kind="error_rate",
                    summary="cartservice error-rate climbed sharply after Redis became unavailable",
                    value="5xx rate 0.63 req/s over 5m",
                    excerpt="cartservice status=500 requests increased 7.1x from baseline",
                    observed_at="2026-04-04T10:06:00Z",
                ),
                EvidenceSeed(
                    id="metric.redis.latency",
                    kind="latency",
                    summary="cartservice p95 latency regressed while waiting on cache timeouts",
                    value="p95 latency 2.7s over 5m",
                    excerpt="p95 request latency rose from 180ms to 2.7s as cache calls stalled",
                    observed_at="2026-04-04T10:06:20Z",
                ),
            ],
            traces=[
                EvidenceSeed(
                    id="trace.redis.cart_failure",
                    kind="failed_trace",
                    summary="cart trace failed on cache get span",
                    value="68% of sampled traces ended with status=error",
                    excerpt="Span cache.get failed with connection refused and the request aborted",
                    observed_at="2026-04-04T10:07:00Z",
                ),
                EvidenceSeed(
                    id="trace.redis.slow_cart",
                    kind="slow_trace",
                    summary="slow traces are dominated by Redis timeout waits",
                    value="max duration 4.1s",
                    excerpt="The trace spends most of its time waiting on cache retries before timing out",
                    observed_at="2026-04-04T10:07:30Z",
                ),
            ],
            changes=[
                EvidenceSeed(
                    id="change.redis.none",
                    kind="recent_change",
                    summary="No recent deploy explains the cache outage",
                    value="last deploy 5h earlier",
                    excerpt="The last cartservice deploy completed well before the cache failures started.",
                    observed_at="2026-04-04T10:04:00Z",
                ),
            ],
        ),
    }


def build_alert_for_scenario(name: str) -> AlertPayload:
    scenario = get_builtin_scenarios()[name]
    return AlertPayload(
        incident_id=f"{name}-demo-001",
        summary=scenario.summary,
        severity="critical",
        service=scenario.service,
        environment="local-dev",
        started_at=scenario.logs[0].observed_at,
        window_minutes=15,
        scenario=name,
        labels={"source": "fixture", "scenario": name},
    )
