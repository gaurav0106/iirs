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

from iirs.input_parsing import (
    classify_user_message as _classify_user_message,
    looks_like_contextual_follow_up as _looks_like_contextual_follow_up,
    parse_user_alert as _parse_user_alert,
)
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
            "Send a plain-English incident description or an alert JSON payload. "
            "The app treats ordinary text as a new incident prompt and broad health prompts as live health checks. "
            "The UI will show Retriever, Analyst, Critic, and Planner handoffs in sequence. "
            "After that, ask normal follow-up questions like `how sure are we?`, "
            "`why?`, `show me the evidence`, or `then what?`."
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    pipeline = cl.user_session.get("pipeline") or IIRSPipeline()
    last_state = cl.user_session.get("last_state")

    message_kind, alert = _classify_user_message(
        message.content,
        pipeline,
        has_last_state=last_state is not None,
    )
    if message_kind == "follow-up" and last_state is not None:
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
            "- `cart is broken, cache lookups are timing out`",
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
