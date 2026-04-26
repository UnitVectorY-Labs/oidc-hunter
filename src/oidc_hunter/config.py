"""Runtime configuration for oidc-hunter."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path


DEFAULT_CATALOG_URL = (
    "https://raw.githubusercontent.com/UnitVectorY-Labs/jwks-catalog/"
    "refs/heads/main/data/services.yaml"
)
DEFAULT_CLOUDFLARE_BASE_URL = "https://api.cloudflare.com/client/v4"


def _load_dotenv() -> dict[str, str]:
    path = Path(".env")
    if not path.exists():
        return {}

    loaded: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        loaded[key.strip()] = value.strip().strip("'").strip('"')
    return loaded


_DOTENV = _load_dotenv()


def _getenv(*keys: str, default: str | None = None) -> str | None:
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    for key in keys:
        value = _DOTENV.get(key)
        if value:
            return value
    return default


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class AppConfig:
    """Configuration loaded from environment variables."""

    state_dir: Path = field(
        default_factory=lambda: Path(_getenv("OIDC_HUNTER_STATE_DIR", default="data"))
    )
    catalog_url: str = field(
        default_factory=lambda: _getenv(
            "OIDC_HUNTER_CATALOG_URL", default=DEFAULT_CATALOG_URL
        )
        or DEFAULT_CATALOG_URL
    )
    llm_base_url: str | None = field(
        default_factory=lambda: _getenv(
            "OIDC_HUNTER_LLM_BASE_URL", "OPENAI_BASE_URL", "OPENAPI_BASE_URL"
        )
    )
    llm_model: str | None = field(
        default_factory=lambda: _getenv("OIDC_HUNTER_LLM_MODEL", "MODEL")
    )
    llm_api_key: str | None = field(
        default_factory=lambda: _getenv(
            "OIDC_HUNTER_LLM_API_KEY", "OPENAI_API_KEY", default="unused"
        )
    )
    llm_timeout_seconds: float = field(
        default_factory=lambda: float(
            _getenv("OIDC_HUNTER_LLM_TIMEOUT_SECONDS", default="45") or "45"
        )
    )
    agentic_timeout_seconds: float = field(
        default_factory=lambda: float(
            _getenv("OIDC_HUNTER_AGENTIC_TIMEOUT_SECONDS", default="90") or "90"
        )
    )
    cloudflare_api_base_url: str = field(
        default_factory=lambda: _getenv(
            "OIDC_HUNTER_CLOUDFLARE_BASE_URL", default=DEFAULT_CLOUDFLARE_BASE_URL
        )
        or DEFAULT_CLOUDFLARE_BASE_URL
    )
    cloudflare_api_token: str | None = field(
        default_factory=lambda: _getenv(
            "OIDC_HUNTER_CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_KEY"
        )
    )
    cloudflare_dataset_alias: str | None = field(
        default_factory=lambda: _getenv("OIDC_HUNTER_CLOUDFLARE_DATASET_ALIAS")
    )
    cloudflare_top_limit: int = field(
        default_factory=lambda: int(_getenv("OIDC_HUNTER_CLOUDFLARE_TOP_LIMIT", default="100") or "100")
    )
    cloudflare_seed_sample_size: int = field(
        default_factory=lambda: int(
            _getenv("OIDC_HUNTER_CLOUDFLARE_SEED_SAMPLE_SIZE", default="30") or "30"
        )
    )
    probe_domains: list[str] = field(
        default_factory=lambda: _split_csv(_getenv("OIDC_HUNTER_PROBE_DOMAINS"))
    )
    probe_timeout_seconds: float = field(
        default_factory=lambda: float(
            _getenv("OIDC_HUNTER_PROBE_TIMEOUT_SECONDS", default="8") or "8"
        )
    )
    probe_concurrency: int = field(
        default_factory=lambda: int(
            _getenv("OIDC_HUNTER_PROBE_CONCURRENCY", default="8") or "8"
        )
    )
    investigation_iterations: int = field(
        default_factory=lambda: int(
            _getenv("OIDC_HUNTER_INVESTIGATION_ITERATIONS", default="3") or "3"
        )
    )
    review_iterations: int = field(
        default_factory=lambda: int(
            _getenv("OIDC_HUNTER_REVIEW_ITERATIONS", default="20") or "20"
        )
    )
    investigation_target_limit: int = field(
        default_factory=lambda: int(
            _getenv("OIDC_HUNTER_INVESTIGATION_TARGET_LIMIT", default="60") or "60"
        )
    )
    keep_probe_artifacts: bool = field(
        default_factory=lambda: (_getenv("OIDC_HUNTER_KEEP_PROBE_ARTIFACTS", default="0") or "0")
        == "1"
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

    @property
    def lessons_dir(self) -> Path:
        return self.state_dir / "lessons"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)

    def ensure_directories(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.lessons_dir.mkdir(parents=True, exist_ok=True)
