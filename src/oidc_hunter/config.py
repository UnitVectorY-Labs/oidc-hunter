"""Runtime configuration for oidc-hunter."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path


DEFAULT_CATALOG_URL = (
    "https://raw.githubusercontent.com/UnitVectorY-Labs/jwks-catalog/"
    "refs/heads/main/data/services.yaml"
)


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class AppConfig:
    """Configuration loaded from environment variables."""

    state_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("OIDC_HUNTER_STATE_DIR", "state"))
    )
    catalog_url: str = field(
        default_factory=lambda: os.environ.get(
            "OIDC_HUNTER_CATALOG_URL", DEFAULT_CATALOG_URL
        )
    )
    llm_base_url: str | None = field(
        default_factory=lambda: os.environ.get("OIDC_HUNTER_LLM_BASE_URL")
    )
    llm_model: str | None = field(
        default_factory=lambda: os.environ.get("OIDC_HUNTER_LLM_MODEL")
    )
    llm_api_key: str | None = field(
        default_factory=lambda: os.environ.get("OIDC_HUNTER_LLM_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    probe_domains: list[str] = field(
        default_factory=lambda: _split_csv(os.environ.get("OIDC_HUNTER_PROBE_DOMAINS"))
    )
    probe_timeout_seconds: float = field(
        default_factory=lambda: float(
            os.environ.get("OIDC_HUNTER_PROBE_TIMEOUT_SECONDS", "8")
        )
    )

    @property
    def db_path(self) -> Path:
        return self.state_dir / "oidc-hunter.db"

    @property
    def candidates_path(self) -> Path:
        return self.state_dir / "candidates.yaml"

    @property
    def reports_dir(self) -> Path:
        return self.state_dir / "reports"

    @property
    def artifacts_dir(self) -> Path:
        return self.state_dir / "artifacts"

    def ensure_directories(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
