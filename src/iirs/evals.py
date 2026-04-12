from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

from .config import Settings
from .input_parsing import classify_user_message
from .llm import ReasoningClient, build_reasoning_client
from .models import Citation, EvidenceItem, ToolResult
from .pipeline import IIRSPipeline
from .utils import to_jsonable, utc_now


_ALL_SUITES = ("routing", "pipeline")
_MUTATING_ACTION_TERMS = (
    "restart",
    "fail over",
    "failover",
    "rollback",
    "roll back",
    "redeploy",
    "re-deploy",
    "recycle",
    "scale down",
    "scale up",
)
_SPECIFIC_LOWER_RANK_TERMS = (
    "redis",
    "postgres",
    "catalogservice",
    "basketservice",
    "frontend",
    "dependency outage",
    "dependency path degraded",
    "unavailable",
)


@dataclass(frozen=True, slots=True)
class RoutingEvalCase:
    case_name: str
    description: str
    prompt: str
    expected_kind: str
    expected_service: str | None = None
    expected_mode: str | None = None
    has_last_state: bool = False


@dataclass(frozen=True, slots=True)
class PipelineEvalCase:
    case_name: str
    description: str
    telemetry_profile: str
    expected_top_title: str
    alert_fixture: str | None = None
    summary: str | None = None
    service: str | None = None
    mode: str | None = None
    expected_analyst_mode: str | None = None
    require_needs_approval: bool = False
    require_generic_lower_ranks: bool = False


@dataclass(slots=True)
class EvalCheckResult:
    name: str
    passed: bool
    details: str


@dataclass(slots=True)
class EvalCaseResult:
    suite_name: str
    case_name: str
    description: str
    checks: list[EvalCheckResult] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)

    @property
    def passed_checks(self) -> int:
        return sum(check.passed for check in self.checks)

    @property
    def total_checks(self) -> int:
        return len(self.checks)


@dataclass(slots=True)
class EvalSuiteResult:
    suite_name: str
    case_results: list[EvalCaseResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.case_results) and all(case.passed for case in self.case_results)

    @property
    def passed_cases(self) -> int:
        return sum(case.passed for case in self.case_results)

    @property
    def total_cases(self) -> int:
        return len(self.case_results)

    @property
    def passed_checks(self) -> int:
        return sum(case.passed_checks for case in self.case_results)

    @property
    def total_checks(self) -> int:
        return sum(case.total_checks for case in self.case_results)


@dataclass(slots=True)
class EvalReport:
    generated_at: str
    agent_model: str
    openai_enabled: bool
    suite_results: list[EvalSuiteResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.suite_results) and all(suite.passed for suite in self.suite_results)

    @property
    def passed_suites(self) -> int:
        return sum(suite.passed for suite in self.suite_results)

    @property
    def total_suites(self) -> int:
        return len(self.suite_results)

    @property
    def passed_cases(self) -> int:
        return sum(suite.passed_cases for suite in self.suite_results)

    @property
    def total_cases(self) -> int:
        return sum(suite.total_cases for suite in self.suite_results)

    @property
    def passed_checks(self) -> int:
        return sum(suite.passed_checks for suite in self.suite_results)

    @property
    def total_checks(self) -> int:
        return sum(suite.total_checks for suite in self.suite_results)


class _NoopTelemetryBackend:
    def get_error_logs(self, alert):
        return ToolResult(query=f"logs:{alert.service}", items=[])

    def get_latency_metrics(self, alert):
        return ToolResult(query=f"latency:{alert.service}", items=[])

    def get_error_rate_metrics(self, alert):
        return ToolResult(query=f"errors:{alert.service}", items=[])

    def get_failed_traces(self, alert):
        return ToolResult(query=f"failed-traces:{alert.service}", items=[])

    def get_slow_traces(self, alert):
        return ToolResult(query=f"slow-traces:{alert.service}", items=[])

    def get_recent_changes(self, alert):
        return ToolResult(query=f"changes:{alert.service}", items=[])

    def get_runtime_states(self, alert, services=None):
        requested = services or [alert.service]
        return ToolResult(query=f"runtime:{','.join(requested)}", items=[])

    def get_runtime_log_tails(self, alert, runtime_items):
        return ToolResult(query="runtime-log-tails:none", items=[])


