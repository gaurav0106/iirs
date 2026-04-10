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
from iirs.pipeline import IIRSPipeline


class ChainlitInputParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trace_dir = ROOT / "traces" / "chainlit-test-output"
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

    def test_parse_user_alert_accepts_freeform_postgres_description(self) -> None:
        alert = _parse_user_alert(
            "catalogservice is timing out and PostgreSQL looks down",
            self.pipeline,
        )

        self.assertIsNotNone(alert)
        self.assertEqual(alert.scenario, "postgres_down")
        self.assertIn("catalogservice is timing out", alert.summary)
        self.assertEqual(alert.labels.get("source"), "chat-freeform")

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


if __name__ == "__main__":
    unittest.main()
