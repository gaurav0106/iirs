from __future__ import annotations

from pathlib import Path
import sys
import unittest

import httpx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from iirs.llm import OpenAIRequestError, OpenAIResponsesReasoner
from iirs.models import (
    AlertPayload,
    Citation,
    ConversationTurn,
    Critique,
    CritiqueFinding,
    EvidenceBundle,
    EvidenceItem,
    Hypothesis,
    IIRSState,
    IncidentBrief,
)


class FakeHTTPResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class FakeHTTPClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def post(self, url, headers, json):
        self.calls.append(json)
        return FakeHTTPResponse(self.payloads.pop(0))


def _sample_state() -> IIRSState:
    evidence_items = [
        EvidenceItem(
            id="runtime.pg.down",
            category="runtime_states",
            service="postgres",
            summary="postgres container is exited",
            value="exited",
            citations=[
                Citation(
                    id="cite-runtime-pg-down",
                    source_type="runtime",
                    source="aspire",
                    query="postgres runtime state",
                    observed_at="2026-04-12T13:00:00Z",
                    excerpt="postgres exited",
                )
            ],
            metadata={"family": "postgres", "resource": "postgres"},
        ),
        EvidenceItem(
            id="log.pg.connection_refused",
            category="logs",
            service="catalogservice",
            summary="catalogservice failed to connect to PostgreSQL",
            value="connection refused",
            citations=[
                Citation(
                    id="cite-log-pg-conn",
                    source_type="loki",
                    source="loki",
                    query='service_name="catalogservice"',
                    observed_at="2026-04-12T13:00:10Z",
                    excerpt="Npgsql connection refused",
                )
            ],
        ),
        EvidenceItem(
            id="metric.pg.error_rate",
            category="metrics",
            service="catalogservice",
            summary="catalogservice 5xx rate spiked",
            value="12.4%",
            citations=[
                Citation(
                    id="cite-metric-pg-err",
                    source_type="prometheus",
                    source="prometheus",
                    query='http_response_status_code=~"499|5.."',
                    observed_at="2026-04-12T13:00:20Z",
                    excerpt="5xx rate elevated",
                )
            ],
        ),
        EvidenceItem(
            id="trace.pg.checkout_failure",
            category="traces",
            service="catalogservice",
            summary="failed trace shows PostgreSQL checkout timeout",
            value="timeout",
            citations=[
                Citation(
                    id="cite-trace-pg-timeout",
                    source_type="tempo",
                    source="tempo",
                    query="status = error",
                    observed_at="2026-04-12T13:00:30Z",
                    excerpt="db checkout timeout",
                )
            ],
        ),
    ]
    bundle = EvidenceBundle(
        runtime_states=[evidence_items[0]],
        logs=[evidence_items[1]],
        metrics=[evidence_items[2]],
        traces=[evidence_items[3]],
    )
    hypotheses = [
        Hypothesis(
            rank=1,
            title="PostgreSQL dependency outage",
            confidence=0.91,
            supporting_evidence_ids=[item.id for item in evidence_items[:4]],
            contradicting_evidence_ids=[],
            next_checks=[
                "Inspect PostgreSQL health and logs without changing state.",
                "Confirm the first PostgreSQL connection failures.",
            ],
        )
    ]
    critique = Critique(
        relevant_evidence_ids=[evidence_items[0].id, evidence_items[1].id],
        hallucination_risks=["Recent change evidence is missing."],
        missing_data=["No change feed result was captured near incident start."],
        safety_notes=["Treat state-changing remediation as needs-approval."],
        findings=[
            CritiqueFinding(
                severity="warning",
                message="The top hypothesis still needs explicit change-data exclusion.",
                evidence_ids=[evidence_items[1].id],
            )
        ],
    )
    brief = IncidentBrief(
        title="Incident Brief: postgres_down",
        summary="Most likely root cause: PostgreSQL dependency outage.",
        probable_root_causes=hypotheses,
        recommended_actions=[],
        open_questions=critique.missing_data[:],
        evidence_snapshot=[item.summary for item in evidence_items[:3]],
    )
    return {
        "alert": AlertPayload(
            incident_id="inc-123",
            summary="catalogservice is failing with database errors",
            severity="high",
            service="catalogservice",
            environment="dev",
            started_at="2026-04-12T13:00:00Z",
            labels={"mode": "live-diagnosis"},
        ),
        "evidence_bundle": bundle,
        "hypotheses": hypotheses,
        "critique": critique,
        "incident_brief": brief,
        "messages": [
            ConversationTurn(
                role="user",
                content="What broke?",
                created_at="2026-04-12T13:01:00Z",
            )
        ],
        "trace_runs": [],
    }


