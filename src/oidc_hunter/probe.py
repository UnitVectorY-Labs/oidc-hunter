"""Deterministic OIDC discovery probing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


@dataclass(frozen=True)
class ProbeResult:
    domain: str
    status: str
    classification: str
    openid_configuration_url: str
    issuer: str | None = None
    jwks_uri: str | None = None
    error: str | None = None


def normalize_domain(domain: str) -> str:
    parsed = urlparse(domain if "://" in domain else f"https://{domain}")
    return parsed.netloc.lower().strip("/")


def probe_domain(domain: str, timeout_seconds: float) -> ProbeResult:
    normalized = normalize_domain(domain)
    url = f"https://{normalized}/.well-known/openid-configuration"
    try:
        response = httpx.get(url, timeout=timeout_seconds, follow_redirects=True)
    except httpx.TimeoutException as exc:
        return ProbeResult(normalized, "timeout", "timeout", url, error=str(exc))
    except httpx.HTTPError as exc:
        return ProbeResult(normalized, "error", "request_error", url, error=str(exc))

    if response.status_code == 404:
        return ProbeResult(normalized, "not_found", "not_found", url)
    if response.status_code >= 400:
        return ProbeResult(
            normalized, f"http_{response.status_code}", "invalid_response", url
        )

    try:
        payload = response.json()
    except ValueError as exc:
        return ProbeResult(normalized, "ok", "invalid_json", url, error=str(exc))

    issuer = payload.get("issuer")
    jwks_uri = payload.get("jwks_uri")
    if isinstance(issuer, str) and isinstance(jwks_uri, str):
        return ProbeResult(normalized, "ok", "valid_oidc", url, issuer, jwks_uri)
    return ProbeResult(normalized, "ok", "missing_required_fields", url)


async def _probe_domain_async(
    client: httpx.AsyncClient, domain: str, timeout_seconds: float
) -> ProbeResult:
    normalized = normalize_domain(domain)
    url = f"https://{normalized}/.well-known/openid-configuration"
    try:
        response = await client.get(url, timeout=timeout_seconds, follow_redirects=True)
    except httpx.TimeoutException as exc:
        return ProbeResult(normalized, "timeout", "timeout", url, error=str(exc))
    except httpx.HTTPError as exc:
        return ProbeResult(normalized, "error", "request_error", url, error=str(exc))

    if response.status_code == 404:
        return ProbeResult(normalized, "not_found", "not_found", url)
    if response.status_code >= 400:
        return ProbeResult(
            normalized, f"http_{response.status_code}", "invalid_response", url
        )

    try:
        payload = response.json()
    except ValueError as exc:
        return ProbeResult(normalized, "ok", "invalid_json", url, error=str(exc))

    issuer = payload.get("issuer")
    jwks_uri = payload.get("jwks_uri")
    if isinstance(issuer, str) and isinstance(jwks_uri, str):
        return ProbeResult(normalized, "ok", "valid_oidc", url, issuer, jwks_uri)
    return ProbeResult(normalized, "ok", "missing_required_fields", url)


async def probe_many_domains(
    domains: list[str], timeout_seconds: float, concurrency: int
) -> list[ProbeResult]:
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async with httpx.AsyncClient() as client:
        async def run_one(domain: str) -> ProbeResult:
            async with semaphore:
                return await _probe_domain_async(client, domain, timeout_seconds)

        return list(await asyncio.gather(*(run_one(domain) for domain in domains)))


def store_probe_result(conn, run_id: str, result: ProbeResult) -> None:
    conn.execute(
        """
        INSERT INTO domain_state(
            domain, discovered_by_tactic, first_seen_run_id, last_seen_run_id,
            last_probe_status, last_probe_classification,
            last_openid_configuration_url, last_issuer, last_jwks_uri, needs_followup
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(domain) DO UPDATE SET
            last_seen_run_id = excluded.last_seen_run_id,
            last_probe_status = excluded.last_probe_status,
            last_probe_classification = excluded.last_probe_classification,
            last_openid_configuration_url = excluded.last_openid_configuration_url,
            last_issuer = excluded.last_issuer,
            last_jwks_uri = excluded.last_jwks_uri,
            needs_followup = excluded.needs_followup
        """,
        (
            result.domain,
            "configured_probe_domains",
            run_id,
            run_id,
            result.status,
            result.classification,
            result.openid_configuration_url,
            result.issuer,
            result.jwks_uri,
            0 if result.classification == "valid_oidc" else 1,
        ),
    )


def already_known(conn, issuer: str | None, jwks_uri: str | None) -> bool:
    if not issuer and not jwks_uri:
        return False
    catalog_match = conn.execute(
        """
        SELECT 1
        FROM catalog_entries
        WHERE issuer_hint = ? OR jwks_uri = ?
        LIMIT 1
        """,
        (issuer, jwks_uri),
    ).fetchone()
    if catalog_match:
        return True
    candidate_match = conn.execute(
        """
        SELECT 1
        FROM candidate_entries
        WHERE status = 'active' AND (issuer = ? OR jwks_uri = ?)
        LIMIT 1
        """,
        (issuer, jwks_uri),
    ).fetchone()
    return candidate_match is not None


def record_candidate_decision(conn, run_id: str, result: ProbeResult) -> str:
    if result.classification != "valid_oidc" or not result.issuer:
        return "no_candidate"
    if already_known(conn, result.issuer, result.jwks_uri):
        decision = "reject"
        reason = "Issuer or JWKS URI already exists in known catalog/candidates set."
    else:
        decision = "new_candidate"
        reason = "Valid OIDC discovery document not present in known set."
        conn.execute(
            """
            INSERT OR IGNORE INTO candidate_entries(
                candidate_id, name, issuer, openid_configuration_url, jwks_uri, status, source
            )
            VALUES (?, ?, ?, ?, ?, 'active', 'discovered')
            """,
            (
                result.domain,
                result.domain,
                result.issuer,
                result.openid_configuration_url,
                result.jwks_uri,
            ),
        )
    conn.execute(
        """
        INSERT OR IGNORE INTO candidate_decisions(
            run_id, issuer, domain, decision, reason, created_at
        )
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        """,
        (run_id, result.issuer, result.domain, decision, reason),
    )
    return decision