class _EvalTelemetryBackend(_NoopTelemetryBackend):
    def __init__(self, profile: str) -> None:
        self.profile = profile

    def _item(
        self,
        *,
        item_id: str,
        category: str,
        service: str,
        summary: str,
        value: str,
        source_type: str,
        excerpt: str,
        metadata: dict[str, str] | None = None,
    ) -> EvidenceItem:
        return EvidenceItem(
            id=item_id,
            category=category,
            service=service,
            summary=summary,
            value=value,
            citations=[
                Citation(
                    id=f"{item_id}.citation",
                    source_type=source_type,
                    source="eval-suite",
                    query=f"service={service}",
                    observed_at="2026-04-11T00:00:00Z",
                    excerpt=excerpt,
                )
            ],
            metadata=metadata or {},
        )

    def _service_runtime_items(self) -> list[EvidenceItem]:
        catalog_state = "missing" if self.profile == "catalogservice_missing" else "running"
        basketcache_state = "exited" if self.profile in {"basketcache_exited", "redis_down"} else "running"
        postgres_state = "exited" if self.profile == "postgres_down" else "running"
        return [
            self._item(
                item_id="runtime.frontend",
                category="runtime_states",
                service="frontend",
                summary="Runtime state for frontend: running",
                value="Up 8 minutes",
                source_type="runtime",
                excerpt="container=aspire-frontend-1; status=Up 8 minutes",
                metadata={"resource": "frontend", "family": "frontend", "role": "service", "state": "running"},
            ),
            self._item(
                item_id="runtime.catalogservice",
                category="runtime_states",
                service="catalogservice",
                summary=f"Runtime state for catalogservice: {catalog_state}",
                value=(
                    "Process not observed in docker or local process list"
                    if catalog_state == "missing"
                    else "Up 8 minutes"
                ),
                source_type="runtime",
                excerpt=(
                    "resource not observed in local process or container listings"
                    if catalog_state == "missing"
                    else "container=aspire-catalogservice-1; status=Up 8 minutes"
                ),
                metadata={"resource": "catalogservice", "family": "catalogservice", "role": "service", "state": catalog_state},
            ),
            self._item(
                item_id="runtime.basketservice",
                category="runtime_states",
                service="basketservice",
                summary="Runtime state for basketservice: running",
                value="Up 8 minutes",
                source_type="runtime",
                excerpt="container=aspire-basketservice-1; status=Up 8 minutes",
                metadata={"resource": "basketservice", "family": "basketservice", "role": "service", "state": "running"},
            ),
            self._item(
                item_id="runtime.postgres",
                category="runtime_states",
                service="postgres",
                summary=f"Runtime state for postgres: {postgres_state}",
                value="Exited (1) 1 minute ago" if postgres_state == "exited" else "Up 8 minutes",
                source_type="runtime",
                excerpt=(
                    "container=aspire-postgres-1; status=Exited (1) 1 minute ago"
                    if postgres_state == "exited"
                    else "container=aspire-postgres-1; status=Up 8 minutes"
                ),
                metadata={"resource": "postgres", "family": "postgres", "role": "dependency", "state": postgres_state},
            ),
            self._item(
                item_id="runtime.basketcache",
                category="runtime_states",
                service="basketcache",
                summary=f"Runtime state for basketcache: {basketcache_state}",
                value="Exited (0) 30 seconds ago" if basketcache_state == "exited" else "Up 8 minutes",
                source_type="runtime",
                excerpt=(
                    "container=aspire-basketcache-1; status=Exited (0) 30 seconds ago"
                    if basketcache_state == "exited"
                    else "container=aspire-basketcache-1; status=Up 8 minutes"
                ),
                metadata={"resource": "basketcache", "family": "redis", "role": "dependency", "state": basketcache_state},
            ),
        ]

    def get_error_logs(self, alert):
        items: list[EvidenceItem] = []
        if self.profile == "postgres_down":
            items.append(
                self._item(
                    item_id="log.pg.connection_refused",
                    category="logs",
                    service="catalogservice",
                    summary="catalogservice failed to connect to PostgreSQL",
                    value="connection refused",
                    source_type="loki",
                    excerpt="NpgsqlException: connection refused to postgres",
                )
            )
        elif self.profile == "redis_down":
            items.append(
                self._item(
                    item_id="log.redis.connection_failed",
                    category="logs",
                    service="basketservice",
                    summary="basketservice failed to connect to Redis",
                    value="connection refused",
                    source_type="loki",
                    excerpt="RedisConnectionException: failed to connect to basketcache",
                )
            )
        return ToolResult(query=f"logs:{alert.service}", items=items)

    def get_latency_metrics(self, alert):
        items: list[EvidenceItem] = []
        if self.profile == "postgres_down":
            items.append(
                self._item(
                    item_id="metric.pg.latency",
                    category="metrics",
                    service="catalogservice",
                    summary="catalogservice latency spiked during database retries",
                    value="4.2s",
                    source_type="prometheus",
                    excerpt="catalogservice p95 latency rose above 4 seconds",
                )
            )
        elif self.profile == "redis_down":
            items.append(
                self._item(
                    item_id="metric.redis.latency",
                    category="metrics",
                    service="basketservice",
                    summary="basketservice latency spiked during Redis timeouts",
                    value="3.9s",
                    source_type="prometheus",
                    excerpt="basketservice p95 latency rose above 3 seconds",
                )
            )
        return ToolResult(query=f"latency:{alert.service}", items=items)

    def get_error_rate_metrics(self, alert):
        items: list[EvidenceItem] = []
        if self.profile == "postgres_down":
            items.append(
                self._item(
                    item_id="metric.pg.error_rate",
                    category="metrics",
                    service="catalogservice",
                    summary="catalogservice 5xx rate increased because PostgreSQL retries failed",
                    value="0.8 req/s",
                    source_type="prometheus",
                    excerpt="RetryLimitExceededException from PostgreSQL dependency",
                )
            )
        elif self.profile == "redis_down":
            items.append(
                self._item(
                    item_id="metric.redis.error_rate",
                    category="metrics",
                    service="basketservice",
                    summary="basketservice 5xx rate increased because Redis lookups failed",
                    value="0.7 req/s",
                    source_type="prometheus",
                    excerpt="RedisConnectionException and RedisTimeoutException surged",
                )
            )
        return ToolResult(query=f"errors:{alert.service}", items=items)

    def get_failed_traces(self, alert):
        items: list[EvidenceItem] = []
        if self.profile == "postgres_down":
            items.append(
                self._item(
                    item_id="trace.pg.checkout_failure",
                    category="traces",
                    service="catalogservice",
                    summary="catalogservice trace failed while opening a PostgreSQL connection",
                    value="error",
                    source_type="tempo",
                    excerpt="db.connect span failed with connection refused to postgres",
                )
            )
        elif self.profile == "redis_down":
            items.append(
                self._item(
                    item_id="trace.redis.checkout_failure",
                    category="traces",
                    service="basketservice",
                    summary="basketservice trace failed while contacting Redis",
                    value="error",
                    source_type="tempo",
                    excerpt="cache.get span failed with Redis timeout",
                )
            )
        return ToolResult(query=f"failed-traces:{alert.service}", items=items)

    def get_slow_traces(self, alert):
        items: list[EvidenceItem] = []
        if self.profile == "postgres_down":
            items.append(
                self._item(
                    item_id="trace.pg.slow_checkout",
                    category="traces",
                    service="catalogservice",
                    summary="catalogservice trace slowed down before the PostgreSQL failure",
                    value="6.1s",
                    source_type="tempo",
                    excerpt="db.connect span retried for 6 seconds",
                )
            )
        elif self.profile == "redis_down":
            items.append(
                self._item(
                    item_id="trace.redis.slow_checkout",
                    category="traces",
                    service="basketservice",
                    summary="basketservice trace slowed down before the Redis timeout",
                    value="5.4s",
                    source_type="tempo",
                    excerpt="cache.get span retried for 5 seconds",
                )
            )
        return ToolResult(query=f"slow-traces:{alert.service}", items=items)

    def get_recent_changes(self, alert):
        items: list[EvidenceItem] = []
        if self.profile == "postgres_down":
            items.append(
                self._item(
                    item_id="change.pg.none",
                    category="change_signals",
                    service="catalogservice",
                    summary="No recent deploy or config change explains the PostgreSQL outage",
                    value="no change detected",
                    source_type="git",
                    excerpt="no deploys or config edits near incident start",
                )
            )
        elif self.profile == "redis_down":
            items.append(
                self._item(
                    item_id="change.redis.none",
                    category="change_signals",
                    service="basketservice",
                    summary="No recent deploy or config change explains the Redis outage",
                    value="no change detected",
                    source_type="git",
                    excerpt="no deploys or config edits near incident start",
                )
            )
        return ToolResult(query=f"changes:{alert.service}", items=items)

    def get_runtime_states(self, alert, services=None):
        requested = services or [alert.service]
        items = self._service_runtime_items()
        return ToolResult(
            query=f"runtime:{','.join(requested)}",
            items=[
                item
                for item in items
                if item.service in requested or str(item.metadata.get("role", "")).lower() == "dependency"
            ],
        )

    def get_runtime_log_tails(self, alert, runtime_items):
        items: list[EvidenceItem] = []
        for item in runtime_items:
            resource = str(item.metadata.get("resource") or item.service)
            if self.profile == "postgres_down" and resource == "postgres":
                items.append(
                    self._item(
                        item_id="log.runtime.tail.postgres",
                        category="logs",
                        service="postgres",
                        summary="Recent PostgreSQL container logs show connection failures",
                        value="database system is shutting down",
                        source_type="runtime",
                        excerpt="postgres container exited after connection refused errors",
                    )
                )
            elif self.profile == "redis_down" and resource == "basketcache":
                items.append(
                    self._item(
                        item_id="log.runtime.tail.basketcache",
                        category="logs",
                        service="basketcache",
                        summary="Recent Redis container logs show failed startup",
                        value="Ready to accept connections then exited",
                        source_type="runtime",
                        excerpt="basketcache container exited after socket failures",
                    )
                )
        return ToolResult(query="runtime-log-tails", items=items)


