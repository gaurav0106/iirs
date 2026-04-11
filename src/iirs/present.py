from __future__ import annotations

import json

from .models import AgentRun, IncidentBrief
from .utils import to_jsonable


def render_brief_markdown(brief: IncidentBrief) -> str:
    lines = [
        f"# {brief.title}",
        "",
        brief.summary,
        "",
        "## Ranked root causes",
    ]
    for hypothesis in brief.probable_root_causes:
        lines.append(
            f"{hypothesis.rank}. {hypothesis.title} (confidence {hypothesis.confidence:.2f})"
        )
    lines.extend(["", "## Recommended actions"])
    for step in brief.recommended_actions:
        lines.append(f"{step.order}. [{step.action_type}] {step.description}")
    lines.extend(["", "## Open questions"])
    for question in brief.open_questions:
        lines.append(f"- {question}")
    return "\n".join(lines)


def render_trace_text(runs: list[AgentRun]) -> str:
    lines = []
    for run in runs:
        lines.append(f"{run.agent_name} [{run.execution_mode}]: {run.output_summary}")
        for tool_call in run.tool_calls:
            lines.append(
                f"  - {tool_call.tool_name} -> {', '.join(tool_call.evidence_ids) or 'no evidence'}"
            )
    return "\n".join(lines)


def render_brief_json(brief: IncidentBrief) -> str:
    return json.dumps(to_jsonable(brief), indent=2)
