from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .backends import TelemetryConfigurationError, TelemetryRequestError
from .config import load_settings
from .llm import OpenAIConfigurationError, OpenAIRequestError, build_reasoning_client
from .live_signatures import (
    LiveSignatureHarness,
    render_live_signature_json,
    render_live_signature_markdown,
)
from .models import AlertPayload
from .pipeline import IIRSPipeline
from .present import render_brief_json, render_brief_markdown, render_trace_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the IIRS demo pipeline.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run an alert payload or live summary through IIRS.")
    run_parser.add_argument(
        "--alert-file",
        type=Path,
        help="Path to an alert payload JSON file.",
    )
    run_parser.add_argument(
        "--summary",
        help="Plain-English incident summary for a live alert.",
    )
    run_parser.add_argument(
        "--service",
        help="Service to target when using --summary. Defaults to aspire-shop.",
    )
    run_parser.add_argument(
        "--environment",
        help="Environment label when using --summary. Defaults to local-dev.",
    )
    run_parser.add_argument(
        "--window-minutes",
        type=int,
        help="Alert window in minutes when using --summary. Defaults to 10.",
    )
    run_parser.add_argument(
        "--mode",
        choices=["live-diagnosis", "live-health-check"],
        help="Alert mode when using --summary. Defaults to live-diagnosis.",
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
        help="Run an alert payload or live summary and ask a follow-up question against the resulting incident state.",
    )
    ask_parser.add_argument(
        "--alert-file",
        type=Path,
        help="Path to an alert payload JSON file.",
    )
    ask_parser.add_argument(
        "--summary",
        help="Plain-English incident summary for a live alert.",
    )
    ask_parser.add_argument(
        "--service",
        help="Service to target when using --summary. Defaults to aspire-shop.",
    )
    ask_parser.add_argument(
        "--environment",
        help="Environment label when using --summary. Defaults to local-dev.",
    )
    ask_parser.add_argument(
        "--window-minutes",
        type=int,
        help="Alert window in minutes when using --summary. Defaults to 10.",
    )
    ask_parser.add_argument(
        "--mode",
        choices=["live-diagnosis", "live-health-check"],
        help="Alert mode when using --summary. Defaults to live-diagnosis.",
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

    subparsers.add_parser(
        "llm-check",
        help="Verify that the configured OpenAI-backed reasoning client is reachable.",
    )

    live_parser = subparsers.add_parser(
        "verify-live",
        help="Validate live telemetry signatures for built-in fault profiles against the PLT backend.",
    )
    live_parser.add_argument(
        "--profile",
        action="append",
        help="Live signature profile to validate. Repeat to select multiple profiles. Defaults to all profiles.",
    )
    live_parser.add_argument(
        "--started-at",
        help="UTC timestamp to center the live validation window on. Defaults to now.",
    )
    live_parser.add_argument(
        "--window-minutes",
        type=int,
        help="Override the alert time window in minutes. Defaults to the profile value.",
    )
    live_parser.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format for the live signature report.",
    )

    return parser


def _build_alert_from_run_args(
    pipeline: IIRSPipeline,
    parser: argparse.ArgumentParser,
    *,
    alert_file: Path | None,
    summary: str | None,
    service: str | None,
    environment: str | None,
    window_minutes: int | None,
    mode: str | None,
) -> AlertPayload:
    if bool(summary) == bool(alert_file):
        parser.error("Specify exactly one of --summary or --alert-file.")
    if alert_file:
        if any(value is not None for value in (service, environment, window_minutes, mode)):
            parser.error("--service, --environment, --window-minutes, and --mode require --summary.")
        return pipeline.load_alert(alert_file)
    if window_minutes is not None and window_minutes < 1:
        parser.error("--window-minutes must be at least 1.")
    return pipeline.build_live_alert(
        summary or "",
        service=service,
        environment=environment or "local-dev",
        window_minutes=window_minutes or 10,
        mode=mode or "live-diagnosis",
        source="cli-live",
    )


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
            alert = _build_alert_from_run_args(
                pipeline,
                parser,
                alert_file=args.alert_file,
                summary=args.summary,
                service=args.service,
                environment=args.environment,
                window_minutes=args.window_minutes,
                mode=args.mode,
            )
            state = pipeline.run(alert)
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
            alert = _build_alert_from_run_args(
                pipeline,
                parser,
                alert_file=args.alert_file,
                summary=args.summary,
                service=args.service,
                environment=args.environment,
                window_minutes=args.window_minutes,
                mode=args.mode,
            )
            state = pipeline.run(alert)
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

    if args.command == "verify-live":
        pipeline = IIRSPipeline()
        if args.window_minutes is not None and args.window_minutes < 1:
            parser.error("--window-minutes must be at least 1.")

        harness = LiveSignatureHarness.from_directory(pipeline, pipeline.settings.live_signature_dir)
        profile_names = args.profile or sorted(harness.profiles)
        report = harness.validate_profiles(
            profile_names,
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