class OpenAIReasonerTests(unittest.TestCase):
    def test_structured_response_retries_when_partial_json_was_truncated(self) -> None:
        client = FakeHTTPClient(
            [
                {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "output_text": '{"summary":"truncated',
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": '{"summary":"truncated',
                                }
                            ],
                        }
                    ],
                },
                {
                    "status": "completed",
                    "output_text": '{"summary":"ok"}',
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": '{"summary":"ok"}',
                                }
                            ],
                        }
                    ],
                },
            ]
        )
        reasoner = OpenAIResponsesReasoner(
            api_key="test-key",
            model="gpt-5-mini",
            client=client,
        )

        result = reasoner._structured_response(
            schema_name="test_response",
            schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
            system_prompt="Return JSON.",
            user_prompt="Return a summary.",
        )

        self.assertEqual(result, {"summary": "ok"})
        self.assertEqual(client.calls[0]["max_output_tokens"], 1200)
        self.assertEqual(client.calls[1]["max_output_tokens"], 2400)

    def test_structured_response_surfaces_clear_read_timeout_message(self) -> None:
        class TimeoutClient:
            def post(self, url, headers, json):
                raise httpx.ReadTimeout("The read operation timed out")

        reasoner = OpenAIResponsesReasoner(
            api_key="test-key",
            model="gpt-5-mini",
            timeout_seconds=45.0,
            client=TimeoutClient(),
        )

        with self.assertRaises(OpenAIRequestError) as context:
            reasoner._structured_response(
                schema_name="test_response",
                schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"],
                },
                system_prompt="Return JSON.",
                user_prompt="Return a summary.",
            )

        self.assertIn("read timed out", str(context.exception).lower())
        self.assertIn("45s", str(context.exception))

    def test_analyze_incident_request_uses_tuned_system_and_user_prompts(self) -> None:
        client = FakeHTTPClient(
            [
                {
                    "status": "completed",
                    "output_text": (
                        '{"summary":"ok","hypotheses":[{"title":"PostgreSQL dependency outage",'
                        '"confidence":0.91,"supporting_evidence_ids":["runtime.pg.down","log.pg.connection_refused",'
                        '"metric.pg.error_rate"],"contradicting_evidence_ids":[],"next_checks":["Inspect PostgreSQL health"]}]}'
                    ),
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": (
                                        '{"summary":"ok","hypotheses":[{"title":"PostgreSQL dependency outage",'
                                        '"confidence":0.91,"supporting_evidence_ids":["runtime.pg.down","log.pg.connection_refused",'
                                        '"metric.pg.error_rate"],"contradicting_evidence_ids":[],"next_checks":["Inspect PostgreSQL health"]}]}'
                                    ),
                                }
                            ],
                        }
                    ],
                }
            ]
        )
        reasoner = OpenAIResponsesReasoner(
            api_key="test-key",
            model="gpt-5-mini",
            client=client,
        )

        reasoner.analyze_incident(_sample_state())

        system_prompt = client.calls[0]["input"][0]["content"][0]["text"]
        user_prompt = client.calls[0]["input"][1]["content"][0]["text"]
        self.assertIn("Output must match the provided JSON schema exactly.", system_prompt)
        self.assertIn("No clear live fault detected", system_prompt)
        self.assertIn("dependency path degraded", system_prompt)
        self.assertIn("rank that service unavailable first", system_prompt)
        self.assertIn("keep ranks 2 and 3 generic", system_prompt)
        self.assertIn("Execution checklist:", user_prompt)
        self.assertIn("Use only evidence IDs from evidence_bundle.items[*].id.", user_prompt)
        self.assertIn('"evidence_bundle"', user_prompt)

    def test_critic_system_prompt_requires_non_rubber_stamp_caveats(self) -> None:
        reasoner = OpenAIResponsesReasoner(
            api_key="test-key",
            model="gpt-5-mini",
            client=FakeHTTPClient([]),
        )

        prompt = reasoner._critic_system_prompt()

        self.assertIn("surface at least one concrete risk", prompt)
        self.assertIn("missing traces", prompt)
        self.assertIn("need approval", prompt)

    def test_planner_prompts_preserve_missing_data_and_action_order(self) -> None:
        reasoner = OpenAIResponsesReasoner(
            api_key="test-key",
            model="gpt-5-mini",
            client=FakeHTTPClient([]),
        )

        system_prompt = reasoner._planner_system_prompt()
        user_prompt = reasoner._planner_prompt(_sample_state())

        self.assertIn("Copy each item from critique.missing_data into open_questions verbatim", system_prompt)
        self.assertIn("Inspect PostgreSQL health", system_prompt)
        self.assertIn("Restart or fail over PostgreSQL", system_prompt)
        self.assertIn("Start with auto-safe inspect or correlate steps", user_prompt)
        self.assertIn('"missing_data"', user_prompt)


if __name__ == "__main__":
    unittest.main()
