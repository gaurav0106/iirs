from __future__ import annotations

from contextlib import redirect_stdout
import io
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

from iirs.backends import TelemetryConfigurationError, TelemetryRequestError
from iirs.cli import main
from iirs.llm import OpenAIRequestError
from iirs.models import AgentRun, AlertPayload, IncidentBrief, PlanStep, Hypothesis


class FakeLLM:
    def check_connection(self):
        return "READY gpt-5-mini"


class FakePipeline:
    def __init__(self):
        self.context = type("Context", (), {"llm": FakeLLM()})()
        self.loaded_alert_path = None
        self.last_question = None

    def load_alert(self, path):
        self.loaded_alert_path = path
        return "loaded-alert"

    def build_live_alert(self, summary, *, service=None, environment="local-dev", window_minutes=10, mode="live-diagnosis", source="cli-live"):
        return AlertPayload(
            incident_id="live-1",
            summary=summary,
            severity="critical",
            service=service or "catalogservice",
            environment=environment,
            started_at="2026-04-11T00:00:00Z",
            window_minutes=window_minutes,
            scenario=None,
            labels={"source": source, "mode": mode},
        )

    def run(self, alert):
        return self._state("live")

    def follow_up(self, question, state):
        self.last_question = question
        return f"FOLLOW-UP:{question}"

    def _state(self, scenario_name):
        return {
            "trace_path": "/tmp/fake-trace.json",
            "trace_runs": [
                AgentRun(
                    agent_name="Retriever",
                    started_at="2026-04-11T00:00:00+00:00",
                    finished_at="2026-04-11T00:00:01+00:00",
                    input_summary="input",
                    output_summary="output",
                    execution_mode="tooling",
                )
            ],
            "incident_brief": IncidentBrief(
                title=f"Incident Brief: {scenario_name}",
                summary="summary",
                probable_root_causes=[
                    Hypothesis(
                        rank=1,
                        title="PostgreSQL dependency outage",
                        confidence=0.91,
                        supporting_evidence_ids=["log.pg.connection_refused"],
                        contradicting_evidence_ids=[],
                        next_checks=["check it"],
                    )
                ],
                recommended_actions=[
                    PlanStep(
                        order=1,
                        description="Inspect dependency health.",
                        action_type="auto-safe",
                        rationale="safe",
                        evidence_ids=["log.pg.connection_refused"],
                    )
                ],
                open_questions=[],
                evidence_snapshot=[],
            ),
            "alert": AlertPayload(
                incident_id="fake-1",
                summary="summary",
                severity="critical",
                service="catalogservice",
                environment="local-dev",
                started_at="2026-04-11T00:00:00Z",
                scenario=None,
            ),
        }


class FailingRunPipeline(FakePipeline):
    def run(self, alert):
        raise OpenAIRequestError("The read operation timed out")


class FailingFollowUpPipeline(FakePipeline):
    def follow_up(self, question, state):
        raise OpenAIRequestError("The read operation timed out")


class TelemetryFailingRunPipeline(FakePipeline):
    def run(self, alert):
        raise TelemetryRequestError("loki query failed: timed out")


class TelemetryFailingAskPipeline(FakePipeline):
    def follow_up(self, question, state):
        raise TelemetryConfigurationError("PLT backend requires IIRS_PROMETHEUS_URL")


class CLITests(unittest.TestCase):
    def test_ask_command_prints_follow_up_and_trace(self) -> None:
        stdout = io.StringIO()
        pipeline = FakePipeline()
        with patch("iirs.cli.IIRSPipeline", return_value=pipeline):
            with redirect_stdout(stdout):
                rc = main(["ask", "--summary", "catalogservice is timing out and PostgreSQL looks down", "How sure are we?"])

        output = stdout.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("FOLLOW-UP:How sure are we?", output)
        self.assertIn("Trace: /tmp/fake-trace.json", output)

    def test_llm_check_command_succeeds_when_llm_is_configured(self) -> None:
        stdout = io.StringIO()
        with patch("iirs.cli.load_settings", return_value=object()):
            with patch("iirs.cli.build_reasoning_client", return_value=FakeLLM()):
                with patch("iirs.cli.IIRSPipeline", side_effect=AssertionError("should not build pipeline")):
                    with redirect_stdout(stdout):
                        rc = main(["llm-check"])

        self.assertEqual(rc, 0)
        self.assertIn("READY gpt-5-mini", stdout.getvalue())

    def test_llm_check_command_fails_cleanly_when_llm_is_disabled(self) -> None:
        stdout = io.StringIO()
        with patch("iirs.cli.load_settings", return_value=object()):
            with patch("iirs.cli.build_reasoning_client", return_value=None):
                with patch("iirs.cli.IIRSPipeline", side_effect=AssertionError("should not build pipeline")):
                    with redirect_stdout(stdout):
                        rc = main(["llm-check"])

        self.assertEqual(rc, 1)
        self.assertIn("OpenAI-backed reasoning is not enabled", stdout.getvalue())

    def test_run_command_fails_cleanly_when_model_request_fails(self) -> None:
        stdout = io.StringIO()
        with patch("iirs.cli.IIRSPipeline", return_value=FailingRunPipeline()):
            with redirect_stdout(stdout):
                rc = main(["run", "--summary", "catalogservice is timing out and PostgreSQL looks down"])

        self.assertEqual(rc, 1)
        self.assertIn("Model request failed; stopping instead of falling back", stdout.getvalue())

    def test_run_command_fails_cleanly_when_telemetry_request_fails(self) -> None:
        stdout = io.StringIO()
        with patch("iirs.cli.IIRSPipeline", return_value=TelemetryFailingRunPipeline()):
            with redirect_stdout(stdout):
                rc = main(["run", "--summary", "catalogservice is timing out and PostgreSQL looks down"])

        self.assertEqual(rc, 1)
        self.assertIn("Telemetry failed; stopping cleanly", stdout.getvalue())

    def test_ask_command_fails_cleanly_when_follow_up_model_request_fails(self) -> None:
        stdout = io.StringIO()
        with patch("iirs.cli.IIRSPipeline", return_value=FailingFollowUpPipeline()):
            with redirect_stdout(stdout):
                rc = main(["ask", "--summary", "catalogservice is timing out and PostgreSQL looks down", "What is the root cause?"])

        self.assertEqual(rc, 1)
        self.assertIn("Model request failed; stopping instead of falling back", stdout.getvalue())

    def test_ask_command_fails_cleanly_when_telemetry_configuration_fails(self) -> None:
        stdout = io.StringIO()
        with patch("iirs.cli.IIRSPipeline", return_value=TelemetryFailingAskPipeline()):
            with redirect_stdout(stdout):
                rc = main(["ask", "--summary", "catalogservice is timing out and PostgreSQL looks down", "What is the root cause?"])

        self.assertEqual(rc, 1)
        self.assertIn("Telemetry failed; stopping cleanly", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
