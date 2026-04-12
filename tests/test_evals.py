from __future__ import annotations

import json
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
from iirs.evals import render_eval_json, run_evals


class EvalHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trace_dir = ROOT / "traces" / "eval-test-output"
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.settings = Settings(
            trace_dir=self.trace_dir,
            runbooks_dir=ROOT / "runbooks",
            fixtures_dir=ROOT / "fixtures" / "alerts",
            prefer_langgraph=False,
            openai_enabled=False,
        )

    def test_run_evals_passes_default_suites(self) -> None:
        report = run_evals(self.settings)

        self.assertTrue(report.passed)
        self.assertEqual([suite.suite_name for suite in report.suite_results], ["routing", "pipeline"])
        self.assertEqual(report.total_cases, 9)
        self.assertGreater(report.total_checks, report.total_cases)

    def test_run_evals_can_filter_single_pipeline_case(self) -> None:
        report = run_evals(
            self.settings,
            suite_names=["pipeline"],
            case_names=["healthy-live-health-check"],
        )

        self.assertTrue(report.passed)
        self.assertEqual(report.total_suites, 1)
        self.assertEqual(report.total_cases, 1)
        self.assertEqual(report.suite_results[0].case_results[0].case_name, "healthy-live-health-check")

    def test_run_evals_rejects_case_outside_selected_suites(self) -> None:
        with self.assertRaises(KeyError):
            run_evals(
                self.settings,
                suite_names=["routing"],
                case_names=["postgres-fixture"],
            )

    def test_render_eval_json_includes_pass_flag_and_suite_name(self) -> None:
        report = run_evals(
            self.settings,
            suite_names=["routing"],
            case_names=["health-check-aspireshop"],
        )

        payload = json.loads(render_eval_json(report))

        self.assertTrue(payload["passed"])
        self.assertEqual(payload["suite_results"][0]["suite_name"], "routing")


if __name__ == "__main__":
    unittest.main()
