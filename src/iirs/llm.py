from __future__ import annotations

import json
from typing import Any, Protocol

import httpx

from .config import Settings
from .models import Critique, EvidenceBundle, Hypothesis, IIRSState
from .utils import to_jsonable


class ReasoningClient(Protocol):
    def analyze_incident(self, state: IIRSState) -> dict[str, Any]: ...

    def critique_incident(self, state: IIRSState) -> dict[str, Any]: ...

    def plan_incident(self, state: IIRSState) -> dict[str, Any]: ...

    def answer_follow_up(self, question: str, state: IIRSState) -> str: ...


class OpenAIConfigurationError(RuntimeError):
    pass


class OpenAIRequestError(RuntimeError):
    pass


def _json_schema(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": name,
        "schema": schema,
        "strict": True,
    }


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


def _string_array() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}}


class OpenAIResponsesReasoner:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        reasoning_effort: str = "medium",
        timeout_seconds: float = 30.0,
        verify_tls: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.reasoning_effort = reasoning_effort
        self.client = client or httpx.Client(
            timeout=timeout_seconds,
            verify=verify_tls,
            follow_redirects=True,
        )

    def analyze_incident(self, state: IIRSState) -> dict[str, Any]:
        schema = _object(
            {
                "summary": {"type": "string"},
                "hypotheses": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": _object(
                        {
                            "title": {"type": "string"},
                            "confidence": {"type": "number"},
                            "supporting_evidence_ids": _string_array(),
                            "contradicting_evidence_ids": _string_array(),
                            "next_checks": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
                        },
                        [
                            "title",
                            "confidence",
                            "supporting_evidence_ids",
                            "contradicting_evidence_ids",
                            "next_checks",
                        ],
                    ),
                },
            },
            ["summary", "hypotheses"],
        )
        system_prompt = (
            "You are the IIRS Analyst agent. Rank likely root causes using only the provided alert and evidence. "
            "Every evidence reference must use one of the provided evidence IDs. "
            "Prefer short canonical titles. If the evidence clearly shows PostgreSQL is unavailable, use the title "
            "'PostgreSQL dependency outage'. If it clearly shows Redis is unavailable, use the title "
            "'Redis dependency outage'. Keep the response concise."
        )
        return self._structured_response(
            schema_name="iirs_analyst_response",
            schema=schema,
            system_prompt=system_prompt,
            user_prompt=self._analyst_prompt(state),
        )

    def critique_incident(self, state: IIRSState) -> dict[str, Any]:
        schema = _object(
            {
                "summary": {"type": "string"},
                "relevant_evidence_ids": {"type": "array", "maxItems": 5, "items": {"type": "string"}},
                "hallucination_risks": {"type": "array", "maxItems": 4, "items": {"type": "string"}},
                "missing_data": {"type": "array", "maxItems": 4, "items": {"type": "string"}},
                "safety_notes": {"type": "array", "maxItems": 4, "items": {"type": "string"}},
                "findings": {
                    "type": "array",
                    "maxItems": 3,
                    "items": _object(
                        {
                            "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                            "message": {"type": "string"},
                            "evidence_ids": _string_array(),
                        },
                        ["severity", "message", "evidence_ids"],
                    ),
                },
            },
            [
                "summary",
                "relevant_evidence_ids",
                "hallucination_risks",
                "missing_data",
                "safety_notes",
                "findings",
            ],
        )
        system_prompt = (
            "You are the IIRS Critic agent. Validate that the analyst output is evidence-grounded, conservative, "
            "and safe. Flag weak support, missing data, or mitigation risk. Use only provided evidence IDs. "
            "Keep lists short and avoid repeating the same point."
        )
        return self._structured_response(
            schema_name="iirs_critic_response",
            schema=schema,
            system_prompt=system_prompt,
            user_prompt=self._critic_prompt(state),
        )

    def plan_incident(self, state: IIRSState) -> dict[str, Any]:
        schema = _object(
            {
                "summary": {"type": "string"},
                "brief_title": {"type": "string"},
                "brief_summary": {"type": "string"},
                "open_questions": {"type": "array", "maxItems": 4, "items": {"type": "string"}},
                "evidence_snapshot": {"type": "array", "maxItems": 5, "items": {"type": "string"}},
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 5,
                    "items": _object(
                        {
                            "description": {"type": "string"},
                            "action_type": {"type": "string", "enum": ["auto-safe", "needs-approval"]},
                            "rationale": {"type": "string"},
                            "evidence_ids": _string_array(),
                        },
                        ["description", "action_type", "rationale", "evidence_ids"],
                    ),
                },
            },
            [
                "summary",
                "brief_title",
                "brief_summary",
                "open_questions",
                "evidence_snapshot",
                "steps",
            ],
        )
        system_prompt = (
            "You are the IIRS Planner agent. Produce a concise incident brief and a step-by-step triage plan. "
            "Mark read-only diagnostic actions as 'auto-safe'. Mark any state-changing actions such as restarts, "
            "failover, rollback, or traffic changes as 'needs-approval'. Use only provided evidence IDs. "
            "Keep the plan concise."
        )
        return self._structured_response(
            schema_name="iirs_planner_response",
            schema=schema,
            system_prompt=system_prompt,
            user_prompt=self._planner_prompt(state),
        )

    def answer_follow_up(self, question: str, state: IIRSState) -> str:
        system_prompt = (
            "You are the IIRS follow-up assistant. Answer only from the provided incident state. "
            "Be concise, mention uncertainty when needed, and cite evidence IDs inline when useful."
        )
        return self._text_response(
            system_prompt=system_prompt,
            user_prompt=self._follow_up_prompt(question, state),
            max_output_tokens=500,
        )

    def _analyst_prompt(self, state: IIRSState) -> str:
        payload = {
            "alert": to_jsonable(state["alert"]),
            "evidence_bundle": self._serialize_evidence_bundle(state["evidence_bundle"]),
        }
        return "Analyze this incident and rank likely root causes.\n" + json.dumps(payload, indent=2)

    def _critic_prompt(self, state: IIRSState) -> str:
        payload = {
            "alert": to_jsonable(state["alert"]),
            "evidence_bundle": self._serialize_evidence_bundle(state["evidence_bundle"]),
            "hypotheses": [self._serialize_hypothesis(item) for item in state["hypotheses"]],
        }
        return "Critique this incident analysis.\n" + json.dumps(payload, indent=2)

    def _planner_prompt(self, state: IIRSState) -> str:
        payload = {
            "alert": to_jsonable(state["alert"]),
            "evidence_bundle": self._serialize_evidence_bundle(state["evidence_bundle"]),
            "hypotheses": [self._serialize_hypothesis(item) for item in state["hypotheses"]],
            "critique": self._serialize_critique(state["critique"]),
        }
        return "Create the final incident brief and triage plan.\n" + json.dumps(payload, indent=2)

    def _follow_up_prompt(self, question: str, state: IIRSState) -> str:
        payload = {
            "question": question,
            "alert": to_jsonable(state["alert"]),
            "incident_brief": to_jsonable(state["incident_brief"]),
            "evidence_bundle": self._serialize_evidence_bundle(state["evidence_bundle"]),
        }
        return json.dumps(payload, indent=2)

    def _serialize_evidence_bundle(self, bundle: EvidenceBundle) -> dict[str, Any]:
        return {
            "items": [
                {
                    "id": item.id,
                    "category": item.category,
                    "service": item.service,
                    "summary": item.summary,
                    "value": item.value,
                    "citation": {
                        "source_type": item.citations[0].source_type,
                        "observed_at": item.citations[0].observed_at,
                        "excerpt": item.citations[0].excerpt,
                    },
                }
                for item in bundle.all_items()
            ]
        }

    def _serialize_hypothesis(self, hypothesis: Hypothesis) -> dict[str, Any]:
        return {
            "rank": hypothesis.rank,
            "title": hypothesis.title,
            "confidence": hypothesis.confidence,
            "supporting_evidence_ids": hypothesis.supporting_evidence_ids,
            "contradicting_evidence_ids": hypothesis.contradicting_evidence_ids,
            "next_checks": hypothesis.next_checks,
        }

    def _serialize_critique(self, critique: Critique) -> dict[str, Any]:
        return {
            "relevant_evidence_ids": critique.relevant_evidence_ids,
            "hallucination_risks": critique.hallucination_risks,
            "missing_data": critique.missing_data,
            "safety_notes": critique.safety_notes,
            "findings": [
                {
                    "severity": finding.severity,
                    "message": finding.message,
                    "evidence_ids": finding.evidence_ids,
                }
                for finding in critique.findings
            ],
        }

    def _structured_response(
        self,
        *,
        schema_name: str,
        schema: dict[str, Any],
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        max_output_tokens = 900
        effort = self.reasoning_effort
        for attempt in range(2):
            payload: dict[str, Any] = {
                "model": self.model,
                "input": self._build_input(system_prompt, user_prompt),
                "text": {"format": _json_schema(schema_name, schema), "verbosity": "low"},
                "max_output_tokens": max_output_tokens,
            }
            if self._supports_reasoning():
                payload["reasoning"] = {"effort": effort}
            data = self._post_responses(payload)
            try:
                text = self._extract_output_text(data)
            except OpenAIRequestError:
                if attempt == 0 and self._should_retry_for_output(data):
                    max_output_tokens = 1800
                    effort = self._fallback_reasoning_effort(effort)
                    continue
                raise
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                if attempt == 0 and self._should_retry_for_output(data):
                    max_output_tokens = 1800
                    effort = self._fallback_reasoning_effort(effort)
                    continue
                raise OpenAIRequestError(f"OpenAI response did not contain valid JSON: {text!r}") from exc
        raise OpenAIRequestError("OpenAI response retry logic exhausted without producing structured output.")

    def _text_response(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": self._build_input(system_prompt, user_prompt),
            "max_output_tokens": max_output_tokens,
        }
        if self._supports_reasoning():
            payload["reasoning"] = {"effort": self.reasoning_effort}
        data = self._post_responses(payload)
        return self._extract_output_text(data).strip()

    def _build_input(self, system_prompt: str, user_prompt: str) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
        ]

    def _post_responses(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = self.client.post(f"{self.base_url}/responses", headers=headers, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OpenAIRequestError(f"OpenAI Responses API request failed: {exc}") from exc

        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            raise OpenAIRequestError(f"OpenAI Responses API error: {data['error']}")
        return data

    def _extract_output_text(self, payload: dict[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        parts: list[str] = []
        for item in payload.get("output", []):
            for content in item.get("content", []):
                text = content.get("text")
                if content.get("type") in {"output_text", "text"} and isinstance(text, str):
                    parts.append(text)
        if parts:
            return "".join(parts)
        raise OpenAIRequestError(f"OpenAI response did not include text output: {payload}")

    def _supports_reasoning(self) -> bool:
        return self.model.startswith("gpt-5") or self.model.startswith("o")

    def _should_retry_for_output(self, payload: dict[str, Any]) -> bool:
        incomplete = payload.get("incomplete_details") or {}
        if incomplete.get("reason") != "max_output_tokens":
            return False
        output = payload.get("output", [])
        return bool(output) and all(item.get("type") == "reasoning" for item in output)

    def _fallback_reasoning_effort(self, effort: str) -> str:
        order = ["high", "medium", "low", "minimal"]
        if effort not in order:
            return "low"
        index = order.index(effort)
        if index == len(order) - 1:
            return effort
        return order[index + 1]


def build_reasoning_client(
    settings: Settings,
    *,
    client: httpx.Client | None = None,
) -> ReasoningClient | None:
    if not settings.openai_enabled:
        return None
    if not settings.openai_api_key:
        raise OpenAIConfigurationError(
            "OpenAI-backed agents are enabled but no API key was found in OPENAI_API_KEY or IIRS_OPENAI_API_KEY."
        )
    return OpenAIResponsesReasoner(
        api_key=settings.openai_api_key,
        model=settings.agent_model,
        base_url=settings.openai_base_url,
        reasoning_effort=settings.openai_reasoning_effort,
        timeout_seconds=max(60.0, settings.http_timeout_seconds),
        verify_tls=settings.verify_tls,
        client=client,
    )
