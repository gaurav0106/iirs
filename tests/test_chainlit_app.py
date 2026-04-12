from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chainlit_app import _classify_user_message, _looks_like_contextual_follow_up, _parse_user_alert
from iirs.config import Settings
from iirs.pipeline import IIRSPipeline
from tests.helpers import NoopTelemetryBackend


class ChainlitInputParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trace_dir = ROOT / "traces" / "chainlit-test-output"
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.pipeline = IIRSPipeline(
            settings=Settings(
                trace_dir=self.trace_dir,
                runbooks_dir=ROOT / "runbooks",
                fixtures_dir=ROOT / "fixtures" / "alerts",
                prefer_langgraph=False,
                openai_enabled=False,
            ),
            telemetry_backend=NoopTelemetryBackend(),
        )

    def test_parse_user_alert_accepts_freeform_postgres_description(self) -> None:
        alert = _parse_user_alert(
            "catalogservice is timing out and PostgreSQL looks down",
            self.pipeline,
        )

        self.assertIsNotNone(alert)
        self.assertIsNone(alert.scenario)
        self.assertEqual(alert.service, "catalogservice")
        self.assertIn("catalogservice is timing out", alert.summary)
        self.assertEqual(alert.labels.get("source"), "chat-live")
        self.assertEqual(alert.labels.get("mode"), "live-diagnosis")
        self.assertTrue(alert.incident_id.startswith("live-chat-"))

    def test_parse_user_alert_assigns_unique_incident_ids_for_repeated_freeform_prompts(self) -> None:
        with patch("iirs.pipeline.utc_now", return_value="2026-04-11T12:00:00+00:00"):
            with patch("iirs.pipeline.unique_suffix", side_effect=["first123", "second45"]):
                first = _parse_user_alert(
                    "catalogservice is timing out and PostgreSQL looks down",
                    self.pipeline,
                )
                second = _parse_user_alert(
                    "catalogservice is timing out and PostgreSQL looks down",
                    self.pipeline,
                )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first.incident_id, second.incident_id)

    def test_parse_user_alert_accepts_fenced_json_payload(self) -> None:
        alert = _parse_user_alert(
            """```json
{
  "incident_id": "demo-123",
  "summary": "basketservice cannot reach Redis",
  "severity": "critical",
  "service": "basketservice",
  "environment": "local-dev",
  "started_at": "2026-04-10T11:17:38Z",
  "window_minutes": 10,
  "scenario": "redis_down",
  "labels": { "source": "demo" }
}
```""",
            self.pipeline,
        )

        self.assertIsNotNone(alert)
        self.assertEqual(alert.incident_id, "demo-123")
        self.assertEqual(alert.scenario, "redis_down")

    def test_parse_user_alert_accepts_curveball_postgres_description(self) -> None:
        alert = _parse_user_alert(
            "the catalog page spins forever and db connections keep failing",
            self.pipeline,
        )

        self.assertIsNotNone(alert)
        self.assertIsNone(alert.scenario)
        self.assertEqual(alert.service, "catalogservice")

    def test_parse_user_alert_accepts_curveball_redis_description(self) -> None:
        alert = _parse_user_alert(
            "cart is broken and cache lookups are timing out",
            self.pipeline,
        )

        self.assertIsNotNone(alert)
        self.assertIsNone(alert.scenario)
        self.assertEqual(alert.service, "basketservice")

    def test_parse_user_alert_builds_live_diagnosis_alert_for_generic_breakage(self) -> None:
        alert = _parse_user_alert(
            "what broke in aspire shop right now?",
            self.pipeline,
        )

        self.assertIsNotNone(alert)
        self.assertIsNone(alert.scenario)
        self.assertEqual(alert.service, "aspire-shop")
        self.assertEqual(alert.labels.get("mode"), "live-diagnosis")

    def test_parse_user_alert_assigns_unique_incident_ids_for_repeated_live_alerts(self) -> None:
        with patch("iirs.pipeline.utc_now", return_value="2026-04-11T12:00:00+00:00"):
            with patch("iirs.pipeline.unique_suffix", side_effect=["live1234", "live5678"]):
                first = _parse_user_alert(
                    "what broke in aspire shop right now?",
                    self.pipeline,
                )
                second = _parse_user_alert(
                    "what broke in aspire shop right now?",
                    self.pipeline,
                )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first.incident_id, second.incident_id)

    def test_parse_user_alert_builds_live_health_check_alert_for_generic_health_question(self) -> None:
        alert = _parse_user_alert(
            "is everything healthy or broken right now?",
            self.pipeline,
        )

        self.assertIsNotNone(alert)
        self.assertIsNone(alert.scenario)
        self.assertEqual(alert.service, "aspire-shop")
        self.assertEqual(alert.labels.get("mode"), "live-health-check")

    def test_parse_user_alert_builds_live_health_check_alert_for_health_check_phrase(self) -> None:
        alert = _parse_user_alert(
            "can you check the health of aspireshop?",
            self.pipeline,
        )

        self.assertIsNotNone(alert)
        self.assertIsNone(alert.scenario)
        self.assertEqual(alert.service, "aspire-shop")
        self.assertEqual(alert.labels.get("mode"), "live-health-check")

    def test_parse_user_alert_builds_live_health_check_alert_for_healthy_or_having_issues_phrase(self) -> None:
        alert = _parse_user_alert(
            "is the aspireshop healthy or having issues?",
            self.pipeline,
        )

        self.assertIsNotNone(alert)
        self.assertIsNone(alert.scenario)
        self.assertEqual(alert.service, "aspire-shop")
        self.assertEqual(alert.labels.get("mode"), "live-health-check")

    def test_parse_user_alert_builds_live_diagnosis_alert_for_catalog_issue_without_dependency_keyword(self) -> None:
        alert = _parse_user_alert(
            "catalog page is failing and I need you to investigate",
            self.pipeline,
        )

        self.assertIsNotNone(alert)
        self.assertIsNone(alert.scenario)
        self.assertEqual(alert.service, "catalogservice")
        self.assertEqual(alert.labels.get("source"), "chat-live")

    def test_parse_user_alert_builds_live_diagnosis_alert_for_page_not_loading_prompt(self) -> None:
        alert = _parse_user_alert(
            "can you check why the aspire shop page is not loading at all?",
            self.pipeline,
        )

        self.assertIsNotNone(alert)
        self.assertIsNone(alert.scenario)
        self.assertEqual(alert.service, "frontend")
        self.assertEqual(alert.labels.get("mode"), "live-diagnosis")

    def test_contextual_follow_up_detects_short_health_question(self) -> None:
        self.assertTrue(_looks_like_contextual_follow_up("is it healthy?"))

    def test_contextual_follow_up_does_not_capture_broad_health_check(self) -> None:
        self.assertFalse(_looks_like_contextual_follow_up("is everything healthy or broken right now?"))

    def test_classify_user_message_routes_explicit_follow_up_when_state_exists(self) -> None:
        kind, alert = _classify_user_message(
            "What is the root cause?",
            self.pipeline,
            has_last_state=True,
        )

        self.assertEqual(kind, "follow-up")
        self.assertIsNone(alert)

    def test_classify_user_message_keeps_parse_miss_as_new_incident_prompt(self) -> None:
        kind, alert = _classify_user_message(
            "checkout is slow",
            self.pipeline,
            has_last_state=True,
        )

        self.assertEqual(kind, "incident")
        self.assertIsNotNone(alert)
        self.assertEqual(alert.service, "basketservice")


if __name__ == "__main__":
    unittest.main()
