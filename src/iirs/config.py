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


@dataclass(slots=True)
class Settings:
    trace_dir: Path = field(default_factory=lambda: PROJECT_ROOT / os.getenv("IIRS_TRACE_DIR", "traces"))
    runbooks_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "runbooks")
    fixtures_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "fixtures" / "alerts")
    prefer_langgraph: bool = field(default_factory=lambda: _env_flag("IIRS_PREFER_LANGGRAPH", True))

    def ensure_runtime_dirs(self) -> None:
        ensure_directory(self.trace_dir)


def load_settings() -> Settings:
    settings = Settings()
    settings.ensure_runtime_dirs()
    return settings
