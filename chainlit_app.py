from __future__ import annotations

import asyncio
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import chainlit as cl

from iirs.llm import OpenAIRequestError
from iirs.models import ConversationTurn
from iirs.pipeline import IIRSPipeline
from iirs.present import render_brief_markdown
from iirs.utils import utc_now


async def _stream_markdown(content: str, *, delay: float = 0.02) -> None:
    message = cl.Message(content="")
    await message.send()
    for chunk in re.split(r"(\s+)", content):
        if not chunk:
            continue
        await message.stream_token(chunk)
        await asyncio.sleep(delay)
    await message.update()


def _extract_json_payload(message: str) -> str | None:
    text = message.strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _infer_scenario_from_text(text: str) -> str | None:
    lowered = f" {text.lower()} "
    postgres_terms = {
        "postgres": 5,
        "postgresql": 5,
        "database": 4,
        " db ": 3,
        "npgsql": 5,
        "entityframework": 3,
        "entity framework": 3,
        "sql": 2,
    }
    redis_terms = {
        "redis": 5,
        "cache": 4,
        "checkout cart": 2,
        "session": 2,
        "stackexchange.redis": 5,
        "keyspace": 2,
    }

    postgres_score = sum(weight for term, weight in postgres_terms.items() if term in lowered)
    redis_score = sum(weight for term, weight in redis_terms.items() if term in lowered)

    if postgres_score == 0 and redis_score == 0:
        return None
    if postgres_score and redis_score and abs(postgres_score - redis_score) <= 2:
        return None
    if postgres_score >= redis_score:
        return "postgres_down"
    return "redis_down"


