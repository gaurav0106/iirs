from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import TYPE_CHECKING, Callable

from .backends import TelemetryConfigurationError
from .evaluation import EvidenceExpectation
from .models import ToolResult
from .utils import read_json, to_jsonable, utc_now

if TYPE_CHECKING:
    from pathlib import Path

    from .pipeline import IIRSPipeline
    from .scenarios import ScenarioDefinition
    from .models import AlertPayload


@dataclass(slots=True)
class LiveSignatureProfile:
    scenario_name: str
    checks: list[EvidenceExpectation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: dict[str, object]) -> "LiveSignatureProfile":
        checks = [EvidenceExpectation.from_mapping(item) for item in payload.get("checks", [])]
        for check in checks:
            if not check.tool_name:
                raise KeyError(
                    f"Live signature check {check.description!r} is missing required field 'tool_name'."
                )
        return cls(
            scenario_name=str(payload["scenario_name"]),
            checks=checks,
            notes=[str(item) for item in payload.get("notes", [])],
        )


@dataclass(slots=True)
class LiveSignatureCheckResult:
    description: str
    tool_name: str
    observed_ids: list[str] = field(default_factory=list)
    matched_ids: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def satisfied(self) -> bool:
        return self.error is None and bool(self.matched_ids)


@dataclass(slots=True)
class LiveSignatureScenarioReport:
    scenario_name: str
    incident_id: str
    started_at: str
    window_minutes: int
    check_results: list[LiveSignatureCheckResult] = field(default_factory=list)

    @property
    def total_checks(self) -> int:
        return len(self.check_results)

    @property
    def passed_checks(self) -> int:
        return sum(result.satisfied for result in self.check_results)

    @property
    def passed(self) -> bool:
        return self.total_checks > 0 and self.passed_checks == self.total_checks


@dataclass(slots=True)
class LiveSignatureReport:
    generated_at: str
    telemetry_backend: str
    scenario_reports: list[LiveSignatureScenarioReport] = field(default_factory=list)

    @property
    def total_scenarios(self) -> int:
        return len(self.scenario_reports)

    @property
    def passed_scenarios(self) -> int:
        return sum(report.passed for report in self.scenario_reports)

    @property
    def total_checks(self) -> int:
        return sum(report.total_checks for report in self.scenario_reports)

    @property
    def passed_checks(self) -> int:
        return sum(report.passed_checks for report in self.scenario_reports)

    @property
    def passed(self) -> bool:
        return self.total_scenarios > 0 and self.passed_scenarios == self.total_scenarios


def load_live_signature_profiles(directory: "Path") -> dict[str, LiveSignatureProfile]:
    profiles: dict[str, LiveSignatureProfile] = {}
    for path in sorted(directory.glob("*.json")):
        profile = LiveSignatureProfile.from_mapping(read_json(path))
        profiles[profile.scenario_name] = profile
    if not profiles:
        raise FileNotFoundError(f"No live signature profiles found in {directory}")
    return profiles


