"""Application orchestration for oidc-hunter."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
import traceback

from .adk_app import run_agentic_workflow
from .config import AppConfig
from .db import close_run, database, start_run
from .export import write_report


def make_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


async def run(config: AppConfig | None = None) -> int:
    config = config or AppConfig()
    config.ensure_directories()
    run_id = make_run_id()
    run_dir = config.artifacts_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    with database(config.db_path) as conn:
        start_run(conn, run_id)

    try:
        outcome = await run_agentic_workflow(config=config, run_id=run_id, run_dir=run_dir)
    except Exception as exc:
        failure_report = (
            f"# oidc-hunter run {run_id}\n\n"
            "## Failure\n\n"
            "```text\n"
            f"{type(exc).__name__}: {exc}\n\n"
            f"{traceback.format_exc()}\n"
            "```\n"
        )
        report_path = config.reports_dir / f"{run_id}.md"
        write_report(report_path, failure_report)
        with database(config.db_path) as conn:
            close_run(conn, run_id, "failed", f"Failure report written to {report_path}")
        raise

    report_path = config.reports_dir / f"{run_id}.md"
    print(f"Completed oidc-hunter run {run_id}")
    print(f"State directory: {config.state_dir}")
    print(f"Run directory: {run_dir}")
    print(f"Report: {report_path}")
    print(f"Outcome: {outcome}")
    return 0


def main() -> int:
    return asyncio.run(run())
