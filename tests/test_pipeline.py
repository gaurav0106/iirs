from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from iirs.config import Settings
from iirs.models import Citation, EvidenceItem, ToolResult
from iirs.pipeline import IIRSPipeline
from tests.helpers import StaticScenarioTelemetryBackend, load_alert_fixture


class FakeLiveTelemetryBackend:
    def _item(self, *, item_id: str, category: str, service: str, summary: str, value: str, excerpt: str) -> EvidenceItem:
        return EvidenceItem(
            id=item_id,
            category=category,
            service=service,
            summary=summary,
            value=value,
            citations=[
                Citation(
                    id=f"{item_id}.citation",
                    source_type="test",
                    source="fake-live-backend",
                    query=f"service={service}",
                    observed_at="2026-04-11T00:00:00Z",
                    excerpt=excerpt,
                )
            ],
        )

    def get_error_logs(self, alert):
        if alert.service == "catalogservice":
            items = [
                self._item(
                    item_id="log.live.catalog.pg",
                    category="logs",
                    service="catalogservice",
                    summary="catalogservice failed to connect to PostgreSQL",
                    value="connection refused",
                    excerpt="NpgsqlException: connection refused to postgres",
                )
            ]
        elif alert.service == "frontend":
            items = [
                self._item(
                    item_id="log.live.frontend.unavailable",
                    category="logs",
                    service="frontend",
                    summary="frontend returned 503s to users",
                    value="upstream unavailable",
                    excerpt="frontend requests are failing with upstream unavailable",
                )
            ]
        else:
            items = []
        return ToolResult(query=f"logs:{alert.service}", items=items)

    def get_runtime_states(self, alert, services=None):
        return ToolResult(query=f"runtime:{','.join(services or [alert.service])}", items=[])

    def get_runtime_log_tails(self, alert, runtime_items):
        return ToolResult(query="runtime-log-tails:none", items=[])

    def get_latency_metrics(self, alert):
        if alert.service == "catalogservice":
            items = [
                self._item(
                    item_id="metric.live.catalog.latency",
                    category="metrics",
                    service="catalogservice",
                    summary="catalogservice latency spiked",
                    value="4.2s",
                    excerpt="p95 latency rose while waiting on database retries",
                )
            ]
        else:
            items = []
        return ToolResult(query=f"latency:{alert.service}", items=items)

    def get_error_rate_metrics(self, alert):
        if alert.service == "catalogservice":
            items = [
                self._item(
                    item_id="metric.live.catalog.errors",
                    category="metrics",
                    service="catalogservice",
                    summary="catalogservice 5xx rate increased sharply",
                    value="0.8 req/s",
                    excerpt="error_type=Microsoft.EntityFrameworkCore.Storage.RetryLimitExceededException",
                )
            ]
        else:
            items = []
        return ToolResult(query=f"errors:{alert.service}", items=items)

    def get_failed_traces(self, alert):
        if alert.service == "catalogservice":
            items = [
                self._item(
                    item_id="trace.live.catalog.failed",
                    category="traces",
                    service="catalogservice",
                    summary="catalogservice trace failed on db.connect",
                    value="error",
                    excerpt="db.connect span failed with postgres connection refused",
                )
            ]
        else:
            items = []
        return ToolResult(query=f"failed-traces:{alert.service}", items=items)

    def get_slow_traces(self, alert):
        return ToolResult(query=f"slow-traces:{alert.service}", items=[])

    def get_recent_changes(self, alert):
        return ToolResult(query=f"changes:{alert.service}", items=[])


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trace_dir = ROOT / "traces" / "test-output"
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.settings = Settings(
            trace_dir=self.trace_dir,
            runbooks_dir=ROOT / "runbooks",
            fixtures_dir=ROOT / "fixtures" / "alerts",
            prefer_langgraph=False,
        )
        self.pipeline = IIRSPipeline(
            settings=self.settings,
            telemetry_backend=StaticScenarioTelemetryBackend(),
        )

    def _load_alert(self, name: str):
        return load_alert_fixture(name)

    def test_postgres_scenario_produces_expected_root_cause(self) -> None:
        state = self.pipeline.run(self._load_alert("postgres_down"))

        brief = state["incident_brief"]
        self.assertEqual(brief.probable_root_causes[0].title, "PostgreSQL dependency outage")
        self.assertEqual(len(state["trace_runs"]), 4)
        self.assertEqual([run.execution_mode for run in state["trace_runs"]], ["tooling", "deterministic", "deterministic", "deterministic"])
        self.assertTrue(any(step.action_type == "needs-approval" for step in brief.recommended_actions))
        self.assertTrue(Path(state["trace_path"]).exists())

    def test_redis_scenario_produces_expected_root_cause(self) -> None:
        state = self.pipeline.run(self._load_alert("redis_down"))

        brief = state["incident_brief"]
        self.assertEqual(brief.probable_root_causes[0].title, "Redis dependency outage")
        self.assertGreaterEqual(len(state["evidence_bundle"].all_items()), 7)

    def test_follow_up_uses_last_incident_state(self) -> None:
        state = self.pipeline.run(self._load_alert("postgres_down"))

        answer = self.pipeline.follow_up("What is the root cause and what evidence supports it?", state)

        self.assertIn("PostgreSQL dependency outage", answer)
        self.assertIn("Supporting evidence", answer)

    def test_build_initial_state_and_finalize_state_support_stepwise_runs(self) -> None:
        alert = self._load_alert("postgres_down")
        state = self.pipeline.build_initial_state(alert)

        self.assertEqual(state["alert"].incident_id, alert.incident_id)
        self.assertEqual(len(state["messages"]), 1)
        self.assertEqual([name for name, _ in self.pipeline.named_nodes], ["Retriever", "Analyst", "Critic", "Planner"])

        for _, node in self.pipeline.named_nodes:
            state.update(node(state))

        finalized = self.pipeline.finalize_state(state)
        self.assertEqual(finalized["incident_brief"].probable_root_causes[0].title, "PostgreSQL dependency outage")
        self.assertTrue(Path(finalized["trace_path"]).exists())

    def test_live_diagnosis_alert_can_probe_multiple_services_and_find_live_fault(self) -> None:
        self.pipeline.context.telemetry = FakeLiveTelemetryBackend()

        state = self.pipeline.run(self.pipeline.build_live_alert("What broke in Aspire Shop right now?"))

        brief = state["incident_brief"]
        retriever_trace = state["trace_runs"][0]
        self.assertIsNone(state.get("scenario_name"))
        self.assertEqual(brief.probable_root_causes[0].title, "catalogservice to PostgreSQL dependency path degraded")
        self.assertIn("does not prove the dependency is fully down", brief.summary)
        self.assertIn("frontend, catalogservice, basketservice", retriever_trace.output_summary)
        self.assertEqual(retriever_trace.execution_mode, "tooling")

    def test_live_diagnosis_prefers_runtime_state_when_multiple_resources_are_down(self) -> None:
        class FakeRuntimeAwareTelemetryBackend(FakeLiveTelemetryBackend):
            def get_runtime_states(self, alert, services=None):
                requested = services or [alert.service]
                items = [
                    self._item(
                        item_id="runtime.postgres",
                        category="runtime_states",
                        service="postgres",
                        summary="Runtime state for postgres: exited",
                        value="Exited (0) 2 minutes ago",
                        excerpt="container=aspire-postgres-123; status=Exited (0) 2 minutes ago",
                    ),
                    self._item(
                        item_id="runtime.basketcache",
                        category="runtime_states",
                        service="basketcache",
                        summary="Runtime state for basketcache: exited",
                        value="Exited (0) 2 minutes ago",
                        excerpt="container=aspire-basketcache-456; status=Exited (0) 2 minutes ago",
                    ),
                    self._item(
                        item_id="runtime.catalogservice",
                        category="runtime_states",
                        service="catalogservice",
                        summary="Runtime state for catalogservice: unhealthy",
                        value="Up 3 minutes (unhealthy)",
                        excerpt="container=aspire-catalogservice-789; status=Up 3 minutes (unhealthy)",
                    ),
                    self._item(
                        item_id="runtime.frontend",
                        category="runtime_states",
                        service="frontend",
                        summary="Runtime state for frontend: unhealthy",
                        value="Up 3 minutes (unhealthy)",
                        excerpt="container=aspire-frontend-999; status=Up 3 minutes (unhealthy)",
                    ),
                ]
                metadata = {
                    "runtime.postgres": {"resource": "postgres", "family": "postgres", "role": "dependency", "state": "exited"},
                    "runtime.basketcache": {"resource": "basketcache", "family": "redis", "role": "dependency", "state": "exited"},
                    "runtime.catalogservice": {"resource": "catalogservice", "family": "catalogservice", "role": "service", "state": "unhealthy"},
                    "runtime.frontend": {"resource": "frontend", "family": "frontend", "role": "service", "state": "unhealthy"},
                }
                filtered = []
                for item in items:
                    if item.service in requested or item.service in {"postgres", "basketcache"}:
                        item.metadata.update(metadata[item.id])
                        filtered.append(item)
                return ToolResult(query=f"runtime:{','.join(requested)}", items=filtered)

        self.pipeline.context.telemetry = FakeRuntimeAwareTelemetryBackend()

        state = self.pipeline.run(self.pipeline.build_live_alert("what broke in aspire shop right now?"))

        brief = state["incident_brief"]
        self.assertEqual(brief.probable_root_causes[0].title, "Multiple service outages in Aspire Shop")
        self.assertIn("postgres and basketcache", brief.summary)
        self.assertIn("Confirm in Docker or the Aspire dashboard", brief.recommended_actions[0].description)
        self.assertIn("postgres", brief.recommended_actions[0].description)
        self.assertNotIn("Inspect frontend health", brief.recommended_actions[0].description)

    def test_live_diagnosis_distinguishes_dependency_path_degradation_from_hard_outage(self) -> None:
        class FakePathAwareTelemetryBackend(FakeLiveTelemetryBackend):
            def get_runtime_states(self, alert, services=None):
                requested = services or [alert.service]
                items = [
                    self._item(
                        item_id="runtime.frontend",
                        category="runtime_states",
                        service="frontend",
                        summary="Runtime state for frontend: running",
                        value="Up 8 minutes",
                        excerpt="container=aspire-frontend-1; status=Up 8 minutes",
                    ),
                    self._item(
                        item_id="runtime.catalogservice",
                        category="runtime_states",
                        service="catalogservice",
                        summary="Runtime state for catalogservice: running",
                        value="Up 5 minutes",
                        excerpt="container=aspire-catalogservice-1; status=Up 5 minutes",
                    ),
                    self._item(
                        item_id="runtime.postgres",
                        category="runtime_states",
                        service="postgres",
                        summary="Runtime state for postgres: running",
                        value="Up 5 minutes",
                        excerpt="container=aspire-postgres-1; status=Up 5 minutes",
                    ),
                ]
                metadata = {
                    "runtime.frontend": {"resource": "frontend", "family": "frontend", "role": "service", "state": "running"},
                    "runtime.catalogservice": {"resource": "catalogservice", "family": "catalogservice", "role": "service", "state": "running"},
                    "runtime.postgres": {"resource": "postgres", "family": "postgres", "role": "dependency", "state": "running"},
                }
                filtered = []
                for item in items:
                    if item.service in requested or item.service == "postgres":
                        item.metadata.update(metadata[item.id])
                        filtered.append(item)
                return ToolResult(query=f"runtime:{','.join(requested)}", items=filtered)

            def get_error_rate_metrics(self, alert):
                if alert.service == "catalogservice":
                    items = [
                        self._item(
                            item_id="metric.live.catalog.errors",
                            category="metrics",
                            service="catalogservice",
                            summary="catalogservice database requests are timing out",
                            value="0.8 req/s",
                            excerpt="Npgsql.NpgsqlException: The operation has timed out",
                        )
                    ]
                elif alert.service == "frontend":
                    items = [
                        self._item(
                            item_id="metric.live.frontend.errors",
                            category="metrics",
                            service="frontend",
                            summary="frontend error-rate increased",
                            value="status=499",
                            excerpt="frontend returned 499 to the browser",
                        )
                    ]
                else:
                    items = []
                return ToolResult(query=f"errors:{alert.service}", items=items)

        self.pipeline.context.telemetry = FakePathAwareTelemetryBackend()

        state = self.pipeline.run(self.pipeline.build_live_alert("what broke in aspire shop right now?"))

        brief = state["incident_brief"]
        self.assertEqual(brief.probable_root_causes[0].title, "catalogservice to PostgreSQL dependency path degraded")
        self.assertNotEqual(brief.probable_root_causes[0].title, "PostgreSQL dependency outage")
        self.assertIn("still running", brief.summary)
        self.assertIn("PostgreSQL-related timeout or retry", brief.recommended_actions[0].description)

    def test_live_diagnosis_prefers_missing_service_over_unrelated_dependency_noise(self) -> None:
        class FakeMissingCatalogTelemetryBackend(FakeLiveTelemetryBackend):
            def get_runtime_states(self, alert, services=None):
                requested = services or [alert.service]
                items = [
                    self._item(
                        item_id="runtime.catalogservice",
                        category="runtime_states",
                        service="catalogservice",
                        summary="Runtime state for catalogservice: missing",
                        value="Process not observed in docker or local process list",
                        excerpt="resource not observed in local process or container listings",
                    ),
                    self._item(
                        item_id="runtime.basketservice",
                        category="runtime_states",
                        service="basketservice",
                        summary="Runtime state for basketservice: running",
                        value="Up 8 minutes",
                        excerpt="container=aspire-basketservice-1; status=Up 8 minutes",
                    ),
                    self._item(
                        item_id="runtime.basketcache",
                        category="runtime_states",
                        service="basketcache",
                        summary="Runtime state for basketcache: running",
                        value="Up 8 minutes",
                        excerpt="container=aspire-basketcache-1; status=Up 8 minutes",
                    ),
                    self._item(
                        item_id="runtime.rediscommander",
                        category="runtime_states",
                        service="rediscommander",
                        summary="Runtime state for rediscommander: running",
                        value="Up 8 minutes (healthy)",
                        excerpt="container=aspire-rediscommander-1; status=Up 8 minutes (healthy)",
                    ),
                ]
                metadata = {
                    "runtime.catalogservice": {"resource": "catalogservice", "family": "catalogservice", "role": "service", "state": "missing"},
                    "runtime.basketservice": {"resource": "basketservice", "family": "basketservice", "role": "service", "state": "running"},
                    "runtime.basketcache": {"resource": "basketcache", "family": "redis", "role": "dependency", "state": "running"},
                    "runtime.rediscommander": {"resource": "rediscommander", "family": "redis", "role": "support", "state": "running"},
                }
                filtered = []
                for item in items:
                    if item.service in requested or item.service in {"basketcache", "rediscommander"}:
                        item.metadata.update(metadata[item.id])
                        filtered.append(item)
                return ToolResult(query=f"runtime:{','.join(requested)}", items=filtered)

            def get_error_logs(self, alert):
                if alert.service == "basketservice":
                    items = [
                        self._item(
                            item_id="log.live.basket.redis",
                            category="logs",
                            service="basketservice",
                            summary="basketservice saw Redis timeout noise",
                            value="Redis timeout",
                            excerpt="StackExchange.Redis.RedisConnectionException: timeout while waiting for cache",
                        )
                    ]
                else:
                    items = []
                return ToolResult(query=f"logs:{alert.service}", items=items)

            def get_error_rate_metrics(self, alert):
                if alert.service == "frontend":
                    items = [
                        self._item(
                            item_id="metric.live.frontend.499",
                            category="metrics",
                            service="frontend",
                            summary="frontend error-rate increased",
                            value="status=499",
                            excerpt="frontend returned 499 to the browser",
                        )
                    ]
                else:
                    items = []
                return ToolResult(query=f"errors:{alert.service}", items=items)

        self.pipeline.context.telemetry = FakeMissingCatalogTelemetryBackend()

        state = self.pipeline.run(self.pipeline.build_live_alert("what broke in aspire shop right now?"))

        brief = state["incident_brief"]
        self.assertEqual(brief.probable_root_causes[0].title, "catalogservice unavailable")
        self.assertNotEqual(brief.probable_root_causes[0].title, "basketservice to Redis dependency path degraded")
        self.assertIn("catalogservice is missing or not healthy", brief.summary)

    def test_live_diagnosis_forces_deterministic_analyst_when_runtime_proves_service_missing(self) -> None:
        class FakeMissingCatalogTelemetryBackend(FakeLiveTelemetryBackend):
            def get_runtime_states(self, alert, services=None):
                requested = services or [alert.service]
                items = [
                    self._item(
                        item_id="runtime.catalogservice",
                        category="runtime_states",
                        service="catalogservice",
                        summary="Runtime state for catalogservice: missing",
                        value="Process not observed in docker or local process list",
                        excerpt="resource not observed in local process or container listings",
                    ),
                    self._item(
                        item_id="runtime.frontend",
                        category="runtime_states",
                        service="frontend",
                        summary="Runtime state for frontend: running",
                        value="Up 8 minutes",
                        excerpt="container=aspire-frontend-1; status=Up 8 minutes",
                    ),
                ]
                metadata = {
                    "runtime.catalogservice": {
                        "resource": "catalogservice",
                        "family": "catalogservice",
                        "role": "service",
                        "state": "missing",
                    },
                    "runtime.frontend": {
                        "resource": "frontend",
                        "family": "frontend",
                        "role": "service",
                        "state": "running",
                    },
                }
                filtered = []
                for item in items:
                    if item.service in requested:
                        item.metadata.update(metadata[item.id])
                        filtered.append(item)
                return ToolResult(query=f"runtime:{','.join(requested)}", items=filtered)

            def get_error_rate_metrics(self, alert):
                if alert.service == "frontend":
                    items = [
                        self._item(
                            item_id="metric.live.frontend.503",
                            category="metrics",
                            service="frontend",
                            summary="frontend returned 503s while catalogservice was missing",
                            value="status=503",
                            excerpt="frontend showed upstream 503s",
                        )
                    ]
                else:
                    items = []
                return ToolResult(query=f"errors:{alert.service}", items=items)

        class FakeMisleadingReasoningClient:
            def analyze_incident(self, state):
                return {
                    "summary": "Model overfit the frontend symptoms.",
                    "hypotheses": [
                        {
                            "title": "Catalogservice process missing",
                            "confidence": 0.70,
                            "supporting_evidence_ids": ["metric.live.frontend.503"],
                            "contradicting_evidence_ids": [],
                            "next_checks": ["Inspect frontend handlers."],
                        },
                        {
                            "title": "Frontend application errors / request handling bug",
                            "confidence": 0.50,
                            "supporting_evidence_ids": ["metric.live.frontend.503"],
                            "contradicting_evidence_ids": [],
                            "next_checks": ["Inspect frontend handlers."],
                        },
                    ],
                }

            def critique_incident(self, state):
                return {
                    "relevant_evidence_ids": state["hypotheses"][0].supporting_evidence_ids,
                    "hallucination_risks": [],
                    "missing_data": [],
                    "safety_notes": [],
                    "findings": [],
                }

            def plan_incident(self, state):
                return {
                    "brief_title": "Incident Brief: live diagnosis for Aspire Shop",
                    "brief_summary": "catalogservice is the missing resource.",
                    "open_questions": [],
                    "evidence_snapshot": [],
                    "steps": [
                        {
                            "description": "Inspect runtime state.",
                            "action_type": "auto-safe",
                            "rationale": "safe",
                            "evidence_ids": state["hypotheses"][0].supporting_evidence_ids,
                        }
                    ],
                }

            def answer_follow_up(self, question, state):
                return "ok"

        pipeline = IIRSPipeline(
            settings=self.settings,
            reasoning_client=FakeMisleadingReasoningClient(),
            telemetry_backend=FakeMissingCatalogTelemetryBackend(),
        )

        state = pipeline.run(pipeline.build_live_alert("what broke in aspire shop right now?"))

        self.assertEqual(state["trace_runs"][1].execution_mode, "deterministic")
        self.assertEqual(state["incident_brief"].probable_root_causes[0].title, "catalogservice unavailable")
        self.assertNotEqual(
            state["incident_brief"].probable_root_causes[1].title,
            "Frontend application errors / request handling bug",
        )

    def test_live_diagnosis_collects_targeted_runtime_log_tails_for_unhealthy_resources(self) -> None:
        class FakeTailingTelemetryBackend(FakeLiveTelemetryBackend):
            def get_runtime_states(self, alert, services=None):
                requested = services or [alert.service]
                items = [
                    self._item(
                        item_id="runtime.catalogservice",
                        category="runtime_states",
                        service="catalogservice",
                        summary="Runtime state for catalogservice: missing",
                        value="Process not observed in docker or local process list",
                        excerpt="resource not observed in local process or container listings",
                    )
                ]
                items[0].metadata.update(
                    {"resource": "catalogservice", "family": "catalogservice", "role": "service", "state": "missing"}
                )
                return ToolResult(query=f"runtime:{','.join(requested)}", items=items)

            def get_runtime_log_tails(self, alert, runtime_items):
                assert [item.metadata["resource"] for item in runtime_items] == ["catalogservice"]
                items = [
                    self._item(
                        item_id="log.runtime.tail.catalogservice.loki.1",
                        category="logs",
                        service="catalogservice",
                        summary="Recent runtime log tail for catalogservice",
                        value="Unhandled exception during startup",
                        excerpt="Unhandled exception during startup: invalid connection string",
                    )
                ]
                return ToolResult(query="runtime-log-tails:catalogservice", items=items)

        self.pipeline.context.telemetry = FakeTailingTelemetryBackend()

        state = self.pipeline.run(self.pipeline.build_live_alert("what broke in aspire shop right now?"))

        retriever_trace = state["trace_runs"][0]
        log_ids = [item.id for item in state["evidence_bundle"].logs]
        self.assertIn("log.runtime.tail.catalogservice.loki.1", log_ids)
        self.assertTrue(any(call.tool_name == "get_runtime_log_tails" for call in retriever_trace.tool_calls))

    def test_live_health_check_prefers_no_clear_fault_when_runtime_is_green(self) -> None:
        class FakeHealthyRuntimeTelemetryBackend(FakeLiveTelemetryBackend):
            def get_runtime_states(self, alert, services=None):
                requested = services or [alert.service]
                items = [
                    self._item(
                        item_id="runtime.frontend",
                        category="runtime_states",
                        service="frontend",
                        summary="Runtime state for frontend: running",
                        value="Up 8 minutes",
                        excerpt="container=aspire-frontend-1; status=Up 8 minutes",
                    ),
                    self._item(
                        item_id="runtime.catalogservice",
                        category="runtime_states",
                        service="catalogservice",
                        summary="Runtime state for catalogservice: running",
                        value="Up 8 minutes",
                        excerpt="container=aspire-catalogservice-1; status=Up 8 minutes",
                    ),
                    self._item(
                        item_id="runtime.basketservice",
                        category="runtime_states",
                        service="basketservice",
                        summary="Runtime state for basketservice: running",
                        value="Up 8 minutes",
                        excerpt="container=aspire-basketservice-1; status=Up 8 minutes",
                    ),
                    self._item(
                        item_id="runtime.postgres",
                        category="runtime_states",
                        service="postgres",
                        summary="Runtime state for postgres: running",
                        value="Up 8 minutes",
                        excerpt="container=aspire-postgres-1; status=Up 8 minutes",
                    ),
                    self._item(
                        item_id="runtime.basketcache",
                        category="runtime_states",
                        service="basketcache",
                        summary="Runtime state for basketcache: running",
                        value="Up 8 minutes",
                        excerpt="container=aspire-basketcache-1; status=Up 8 minutes",
                    ),
                ]
                metadata = {
                    "runtime.frontend": {"resource": "frontend", "family": "frontend", "role": "service", "state": "running"},
                    "runtime.catalogservice": {"resource": "catalogservice", "family": "catalogservice", "role": "service", "state": "running"},
                    "runtime.basketservice": {"resource": "basketservice", "family": "basketservice", "role": "service", "state": "running"},
                    "runtime.postgres": {"resource": "postgres", "family": "postgres", "role": "dependency", "state": "running"},
                    "runtime.basketcache": {"resource": "basketcache", "family": "redis", "role": "dependency", "state": "running"},
                }
                filtered = []
                for item in items:
                    if item.service in requested or item.service in {"postgres", "basketcache"}:
                        item.metadata.update(metadata[item.id])
                        filtered.append(item)
                return ToolResult(query=f"runtime:{','.join(requested)}", items=filtered)

        self.pipeline.context.telemetry = FakeHealthyRuntimeTelemetryBackend()

        state = self.pipeline.run(
            self.pipeline.build_live_alert(
                "is everything healthy or broken right now?",
                mode="live-health-check",
            )
        )

        brief = state["incident_brief"]
        self.assertEqual(brief.probable_root_causes[0].title, "No clear live fault detected")
        self.assertEqual(brief.probable_root_causes[1].title, "Transient issue or stale telemetry from earlier faults")
        self.assertNotIn("dependency path degraded", brief.probable_root_causes[1].title.lower())
        self.assertIn("does not show a clear active fault", brief.summary)

    def test_live_diagnosis_prefers_no_clear_fault_when_runtime_is_green_and_no_direct_signal(self) -> None:
        class FakeGreenRuntimeOnlyTelemetryBackend(FakeLiveTelemetryBackend):
            def get_error_logs(self, alert):
                return ToolResult(query=f"logs:{alert.service}", items=[])

            def get_latency_metrics(self, alert):
                return ToolResult(query=f"latency:{alert.service}", items=[])

            def get_error_rate_metrics(self, alert):
                return ToolResult(query=f"errors:{alert.service}", items=[])

            def get_failed_traces(self, alert):
                return ToolResult(query=f"failed-traces:{alert.service}", items=[])

            def get_runtime_states(self, alert, services=None):
                requested = services or [alert.service]
                items = [
                    self._item(
                        item_id="runtime.frontend",
                        category="runtime_states",
                        service="frontend",
                        summary="Runtime state for frontend: running",
                        value="Up 8 minutes",
                        excerpt="container=aspire-frontend-1; status=Up 8 minutes",
                    ),
                    self._item(
                        item_id="runtime.catalogservice",
                        category="runtime_states",
                        service="catalogservice",
                        summary="Runtime state for catalogservice: running",
                        value="Up 8 minutes",
                        excerpt="container=aspire-catalogservice-1; status=Up 8 minutes",
                    ),
                    self._item(
                        item_id="runtime.basketservice",
                        category="runtime_states",
                        service="basketservice",
                        summary="Runtime state for basketservice: running",
                        value="Up 8 minutes",
                        excerpt="container=aspire-basketservice-1; status=Up 8 minutes",
                    ),
                    self._item(
                        item_id="runtime.postgres",
                        category="runtime_states",
                        service="postgres",
                        summary="Runtime state for postgres: running",
                        value="Up 8 minutes",
                        excerpt="container=aspire-postgres-1; status=Up 8 minutes",
                    ),
                    self._item(
                        item_id="runtime.basketcache",
                        category="runtime_states",
                        service="basketcache",
                        summary="Runtime state for basketcache: running",
                        value="Up 8 minutes",
                        excerpt="container=aspire-basketcache-1; status=Up 8 minutes",
                    ),
                ]
                metadata = {
                    "runtime.frontend": {"resource": "frontend", "family": "frontend", "role": "service", "state": "running"},
                    "runtime.catalogservice": {"resource": "catalogservice", "family": "catalogservice", "role": "service", "state": "running"},
                    "runtime.basketservice": {"resource": "basketservice", "family": "basketservice", "role": "service", "state": "running"},
                    "runtime.postgres": {"resource": "postgres", "family": "postgres", "role": "dependency", "state": "running"},
                    "runtime.basketcache": {"resource": "basketcache", "family": "redis", "role": "dependency", "state": "running"},
                }
                filtered = []
                for item in items:
                    if item.service in requested or item.service in {"postgres", "basketcache"}:
                        item.metadata.update(metadata[item.id])
                        filtered.append(item)
                return ToolResult(query=f"runtime:{','.join(requested)}", items=filtered)

        self.pipeline.context.telemetry = FakeGreenRuntimeOnlyTelemetryBackend()

        state = self.pipeline.run(self.pipeline.build_live_alert("what broke in aspire shop right now?"))

        brief = state["incident_brief"]
        self.assertEqual(brief.probable_root_causes[0].title, "No clear live fault detected")
        self.assertNotEqual(brief.probable_root_causes[0].title, "basketservice unavailable")

    def test_live_health_check_detects_redis_outage_when_basketcache_is_down(self) -> None:
        class FakeBasketcacheDownTelemetryBackend(FakeLiveTelemetryBackend):
            def get_error_logs(self, alert):
                return ToolResult(query=f"logs:{alert.service}", items=[])

            def get_latency_metrics(self, alert):
                return ToolResult(query=f"latency:{alert.service}", items=[])

            def get_error_rate_metrics(self, alert):
                return ToolResult(query=f"errors:{alert.service}", items=[])

            def get_failed_traces(self, alert):
                return ToolResult(query=f"failed-traces:{alert.service}", items=[])

            def get_runtime_states(self, alert, services=None):
                requested = services or [alert.service]
                items = [
                    self._item(
                        item_id="runtime.frontend",
                        category="runtime_states",
                        service="frontend",
                        summary="Runtime state for frontend: running",
                        value="Up 8 minutes",
                        excerpt="container=aspire-frontend-1; status=Up 8 minutes",
                    ),
                    self._item(
                        item_id="runtime.catalogservice",
                        category="runtime_states",
                        service="catalogservice",
                        summary="Runtime state for catalogservice: running",
                        value="Up 8 minutes",
                        excerpt="container=aspire-catalogservice-1; status=Up 8 minutes",
                    ),
                    self._item(
                        item_id="runtime.basketservice",
                        category="runtime_states",
                        service="basketservice",
                        summary="Runtime state for basketservice: running",
                        value="Up 8 minutes",
                        excerpt="container=aspire-basketservice-1; status=Up 8 minutes",
                    ),
                    self._item(
                        item_id="runtime.postgres",
                        category="runtime_states",
                        service="postgres",
                        summary="Runtime state for postgres: running",
                        value="Up 8 minutes",
                        excerpt="container=aspire-postgres-1; status=Up 8 minutes",
                    ),
                    self._item(
                        item_id="runtime.basketcache",
                        category="runtime_states",
                        service="basketcache",
                        summary="Runtime state for basketcache: exited",
                        value="Exited (0) 30 seconds ago",
                        excerpt="container=aspire-basketcache-1; status=Exited (0) 30 seconds ago",
                    ),
                ]
                metadata = {
                    "runtime.frontend": {"resource": "frontend", "family": "frontend", "role": "service", "state": "running"},
                    "runtime.catalogservice": {"resource": "catalogservice", "family": "catalogservice", "role": "service", "state": "running"},
                    "runtime.basketservice": {"resource": "basketservice", "family": "basketservice", "role": "service", "state": "running"},
                    "runtime.postgres": {"resource": "postgres", "family": "postgres", "role": "dependency", "state": "running"},
                    "runtime.basketcache": {"resource": "basketcache", "family": "redis", "role": "dependency", "state": "exited"},
                }
                filtered = []
                for item in items:
                    if item.service in requested or item.service in {"postgres", "basketcache"}:
                        item.metadata.update(metadata[item.id])
                        filtered.append(item)
                return ToolResult(query=f"runtime:{','.join(requested)}", items=filtered)

        self.pipeline.context.telemetry = FakeBasketcacheDownTelemetryBackend()

        state = self.pipeline.run(
            self.pipeline.build_live_alert(
                "can you check the health of aspireshop?",
                mode="live-health-check",
            )
        )

        brief = state["incident_brief"]
        self.assertEqual(brief.probable_root_causes[0].title, "Redis dependency outage")

    def test_alert_driven_triage_falls_back_to_generic_service_investigation_for_unknown_service(self) -> None:
        self.pipeline.context.telemetry = FakeLiveTelemetryBackend()
        alert = self.pipeline.build_live_alert("frontend is returning 503s", service="frontend")
        alert.labels = {"source": "demo-alert"}

        state = self.pipeline.run(alert)

        self.assertIsNone(state.get("scenario_name"))
        self.assertEqual(state["incident_brief"].probable_root_causes[0].title, "frontend unavailable")

    def test_deterministic_follow_up_expands_short_why_question(self) -> None:
        state = self.pipeline.run(self._load_alert("postgres_down"))

        follow_up = self.pipeline.follow_up("why?", state)

        self.assertIn("Most likely root cause", follow_up)
        self.assertIn("Supporting evidence", follow_up)

    def test_deterministic_follow_up_expands_short_next_question(self) -> None:
        state = self.pipeline.run(self._load_alert("redis_down"))

        follow_up = self.pipeline.follow_up("then what?", state)

        self.assertIn("Start here", follow_up)


if __name__ == "__main__":
    unittest.main()
