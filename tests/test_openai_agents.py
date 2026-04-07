from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from iirs.config import Settings
from iirs.llm import OpenAIConfigurationError, OpenAIRequestError, build_reasoning_client
from iirs.pipeline import IIRSPipeline


class FakeReasoningClient:
    def analyze_incident(self, state):
        return {
            "summary": "Model ranked a PostgreSQL outage first.",
            "hypotheses": [
                {
                    "title": "PostgreSQL dependency outage",
                    "confidence": 0.91,
                    "supporting_evidence_ids": ["log.pg.connection_refused", "metric.pg.error_rate"],
                    "contradicting_evidence_ids": ["change.pg.none"],
                    "next_checks": ["Confirm the PostgreSQL process is reachable."],
                },
                {
                    "title": "Connection pool exhaustion during dependency failure",
                    "confidence": 0.44,
                    "supporting_evidence_ids": ["trace.pg.checkout_failure"],
                    "contradicting_evidence_ids": [],
                    "next_checks": ["Inspect worker saturation."],
                },
            ],
        }

    def critique_incident(self, state):
        return {
            "summary": "Model critique accepted the main hypothesis with approval boundaries.",
            "relevant_evidence_ids": ["log.pg.connection_refused", "metric.pg.error_rate"],
            "hallucination_risks": ["Change evidence is weak compared with direct dependency failure signals."],
            "missing_data": [],
            "safety_notes": ["Do not restart infrastructure without approval."],
            "findings": [
                {
                    "severity": "info",
                    "message": "The top hypothesis is grounded in direct dependency failure evidence.",
                    "evidence_ids": ["log.pg.connection_refused"],
                }
            ],
        }

    def plan_incident(self, state):
        return {
            "summary": "Model planner produced one safe check and one approval step.",
            "brief_title": "Incident Brief: postgres_down",
            "brief_summary": "PostgreSQL is the most likely failed dependency for catalogservice.",
            "open_questions": ["Is PostgreSQL healthy at the container layer?"],
            "evidence_snapshot": ["catalogservice failed to connect to PostgreSQL"],
            "steps": [
                {
                    "description": "Inspect PostgreSQL health and logs without changing service state.",
                    "action_type": "auto-safe",
                    "rationale": "This confirms the dependency failure safely.",
                    "evidence_ids": ["log.pg.connection_refused"],
                },
                {
                    "description": "Restart or fail over PostgreSQL if the service remains unavailable.",
                    "action_type": "needs-approval",
                    "rationale": "This changes dependency state.",
                    "evidence_ids": ["metric.pg.error_rate"],
                },
            ],
        }

    def answer_follow_up(self, question, state):
        return f"model-follow-up:{question}"


class FailingCriticReasoningClient(FakeReasoningClient):
    def critique_incident(self, state):
        raise OpenAIRequestError("The read operation timed out")

    def answer_follow_up(self, question, state):
        raise OpenAIRequestError("The read operation timed out")


class OpenAIAgentIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trace_dir = ROOT / "traces" / "openai-agent-output"
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.settings = Settings(
            trace_dir=self.trace_dir,
            runbooks_dir=ROOT / "runbooks",
            fixtures_dir=ROOT / "fixtures" / "alerts",
            ground_truth_dir=ROOT / "fixtures" / "ground_truth",
            prefer_langgraph=False,
            openai_enabled=False,
        )

    def test_pipeline_uses_injected_reasoning_client(self) -> None:
        pipeline = IIRSPipeline(settings=self.settings, reasoning_client=FakeReasoningClient())

        state = pipeline.run_scenario("postgres_down")

        self.assertEqual(state["incident_brief"].probable_root_causes[0].title, "PostgreSQL dependency outage")
        self.assertEqual(state["incident_brief"].recommended_actions[0].action_type, "auto-safe")
        self.assertTrue(any(step.action_type == "needs-approval" for step in state["incident_brief"].recommended_actions))
        self.assertIn("PostgreSQL", state["incident_brief"].summary)
        self.assertEqual(pipeline.follow_up("What changed?", state), "model-follow-up:What changed?")

    def test_build_reasoning_client_requires_key_when_enabled(self) -> None:
        enabled_settings = Settings(
            trace_dir=self.trace_dir,
            runbooks_dir=ROOT / "runbooks",
            fixtures_dir=ROOT / "fixtures" / "alerts",
            ground_truth_dir=ROOT / "fixtures" / "ground_truth",
            prefer_langgraph=False,
            openai_enabled=True,
            openai_api_key=None,
        )

        with self.assertRaises(OpenAIConfigurationError):
            build_reasoning_client(enabled_settings)

    def test_pipeline_falls_back_when_critic_times_out(self) -> None:
        pipeline = IIRSPipeline(settings=self.settings, reasoning_client=FailingCriticReasoningClient())

        state = pipeline.run_scenario("postgres_down")

        self.assertTrue(state["critique"].findings)
        critic_trace = next(run for run in state["trace_runs"] if run.agent_name == "Critic")
        self.assertIn("Fell back to deterministic critique", critic_trace.output_summary)
        follow_up = pipeline.follow_up("What is the root cause?", state)
        self.assertIn("Most likely root cause", follow_up)


if __name__ == "__main__":
    unittest.main()
