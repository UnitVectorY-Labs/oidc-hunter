"""Application orchestration for the first-pass oidc-hunter build."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
import textwrap

import httpx

from .adk_app import create_run_plan
from .config import AppConfig
from .db import close_run, database, start_run, utc_now
from .export import write_candidates_yaml, write_report
from .importers import import_catalog_yaml, load_candidates
from .probe import probe_domain, record_candidate_decision, store_probe_result


def make_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _fetch_catalog(url: str, run_dir: Path) -> tuple[str, str | None]:
    try:
        response = httpx.get(url, timeout=20, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return "", str(exc)
    imports_dir = run_dir / "imports"
    imports_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = imports_dir / "services.yaml"
    snapshot_path.write_text(response.text, encoding="utf-8")
    return response.text, None


async def run(config: AppConfig | None = None) -> int:
    config = config or AppConfig()
    config.ensure_directories()
    run_id = make_run_id()
    run_dir = config.artifacts_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    with database(config.db_path) as conn:
        start_run(conn, run_id)
        catalog_text, catalog_error = _fetch_catalog(config.catalog_url, run_dir)
        catalog_count = import_catalog_yaml(conn, run_id, catalog_text) if catalog_text else 0
        candidate_count = load_candidates(conn, config.candidates_path)

        probe_summaries = []
        decisions = []
        for domain in config.probe_domains:
            result = probe_domain(domain, config.probe_timeout_seconds)
            store_probe_result(conn, run_id, result)
            decision = record_candidate_decision(conn, run_id, result)
            probe_summaries.append(
                f"{result.domain}: {result.classification} ({result.status})"
            )
            if decision != "no_candidate":
                decisions.append(f"{result.domain}: {decision}")

        initialization_summary = textwrap.dedent(
            f"""
            run_id: {run_id}
            catalog_url: {config.catalog_url}
            catalog_entries_imported: {catalog_count}
            catalog_fetch_error: {catalog_error or "none"}
            existing_candidates_imported: {candidate_count}
            configured_probe_domains: {", ".join(config.probe_domains) or "none"}
            probe_results: {"; ".join(probe_summaries) or "none"}
            candidate_decisions: {"; ".join(decisions) or "none"}
            """
        ).strip()

        plan_text = await create_run_plan(config, run_id, initialization_summary)
        conn.execute(
            """
            INSERT OR REPLACE INTO run_plans(run_id, plan_text, created_at)
            VALUES (?, ?, ?)
            """,
            (run_id, plan_text, utc_now()),
        )
        exported_count = write_candidates_yaml(conn, config.candidates_path)
        report = (
            f"# oidc-hunter run {run_id}\n\n"
            "## Initialization\n\n"
            "```text\n"
            f"{initialization_summary}\n"
            "```\n\n"
            "## ADK Planning Output\n\n"
            f"{plan_text}\n\n"
            "## Candidate Export\n\n"
            f"Exported {exported_count} active candidate(s) to "
            f"`{config.candidates_path}`.\n"
        )
        report_path = config.reports_dir / f"{run_id}.md"
        write_report(report_path, report)
        close_run(conn, run_id, "completed", f"Report written to {report_path}")

    print(f"Completed oidc-hunter run {run_id}")
    print(f"State directory: {config.state_dir}")
    print(f"Report: {report_path}")
    return 0


def main() -> int:
    return asyncio.run(run())
