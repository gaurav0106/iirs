from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

from .utils import ensure_directory


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    return float(raw.strip())


@dataclass(slots=True)
class Settings:
    trace_dir: Path = field(default_factory=lambda: PROJECT_ROOT / os.getenv("IIRS_TRACE_DIR", "traces"))
    runbooks_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "runbooks")
    fixtures_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "fixtures" / "alerts")
    prefer_langgraph: bool = field(default_factory=lambda: _env_flag("IIRS_PREFER_LANGGRAPH", True))
    telemetry_backend: str = field(default_factory=lambda: os.getenv("IIRS_TELEMETRY_BACKEND", "mock").strip().lower())
    allow_backend_fallback: bool = field(default_factory=lambda: _env_flag("IIRS_ALLOW_BACKEND_FALLBACK", False))
    http_timeout_seconds: float = field(default_factory=lambda: _env_float("IIRS_HTTP_TIMEOUT_SECONDS", 10.0))
    verify_tls: bool = field(default_factory=lambda: _env_flag("IIRS_VERIFY_TLS", True))
    tenant_id: str | None = field(default_factory=lambda: os.getenv("IIRS_TENANT_ID") or os.getenv("IIRS_ORG_ID"))
    prometheus_base_url: str | None = field(default_factory=lambda: os.getenv("IIRS_PROMETHEUS_URL"))
    loki_base_url: str | None = field(default_factory=lambda: os.getenv("IIRS_LOKI_URL"))
    tempo_base_url: str | None = field(default_factory=lambda: os.getenv("IIRS_TEMPO_URL"))

    def ensure_runtime_dirs(self) -> None:
        ensure_directory(self.trace_dir)


def load_settings() -> Settings:
    settings = Settings()
    settings.ensure_runtime_dirs()
    return settings