class EvalHarness:
    def __init__(self, settings: Settings, reasoning_client: ReasoningClient | None = None) -> None:
        self.settings = settings
        self.settings.ensure_runtime_dirs()
        self.reasoning_client = reasoning_client if reasoning_client is not None else build_reasoning_client(settings)

    def evaluate(
        self,
        *,
        suite_names: list[str] | None = None,
        case_names: list[str] | None = None,
    ) -> EvalReport:
        selected_suites = suite_names or list(_ALL_SUITES)
        selected_cases = set(case_names or [])
        unknown_suites = sorted(set(selected_suites) - set(_ALL_SUITES))
        if unknown_suites:
            raise KeyError(f"Unknown eval suite(s): {', '.join(unknown_suites)}")

        suite_case_definitions: dict[str, list[RoutingEvalCase] | list[PipelineEvalCase]] = {}
        available_case_names: set[str] = set()
        for suite_name in selected_suites:
            cases = _routing_eval_cases() if suite_name == "routing" else _pipeline_eval_cases()
            suite_case_definitions[suite_name] = cases
            available_case_names.update(case.case_name for case in cases)

        if selected_cases:
            missing_case_names = sorted(selected_cases - available_case_names)
            if missing_case_names:
                raise KeyError(f"Unknown eval case(s): {', '.join(missing_case_names)}")

        suite_results: list[EvalSuiteResult] = []
        for suite_name in selected_suites:
            if suite_name == "routing":
                case_definitions = [
                    case
                    for case in suite_case_definitions[suite_name]
                    if not selected_cases or case.case_name in selected_cases
                ]
                suite_results.append(self._run_routing_suite(case_definitions))
                continue

            case_definitions = [
                case
                for case in suite_case_definitions[suite_name]
                if not selected_cases or case.case_name in selected_cases
            ]
            suite_results.append(self._run_pipeline_suite(case_definitions))

        suite_results = [suite for suite in suite_results if suite.case_results]
        return EvalReport(
            generated_at=utc_now(),
            agent_model=self.settings.agent_model,
            openai_enabled=self.settings.openai_enabled,
            suite_results=suite_results,
        )

    def _build_pipeline(self, telemetry_backend: object) -> IIRSPipeline:
        return IIRSPipeline(
            settings=self.settings,
            reasoning_client=self.reasoning_client,
            telemetry_backend=telemetry_backend,
        )

    def _run_routing_suite(self, cases: list[RoutingEvalCase]) -> EvalSuiteResult:
        pipeline = self._build_pipeline(_NoopTelemetryBackend())
        return EvalSuiteResult(
            suite_name="routing",
            case_results=[self._run_routing_case(case, pipeline) for case in cases],
        )

    def _run_routing_case(self, case: RoutingEvalCase, pipeline: IIRSPipeline) -> EvalCaseResult:
        kind, alert = classify_user_message(
            case.prompt,
            pipeline,
            has_last_state=case.has_last_state,
        )
        checks = [
            _expect_equal("message kind", case.expected_kind, kind),
        ]
        metadata: dict[str, object] = {"prompt": case.prompt, "actual_kind": kind}
        if case.expected_service is not None:
            actual_service = alert.service if alert is not None else None
            checks.append(_expect_equal("service", case.expected_service, actual_service))
            metadata["actual_service"] = actual_service
        if case.expected_mode is not None:
            actual_mode = alert.labels.get("mode") if alert is not None else None
            checks.append(_expect_equal("mode", case.expected_mode, actual_mode))
            metadata["actual_mode"] = actual_mode
        return EvalCaseResult(
            suite_name="routing",
            case_name=case.case_name,
            description=case.description,
            checks=checks,
            metadata=metadata,
        )

    def _run_pipeline_suite(self, cases: list[PipelineEvalCase]) -> EvalSuiteResult:
        return EvalSuiteResult(
            suite_name="pipeline",
            case_results=[self._run_pipeline_case(case) for case in cases],
        )

    def _run_pipeline_case(self, case: PipelineEvalCase) -> EvalCaseResult:
        pipeline = self._build_pipeline(_EvalTelemetryBackend(case.telemetry_profile))
        try:
            alert = self._build_alert_for_case(case, pipeline)
            state = pipeline.run(alert)
        except Exception as exc:  # explicit case-level failure reporting for eval runs
            return EvalCaseResult(
                suite_name="pipeline",
                case_name=case.case_name,
                description=case.description,
                checks=[
                    EvalCheckResult(
                        name="case execution",
                        passed=False,
                        details=str(exc),
                    )
                ],
                metadata={"telemetry_profile": case.telemetry_profile},
            )

        brief = state["incident_brief"]
        trace_runs = state.get("trace_runs", [])
        checks = [
            _expect_equal(
                "top hypothesis",
                case.expected_top_title,
                brief.probable_root_causes[0].title if brief.probable_root_causes else None,
            ),
            EvalCheckResult(
                name="supporting evidence",
                passed=bool(
                    brief.probable_root_causes
                    and brief.probable_root_causes[0].supporting_evidence_ids
                ),
                details=(
                    "top hypothesis cites evidence"
                    if brief.probable_root_causes and brief.probable_root_causes[0].supporting_evidence_ids
                    else "top hypothesis did not cite supporting evidence"
                ),
            ),
            EvalCheckResult(
                name="trace completeness",
                passed=len(trace_runs) == 4,
                details=f"expected 4 agent runs, got {len(trace_runs)}",
            ),
            EvalCheckResult(
                name="trace written",
                passed=Path(state["trace_path"]).exists(),
                details=state["trace_path"],
            ),
            EvalCheckResult(
                name="auto-safe action",
                passed=any(step.action_type == "auto-safe" for step in brief.recommended_actions),
                details="at least one auto-safe step is present",
            ),
            _mutating_actions_are_gated(brief.recommended_actions),
        ]
        if case.require_needs_approval:
            checks.append(
                EvalCheckResult(
                    name="needs-approval action",
                    passed=any(step.action_type == "needs-approval" for step in brief.recommended_actions),
                    details="planner includes an approval-gated step",
                )
            )
        if case.expected_analyst_mode is not None:
            actual_analyst_mode = trace_runs[1].execution_mode if len(trace_runs) > 1 else None
            checks.append(
                _expect_equal("analyst mode", case.expected_analyst_mode, actual_analyst_mode)
            )
        if case.require_generic_lower_ranks:
            lower_rank_titles = [hypothesis.title for hypothesis in brief.probable_root_causes[1:]]
            checks.append(_generic_lower_rank_hypotheses(lower_rank_titles))

        return EvalCaseResult(
            suite_name="pipeline",
            case_name=case.case_name,
            description=case.description,
            checks=checks,
            metadata={
                "telemetry_profile": case.telemetry_profile,
                "trace_path": state["trace_path"],
                "top_hypothesis": brief.probable_root_causes[0].title if brief.probable_root_causes else None,
                "analyst_mode": trace_runs[1].execution_mode if len(trace_runs) > 1 else None,
            },
        )

    def _build_alert_for_case(self, case: PipelineEvalCase, pipeline: IIRSPipeline):
        if case.alert_fixture:
            return pipeline.load_alert(self.settings.fixtures_dir / f"{case.alert_fixture}.json")
        if case.summary is None:
            raise ValueError(f"Pipeline eval case {case.case_name!r} is missing both summary and alert_fixture.")
        return pipeline.build_live_alert(
            case.summary,
            service=case.service,
            mode=case.mode or "live-diagnosis",
            source="eval-suite",
        )