class LiveSignatureHarness:
    def __init__(self, pipeline: "IIRSPipeline", profiles: dict[str, LiveSignatureProfile]) -> None:
        self.pipeline = pipeline
        self.profiles = profiles

    @classmethod
    def from_directory(cls, pipeline: "IIRSPipeline", directory: "Path") -> "LiveSignatureHarness":
        return cls(pipeline, load_live_signature_profiles(directory))

    def validate_scenarios(
        self,
        scenario_names: list[str],
        *,
        started_at: str | None = None,
        window_minutes: int | None = None,
    ) -> LiveSignatureReport:
        if self.pipeline.settings.telemetry_backend != "plt":
            raise TelemetryConfigurationError(
                "Live signature validation requires IIRS_TELEMETRY_BACKEND=plt."
            )

        effective_started_at = started_at or utc_now()
        scenario_reports: list[LiveSignatureScenarioReport] = []
        for scenario_name in scenario_names:
            if scenario_name not in self.profiles:
                raise KeyError(f"No live signature profile found for scenario {scenario_name!r}")

            profile = self.profiles[scenario_name]
            scenario = self.pipeline.scenarios[scenario_name]
            alert = self.pipeline.build_alert_for_scenario(scenario_name)
            alert.incident_id = f"{scenario_name}-live-signature"
            alert.started_at = effective_started_at
            if window_minutes is not None:
                alert.window_minutes = window_minutes

            tool_results = self._collect_tool_results(profile, alert, scenario)
            check_results: list[LiveSignatureCheckResult] = []
            for check in profile.checks:
                outcome = tool_results[check.tool_name or ""]
                if isinstance(outcome, Exception):
                    check_results.append(
                        LiveSignatureCheckResult(
                            description=check.description,
                            tool_name=check.tool_name or "unknown",
                            error=str(outcome),
                        )
                    )
                    continue

                observed_ids = [item.id for item in outcome.items]
                matched_ids = [item.id for item in outcome.items if check.matches(item)]
                check_results.append(
                    LiveSignatureCheckResult(
                        description=check.description,
                        tool_name=check.tool_name or "unknown",
                        observed_ids=observed_ids,
                        matched_ids=matched_ids,
                    )
                )

            scenario_reports.append(
                LiveSignatureScenarioReport(
                    scenario_name=scenario_name,
                    incident_id=alert.incident_id,
                    started_at=alert.started_at,
                    window_minutes=alert.window_minutes,
                    check_results=check_results,
                )
            )

        return LiveSignatureReport(
            generated_at=utc_now(),
            telemetry_backend=self.pipeline.settings.telemetry_backend,
            scenario_reports=scenario_reports,
        )

    def _collect_tool_results(
        self,
        profile: LiveSignatureProfile,
        alert: "AlertPayload",
        scenario: "ScenarioDefinition",
    ) -> dict[str, ToolResult | Exception]:
        fetchers = self._build_fetchers(alert, scenario)
        results: dict[str, ToolResult | Exception] = {}
        for check in profile.checks:
            tool_name = check.tool_name or ""
            if tool_name in results:
                continue
            if tool_name not in fetchers:
                raise KeyError(f"Unsupported live signature tool_name={tool_name!r}")
            try:
                results[tool_name] = fetchers[tool_name]()
            except Exception as exc:
                results[tool_name] = exc
        return results

    def _build_fetchers(
        self,
        alert: "AlertPayload",
        scenario: "ScenarioDefinition",
    ) -> dict[str, Callable[[], ToolResult]]:
        telemetry = self.pipeline.context.telemetry
        runbooks = self.pipeline.context.runbooks
        return {
            "error_logs": lambda: telemetry.get_error_logs(alert, scenario),
            "latency_metrics": lambda: telemetry.get_latency_metrics(alert, scenario),
            "error_rate_metrics": lambda: telemetry.get_error_rate_metrics(alert, scenario),
            "failed_traces": lambda: telemetry.get_failed_traces(alert, scenario),
            "slow_traces": lambda: telemetry.get_slow_traces(alert, scenario),
            "recent_changes": lambda: telemetry.get_recent_changes(alert, scenario),
            "runbook": lambda: runbooks.get_runbook(alert, scenario),
        }


def render_live_signature_markdown(report: LiveSignatureReport) -> str:
    lines = [
        "# IIRS Live Signature Validation",
        "",
        f"- Generated at: `{report.generated_at}`",
        f"- Telemetry backend: `{report.telemetry_backend}`",
        f"- Passing scenarios: `{report.passed_scenarios}/{report.total_scenarios}`",
        f"- Passing checks: `{report.passed_checks}/{report.total_checks}`",
    ]

    for scenario_report in report.scenario_reports:
        lines.extend(
            [
                "",
                f"## {scenario_report.scenario_name}",
                f"- Status: `{'PASS' if scenario_report.passed else 'FAIL'}`",
                f"- Incident id: `{scenario_report.incident_id}`",
                f"- Started at: `{scenario_report.started_at}`",
                f"- Window: `{scenario_report.window_minutes}m`",
            ]
        )
        for check in scenario_report.check_results:
            status = "PASS" if check.satisfied else "FAIL"
            details = []
            if check.matched_ids:
                details.append("matched=" + ",".join(check.matched_ids))
            if check.observed_ids and not check.matched_ids:
                details.append("observed=" + ",".join(check.observed_ids))
            if check.error:
                details.append(f"error={check.error}")
            suffix = " | " + " | ".join(details) if details else ""
            lines.append(f"- {check.tool_name}: {status} | {check.description}{suffix}")

    return "\n".join(lines)


def render_live_signature_json(report: LiveSignatureReport) -> str:
    payload = {
        "generated_at": report.generated_at,
        "telemetry_backend": report.telemetry_backend,
        "passed_scenarios": report.passed_scenarios,
        "total_scenarios": report.total_scenarios,
        "passed_checks": report.passed_checks,
        "total_checks": report.total_checks,
        "passed": report.passed,
        "scenario_reports": report.scenario_reports,
    }
    return json.dumps(to_jsonable(payload), indent=2)
