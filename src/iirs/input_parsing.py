from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import AlertPayload
    from .pipeline import IIRSPipeline


def extract_json_payload(message: str) -> str | None:
    text = message.strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def infer_service_from_text(text: str) -> str | None:
    lowered = f" {text.lower()} "
    service_terms = {
        "catalogservice": (" catalogservice ", " catalog service ", " catalog ", " inventory ", " product page "),
        "basketservice": (" basketservice ", " basket service ", " basket ", " cart ", " checkout ", " checkout cart "),
        "frontend": (
            " frontend ",
            " front end ",
            " storefront ",
            " shop ui ",
            " website ",
            " home page ",
            " landing page ",
            " site ",
            " not loading ",
            " blank page ",
            " white screen ",
        ),
    }
    scores = {
        service: sum(term in lowered for term in terms)
        for service, terms in service_terms.items()
    }
    best_service = max(scores, key=scores.get)
    return best_service if scores[best_service] > 0 else None


def looks_like_health_check_request(text: str) -> bool:
    lowered = " ".join(text.lower().split())
    phrases = (
        "healthy or broken",
        "healthy or having issues",
        "healthy or has issues",
        "healthy or not",
        "is everything healthy",
        "is everything broken",
        "is everything okay",
        "is everything up",
        "can you check the health",
        "check the health of",
        "check health of",
        "health of aspire shop",
        "health of aspireshop",
        "everything healthy",
        "everything broken",
        "overall health",
        "health check",
        "all healthy",
        "all green",
        "system healthy",
        "are things healthy",
    )
    return any(phrase in lowered for phrase in phrases)


def looks_like_contextual_follow_up(text: str) -> bool:
    normalized = " ".join(text.strip().lower().split())
    follow_ups = {
        "why",
        "why?",
        "how so",
        "how so?",
        "why is that",
        "why is that?",
        "are you sure",
        "are you sure?",
        "show me more",
        "show me more.",
        "more",
        "more?",
        "proof?",
        "other one",
        "other one?",
        "the other one",
        "the other one?",
        "and then",
        "and then?",
        "then what",
        "then what?",
        "what next",
        "what next?",
        "next",
        "next?",
        "healthy?",
        "broken?",
        "is it healthy?",
        "is it broken?",
        "is it down?",
    }
    return normalized in follow_ups


def looks_like_explicit_follow_up(text: str) -> bool:
    lowered = " ".join(text.strip().lower().split())
    signals = (
        "root cause",
        "what caused",
        "what happened",
        "summary",
        "recap",
        "how sure",
        "confidence",
        "compare",
        "other hypothesis",
        "runner up",
        "evidence",
        "citation",
        "proof",
        "log",
        "runtime state",
        "resource state",
        "dashboard",
        "container",
        "metric",
        "trace",
        "runbook",
        "playbook",
        "deploy",
        "change",
        "regression",
        "configuration",
        "what are we missing",
        "open question",
        "risk",
        "approval",
        "restart",
        "rollback",
        "what do i do",
        "what should i do",
        "what should i check",
        "plan",
        "when did",
        "start time",
        "which service",
        "affected service",
        "who is affected",
        "who owns",
        "owner",
        "team",
    )
    return any(signal in lowered for signal in signals)


def build_freeform_alert(message: str, pipeline: "IIRSPipeline") -> "AlertPayload | None":
    cleaned = " ".join(message.strip().split())
    if not cleaned:
        return None
    inferred_service = infer_service_from_text(cleaned)
    return pipeline.build_live_alert(
        cleaned,
        service=inferred_service,
        mode="live-health-check" if looks_like_health_check_request(cleaned) else "live-diagnosis",
    )


def parse_user_alert(message: str, pipeline: "IIRSPipeline") -> "AlertPayload | None":
    text = message.strip()
    if json_payload := extract_json_payload(text):
        return pipeline.parse_alert_json(json_payload)
    return build_freeform_alert(text, pipeline)


def classify_user_message(
    message: str,
    pipeline: "IIRSPipeline",
    *,
    has_last_state: bool,
) -> tuple[str, "AlertPayload | None"]:
    if has_last_state and (
        looks_like_contextual_follow_up(message)
        or looks_like_explicit_follow_up(message)
    ):
        return "follow-up", None

    alert = parse_user_alert(message, pipeline)
    if alert is not None:
        return "incident", alert

    return "unknown", None