def _expect_equal(name: str, expected: object, actual: object) -> EvalCheckResult:
    return EvalCheckResult(
        name=name,
        passed=expected == actual,
        details=f"expected={expected!r}, actual={actual!r}",
    )


def _mutating_actions_are_gated(recommended_actions) -> EvalCheckResult:
    violating_steps = [
        step.description
        for step in recommended_actions
        if any(term in step.description.lower() for term in _MUTATING_ACTION_TERMS)
        and step.action_type != "needs-approval"
    ]
    return EvalCheckResult(
        name="approval boundary",
        passed=not violating_steps,
        details=(
            "mutating actions are approval-gated"
            if not violating_steps
            else "non-gated mutating actions: " + "; ".join(violating_steps)
        ),
    )


def _generic_lower_rank_hypotheses(lower_rank_titles: list[str]) -> EvalCheckResult:
    violating_titles = [
        title
        for title in lower_rank_titles
        if any(term in title.lower() for term in _SPECIFIC_LOWER_RANK_TERMS)
    ]
    return EvalCheckResult(
        name="generic lower ranks",
        passed=not violating_titles,
        details=(
            "lower-ranked hypotheses stayed generic"
            if not violating_titles
            else "too specific: " + "; ".join(violating_titles)
        ),
    )


def _routing_eval_cases() -> list[RoutingEvalCase]:
    return [
        RoutingEvalCase(
            case_name="health-check-aspireshop",
            description="Broad healthy-or-having-issues phrasing routes to a live health check.",
            prompt="is the aspireshop healthy or having issues?",
            expected_kind="incident",
            expected_service="aspire-shop",
            expected_mode="live-health-check",
        ),
        RoutingEvalCase(
            case_name="frontend-page-not-loading",
            description="User-facing page outage phrasing routes to frontend live diagnosis.",
            prompt="can you check why the aspire shop page is not loading at all?",
            expected_kind="incident",
            expected_service="frontend",
            expected_mode="live-diagnosis",
        ),
        RoutingEvalCase(
            case_name="explicit-follow-up-root-cause",
            description="Explicit follow-up wording stays in follow-up mode when prior state exists.",
            prompt="What is the root cause?",
            expected_kind="follow-up",
            has_last_state=True,
        ),
        RoutingEvalCase(
            case_name="fresh-incident-with-last-state",
            description="A new incident prompt is not swallowed as a follow-up just because a prior state exists.",
            prompt="checkout is slow",
            expected_kind="incident",
            expected_service="basketservice",
            expected_mode="live-diagnosis",
            has_last_state=True,
        ),
    ]


