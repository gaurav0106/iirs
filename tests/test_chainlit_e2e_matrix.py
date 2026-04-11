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

from chainlit_app import _parse_user_alert
from iirs.config import Settings
from iirs.models import Citation, EvidenceItem, ToolResult
from iirs.pipeline import IIRSPipeline


class ScenarioMatrixTelemetryBackend:
    def __init__(self, runtime_mode: str) -> None:
        self.runtime_mode = runtime_mode

    def _item(
        self,
        *,
        item_id: str,
        category: str,
        service: str,
        summary: str,
        value: str,
        excerpt: str,
        metadata: dict[str, str],
    ) -> EvidenceItem:
        return EvidenceItem(
            id=item_id,
            category=category,
            service=service,
            summary=summary,
            value=value,
            metadata=metadata,
            citations=[
                Citation(
                    id=f"{item_id}.citation",
                    source_type="test",
                    source="scenario-matrix",
                    query=f"service={service}",
                    observed_at="2026-04-11T00:00:00Z",
                    excerpt=excerpt,
                )
            ],
        )

    def get_error_logs(self, alert, scenario):
        return ToolResult(query=f"logs:{alert.service}", items=[])

    def get_latency_metrics(self, alert, scenario):
        return ToolResult(query=f"latency:{alert.service}", items=[])

    def get_error_rate_metrics(self, alert, scenario):
        return ToolResult(query=f"errors:{alert.service}", items=[])

    def get_failed_traces(self, alert, scenario):
        return ToolResult(query=f"failed-traces:{alert.service}", items=[])

    def get_slow_traces(self, alert, scenario):
        return ToolResult(query=f"slow-traces:{alert.service}", items=[])

    def get_recent_changes(self, alert, scenario):
        return ToolResult(query=f"changes:{alert.service}", items=[])

    def get_runtime_log_tails(self, alert, runtime_items):
        return ToolResult(query="runtime-log-tails:none", items=[])

    def get_runtime_states(self, alert, services=None):
        requested = services or [alert.service]
        catalog_state = "missing" if self.runtime_mode == "catalogservice-missing" else "running"
        basketcache_state = "exited" if self.runtime_mode == "basketcache-exited" else "running"

        items = [
            self._item(
                item_id="runtime.frontend",
                category="runtime_states",
                service="frontend",
                summary="Runtime state for frontend: running",
                value="Up 8 minutes",
                excerpt="container=aspire-frontend-1; status=Up 8 minutes",
                metadata={"resource": "frontend", "family": "frontend", "role": "service", "state": "running"},
            ),
            self._item(
                item_id="runtime.catalogservice",
                category="runtime_states",
                service="catalogservice",
                summary=f"Runtime state for catalogservice: {catalog_state}",
                value=(
                    "Process not observed in docker or local process list"
                    if catalog_state == "missing"
                    else "Up 8 minutes"
                ),
                excerpt=(
                    "resource not observed in local process or container listings"
                    if catalog_state == "missing"
                    else "container=aspire-catalogservice-1; status=Up 8 minutes"
                ),
                metadata={"resource": "catalogservice", "family": "catalogservice", "role": "service", "state": catalog_state},
            ),
            self._item(
                item_id="runtime.basketservice",
                category="runtime_states",
                service="basketservice",
                summary="Runtime state for basketservice: running",
                value="Up 8 minutes",
                excerpt="container=aspire-basketservice-1; status=Up 8 minutes",
                metadata={"resource": "basketservice", "family": "basketservice", "role": "service", "state": "running"},
            ),
            self._item(
                item_id="runtime.postgres",
                category="runtime_states",
                service="postgres",
                summary="Runtime state for postgres: running",
                value="Up 8 minutes",
                excerpt="container=aspire-postgres-1; status=Up 8 minutes",
                metadata={"resource": "postgres", "family": "postgres", "role": "dependency", "state": "running"},
            ),
            self._item(
                item_id="runtime.basketcache",
                category="runtime_states",
                service="basketcache",
                summary=f"Runtime state for basketcache: {basketcache_state}",
                value="Exited (0) 30 seconds ago" if basketcache_state == "exited" else "Up 8 minutes",
                excerpt=(
                    "container=aspire-basketcache-1; status=Exited (0) 30 seconds ago"
                    if basketcache_state == "exited"
                    else "container=aspire-basketcache-1; status=Up 8 minutes"
                ),
                metadata={"resource": "basketcache", "family": "redis", "role": "dependency", "state": basketcache_state},
            ),
        ]

        return ToolResult(
            query=f"runtime:{','.join(requested)}",
            items=[
                item
                for item in items
                if item.service in requested or item.service in {"postgres", "basketcache"}
            ],
        )


class ChainlitE2EMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trace_dir = ROOT / "traces" / "chainlit-matrix-output"
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.pipeline = IIRSPipeline(
            settings=Settings(
                trace_dir=self.trace_dir,
                runbooks_dir=ROOT / "runbooks",
                fixtures_dir=ROOT / "fixtures" / "alerts",
                ground_truth_dir=ROOT / "fixtures" / "ground_truth",
                prefer_langgraph=False,
                openai_enabled=False,
            )
        )

    def test_basic_start_stop_prompt_matrix(self) -> None:
        cases = [
            {
                "name": "healthy-breakage",
                "prompt": "what broke in aspire shop right now?",
                "runtime_mode": "healthy",
                "expected_mode": "live-diagnosis",
                "expected_top": "No clear live fault detected",
            },
            {
                "name": "healthy-health-check",
                "prompt": "can you check the health of aspireshop?",
                "runtime_mode": "healthy",
                "expected_mode": "live-health-check",
                "expected_top": "No clear live fault detected",
            },
            {
                "name": "catalogservice-stopped-breakage",
                "prompt": "what broke in aspire shop right now?",
                "runtime_mode": "catalogservice-missing",
                "expected_mode": "live-diagnosis",
                "expected_top": "catalogservice unavailable",
            },
            {
                "name": "catalogservice-stopped-health-check",
                "prompt": "can you check the health of aspireshop?",
                "runtime_mode": "catalogservice-missing",
                "expected_mode": "live-health-check",
                "expected_top": "catalogservice unavailable",
            },
            {
                "name": "basketcache-stopped-breakage",
                "prompt": "what broke in aspire shop right now?",
                "runtime_mode": "basketcache-exited",
                "expected_mode": "live-diagnosis",
                "expected_top": "Redis dependency outage",
            },
            {
                "name": "basketcache-stopped-health-check",
                "prompt": "can you check the health of aspireshop?",
                "runtime_mode": "basketcache-exited",
                "expected_mode": "live-health-check",
                "expected_top": "Redis dependency outage",
            },
        ]

        for case in cases:
            with self.subTest(case["name"]):
                self.pipeline.context.telemetry = ScenarioMatrixTelemetryBackend(case["runtime_mode"])
                alert = _parse_user_alert(case["prompt"], self.pipeline)
                self.assertIsNotNone(alert)
                self.assertEqual(alert.labels.get("mode"), case["expected_mode"])

                state = self.pipeline.run(alert)

                self.assertEqual(
                    state["incident_brief"].probable_root_causes[0].title,
                    case["expected_top"],
                )


if __name__ == "__main__":
    unittest.main()
