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

from iirs.pipeline import IIRSPipeline
from iirs.present import render_brief_markdown


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
    lowered = text.lower()
    postgres_terms = ("postgres", "postgresql", "catalogservice", "catalog")
    redis_terms = ("redis", "basketservice", "basket", "cart", "cache")
    postgres_score = sum(term in lowered for term in postgres_terms)
    redis_score = sum(term in lowered for term in redis_terms)

    if postgres_score == 0 and redis_score == 0:
        return None
    if postgres_score >= redis_score:
        return "postgres_down"
    return "redis_down"


def _build_freeform_alert(message: str, pipeline: IIRSPipeline):
    scenario_name = _infer_scenario_from_text(message)
    if scenario_name is None:
        return None

    alert = pipeline.build_alert_for_scenario(scenario_name)
    cleaned = " ".join(message.strip().split())
    if cleaned:
        alert.summary = cleaned
        alert.labels = {**alert.labels, "source": "chat-freeform"}
    return alert


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


@cl.on_chat_start
async def on_chat_start() -> None:
    pipeline = IIRSPipeline()
    cl.user_session.set("pipeline", pipeline)
    cl.user_session.set("last_state", None)
    await cl.Message(
        content=(
            "Send `postgres_down`, `redis_down`, or paste an alert JSON payload to run the IIRS pipeline. "
            "The UI will show Retriever, Analyst, Critic, and Planner handoffs in sequence. "
            "After that, ask follow-up questions about root cause, evidence, or next actions."
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    pipeline = cl.user_session.get("pipeline") or IIRSPipeline()
    last_state = cl.user_session.get("last_state")

    alert = _parse_user_alert(message.content, pipeline)
    if alert is None and last_state is not None:
        answer = pipeline.follow_up(message.content, last_state)
        await _stream_markdown(f"### Follow-up\n{answer}", delay=0.01)
        return

    if alert is None:
        await _stream_markdown(
            "### I Need An Incident To Investigate\n"
            "Describe the outage in plain English or paste a valid alert JSON payload.\n\n"
            "Examples:\n"
            "- `catalogservice is timing out and PostgreSQL looks down`\n"
            "- `basketservice cannot reach Redis and cart calls are failing`\n"
            "- `postgres_down` or `redis_down` for the demo shortcuts",
            delay=0.008,
        )
        return

    await cl.Message(
        content=f"Starting incident run for `{alert.service}` in `{alert.environment}`."
    ).send()

    state = pipeline.build_initial_state(alert)
    for agent_name, node in pipeline.named_nodes:
        thinking = cl.Message(content=f"### {agent_name}\nWorking...")
        await thinking.send()
        await asyncio.sleep(0.15)
        result = await asyncio.to_thread(node, state)
        state.update(result)
        run = state["trace_runs"][-1]

        summary = [
            f"### {agent_name}",
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