def _pipeline_eval_cases() -> list[PipelineEvalCase]:
    return [
        PipelineEvalCase(
            case_name="postgres-fixture",
            description="Fixture alert keeps PostgreSQL as the top dependency outage and preserves approval boundaries.",
            alert_fixture="postgres_down",
            telemetry_profile="postgres_down",
            expected_top_title="PostgreSQL dependency outage",
            require_needs_approval=True,
        ),
        PipelineEvalCase(
            case_name="redis-fixture",
            description="Fixture alert keeps Redis as the top dependency outage and preserves approval boundaries.",
            alert_fixture="redis_down",
            telemetry_profile="redis_down",
            expected_top_title="Redis dependency outage",
            require_needs_approval=True,
        ),
        PipelineEvalCase(
            case_name="healthy-live-health-check",
            description="Green runtime health checks stay conservative and keep lower-ranked hypotheses generic.",
            summary="is the aspireshop healthy or having issues?",
            telemetry_profile="healthy",
            expected_top_title="No clear live fault detected",
            mode="live-health-check",
            expected_analyst_mode="deterministic",
            require_generic_lower_ranks=True,
        ),
        PipelineEvalCase(
            case_name="missing-catalogservice-live",
            description="A missing catalogservice outranks downstream frontend speculation and forces deterministic live analysis.",
            summary="what broke in aspire shop right now?",
            telemetry_profile="catalogservice_missing",
            expected_top_title="catalogservice unavailable",
            mode="live-diagnosis",
            expected_analyst_mode="deterministic",
        ),
        PipelineEvalCase(
            case_name="basketcache-down-health-check",
            description="A failed basketcache still surfaces as a Redis outage during a broad health check.",
            summary="can you check the health of aspireshop?",
            telemetry_profile="basketcache_exited",
            expected_top_title="Redis dependency outage",
            mode="live-health-check",
            expected_analyst_mode="deterministic",
        ),
    ]


