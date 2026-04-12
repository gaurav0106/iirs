from __future__ import annotations

from pathlib import Path
import sys
import unittest
from urllib.parse import parse_qs

import httpx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from iirs.backends import build_telemetry_backend
from iirs.config import Settings
from iirs.live_signatures import LiveSignatureHarness, render_live_signature_markdown
from iirs.pipeline import IIRSPipeline


class LiveSignatureHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            trace_dir=ROOT / "traces" / "live-signature-output",
            runbooks_dir=ROOT / "runbooks",
            fixtures_dir=ROOT / "fixtures" / "alerts",
            live_signature_dir=ROOT / "fixtures" / "live_signatures",
            prefer_langgraph=False,
            telemetry_backend="plt",
            prometheus_base_url="http://prometheus.test",
            loki_base_url="http://loki.test",
            tempo_base_url="http://tempo.test",
        )

    def test_live_signature_validation_passes_for_mocked_live_signals(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            params = parse_qs(request.url.query.decode())

            if request.url.host == "prometheus.test":
                query = params["query"][0]
                if "histogram_quantile" in query:
                    return httpx.Response(
                        200,
                        json={
                            "status": "success",
                            "data": {
                                "result": [
                                    {
                                        "metric": {
                                            "exported_job": "catalogservice",
                                            "http_route": "/api/v1/catalog/items/type/all",
                                        },
                                        "values": [[1712222100, "1.8"]],
                                    }
                                ]
                            },
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "status": "success",
                        "data": {
                            "result": [
                                {
                                    "metric": {
                                        "exported_job": "catalogservice",
                                        "http_response_status_code": "499",
                                        "http_route": "/api/v1/catalog/items/type/all",
                                        "error_type": "Microsoft.EntityFrameworkCore.Storage.RetryLimitExceededException",
                                    },
                                    "values": [[1712222100, "0.7"]],
                                }
                            ]
                        },
                    },
                )

            if request.url.host == "loki.test":
                return httpx.Response(
                    200,
                    json={
                        "status": "success",
                        "data": {
                            "result": [
                                {
                                    "stream": {"service_name": "catalogservice"},
                                    "values": [["1712222100000000000", "connection refused to postgres"]],
                                }
                            ]
                        },
                    },
                )

            if request.url.host == "tempo.test":
                query = params["q"][0]
                duration = 4200 if "status = error" in query else 6800
                return httpx.Response(
                    200,
                    json={
                        "traces": [
                            {
                                "traceID": "abc123",
                                "rootServiceName": "catalogservice",
                                "rootTraceName": "GET /api/v1/catalog/items",
                                "startTimeUnixNano": "1712222100000000000",
                                "durationMs": duration,
                            }
                        ]
                    },
                )

            return httpx.Response(404, json={"status": "error"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        pipeline = IIRSPipeline(settings=self.settings)
        pipeline.context.telemetry = build_telemetry_backend(self.settings, client=client)
        harness = LiveSignatureHarness.from_directory(pipeline, self.settings.live_signature_dir)

        report = harness.validate_profiles(["postgres_down"], started_at="2026-04-06T12:00:00Z")

        self.assertTrue(report.passed)
        self.assertEqual(report.passed_checks, 5)
        rendered = render_live_signature_markdown(report)
        self.assertIn("# IIRS Live Signature Validation", rendered)
        self.assertIn("catalogservice", rendered)

    def test_live_signature_validation_reports_missing_signals(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "prometheus.test":
                return httpx.Response(200, json={"status": "success", "data": {"result": []}})
            if request.url.host == "loki.test":
                return httpx.Response(200, json={"status": "success", "data": {"result": []}})
            if request.url.host == "tempo.test":
                return httpx.Response(200, json={"traces": []})
            return httpx.Response(404, json={"status": "error"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        pipeline = IIRSPipeline(settings=self.settings)
        pipeline.context.telemetry = build_telemetry_backend(self.settings, client=client)
        harness = LiveSignatureHarness.from_directory(pipeline, self.settings.live_signature_dir)

        report = harness.validate_profiles(["redis_down"], started_at="2026-04-06T12:00:00Z")

        self.assertFalse(report.passed)
        self.assertEqual(report.passed_checks, 0)
        self.assertTrue(all(not check.satisfied for check in report.profile_reports[0].check_results))


if __name__ == "__main__":
    unittest.main()
