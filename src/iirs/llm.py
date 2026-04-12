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

    def check_connection(self) -> str: ...


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
        self.timeout_seconds = timeout_seconds
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(
                connect=min(timeout_seconds, 10.0),
                read=timeout_seconds,
                write=timeout_seconds,
                pool=timeout_seconds,
            ),
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
        return self._structured_response(
            schema_name="iirs_analyst_response",
            schema=schema,
            system_prompt=self._analyst_system_prompt(),
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
        return self._structured_response(
            schema_name="iirs_critic_response",
            schema=schema,
            system_prompt=self._critic_system_prompt(),
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
        return self._structured_response(
            schema_name="iirs_planner_response",
            schema=schema,
            system_prompt=self._planner_system_prompt(),
            user_prompt=self._planner_prompt(state),
        )

    def answer_follow_up(self, question: str, state: IIRSState) -> str:
        return self._text_response(
            system_prompt=self._follow_up_system_prompt(),
            user_prompt=self._follow_up_prompt(question, state),
            max_output_tokens=500,
        )

    def check_connection(self) -> str:
        return self._text_response(
            system_prompt=(
                "You are a connectivity probe for the IIRS incident assistant. "
                "Reply with one short line confirming the model is reachable."
            ),
            user_prompt=(
                f"Return a one-line readiness message that includes the exact model name `{self.model}` "
                "and no extra explanation."
            ),
            max_output_tokens=60,
        )

    def _analyst_system_prompt(self) -> str:
        return "\n".join(
            [
                "You are the IIRS Analyst agent.",
                "Act like a pragmatic incident responder, not a report generator.",
                "Output must match the provided JSON schema exactly.",
                "Operating rules:",
                "1. Rank likely root causes using only the provided alert and evidence; never invent services, resources, or evidence IDs.",
                "2. Prefer direct dependency or runtime-state failures over vague downstream symptoms and stale circumstantial clues.",
                "3. Use canonical titles when they fit exactly: PostgreSQL dependency outage, Redis dependency outage, Multiple service outages in Aspire Shop, and No clear live fault detected.",
                "4. If a service shows dependency-specific failures but runtime state does not prove the dependency is down, use a dependency path degraded title instead of claiming a hard outage.",
                "5. If alert.labels.mode is live-health-check and core runtime resources are still running, prefer No clear live fault detected at rank 1 unless there is direct current failure evidence.",
                "6. If runtime state shows a concrete service missing, exited, or unhealthy, rank that service unavailable first and treat frontend/UI bug theories as downstream impact unless frontend itself is the failing resource.",
                "7. If rank 1 is No clear live fault detected, keep ranks 2 and 3 generic unless current runtime evidence directly proves a specific resource or dependency is unhealthy; do not fill lower ranks with PostgreSQL or Redis theories just because there is weak stale noise.",
                "8. Keep recent deploy or config-regression theories as low-confidence fallbacks unless the evidence explicitly points there.",
                "9. Be conservative with confidence: reserve high confidence for direct outage proof plus supporting telemetry, and lower confidence when evidence is mixed or indirect.",
                "10. On the top hypothesis, cite the strongest 3-4 evidence IDs when available and try to span multiple evidence channels.",
                "11. Keep titles short and canonical, and keep next_checks concrete and read-only.",
            ]
        )

    def _critic_system_prompt(self) -> str:
        return "\n".join(
            [
                "You are the IIRS Critic agent.",
                "Output must match the provided JSON schema exactly.",
                "Validate that the analyst output is evidence-grounded, conservative, and operationally sane.",
                "Operating rules:",
                "1. Use only the provided evidence and evidence IDs; never fill gaps with invented proof.",
                "2. Treat narrow source coverage, missing traces, missing change data, or high confidence without direct runtime proof as material caveats.",
                "3. If evidence is thin or mixed, surface at least one concrete risk, missing-data note, or warning instead of rubber-stamping the analysis.",
                "4. Keep relevant_evidence_ids focused on the strongest evidence behind the current top hypothesis.",
                "5. Keep findings short, specific, and non-overlapping.",
                "6. Make approval boundaries explicit: read-only checks are safe, but restarts, failover, rollback, reconfiguration, or traffic changes need approval.",
            ]
        )

    def _planner_system_prompt(self) -> str:
        return "\n".join(
            [
                "You are the IIRS Planner agent.",
                "Output must match the provided JSON schema exactly.",
                "Produce a concise incident brief and triage plan that is useful under pressure.",
                "Operating rules:",
                "1. Keep the brief aligned with the top hypothesis and cited evidence; do not introduce a new diagnosis.",
                "2. Keep brief_title short and incident-style, typically starting with Incident Brief:.",
                "3. Put auto-safe diagnostic steps first, then needs-approval remediation only if the fault remains confirmed.",
                "4. Mark read-only checks as auto-safe. Mark any restart, failover, rollback, reconfiguration, or traffic change as needs-approval.",
                "5. If the evidence points to PostgreSQL or Redis, name that dependency directly in the brief and step descriptions with concrete wording like Inspect PostgreSQL health or Restart or fail over PostgreSQL.",
                "6. If runtime-state evidence names specific exited or unhealthy resources, mention those resources directly in the brief summary or steps.",
                "7. If the top hypothesis is No clear live fault detected, stay in health-check mode and avoid restarts, rollbacks, or failover unless fresh evidence shows a concrete failing resource.",
                "8. Copy each item from critique.missing_data into open_questions verbatim before adding any new question.",
                "9. evidence_snapshot should restate the most decision-useful retrieved evidence, not generic observations.",
                "10. Keep the plan concise and concrete: inspect, correlate, validate recovery, then approval-gated remediation if needed.",
            ]
        )

    def _follow_up_system_prompt(self) -> str:
        return "\n".join(
            [
                "You are the IIRS follow-up assistant.",
                "Answer the user's actual question from the provided incident state.",
                "Operating rules:",
                "1. Be direct, pragmatic, and concise.",
                "2. Use recent conversation messages to resolve references like that, it, why, or then what and keep continuity across the last few turns.",
                "3. Prefer the current incident brief, hypotheses, critique, and cited evidence over generic advice.",
                "4. If the state does not contain the answer, say so plainly instead of guessing.",
                "5. When useful, separate what is known, what is uncertain, and what should happen next.",
                "6. Cite evidence IDs inline when they materially support the answer.",
                "7. If the user asks what to do first, prioritize auto-safe steps before approval-required actions.",
            ]
        )

    def _analyst_prompt(self, state: IIRSState) -> str:
        payload = {
            "alert": to_jsonable(state["alert"]),
            "evidence_bundle": self._serialize_evidence_bundle(state["evidence_bundle"]),
        }
        return self._task_prompt(
            title="Analyze this incident and rank likely root causes.",
            checklist=[
                "Review the alert before judging the evidence.",
                "Prefer runtime-state proof and direct dependency failures over broad service symptoms.",
                "Use only evidence IDs from evidence_bundle.items[*].id.",
                "Use contradicting_evidence_ids only for evidence that genuinely weakens a hypothesis.",
                "If evidence is thin, lower confidence instead of inventing certainty.",
            ],
            payload=payload,
        )

    def _critic_prompt(self, state: IIRSState) -> str:
        payload = {
            "alert": to_jsonable(state["alert"]),
            "evidence_bundle": self._serialize_evidence_bundle(state["evidence_bundle"]),
            "hypotheses": [self._serialize_hypothesis(item) for item in state["hypotheses"]],
        }
        return self._task_prompt(
            title="Critique this incident analysis.",
            checklist=[
                "Check whether the top hypothesis is backed by strong cited evidence.",
                "Call out missing traces, missing change data, or narrow source coverage when they matter.",
                "Surface risky leaps or overconfidence instead of approving by default.",
                "Use only evidence IDs from the payload.",
            ],
            payload=payload,
        )

    def _planner_prompt(self, state: IIRSState) -> str:
        payload = {
            "alert": to_jsonable(state["alert"]),
            "evidence_bundle": self._serialize_evidence_bundle(state["evidence_bundle"]),
            "hypotheses": [self._serialize_hypothesis(item) for item in state["hypotheses"]],
            "critique": self._serialize_critique(state["critique"]),
        }
        return self._task_prompt(
            title="Create the final incident brief and triage plan.",
            checklist=[
                "Carry forward the current diagnosis instead of inventing a new one.",
                "Copy critique.missing_data into open_questions before adding anything else.",
                "Start with auto-safe inspect or correlate steps, then validate recovery.",
                "Add needs-approval steps only for state-changing actions.",
                "Every step must cite the evidence IDs that justify it.",
            ],
            payload=payload,
        )

    def _follow_up_prompt(self, question: str, state: IIRSState) -> str:
        payload = {
            "question": question,
            "alert": to_jsonable(state["alert"]),
            "hypotheses": [self._serialize_hypothesis(item) for item in state["incident_brief"].probable_root_causes],
            "critique": self._serialize_critique(state["critique"]),
            "incident_brief": to_jsonable(state["incident_brief"]),
            "evidence_bundle": self._serialize_evidence_bundle(state["evidence_bundle"]),
            "messages": [to_jsonable(item) for item in state.get("messages", [])[-8:]],
            "trace_runs": [
                {
                    "agent_name": run.agent_name,
                    "input_summary": run.input_summary,
                    "output_summary": run.output_summary,
                }
                for run in state.get("trace_runs", [])
            ],
        }
        return self._task_prompt(
            title="Answer the user's follow-up question from the current incident state.",
            checklist=[
                "Use the question and the latest incident state together.",
                "Prefer cited evidence and the current incident brief over speculation.",
                "If the state is incomplete, say what is missing.",
            ],
            payload=payload,
        )

    def _task_prompt(
        self,
        *,
        title: str,
        checklist: list[str],
        payload: dict[str, Any],
    ) -> str:
        lines = [title, "", "Execution checklist:"]
        lines.extend(f"{index}. {item}" for index, item in enumerate(checklist, start=1))
        lines.extend(["", "Payload JSON:", json.dumps(payload, indent=2)])
        return "\n".join(lines)

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
        max_output_tokens = 1200
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
                    max_output_tokens = max(max_output_tokens * 2, 2400)
                    effort = self._fallback_reasoning_effort(effort)
                    continue
                raise
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                if attempt == 0 and self._should_retry_for_output(data):
                    max_output_tokens = max(max_output_tokens * 2, 2400)
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
        effort = self.reasoning_effort
        output_limit = max_output_tokens
        for attempt in range(2):
            payload: dict[str, Any] = {
                "model": self.model,
                "input": self._build_input(system_prompt, user_prompt),
                "max_output_tokens": output_limit,
                "text": {"verbosity": "low"},
            }
            if self._supports_reasoning():
                payload["reasoning"] = {"effort": effort}
            data = self._post_responses(payload)
            try:
                return self._extract_output_text(data).strip()
            except OpenAIRequestError:
                if attempt == 0 and self._should_retry_for_output(data):
                    output_limit = max(max_output_tokens * 2, 1000)
                    effort = self._fallback_reasoning_effort(effort)
                    continue
                raise
        raise OpenAIRequestError("OpenAI response retry logic exhausted without producing text output.")

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
        except httpx.ReadTimeout as exc:
            raise OpenAIRequestError(
                "OpenAI Responses API read timed out while waiting for model output. "
                f"Current timeout is {self.timeout_seconds:.0f}s; increase IIRS_OPENAI_TIMEOUT_SECONDS if needed."
            ) from exc
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
        return incomplete.get("reason") == "max_output_tokens"

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
        timeout_seconds=settings.openai_timeout_seconds,
        verify_tls=settings.verify_tls,
        client=client,
    )
