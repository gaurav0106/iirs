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


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def load_local_env_files(paths: list[Path] | None = None) -> None:
    candidates = paths or [PROJECT_ROOT / ".env.local", PROJECT_ROOT / ".env"]
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            os.environ.setdefault(key, _strip_wrapping_quotes(value.strip()))


@dataclass(slots=True)
class Settings:
    trace_dir: Path = field(default_factory=lambda: PROJECT_ROOT / os.getenv("IIRS_TRACE_DIR", "traces"))
    runbooks_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "runbooks")
    fixtures_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "fixtures" / "alerts")
    ground_truth_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "fixtures" / "ground_truth")
    live_signature_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "fixtures" / "live_signatures")
    prefer_langgraph: bool = field(default_factory=lambda: _env_flag("IIRS_PREFER_LANGGRAPH", True))
    telemetry_backend: str = field(default_factory=lambda: os.getenv("IIRS_TELEMETRY_BACKEND", "mock").strip().lower())
    allow_backend_fallback: bool = field(default_factory=lambda: _env_flag("IIRS_ALLOW_BACKEND_FALLBACK", False))
    http_timeout_seconds: float = field(default_factory=lambda: _env_float("IIRS_HTTP_TIMEOUT_SECONDS", 10.0))
    verify_tls: bool = field(default_factory=lambda: _env_flag("IIRS_VERIFY_TLS", True))
    tenant_id: str | None = field(default_factory=lambda: os.getenv("IIRS_TENANT_ID") or os.getenv("IIRS_ORG_ID"))
    prometheus_base_url: str | None = field(default_factory=lambda: os.getenv("IIRS_PROMETHEUS_URL"))
    loki_base_url: str | None = field(default_factory=lambda: os.getenv("IIRS_LOKI_URL"))
    tempo_base_url: str | None = field(default_factory=lambda: os.getenv("IIRS_TEMPO_URL"))
    openai_api_key: str | None = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY") or os.getenv("IIRS_OPENAI_API_KEY")
    )
    openai_enabled: bool = field(
        default_factory=lambda: _env_flag(
            "IIRS_USE_OPENAI_AGENTS",
            bool(os.getenv("OPENAI_API_KEY") or os.getenv("IIRS_OPENAI_API_KEY")),
        )
    )
    openai_base_url: str = field(
        default_factory=lambda: os.getenv("IIRS_OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    )
    openai_reasoning_effort: str = field(
        default_factory=lambda: os.getenv("IIRS_OPENAI_REASONING_EFFORT", "low").strip().lower()
    )
    agent_model: str = field(default_factory=lambda: os.getenv("IIRS_AGENT_MODEL", "gpt-5-mini").strip())
    embedding_model: str = field(
        default_factory=lambda: os.getenv("IIRS_EMBEDDING_MODEL", "text-embedding-3-small").strip()
    )

    def ensure_runtime_dirs(self) -> None:
        ensure_directory(self.trace_dir)


def load_settings() -> Settings:
    load_local_env_files()
    settings = Settings()
    settings.ensure_runtime_dirs()
    return settings
