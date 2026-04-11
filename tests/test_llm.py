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


if __name__ == "__main__":
    unittest.main()
