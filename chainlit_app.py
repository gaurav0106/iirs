from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import chainlit as cl

from iirs.pipeline import IIRSPipeline
from iirs.present import render_brief_markdown


def _parse_user_alert(message: str, pipeline: IIRSPipeline):
    text = message.strip()
    lowered = text.lower()
    if text.startswith("{"):
        return pipeline.parse_alert_json(text)
    if lowered == "postgres_down" or "postgres" in lowered:
        return pipeline.build_alert_for_scenario("postgres_down")
    if lowered == "redis_down" or "redis" in lowered:
        return pipeline.build_alert_for_scenario("redis_down")
    return None


@cl.on_chat_start
async def on_chat_start() -> None:
    pipeline = IIRSPipeline()
    cl.user_session.set("pipeline", pipeline)
    cl.user_session.set("last_state", None)
    await cl.Message(
        content=(
            "Send `postgres_down`, `redis_down`, or paste an alert JSON payload to run the IIRS pipeline. "
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
        await cl.Message(content=answer).send()
        return

    if alert is None:
        await cl.Message(
            content="I need `postgres_down`, `redis_down`, or a JSON alert payload to start an incident run."
        ).send()
        return

    state = await asyncio.to_thread(pipeline.run, alert)
    cl.user_session.set("last_state", state)

    for run in state["trace_runs"]:
        async with cl.Step(name=run.agent_name, type="run") as step:
            step.input = run.input_summary
            step.output = run.output_summary

        for tool_call in run.tool_calls:
            async with cl.Step(name=tool_call.tool_name, type="tool") as step:
                step.input = tool_call.query
                step.output = ", ".join(tool_call.evidence_ids) or "no evidence"

    await cl.Message(content=render_brief_markdown(state["incident_brief"])).send()
    await cl.Message(content=f"Trace written to `{state['trace_path']}`").send()
