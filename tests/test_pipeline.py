from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from iirs.config import Settings
from iirs.pipeline import IIRSPipeline


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
        self.pipeline = IIRSPipeline(settings=self.settings)

    def test_postgres_scenario_produces_expected_root_cause(self) -> None:
        state = self.pipeline.run_scenario("postgres_down")

        brief = state["incident_brief"]
        self.assertEqual(brief.probable_root_causes[0].title, "PostgreSQL dependency outage")
        self.assertEqual(len(state["trace_runs"]), 4)
        self.assertTrue(any(step.action_type == "needs-approval" for step in brief.recommended_actions))
        self.assertTrue(Path(state["trace_path"]).exists())

    def test_redis_scenario_produces_expected_root_cause(self) -> None:
        state = self.pipeline.run_scenario("redis_down")

        brief = state["incident_brief"]
        self.assertEqual(brief.probable_root_causes[0].title, "Redis dependency outage")
        self.assertGreaterEqual(len(state["evidence_bundle"].all_items()), 7)

    def test_follow_up_uses_last_incident_state(self) -> None:
        state = self.pipeline.run_scenario("postgres_down")

        answer = self.pipeline.follow_up("What is the root cause and what evidence supports it?", state)

        self.assertIn("PostgreSQL dependency outage", answer)
        self.assertIn("Supporting evidence", answer)


if __name__ == "__main__":
    unittest.main()
