from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from iirs.backends import MockTelemetryBackend, PLTHttpTelemetryBackend, QueryTemplates, build_telemetry_backend
from iirs.config import Settings
from iirs.models import Citation, EvidenceItem
from iirs.scenarios import build_alert_for_scenario, get_builtin_scenarios


class QueryTemplateTests(unittest.TestCase):
    def test_postgres_live_queries_use_exported_job_and_catalog_route(self) -> None:
        queries = QueryTemplates(service="catalogservice", scenario="postgres_down")

        self.assertIn('exported_job="catalogservice"', queries.latency_metrics())
        self.assertIn('http_route="/api/v1/catalog/items/type/all"', queries.latency_metrics())
        self.assertIn('http_response_status_code=~"499|5.."', queries.error_rate_metrics())
        self.assertIn(
            'Microsoft\\\\.EntityFrameworkCore\\\\.Storage\\\\.RetryLimitExceededException',
            queries.error_rate_metrics(),
        )
        self.assertIn("aspnetcore_diagnostics_exceptions_total", queries.error_rate_metrics())
        self.assertIn('resource.service.name = "catalogservice"', queries.failed_traces())
        self.assertIn("most_recent=true", queries.slow_traces())

    def test_redis_live_queries_use_grpc_route_regex(self) -> None:
        queries = QueryTemplates(service="basketservice", scenario="redis_down")

        self.assertIn('exported_job="basketservice"', queries.latency_metrics())
        self.assertIn('http_route=~"/BasketApi.Basket/', queries.latency_metrics())
        self.assertIn("StackExchange\\\\.Redis\\\\..+", queries.error_rate_metrics())
        self.assertIn("dotnet_exceptions_total", queries.error_rate_metrics())
        self.assertIn("RedisConnectionException|RedisTimeoutException|SocketException", queries.error_rate_metrics())
        self.assertIn('name =~ "POST /BasketApi.Basket/.*"', queries.failed_traces())
        self.assertIn("trace:duration > 4s", queries.failed_traces())


class LiveBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.alert = build_alert_for_scenario("postgres_down")
        self.scenario = get_builtin_scenarios()["postgres_down"]

    def test_live_backend_parses_prometheus_loki_and_tempo(self) -> None:
        seen_requests: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_requests.append((request.url.host or "", request.url.path))
            params = parse_qs(request.url.query.decode())
            self.assertEqual(request.headers.get("X-Scope-OrgID"), "tenant-a")

            if request.url.host == "prometheus.test":
                self.assertEqual(request.url.path, "/api/v1/query_range")
                return httpx.Response(
                    200,
                    json={
                        "status": "success",
                        "data": {
                            "resultType": "matrix",
                            "result": [
                                {
                                    "metric": {
                                        "exported_job": "catalogservice",
                                        "http_route": "/api/v1/catalog/items/type/all",
                                    },
                                    "values": [[1712222100, "4.2"]],
                                }
                            ],
                        },
                    },
                )

            if request.url.host == "loki.test":
                self.assertEqual(request.url.path, "/loki/api/v1/query_range")
                self.assertIn("query", params)
                return httpx.Response(
                    200,
                    json={
                        "status": "success",
                        "data": {
                            "resultType": "streams",
                            "result": [
                                {
                                    "stream": {"service_name": "catalogservice"},
                                    "values": [["1712222100000000000", "connection refused to postgres"]],
                                }
                            ],
                        },
                    },
                )

            if request.url.host == "tempo.test":
                self.assertEqual(request.url.path, "/api/search")
                self.assertIn("q", params)
                self.assertTrue(params["start"][0].isdigit())
                self.assertTrue(params["end"][0].isdigit())
                return httpx.Response(
                    200,
                    json={
                        "traces": [
                            {
                                "traceID": "abc123",
                                "rootServiceName": "catalogservice",
                                "rootTraceName": "POST /checkout",
                                "startTimeUnixNano": "1712222100000000000",
                                "durationMs": 4200,
                            }
                        ]
                    },
                )

            return httpx.Response(404, json={"status": "error", "error": "unexpected route"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        backend = PLTHttpTelemetryBackend(
            prometheus_base_url="http://prometheus.test",
            loki_base_url="http://loki.test",
            tempo_base_url="http://tempo.test",
            tenant_id="tenant-a",
            client=client,
        )

        logs = backend.get_error_logs(self.alert, self.scenario)
        latency = backend.get_latency_metrics(self.alert, self.scenario)
        failed_traces = backend.get_failed_traces(self.alert, self.scenario)

        self.assertEqual(len(logs.items), 1)
        self.assertEqual(logs.items[0].citations[0].source_type, "loki")
        self.assertEqual(len(latency.items), 1)
        self.assertEqual(latency.items[0].value, "4.2")
        self.assertEqual(len(failed_traces.items), 1)
        self.assertEqual(failed_traces.items[0].metadata["trace_id"], "abc123")
        self.assertIn(("tempo.test", "/api/search"), seen_requests)

    def test_backend_factory_falls_back_when_requested(self) -> None:
        settings = Settings(
            trace_dir=ROOT / "traces" / "test-output",
            runbooks_dir=ROOT / "runbooks",
            fixtures_dir=ROOT / "fixtures" / "alerts",
            prefer_langgraph=False,
            telemetry_backend="plt",
            allow_backend_fallback=True,
            prometheus_base_url=None,
            loki_base_url=None,
            tempo_base_url=None,
        )

        backend = build_telemetry_backend(settings)

        self.assertIsInstance(backend, MockTelemetryBackend)

    def test_runtime_states_include_host_processes_and_missing_services(self) -> None:
        class Completed:
            def __init__(self, stdout: str) -> None:
                self.stdout = stdout

        calls: list[list[str]] = []

        def fake_run(args, capture_output, text, check):
            calls.append(args)
            if args[:3] == ["docker", "ps", "-a"]:
                return Completed(
                    "aspire-postgres-1\tUp 5 minutes\tpostgres:16\n"
                    "aspire-basketcache-1\tUp 8 minutes\treedis:7\n"
                )
            if args == ["ps", "-eo", "pid=,stat=,args="]:
                return Completed(
                    "101 Ssl dotnet /tmp/AspireShop.Frontend.dll\n"
                    "102 Ssl dotnet /tmp/AspireShop.CatalogService.dll\n"
                )
            raise AssertionError(f"unexpected subprocess args: {args}")

        backend = PLTHttpTelemetryBackend(
            prometheus_base_url="http://prometheus.test",
            loki_base_url="http://loki.test",
            tempo_base_url="http://tempo.test",
        )

        with patch("iirs.backends.subprocess.run", side_effect=fake_run):
            runtime = backend.get_runtime_states(self.alert, services=["aspire-shop"])

        states = {item.metadata["resource"]: item for item in runtime.items}
        self.assertEqual(states["catalogservice"].metadata["state"], "running")
        self.assertEqual(states["catalogservice"].citations[0].source, "ps -eo")
        self.assertEqual(states["frontend"].metadata["state"], "running")
        self.assertEqual(states["postgres"].metadata["state"], "running")
        self.assertEqual(states["basketservice"].metadata["state"], "missing")
        self.assertIn("docker ps -a / ps -eo", states["basketservice"].citations[0].source)
        self.assertEqual(calls[0][:3], ["docker", "ps", "-a"])
        self.assertEqual(calls[1], ["ps", "-eo", "pid=,stat=,args="])

    def test_runtime_log_tails_use_docker_for_dependencies_and_loki_for_services(self) -> None:
        class Completed:
            def __init__(self, stdout: str = "", stderr: str = "") -> None:
                self.stdout = stdout
                self.stderr = stderr

        def handler(request: httpx.Request) -> httpx.Response:
            params = parse_qs(request.url.query.decode())
            self.assertEqual(request.url.path, "/loki/api/v1/query_range")
            self.assertEqual(params["query"][0], '{service_name="catalogservice"}')
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "result": [
                            {
                                "stream": {"service_name": "catalogservice"},
                                "values": [
                                    ["1712222100000000000", "INFO started ok"],
                                    ["1712222160000000000", "Unhandled exception during startup"],
                                ],
                            }
                        ]
                    },
                },
            )

        def fake_run(args, capture_output, text, check):
            if args[:2] == ["docker", "logs"]:
                return Completed(stdout="database system is ready\ndatabase connection refused")
            raise AssertionError(f"unexpected subprocess args: {args}")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        backend = PLTHttpTelemetryBackend(
            prometheus_base_url="http://prometheus.test",
            loki_base_url="http://loki.test",
            tempo_base_url="http://tempo.test",
            client=client,
        )
        runtime_items = [
            EvidenceItem(
                id="runtime.postgres",
                category="runtime_states",
                service="postgres",
                summary="Runtime state for postgres: exited",
                value="Exited (1) 1 minute ago",
                citations=[
                    Citation(
                        id="runtime.postgres.citation",
                        source_type="runtime",
                        source="docker ps -a",
                        query="runtime",
                        observed_at=self.alert.started_at,
                        excerpt="container=aspire-postgres-1",
                    )
                ],
                metadata={"resource": "postgres", "family": "postgres", "role": "dependency", "state": "exited", "container_name": "aspire-postgres-1"},
            ),
            EvidenceItem(
                id="runtime.catalogservice",
                category="runtime_states",
                service="catalogservice",
                summary="Runtime state for catalogservice: missing",
                value="Process not observed in docker or local process list",
                citations=[
                    Citation(
                        id="runtime.catalogservice.citation",
                        source_type="runtime",
                        source="docker ps -a / ps -eo",
                        query="runtime",
                        observed_at=self.alert.started_at,
                        excerpt="resource not observed",
                    )
                ],
                metadata={"resource": "catalogservice", "family": "catalogservice", "role": "service", "state": "missing"},
            ),
        ]

        with patch("iirs.backends.subprocess.run", side_effect=fake_run):
            result = backend.get_runtime_log_tails(self.alert, runtime_items)

        ids = [item.id for item in result.items]
        self.assertIn("log.runtime.tail.postgres.docker.1", ids)
        self.assertIn("log.runtime.tail.catalogservice.loki.1", ids)
        self.assertIn("connection refused", result.items[0].value.lower())
