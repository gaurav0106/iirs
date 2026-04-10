from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from iirs.config import Settings
from iirs.evaluation import EvaluationHarness, load_ground_truth_labels, render_evaluation_markdown
from iirs.pipeline import IIRSPipeline


class EvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trace_dir = ROOT / "traces" / "evaluation-output"
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.settings = Settings(
            trace_dir=self.trace_dir,
            runbooks_dir=ROOT / "runbooks",
            fixtures_dir=ROOT / "fixtures" / "alerts",
            ground_truth_dir=ROOT / "fixtures" / "ground_truth",
            prefer_langgraph=False,
        )
        self.pipeline = IIRSPipeline(settings=self.settings)

    def test_ground_truth_labels_load_for_both_scenarios(self) -> None:
        labels = load_ground_truth_labels(self.settings.ground_truth_dir)

        self.assertEqual(set(labels), {"postgres_down", "redis_down"})
        self.assertEqual(labels["postgres_down"].expected_root_cause, "PostgreSQL dependency outage")
        self.assertEqual(labels["redis_down"].required_action_types, ["auto-safe", "needs-approval"])

    def test_quantitative_evaluation_passes_for_mock_scenarios(self) -> None:
        harness = EvaluationHarness.from_directory(self.pipeline, self.settings.ground_truth_dir)

        report = harness.evaluate_scenarios(["postgres_down", "redis_down"], runs_per_scenario=2)

        self.assertEqual(report.total_runs, 4)
        self.assertEqual(report.top1_accuracy, 1.0)
        self.assertEqual(report.top3_accuracy, 1.0)
        self.assertGreater(report.qualitative_score, 0.0)
        self.assertTrue(report.passed)

        postgres_report = next(
            scenario_report
            for scenario_report in report.scenario_reports
            if scenario_report.scenario_name == "postgres_down"
        )
        self.assertTrue(Path(postgres_report.runs[0].trace_path).exists())
        self.assertFalse(postgres_report.runs[0].missing_evidence_descriptions)
        self.assertFalse(postgres_report.runs[0].missing_action_keywords)
        self.assertFalse(postgres_report.runs[0].missing_action_types)
        self.assertEqual(
            postgres_report.runs[0].qualitative_passed_count,
            postgres_report.runs[0].qualitative_total,
        )

    def test_markdown_renderer_includes_accuracy_summary(self) -> None:
        harness = EvaluationHarness.from_directory(self.pipeline, self.settings.ground_truth_dir)
        report = harness.evaluate_scenarios(["postgres_down"], runs_per_scenario=1)

        rendered = render_evaluation_markdown(report)

        self.assertIn("# IIRS Evaluation", rendered)
        self.assertIn("Top-1 accuracy", rendered)
        self.assertIn("Qualitative review score", rendered)
        self.assertIn("postgres_down", rendered)


if __name__ == "__main__":
    unittest.main()