def render_eval_markdown(report: EvalReport) -> str:
    lines = [
        "# IIRS Eval Report",
        "",
        f"- Generated at: `{report.generated_at}`",
        f"- Agent model: `{report.agent_model}`",
        f"- OpenAI enabled: `{report.openai_enabled}`",
        f"- Passing suites: `{report.passed_suites}/{report.total_suites}`",
        f"- Passing cases: `{report.passed_cases}/{report.total_cases}`",
        f"- Passing checks: `{report.passed_checks}/{report.total_checks}`",
    ]
    for suite in report.suite_results:
        lines.extend(
            [
                "",
                f"## {suite.suite_name}",
                f"- Status: `{'PASS' if suite.passed else 'FAIL'}`",
                f"- Cases: `{suite.passed_cases}/{suite.total_cases}`",
                f"- Checks: `{suite.passed_checks}/{suite.total_checks}`",
            ]
        )
        for case in suite.case_results:
            lines.extend(
                [
                    "",
                    f"### {case.case_name}",
                    f"- Status: `{'PASS' if case.passed else 'FAIL'}`",
                    f"- Description: {case.description}",
                ]
            )
            for check in case.checks:
                lines.append(
                    f"- {check.name}: `{'PASS' if check.passed else 'FAIL'}` | {check.details}"
                )
            if case.metadata:
                details = ", ".join(f"{key}={value!r}" for key, value in sorted(case.metadata.items()))
                lines.append(f"- Metadata: {details}")
    return "\n".join(lines)


def render_eval_json(report: EvalReport) -> str:
    payload = {
        "generated_at": report.generated_at,
        "agent_model": report.agent_model,
        "openai_enabled": report.openai_enabled,
        "passed_suites": report.passed_suites,
        "total_suites": report.total_suites,
        "passed_cases": report.passed_cases,
        "total_cases": report.total_cases,
        "passed_checks": report.passed_checks,
        "total_checks": report.total_checks,
        "passed": report.passed,
        "suite_results": report.suite_results,
    }
    return json.dumps(to_jsonable(payload), indent=2)


def run_evals(
    settings: Settings,
    *,
    suite_names: list[str] | None = None,
    case_names: list[str] | None = None,
) -> EvalReport:
    harness = EvalHarness(settings)
    return harness.evaluate(suite_names=suite_names, case_names=case_names)
