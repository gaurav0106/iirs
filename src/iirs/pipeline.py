from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .agents import (
    AgentContext,
    answer_follow_up,
    make_analyst_node,
    make_critic_node,
    make_planner_node,
    make_retriever_node,
)
from .backends import RunbookStore, TelemetryBackend, build_telemetry_backend
from .config import Settings, load_settings
from .llm import ReasoningClient, build_reasoning_client
from .models import AlertPayload, ConversationTurn, IIRSState
from .utils import read_json, unique_suffix, utc_now, write_json


class LinearGraphRunner:
    def __init__(self, nodes: list[Callable[[IIRSState], dict[str, object]]]) -> None:
        self.nodes = nodes

    def invoke(self, state: IIRSState) -> IIRSState:
        current = dict(state)
        for node in self.nodes:
            current.update(node(current))
        return current


class IIRSPipeline:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        reasoning_client: ReasoningClient | None = None,
        telemetry_backend: TelemetryBackend | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.context = AgentContext(
            telemetry=telemetry_backend if telemetry_backend is not None else build_telemetry_backend(self.settings),
            runbooks=RunbookStore(self.settings.runbooks_dir),
            llm=reasoning_client if reasoning_client is not None else build_reasoning_client(self.settings),
        )
        self.named_nodes = self._build_nodes()
        self.runner, self.used_langgraph = self._build_runner()

    def _build_nodes(self) -> list[tuple[str, Callable[[IIRSState], dict[str, object]]]]:
        return [
            ("Retriever", make_retriever_node(self.context)),
            ("Analyst", make_analyst_node(self.context)),
            ("Critic", make_critic_node(self.context)),
            ("Planner", make_planner_node(self.context)),
        ]

    def _build_runner(self) -> tuple[object, bool]:
        nodes = [
            make_retriever_node(self.context),
            make_analyst_node(self.context),
            make_critic_node(self.context),
            make_planner_node(self.context),
        ]
        if not self.settings.prefer_langgraph:
            return LinearGraphRunner(nodes), False
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError:
            return LinearGraphRunner(nodes), False

        graph = StateGraph(IIRSState)
        graph.add_node("retriever", nodes[0])
        graph.add_node("analyst", nodes[1])
        graph.add_node("critic", nodes[2])
        graph.add_node("planner", nodes[3])
        graph.add_edge(START, "retriever")
        graph.add_edge("retriever", "analyst")
        graph.add_edge("analyst", "critic")
        graph.add_edge("critic", "planner")
        graph.add_edge("planner", END)
        return graph.compile(), True

    def build_live_alert(
        self,
        summary: str,
        *,
        service: str | None = None,
        environment: str = "local-dev",
        window_minutes: int = 10,
        mode: str = "live-diagnosis",
        source: str = "chat-live",
    ) -> AlertPayload:
        timestamp = utc_now()
        incident_suffix = timestamp.replace(":", "").replace("-", "").replace("+00:00", "Z")
        return AlertPayload(
            incident_id=f"live-chat-{incident_suffix}-{unique_suffix()}",
            summary=summary,
            severity="unknown",
            service=service or "aspire-shop",
            environment=environment,
            started_at=timestamp,
            window_minutes=window_minutes,
            scenario=None,
            labels={"source": source, "mode": mode},
        )

    def load_alert(self, path: Path) -> AlertPayload:
        return AlertPayload.from_mapping(read_json(path))

    def parse_alert_json(self, payload: str) -> AlertPayload:
        return AlertPayload.from_mapping(json.loads(payload))

    def build_initial_state(self, alert: AlertPayload) -> IIRSState:
        return {
            "alert": alert,
            "messages": [
                ConversationTurn(
                    role="user",
                    content=f"Investigate incident: {alert.summary}",
                    created_at=utc_now(),
                )
            ],
            "trace_runs": [],
        }

    def finalize_state(self, state: IIRSState) -> IIRSState:
        current = dict(state)
        trace_path = self._write_trace(current)
        current["trace_path"] = str(trace_path)
        return current

    def run(self, alert: AlertPayload) -> IIRSState:
        initial_state = self.build_initial_state(alert)
        state = self.runner.invoke(initial_state)
        return self.finalize_state(state)

    def follow_up(self, question: str, state: IIRSState) -> str:
        return answer_follow_up(question, state, self.context.llm)

    def _write_trace(self, state: IIRSState) -> Path:
        incident_id = state["alert"].incident_id
        safe_name = incident_id.replace("/", "-")
        trace_path = self.settings.trace_dir / f"{safe_name}.json"
        payload = {
            "incident_id": incident_id,
            "used_langgraph": self.used_langgraph,
            "alert": state["alert"],
            "hypotheses": state.get("hypotheses", []),
            "critique": state.get("critique"),
            "triage_plan": state.get("triage_plan", []),
            "incident_brief": state.get("incident_brief"),
            "messages": state.get("messages", []),
            "agents": state.get("trace_runs", []),
        }
        write_json(trace_path, payload)
        return trace_path
