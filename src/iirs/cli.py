from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .evaluation import EvaluationHarness, render_evaluation_json, render_evaluation_markdown
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    pipeline = IIRSPipeline()

    if args.command == "run":
        if bool(args.scenario) == bool(args.alert_file):
            parser.error("Specify exactly one of --scenario or --alert-file.")
        if args.scenario:
            state = pipeline.run_scenario(args.scenario)
        else:
            state = pipeline.run(pipeline.load_alert(args.alert_file))

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

    if args.command == "eval":
        if args.runs < 1:
            parser.error("--runs must be at least 1.")

        harness = EvaluationHarness.from_directory(pipeline, pipeline.settings.ground_truth_dir)
        scenario_names = args.scenario or sorted(pipeline.scenarios)
        report = harness.evaluate_scenarios(scenario_names, runs_per_scenario=args.runs)

        if args.format == "json":
            print(render_evaluation_json(report))
        else:
            print(render_evaluation_markdown(report))

        return 0 if report.passed else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
