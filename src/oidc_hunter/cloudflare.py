"""Cloudflare Radar ingestion helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import httpx

from .config import AppConfig


@dataclass(frozen=True)
class CloudflareSeedBatch:
    domains: list[str]
    source: str
    artifact_ref: str | None
    metadata: dict[str, object]


def _headers(config: AppConfig) -> dict[str, str]:
    token = config.cloudflare_api_token
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def fetch_cloudflare_seed_batch(
    config: AppConfig, run_imports_dir: Path
) -> CloudflareSeedBatch:
    """Fetch one bounded domain seed batch from Cloudflare Radar.

    If no Cloudflare token is configured, the app falls back to the explicitly
    configured probe domain list so the pipeline can still run locally.
    """

    run_imports_dir.mkdir(parents=True, exist_ok=True)

    if not config.cloudflare_api_token:
        artifact_path = run_imports_dir / "configured-probe-domains.json"
        payload = {"domains": config.probe_domains, "source": "configured_probe_domains"}
        artifact_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return CloudflareSeedBatch(
            domains=config.probe_domains,
            source="configured_probe_domains",
            artifact_ref=str(artifact_path),
            metadata={"count": len(config.probe_domains), "fallback": True},
        )

    if config.cloudflare_dataset_alias:
        return _fetch_dataset_alias(config, run_imports_dir)
    return _fetch_top_domains(config, run_imports_dir)


def _fetch_top_domains(config: AppConfig, run_imports_dir: Path) -> CloudflareSeedBatch:
    response = httpx.get(
        f"{config.cloudflare_api_base_url}/radar/ranking/top",
        params={"name": "top", "limit": min(config.cloudflare_top_limit, 100)},
        headers=_headers(config),
        timeout=30,
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    result_payload = payload.get("result", {})
    results = result_payload.get("top_0") or result_payload.get("top") or []
    domains = [
        item["domain"].strip().lower()
        for item in results
        if isinstance(item, dict) and isinstance(item.get("domain"), str)
    ]
    artifact_path = run_imports_dir / "cloudflare-top.json"
    artifact_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return CloudflareSeedBatch(
        domains=domains,
        source="cloudflare_top",
        artifact_ref=str(artifact_path),
        metadata={
            "count": len(domains),
            "top_limit": min(config.cloudflare_top_limit, 100),
            "success": payload.get("success", True),
        },
    )


def _fetch_dataset_alias(
    config: AppConfig, run_imports_dir: Path
) -> CloudflareSeedBatch:
    alias = config.cloudflare_dataset_alias
    response = httpx.get(
        f"{config.cloudflare_api_base_url}/radar/datasets/{alias}",
        headers=_headers(config),
        timeout=30,
        follow_redirects=True,
    )
    response.raise_for_status()
    lines = [line.strip().lower() for line in response.text.splitlines() if line.strip()]
    if lines and lines[0] == "domain":
        lines = lines[1:]
    domains = lines[: config.cloudflare_seed_sample_size]
    artifact_path = run_imports_dir / f"{alias}.txt"
    artifact_path.write_text(response.text, encoding="utf-8")
    return CloudflareSeedBatch(
        domains=domains,
        source=f"cloudflare_dataset:{alias}",
        artifact_ref=str(artifact_path),
        metadata={
            "count": len(domains),
            "dataset_alias": alias,
            "sample_size": config.cloudflare_seed_sample_size,
        },
    )