def _infer_service_from_text(text: str) -> str | None:
    lowered = f" {text.lower()} "
    service_terms = {
        "catalogservice": (" catalogservice ", " catalog service ", " catalog ", " inventory ", " product page "),
        "basketservice": (" basketservice ", " basket service ", " basket ", " cart ", " checkout cart "),
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


def _looks_like_live_diagnosis_request(text: str) -> bool:
    lowered = text.lower()
    signals = (
        "what's wrong",
        "whats wrong",
        "what broke",
        "is it down",
        "down?",
        "broken",
        "failing",
        "failure",
        "health",
        "healthy",
        "unhealthy",
        "working",
        "status",
        "diagnose",
        "investigate",
        "timeout",
        "error",
        "issue",
        "problem",
        "outage",
        "not loading",
        "won't load",
        "wont load",
        "not opening",
        "won't open",
        "wont open",
        "blank page",
        "white screen",
    )
    return any(signal in lowered for signal in signals)


def _looks_like_health_check_request(text: str) -> bool:
    lowered = " ".join(text.lower().split())
    phrases = (
        "healthy or broken",
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


def _looks_like_contextual_follow_up(text: str) -> bool:
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


def _build_freeform_alert(message: str, pipeline: IIRSPipeline):
    scenario_name = _infer_scenario_from_text(message)
    cleaned = " ".join(message.strip().split())
    inferred_service = _infer_service_from_text(message)

    if scenario_name is not None:
        alert = pipeline.build_alert_for_scenario(scenario_name)
        if cleaned:
            alert.summary = cleaned
            alert.started_at = utc_now()
            alert.labels = {**alert.labels, "source": "chat-freeform", "mode": "scenario-triage"}
        return alert

    if not cleaned:
        return None
    if _looks_like_health_check_request(cleaned):
        return pipeline.build_live_alert(
            cleaned,
            service=inferred_service,
            mode="live-health-check",
        )
    if inferred_service is not None or _looks_like_live_diagnosis_request(cleaned):
        return pipeline.build_live_alert(cleaned, service=inferred_service)
    return None


def _parse_user_alert(message: str, pipeline: IIRSPipeline):
    text = message.strip()
    lowered = text.lower()
    if json_payload := _extract_json_payload(text):
        return pipeline.parse_alert_json(json_payload)
    if lowered == "postgres_down":
        return pipeline.build_alert_for_scenario("postgres_down")
    if lowered == "redis_down":
        return pipeline.build_alert_for_scenario("redis_down")
    return _build_freeform_alert(text, pipeline)


def _remember_follow_up(state, question: str, answer: str):
    updated_state = dict(state)
    updated_state["messages"] = [
        *updated_state.get("messages", []),
        ConversationTurn(role="user", content=question, created_at=utc_now()),
        ConversationTurn(role="assistant", content=answer, created_at=utc_now()),
    ]
    return updated_state


@cl.on_chat_start
async def on_chat_start() -> None:
    pipeline = IIRSPipeline()
    cl.user_session.set("pipeline", pipeline)
    cl.user_session.set("last_state", None)
    await cl.Message(
        content=(
            "Send `postgres_down`, `redis_down`, a plain-English incident description, or an alert JSON payload. "
            "You can also ask broad live questions like `is everything healthy or broken right now?`. "
            "The UI will show Retriever, Analyst, Critic, and Planner handoffs in sequence. "
            "After that, ask normal follow-up questions like `how sure are we?`, "
            "`why?`, `show me the evidence`, or `then what?`."
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    pipeline = cl.user_session.get("pipeline") or IIRSPipeline()
    last_state = cl.user_session.get("last_state")

    if last_state is not None and _looks_like_contextual_follow_up(message.content):
        try:
            answer = pipeline.follow_up(message.content, last_state)
        except OpenAIRequestError as exc:
            await _stream_markdown(
                "### Follow-up Stopped\n"
                "The model request failed, so I stopped instead of falling back to a weaker answer.\n\n"
                f"Error: `{exc}`",
                delay=0.008,
            )
            return
        cl.user_session.set("last_state", _remember_follow_up(last_state, message.content, answer))
        await _stream_markdown(f"### Follow-up\n{answer}", delay=0.01)
        return

    alert = _parse_user_alert(message.content, pipeline)
    if alert is None and last_state is not None:
        try:
            answer = pipeline.follow_up(message.content, last_state)
        except OpenAIRequestError as exc:
            await _stream_markdown(
                "### Follow-up Stopped\n"
                "The model request failed, so I stopped instead of falling back to a weaker answer.\n\n"
                f"Error: `{exc}`",
                delay=0.008,
            )
            return
        cl.user_session.set("last_state", _remember_follow_up(last_state, message.content, answer))
        await _stream_markdown(f"### Follow-up\n{answer}", delay=0.01)
        return

    if alert is None:
        await _stream_markdown(
            "### I Need An Incident To Investigate\n"
            "Describe the outage in plain English or paste a valid alert JSON payload.\n\n"
            "Examples:\n"
            "- `catalogservice is timing out and PostgreSQL looks down`\n"
            "- `basketservice cannot reach Redis and cart calls are failing`\n"
            "- `the catalog page spins forever and DB connections keep failing`\n"
            "- `the aspire shop page is not loading at all`\n"
            "- `cart is broken, cache lookups are timing out`\n"
            "- `postgres_down` or `redis_down` for the demo shortcuts",
            delay=0.008,
        )
        return

    run_label = "live health check" if alert.labels.get("mode") == "live-health-check" else "incident run"
    await cl.Message(content=f"Starting {run_label} for `{alert.service}` in `{alert.environment}`.").send()

    state = pipeline.build_initial_state(alert)
    for agent_name, node in pipeline.named_nodes:
        thinking = cl.Message(content=f"### {agent_name}\nWorking...")
        await thinking.send()
        await asyncio.sleep(0.15)
        try:
            result = await asyncio.to_thread(node, state)
        except OpenAIRequestError as exc:
            thinking.content = (
                f"### {agent_name} [model-error]\n"
                "The model request failed, so this run stopped instead of falling back to a lower-confidence answer.\n\n"
                f"Error: `{exc}`"
            )
            await thinking.update()
            return
        state.update(result)
        run = state["trace_runs"][-1]

        summary = [
            f"### {agent_name} [{run.execution_mode}]",
            run.output_summary,
        ]
        if run.tool_calls:
            summary.append("")
            summary.append("Tool calls:")
            for tool_call in run.tool_calls:
                summary.append(
                    f"- `{tool_call.tool_name}` -> {', '.join(tool_call.evidence_ids) or 'no evidence'}"
                )

        thinking.content = "\n".join(summary)
        await thinking.update()
        await asyncio.sleep(0.35)

    state = await asyncio.to_thread(pipeline.finalize_state, state)
    cl.user_session.set("last_state", state)

    await _stream_markdown(render_brief_markdown(state["incident_brief"]), delay=0.008)
    await cl.Message(content=f"Trace written to `{state['trace_path']}`").send()
