from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .backends import TelemetryConfigurationError, TelemetryRequestError
from .config import load_settings
from .evaluation import EvaluationHarness, render_evaluation_json, render_evaluation_markdown
from .llm import OpenAIConfigurationError, OpenAIRequestError, build_reasoning_client
from .live_signatures import (
    LiveSignatureHarness,
    render_live_signature_json,
    render_live_signature_markdown,
)
from .pipeline import IIRSPipeline
from .present import render_brief_json, render_brief_markdown, render_trace_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the IIRS demo pipeline.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a scenario or alert payload through IIRS.")
    run_parser.add_argument(
        "--scenario",
        choices=["postgres_down", "redis_down"],
        help="Built-in incident scenario to execute.",
    )
    run_parser.add_argument(
        "--alert-file",
        type=Path,
        help="Path to an alert payload JSON file.",
    )
    run_parser.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format for the incident brief.",
    )
    run_parser.add_argument(
        "--show-trace",
        action="store_true",
        help="Print a compact trace summary after the incident brief.",
    )

    ask_parser = subparsers.add_parser(
        "ask",
        help="Run a scenario or alert and ask a follow-up question against the resulting incident state.",
    )
    ask_parser.add_argument(
        "--scenario",
        choices=["postgres_down", "redis_down"],
        help="Built-in incident scenario to execute.",
    )
    ask_parser.add_argument(
        "--alert-file",
        type=Path,
        help="Path to an alert payload JSON file.",
    )
    ask_parser.add_argument(
        "--show-trace",
        action="store_true",
        help="Print a compact trace summary after the follow-up answer.",
    )
    ask_parser.add_argument(
        "question",
        help="Follow-up question to ask about the resulting incident state.",
    )

    llm_parser = subparsers.add_parser(
        "llm-check",
        help="Verify that the configured OpenAI-backed reasoning client is reachable.",
    )

    eval_parser = subparsers.add_parser("eval", help="Evaluate built-in scenarios against ground-truth labels.")
    eval_parser.add_argument(
        "--scenario",
        action="append",
        choices=["postgres_down", "redis_down"],
        help="Scenario to evaluate. Repeat to select multiple scenarios. Defaults to all built-ins.",
    )
    eval_parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of repeated runs to execute per scenario.",
    )
    eval_parser.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format for the evaluation report.",
    )

    live_parser = subparsers.add_parser(
        "verify-live",
        help="Validate live telemetry signatures for built-in scenarios against the PLT backend.",
    )
    live_parser.add_argument(
        "--scenario",
        action="append",
        choices=["postgres_down", "redis_down"],
        help="Scenario to validate. Repeat to select multiple scenarios. Defaults to all live signature profiles.",
    )
    live_parser.add_argument(
        "--started-at",
        help="UTC timestamp to center the live validation window on. Defaults to now.",
    )
    live_parser.add_argument(
        "--window-minutes",
        type=int,
        help="Override the alert time window in minutes. Defaults to the scenario fixture value.",
    )
    live_parser.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format for the live signature report.",
    )

    return parser


def _load_state_from_run_args(
    pipeline: IIRSPipeline,
    parser: argparse.ArgumentParser,
    *,
    scenario: str | None,
    alert_file: Path | None,
):
    if bool(scenario) == bool(alert_file):
        parser.error("Specify exactly one of --scenario or --alert-file.")
    if scenario:
        return pipeline.run_scenario(scenario)
    return pipeline.run(pipeline.load_alert(alert_file))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "llm-check":
        try:
            llm = build_reasoning_client(load_settings())
        except OpenAIConfigurationError as exc:
            print(f"LLM check failed: {exc}")
            return 1
        if llm is None:
            print(
                "OpenAI-backed reasoning is not enabled. Set OPENAI_API_KEY and leave "
                "IIRS_USE_OPENAI_AGENTS unset or set it to true."
            )
            return 1
        try:
            message = llm.check_connection()
        except Exception as exc:
            print(f"LLM check failed: {exc}")
            return 1
        print(message)
        return 0

    if args.command == "run":
        pipeline = IIRSPipeline()
        try:
            state = _load_state_from_run_args(
                pipeline,
                parser,
                scenario=args.scenario,
                alert_file=args.alert_file,
            )
        except OpenAIRequestError as exc:
            print(f"Model request failed; stopping instead of falling back: {exc}")
            return 1
        except (TelemetryConfigurationError, TelemetryRequestError) as exc:
            print(f"Telemetry failed; stopping cleanly: {exc}")
            return 1

        brief = state["incident_brief"]
        if args.format == "json":
            print(render_brief_json(brief))
        else:
            print(render_brief_markdown(brief))

        print(f"\nTrace: {state['trace_path']}")
        if args.show_trace:
            print("\nTrace summary:")
            print(render_trace_text(state["trace_runs"]))
        return 0

    if args.command == "ask":
        pipeline = IIRSPipeline()
        try:
            state = _load_state_from_run_args(
                pipeline,
                parser,
                scenario=args.scenario,
                alert_file=args.alert_file,
            )
            print(pipeline.follow_up(args.question, state))
        except OpenAIRequestError as exc:
            print(f"Model request failed; stopping instead of falling back: {exc}")
            return 1
        except (TelemetryConfigurationError, TelemetryRequestError) as exc:
            print(f"Telemetry failed; stopping cleanly: {exc}")
            return 1
        print(f"\nTrace: {state['trace_path']}")
        if args.show_trace:
            print("\nTrace summary:")
            print(render_trace_text(state["trace_runs"]))
        return 0

    if args.command == "eval":
        pipeline = IIRSPipeline()
        if args.runs < 1:
            parser.error("--runs must be at least 1.")

        harness = EvaluationHarness.from_directory(pipeline, pipeline.settings.ground_truth_dir)
        scenario_names = args.scenario or sorted(pipeline.scenarios)
        try:
            report = harness.evaluate_scenarios(scenario_names, runs_per_scenario=args.runs)
        except OpenAIRequestError as exc:
            print(f"Model request failed during evaluation; stopping instead of falling back: {exc}")
            return 1
        except (TelemetryConfigurationError, TelemetryRequestError) as exc:
            print(f"Telemetry failed during evaluation; stopping cleanly: {exc}")
            return 1

        if args.format == "json":
            print(render_evaluation_json(report))
        else:
            print(render_evaluation_markdown(report))

        return 0 if report.passed else 1

    if args.command == "verify-live":
        pipeline = IIRSPipeline()
        if args.window_minutes is not None and args.window_minutes < 1:
            parser.error("--window-minutes must be at least 1.")

        harness = LiveSignatureHarness.from_directory(pipeline, pipeline.settings.live_signature_dir)
        scenario_names = args.scenario or sorted(harness.profiles)
        report = harness.validate_scenarios(
            scenario_names,
            started_at=args.started_at,
            window_minutes=args.window_minutes,
        )

        if args.format == "json":
            print(render_live_signature_json(report))
        else:
            print(render_live_signature_markdown(report))

        return 0 if report.passed else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
