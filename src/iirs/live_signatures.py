from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import TYPE_CHECKING, Callable

from .backends import TelemetryConfigurationError
from .expectations import EvidenceExpectation
from .models import AlertPayload, ToolResult
from .utils import read_json, to_jsonable, utc_now

if TYPE_CHECKING:
    from pathlib import Path

    from .pipeline import IIRSPipeline


@dataclass(slots=True)
class LiveSignatureProfile:
    profile_name: str
    service: str
    summary: str
    severity: str = "critical"
    environment: str = "local-dev"
    window_minutes: int = 15
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
            profile_name=str(payload.get("profile_name") or payload["scenario_name"]),
            service=str(payload["service"]),
            summary=str(payload["summary"]),
            severity=str(payload.get("severity", "critical")),
            environment=str(payload.get("environment", "local-dev")),
            window_minutes=int(payload.get("window_minutes", 15)),
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
class LiveSignatureProfileReport:
    profile_name: str
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
    profile_reports: list[LiveSignatureProfileReport] = field(default_factory=list)

    @property
    def total_profiles(self) -> int:
        return len(self.profile_reports)

    @property
    def passed_profiles(self) -> int:
        return sum(report.passed for report in self.profile_reports)

    @property
    def total_checks(self) -> int:
        return sum(report.total_checks for report in self.profile_reports)

    @property
    def passed_checks(self) -> int:
        return sum(report.passed_checks for report in self.profile_reports)

    @property
    def passed(self) -> bool:
        return self.total_profiles > 0 and self.passed_profiles == self.total_profiles


def load_live_signature_profiles(directory: "Path") -> dict[str, LiveSignatureProfile]:
    profiles: dict[str, LiveSignatureProfile] = {}
    for path in sorted(directory.glob("*.json")):
        profile = LiveSignatureProfile.from_mapping(read_json(path))
        profiles[profile.profile_name] = profile
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

    def validate_profiles(
        self,
        profile_names: list[str],
        *,
        started_at: str | None = None,
        window_minutes: int | None = None,
    ) -> LiveSignatureReport:
        if self.pipeline.settings.telemetry_backend != "plt":
            raise TelemetryConfigurationError(
                "Live signature validation requires IIRS_TELEMETRY_BACKEND=plt."
            )

        effective_started_at = started_at or utc_now()
        profile_reports: list[LiveSignatureProfileReport] = []
        for profile_name in profile_names:
            if profile_name not in self.profiles:
                raise KeyError(f"No live signature profile found for profile {profile_name!r}")

            profile = self.profiles[profile_name]
            alert = self._build_alert(profile, started_at=effective_started_at, window_minutes=window_minutes)

            tool_results = self._collect_tool_results(profile, alert)
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

            profile_reports.append(
                LiveSignatureProfileReport(
                    profile_name=profile_name,
                    incident_id=alert.incident_id,
                    started_at=alert.started_at,
                    window_minutes=alert.window_minutes,
                    check_results=check_results,
                )
            )

        return LiveSignatureReport(
            generated_at=utc_now(),
            telemetry_backend=self.pipeline.settings.telemetry_backend,
            profile_reports=profile_reports,
        )

    def _build_alert(
        self,
        profile: LiveSignatureProfile,
        *,
        started_at: str,
        window_minutes: int | None,
    ) -> AlertPayload:
        return AlertPayload(
            incident_id=f"{profile.profile_name}-live-signature",
            summary=profile.summary,
            severity=profile.severity,
            service=profile.service,
            environment=profile.environment,
            started_at=started_at,
            window_minutes=window_minutes if window_minutes is not None else profile.window_minutes,
            scenario=None,
            labels={"source": "live-signature", "profile": profile.profile_name, "mode": "live-diagnosis"},
        )

    def _collect_tool_results(
        self,
        profile: LiveSignatureProfile,
        alert: AlertPayload,
    ) -> dict[str, ToolResult | Exception]:
        fetchers = self._build_fetchers(alert)
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
        alert: AlertPayload,
    ) -> dict[str, Callable[[], ToolResult]]:
        telemetry = self.pipeline.context.telemetry
        runbooks = self.pipeline.context.runbooks
        return {
            "error_logs": lambda: telemetry.get_error_logs(alert),
            "latency_metrics": lambda: telemetry.get_latency_metrics(alert),
            "error_rate_metrics": lambda: telemetry.get_error_rate_metrics(alert),
            "failed_traces": lambda: telemetry.get_failed_traces(alert),
            "slow_traces": lambda: telemetry.get_slow_traces(alert),
            "recent_changes": lambda: telemetry.get_recent_changes(alert),
            "runbook": lambda: runbooks.get_runbook(alert),
        }


def render_live_signature_markdown(report: LiveSignatureReport) -> str:
    lines = [
        "# IIRS Live Signature Validation",
        "",
        f"- Generated at: `{report.generated_at}`",
        f"- Telemetry backend: `{report.telemetry_backend}`",
        f"- Passing profiles: `{report.passed_profiles}/{report.total_profiles}`",
        f"- Passing checks: `{report.passed_checks}/{report.total_checks}`",
    ]

    for profile_report in report.profile_reports:
        lines.extend(
            [
                "",
                f"## {profile_report.profile_name}",
                f"- Status: `{'PASS' if profile_report.passed else 'FAIL'}`",
                f"- Incident id: `{profile_report.incident_id}`",
                f"- Started at: `{profile_report.started_at}`",
                f"- Window: `{profile_report.window_minutes}m`",
            ]
        )
        for check in profile_report.check_results:
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
        "passed_profiles": report.passed_profiles,
        "total_profiles": report.total_profiles,
        "passed_checks": report.passed_checks,
        "total_checks": report.total_checks,
        "passed": report.passed,
        "profile_reports": report.profile_reports,
    }
    return json.dumps(to_jsonable(payload), indent=2)
