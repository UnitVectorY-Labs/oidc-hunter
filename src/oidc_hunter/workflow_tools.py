"""Deterministic workflow tools used by the ADK pipeline."""

from __future__ import annotations

import asyncio
from collections.abc import MutableMapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Any
from urllib.parse import urlparse

import httpx

from .cloudflare import CloudflareSeedBatch, fetch_cloudflare_seed_batch
from .config import AppConfig
from .db import close_run, database, start_run, utc_now
from .export import write_candidates_yaml, write_report
from .importers import import_catalog_yaml, load_candidates
from .probe import ProbeResult, normalize_domain, probe_many_domains


COMMON_TACTICS: tuple[dict[str, Any], ...] = (
    {
        "tactic_id": "common_auth_prefixes",
        "name": "Common Auth Prefixes",
        "description": "Probe high-signal auth-related subdomains for Cloudflare seed domains.",
        "prefixes": ["auth", "login", "sso", "id", "accounts"],
    },
    {
        "tactic_id": "identity_variants",
        "name": "Identity Variants",
        "description": "Probe identity-specific naming patterns and OIDC-oriented subdomains.",
        "prefixes": ["identity", "oauth", "oidc", "signin", "connect"],
    },
    {
        "tactic_id": "historical_expansion",
        "name": "Historical Expansion",
        "description": "Reuse patterns observed in prior successful discoveries to expand into similar domains.",
        "prefixes": ["auth", "sso", "login", "id"],
    },
)


SAFE_INVESTIGATION_BUILTINS = {
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "reversed": reversed,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}


@dataclass(frozen=True)
class KnownMatch:
    source: str
    match_type: str
    identifier: str

    def as_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "match_type": self.match_type,
            "identifier": self.identifier,
        }


class WorkflowTools:
    """Tool surface for the OIDC hunter workflow."""

    _CLEARED_STATE_DEFAULTS: dict[str, Any] = {
        "current_batch_targets": [],
    }

    def __init__(self, config: AppConfig, run_id: str, run_dir: Path):
        self.config = config
        self.run_id = run_id
        self.run_dir = run_dir
        self.local_state: dict[str, Any] = {}

    def log(self, message: str) -> None:
        print(f"[oidc-hunter] {message}", flush=True)

    def record_fallback(self, stage_name: str, reason: str) -> None:
        fallback = {"stage": stage_name, "reason": reason}
        reasons = list(self.local_state.get("llm_fallback_reasons", []))
        reasons.append(fallback)
        self.local_state["llm_fallback_reasons"] = reasons
        if "llm_fallback_reason" not in self.local_state:
            self.local_state["llm_fallback_reason"] = f"{stage_name}: {reason}"

    def state_dict(self, tool_context=None) -> MutableMapping[str, Any]:
        return tool_context.state if tool_context is not None else self.local_state

    def _clear_state_key(self, state: MutableMapping[str, Any], key: str) -> None:
        missing = object()
        try:
            del state[key]
            return
        except KeyError:
            return
        except (AttributeError, NotImplementedError, TypeError):
            current = state.get(key, missing)
            if current is missing:
                return
            state[key] = self._CLEARED_STATE_DEFAULTS.get(key)

    def ensure_run_started(self) -> None:
        with database(self.config.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM runs WHERE run_id = ? LIMIT 1", (self.run_id,)
            ).fetchone()
            if not row:
                start_run(conn, self.run_id)

    def initialize_run(self, tool_context=None) -> dict[str, Any]:
        """Initialize the durable run state from catalog, candidates, and Cloudflare."""

        self.ensure_run_started()
        state = self.state_dict(tool_context)
        imports_dir = self.run_dir / "imports"
        planning_dir = self.run_dir / "planning"
        investigation_dir = self.run_dir / "investigation"
        probes_dir = self.run_dir / "probes"
        summaries_dir = self.run_dir / "summaries"
        retained_dir = self.run_dir / "retained"
        for path in (
            imports_dir,
            planning_dir,
            investigation_dir,
            probes_dir,
            summaries_dir,
            retained_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        catalog_snapshot_id = f"catalog-{self.run_id}"
        candidate_snapshot_id = f"candidates-{self.run_id}"
        catalog_artifact = imports_dir / "services.yaml"

        catalog_text = ""
        catalog_error = None
        try:
            response = httpx.get(
                self.config.catalog_url, timeout=30, follow_redirects=True
            )
            response.raise_for_status()
            catalog_text = response.text
            catalog_artifact.write_text(catalog_text, encoding="utf-8")
        except httpx.HTTPError as exc:
            catalog_error = f"{type(exc).__name__}: {exc}"

        cloudflare_error = None
        seed_batch: CloudflareSeedBatch
        try:
            seed_batch = fetch_cloudflare_seed_batch(self.config, imports_dir)
        except Exception as exc:
            cloudflare_error = f"{type(exc).__name__}: {exc}"
            seed_batch = CloudflareSeedBatch(
                domains=self.config.probe_domains,
                source="configured_probe_domains",
                artifact_ref=None,
                metadata={"count": len(self.config.probe_domains), "error_fallback": True},
            )

        with database(self.config.db_path) as conn:
            self._seed_tactics(conn)
            catalog_count = 0
            if catalog_text:
                catalog_count = import_catalog_yaml(
                    conn, self.run_id, catalog_text, snapshot_id=catalog_snapshot_id
                )
            conn.execute(
                """
                INSERT OR REPLACE INTO catalog_snapshots(
                    snapshot_id, run_id, source_url, artifact_ref, entry_count, imported_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    catalog_snapshot_id,
                    self.run_id,
                    self.config.catalog_url,
                    str(catalog_artifact) if catalog_text else None,
                    catalog_count,
                    utc_now(),
                ),
            )
            candidate_count = load_candidates(
                conn,
                self.config.candidates_path,
                run_id=self.run_id,
                snapshot_id=candidate_snapshot_id,
            )

        batch_sample = seed_batch.domains[: self.config.cloudflare_seed_sample_size]
        state["cloudflare_batch"] = {
            "domains": batch_sample,
            "source": seed_batch.source,
            "artifact_ref": seed_batch.artifact_ref,
            "metadata": seed_batch.metadata,
        }
        state["completed_tactics"] = []
        state["reviewed_clusters"] = []
        state["initialization_summary"] = {
            "run_id": self.run_id,
            "catalog_entries_imported": catalog_count,
            "catalog_error": catalog_error,
            "candidate_entries_imported": candidate_count,
            "cloudflare_seed_source": seed_batch.source,
            "cloudflare_seed_count": len(batch_sample),
            "cloudflare_error": cloudflare_error,
            "probe_timeout_seconds": self.config.probe_timeout_seconds,
        }
        self.log(
            "Initialization complete: "
            f"catalog={catalog_count}, candidates={candidate_count}, "
            f"seed_source={seed_batch.source}, seed_count={len(batch_sample)}"
        )
        return state["initialization_summary"]

    def load_run_context(self, tool_context=None) -> dict[str, Any]:
        """Load a compact run briefing from durable state."""

        state = self.state_dict(tool_context)
        with database(self.config.db_path) as conn:
            recent_runs = conn.execute(
                """
                SELECT run_id, status, started_at, completed_at, summary
                FROM runs
                WHERE run_id != ?
                ORDER BY started_at DESC
                LIMIT 5
                """,
                (self.run_id,),
            ).fetchall()
            active_candidates = conn.execute(
                """
                SELECT candidate_id, issuer, primary_domain, aliases_json
                FROM candidate_entries
                WHERE status = 'active'
                ORDER BY candidate_id
                LIMIT 20
                """
            ).fetchall()
            recent_rejections = conn.execute(
                """
                SELECT decision, domain, reason, created_at
                FROM candidate_decisions
                WHERE decision IN ('reject', 'needs_more_evidence')
                ORDER BY created_at DESC
                LIMIT 10
                """
            ).fetchall()
            tactic_scores = conn.execute(
                """
                SELECT tactic_id, successes, false_positives, last_score
                FROM strategy_tactics
                ORDER BY last_score DESC, successes DESC
                LIMIT 10
                """
            ).fetchall()
            coverage = conn.execute(
                """
                SELECT last_probe_classification, COUNT(*) AS count
                FROM domain_state
                GROUP BY last_probe_classification
                ORDER BY count DESC
                """
            ).fetchall()

        summary = {
            "recent_runs": [dict(row) for row in recent_runs],
            "active_candidate_count": len(active_candidates),
            "active_candidate_sample": [dict(row) for row in active_candidates[:5]],
            "recent_rejections": [dict(row) for row in recent_rejections],
            "tactic_scores": [dict(row) for row in tactic_scores],
            "coverage_summary": [dict(row) for row in coverage],
        }
        state["run_brief"] = summary
        return summary

    def load_cloudflare_batch_metadata(self, tool_context=None) -> dict[str, Any]:
        """Return a compact summary of the Cloudflare seed batch."""

        batch = self.state_dict(tool_context).get("cloudflare_batch", {})
        domains = batch.get("domains", [])
        return {
            "source": batch.get("source"),
            "count": len(domains),
            "sample": list(domains[:10]),
            "artifact_ref": batch.get("artifact_ref"),
            "metadata": batch.get("metadata", {}),
        }

    def sample_candidate_tlds(self, limit: int = 12, tool_context=None) -> dict[str, Any]:
        """Sample bounded domain seeds and TLD hints for planning."""

        batch = self.state_dict(tool_context).get("cloudflare_batch", {})
        domains = [domain for domain in batch.get("domains", []) if isinstance(domain, str)]
        sampled = domains[:limit]
        tlds = sorted({domain.rsplit(".", 1)[-1] for domain in sampled if "." in domain})
        return {"sampled_domains": sampled, "tlds": tlds, "count": len(sampled)}

    def load_heuristics_library(self, tool_context=None) -> dict[str, Any]:
        """Expose the available tactic library."""

        tactics = []
        for tactic in COMMON_TACTICS:
            tactic_copy = dict(tactic)
            tactic_copy["max_targets"] = max(
                10, self.config.investigation_target_limit // len(COMMON_TACTICS)
            )
            tactics.append(tactic_copy)
        return {"tactics": tactics}

    def record_run_plan(
        self,
        selected_tactics: list[str] | None = None,
        success_criteria: list[str] | None = None,
        notes: str = "",
        tool_context=None,
    ) -> dict[str, Any]:
        """Persist the run plan selected by the planner agent."""

        state = self.state_dict(tool_context)
        selected = selected_tactics or [COMMON_TACTICS[0]["tactic_id"], COMMON_TACTICS[1]["tactic_id"]]
        available = {tactic["tactic_id"]: tactic for tactic in COMMON_TACTICS}
        normalized = [tactic_id for tactic_id in selected if tactic_id in available]
        if not normalized:
            normalized = [COMMON_TACTICS[0]["tactic_id"], COMMON_TACTICS[1]["tactic_id"]]

        per_tactic_budget = max(10, self.config.investigation_target_limit // len(normalized))
        plan = {
            "run_id": self.run_id,
            "selected_tactics": normalized,
            "success_criteria": success_criteria
            or [
                "Find valid OIDC discovery documents not already known.",
                "Stop after bounded investigation and review iterations.",
            ],
            "target_budget": self.config.investigation_target_limit,
            "notes": notes.strip(),
            "tactics": [
                {
                    "tactic_id": tactic_id,
                    "description": available[tactic_id]["description"],
                    "prefixes": available[tactic_id]["prefixes"],
                    "max_targets": per_tactic_budget,
                }
                for tactic_id in normalized
            ],
        }
        plan_text = json.dumps(plan, indent=2, sort_keys=True)
        state["run_plan"] = plan
        plan_path = self.run_dir / "planning" / "run-plan.json"
        plan_path.write_text(plan_text, encoding="utf-8")
        with database(self.config.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO run_plans(run_id, plan_json, plan_text, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (self.run_id, plan_text, plan_text, utc_now()),
            )
        self.log(
            "Recorded run plan with tactics: "
            + ", ".join(normalized)
            + f" (budget={plan['target_budget']})"
        )
        return {
            "selected_tactics": normalized,
            "target_budget": plan["target_budget"],
            "plan_artifact": str(plan_path),
        }

    def load_plan_batch(self, tool_context=None) -> dict[str, Any]:
        """Load the next unexecuted tactic from the current plan."""

        state = self.state_dict(tool_context)
        plan = state.get("run_plan") or self._default_plan()
        completed = set(state.get("completed_tactics", []))
        for tactic in plan["tactics"]:
            if tactic["tactic_id"] not in completed:
                state["current_tactic"] = tactic
                return tactic
        state["current_tactic"] = None
        return {"status": "no_remaining_tactics"}

    def load_domain_history_summary(self, limit: int = 25, tool_context=None) -> dict[str, Any]:
        """Load a compact history of prior domain observations."""

        with database(self.config.db_path) as conn:
            rows = conn.execute(
                """
                SELECT domain, discovered_by_tactic, attempt_count, last_probe_classification
                FROM domain_state
                ORDER BY last_seen_run_id DESC, attempt_count DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return {"history": [dict(row) for row in rows]}

    def load_known_issuers_summary(self, limit: int = 25, tool_context=None) -> dict[str, Any]:
        """Load a compact known-set briefing for the investigator."""

        with database(self.config.db_path) as conn:
            catalog = conn.execute(
                """
                SELECT issuer_hint AS issuer, openid_configuration_url, jwks_uri
                FROM catalog_entries
                WHERE issuer_hint IS NOT NULL OR jwks_uri IS NOT NULL
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            candidates = conn.execute(
                """
                SELECT issuer, openid_configuration_url, jwks_uri, primary_domain
                FROM candidate_entries
                WHERE status = 'active'
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return {
            "catalog_known_sample": [dict(row) for row in catalog],
            "candidate_known_sample": [dict(row) for row in candidates],
        }

    def execute_investigation_python(self, code: str = "", tool_context=None) -> dict[str, Any]:
        """Execute bounded investigator-authored Python against compact inputs."""

        state = self.state_dict(tool_context)
        tactic = state.get("current_tactic") or self.load_plan_batch(tool_context)
        tactic_id = tactic.get("tactic_id")
        if not tactic_id:
            return {"status": "no_remaining_tactics"}

        seed_domains = list(state.get("cloudflare_batch", {}).get("domains", []))
        seed_domains = seed_domains[: self.config.cloudflare_seed_sample_size]

        with database(self.config.db_path) as conn:
            blocked_domains = sorted(self._known_domains(conn) | self._observed_domains(conn))
            prior_successes = [
                row["primary_domain"]
                for row in conn.execute(
                    """
                    SELECT primary_domain
                    FROM candidate_entries
                    WHERE status = 'active' AND primary_domain IS NOT NULL
                    ORDER BY last_seen_run_id DESC, candidate_id
                    LIMIT 25
                    """
                ).fetchall()
            ]
            batch_number = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM investigation_batches
                WHERE run_id = ? AND tactic_id = ?
                """,
                (self.run_id, tactic_id),
            ).fetchone()["count"]

        batch_ref = f"{self.run_id}-{tactic_id}-{batch_number + 1:02d}"
        investigation_dir = self.run_dir / "investigation" / batch_ref
        investigation_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "seed_domains": seed_domains,
            "common_prefixes": tactic.get("prefixes", []),
            "prior_successes": prior_successes,
            "blocked_domains": blocked_domains,
            "max_targets": tactic.get("max_targets", 20),
        }

        result = self._run_investigation_code(
            code=code,
            payload=payload,
            investigation_dir=investigation_dir,
        )
        if not result["targets"]:
            result = self._default_investigation_result(tactic, payload)

        result["targets"] = self._filter_candidate_targets(
            result["targets"],
            blocked_domains=set(blocked_domains),
            max_targets=tactic.get("max_targets", 20),
        )
        artifact_path = investigation_dir / "result.json"
        artifact_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

        with database(self.config.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO investigation_batches(
                    batch_ref, run_id, tactic_id, artifact_ref, status, notes,
                    target_count, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_ref,
                    self.run_id,
                    tactic_id,
                    str(artifact_path),
                    "ready_for_probe",
                    "\n".join(result["notes"]),
                    len(result["targets"]),
                    utc_now(),
                ),
            )

        state["current_batch_ref"] = batch_ref
        state["current_batch_targets"] = result["targets"]
        state["current_batch_notes"] = result["notes"]
        self.log(
            f"Prepared investigation batch {batch_ref} with "
            f"{len(result['targets'])} targets for tactic {tactic_id}."
        )
        return {
            "batch_ref": batch_ref,
            "target_count": len(result["targets"]),
            "sample_targets": result["targets"][:10],
            "notes": result["notes"][:5],
            "artifact_ref": str(artifact_path),
        }

    def record_investigation_output(
        self, batch_ref: str | None = None, summary: str = "", tool_context=None
    ) -> dict[str, Any]:
        """Mark a generated investigation batch as recorded."""

        ref = batch_ref or self.state_dict(tool_context).get("current_batch_ref")
        if not ref:
            return {"status": "no_batch"}
        with database(self.config.db_path) as conn:
            conn.execute(
                """
                UPDATE investigation_batches
                SET status = ?, notes = COALESCE(notes, '') || ?
                WHERE batch_ref = ?
                """,
                ("recorded", f"\n{summary.strip()}" if summary.strip() else "", ref),
            )
        return {"batch_ref": ref, "status": "recorded"}

    async def probe_oidc_candidates(
        self,
        batch_ref: str | None = None,
        concurrency: int | None = None,
        timeout: float | None = None,
        tool_context=None,
    ) -> dict[str, Any]:
        """Probe candidate domains and persist summarized outcomes."""

        state = self.state_dict(tool_context)
        ref = batch_ref or state.get("current_batch_ref")
        if not ref:
            return {"status": "no_batch"}

        with database(self.config.db_path) as conn:
            batch_row = conn.execute(
                """
                SELECT tactic_id, artifact_ref
                FROM investigation_batches
                WHERE batch_ref = ?
                """,
                (ref,),
            ).fetchone()
        if not batch_row:
            return {"status": "missing_batch", "batch_ref": ref}

        targets = list(state.get("current_batch_targets", []))
        if not targets and batch_row["artifact_ref"]:
            payload = json.loads(Path(batch_row["artifact_ref"]).read_text(encoding="utf-8"))
            targets = payload.get("targets", [])
        if not targets:
            return {"status": "empty_batch", "batch_ref": ref}

        results = await probe_many_domains(
            targets,
            timeout_seconds=timeout or self.config.probe_timeout_seconds,
            concurrency=concurrency or self.config.probe_concurrency,
        )
        summary_path = self.run_dir / "probes" / f"{ref}.json"
        rendered_results = [result.__dict__ for result in results]
        summary_path.write_text(json.dumps(rendered_results, indent=2), encoding="utf-8")

        cluster_ids: list[str] = []
        valid_results = [result for result in results if result.classification == "valid_oidc" and result.issuer]
        with database(self.config.db_path) as conn:
            for result in results:
                conn.execute(
                    """
                    INSERT INTO probe_results(
                        run_id, batch_ref, domain, status, classification,
                        openid_configuration_url, issuer, jwks_uri, error,
                        evidence_artifact_ref, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.run_id,
                        ref,
                        result.domain,
                        result.status,
                        result.classification,
                        result.openid_configuration_url,
                        result.issuer,
                        result.jwks_uri,
                        result.error,
                        str(summary_path) if self.config.keep_probe_artifacts or result.classification == "valid_oidc" else None,
                        utc_now(),
                    ),
                )
                self._upsert_domain_state(
                    conn,
                    run_id=self.run_id,
                    tactic_id=batch_row["tactic_id"],
                    result=result,
                    artifact_ref=str(summary_path),
                )

            cluster_ids = self._upsert_clusters_from_results(conn, valid_results)
            conn.execute(
                """
                UPDATE investigation_batches
                SET status = ?, completed_at = ?
                WHERE batch_ref = ?
                """,
                ("probed", utc_now(), ref),
            )

        probe_summary = {
            "batch_ref": ref,
            "target_count": len(targets),
            "valid_oidc_count": len(valid_results),
            "cluster_ids": cluster_ids,
            "classifications": self._classification_histogram(results),
            "artifact_ref": str(summary_path),
        }
        state["last_probe_summary"] = probe_summary
        self.log(
            f"Probed batch {ref}: targets={len(targets)}, valid_oidc={len(valid_results)}, "
            f"classifications={probe_summary['classifications']}"
        )
        return probe_summary

    def load_investigation_progress(self, tool_context=None) -> dict[str, Any]:
        """Summarize investigation loop progress."""

        state = self.state_dict(tool_context)
        plan = state.get("run_plan") or self._default_plan()
        completed = set(state.get("completed_tactics", []))
        with database(self.config.db_path) as conn:
            pending_clusters = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM issuer_clusters
                WHERE run_id = ? AND status = 'pending_review'
                """,
                (self.run_id,),
            ).fetchone()["count"]
        return {
            "selected_tactics": [item["tactic_id"] for item in plan["tactics"]],
            "completed_tactics": sorted(completed),
            "remaining_tactics": [
                item["tactic_id"]
                for item in plan["tactics"]
                if item["tactic_id"] not in completed
            ],
            "pending_review_clusters": pending_clusters,
        }

    def mark_tactic_outcome(
        self, tactic_id: str | None = None, outcome: str = "completed", tool_context=None
    ) -> dict[str, Any]:
        """Record that a tactic iteration finished."""

        state = self.state_dict(tool_context)
        current = tactic_id or (state.get("current_tactic") or {}).get("tactic_id")
        if not current:
            return {"status": "no_tactic"}
        completed = list(state.get("completed_tactics", []))
        if current not in completed:
            completed.append(current)
        state["completed_tactics"] = completed
        with database(self.config.db_path) as conn:
            conn.execute(
                """
                UPDATE strategy_tactics
                SET last_used_run_id = ?
                WHERE tactic_id = ?
                """,
                (self.run_id, current),
            )
        self._clear_state_key(state, "current_tactic")
        self._clear_state_key(state, "current_batch_ref")
        self._clear_state_key(state, "current_batch_targets")
        return {"tactic_id": current, "outcome": outcome}

    def load_next_candidate_cluster(self, tool_context=None) -> dict[str, Any]:
        """Load the next candidate cluster that still needs review."""

        state = self.state_dict(tool_context)
        reviewed = set(state.get("reviewed_clusters", []))
        with database(self.config.db_path) as conn:
            rows = conn.execute(
                """
                SELECT cluster_id, issuer, canonical_domain, domains_json, known_match_json, status
                FROM issuer_clusters
                WHERE run_id = ? AND status = 'pending_review'
                ORDER BY created_at ASC
                """,
                (self.run_id,),
            ).fetchall()
        for row in rows:
            if row["cluster_id"] not in reviewed:
                state["current_cluster_id"] = row["cluster_id"]
                domains = json.loads(row["domains_json"] or "[]")
                return {
                    "cluster_id": row["cluster_id"],
                    "issuer": row["issuer"],
                    "canonical_domain": row["canonical_domain"],
                    "domains": domains,
                    "known_match": json.loads(row["known_match_json"] or "{}"),
                }
        state["current_cluster_id"] = None
        return {"status": "no_pending_clusters"}

    def load_cluster_evidence(self, cluster_id: str | None = None, tool_context=None) -> dict[str, Any]:
        """Load compact evidence for one candidate cluster."""

        state = self.state_dict(tool_context)
        ref = cluster_id or state.get("current_cluster_id")
        if not ref:
            return {"status": "no_cluster"}
        with database(self.config.db_path) as conn:
            cluster = conn.execute(
                """
                SELECT *
                FROM issuer_clusters
                WHERE cluster_id = ?
                """,
                (ref,),
            ).fetchone()
            probes = conn.execute(
                """
                SELECT domain, status, classification, openid_configuration_url, issuer, jwks_uri
                FROM probe_results
                WHERE run_id = ? AND issuer = ?
                ORDER BY created_at ASC
                LIMIT 25
                """,
                (self.run_id, cluster["issuer"]),
            ).fetchall()
        domains = json.loads(cluster["domains_json"] or "[]")
        issuer_host = self._host_from_url(cluster["issuer"])
        suspicious = issuer_host not in set(domains) and all(
            not issuer_host.endswith(f".{domain}") and not domain.endswith(f".{issuer_host}")
            for domain in domains
        )
        return {
            "cluster_id": ref,
            "issuer": cluster["issuer"],
            "canonical_domain": cluster["canonical_domain"],
            "domains": domains,
            "known_match": json.loads(cluster["known_match_json"] or "{}"),
            "suspicious_domain_relationship": suspicious,
            "probe_sample": [dict(row) for row in probes],
        }

    def record_analysis_notes(
        self, notes: str, cluster_id: str | None = None, tool_context=None
    ) -> dict[str, Any]:
        """Append review notes to a cluster."""

        ref = cluster_id or self.state_dict(tool_context).get("current_cluster_id")
        if not ref:
            return {"status": "no_cluster"}
        with database(self.config.db_path) as conn:
            conn.execute(
                """
                UPDATE issuer_clusters
                SET notes = COALESCE(notes, '') || ?, updated_at = ?
                WHERE cluster_id = ?
                """,
                (f"\n{notes.strip()}" if notes.strip() else "", utc_now(), ref),
            )
        return {"cluster_id": ref, "status": "noted"}

    def request_followup_probe(
        self, cluster_id: str, probe_type: str, tool_context=None
    ) -> dict[str, Any]:
        """Mark a cluster as needing more evidence."""

        return self.mark_cluster_for_followup(
            actions=f"Requested follow-up probe: {probe_type}",
            cluster_id=cluster_id,
            tool_context=tool_context,
        )

    def mark_cluster_rejected(
        self, reason: str, cluster_id: str | None = None, tool_context=None
    ) -> dict[str, Any]:
        """Reject a candidate cluster."""

        ref = cluster_id or self.state_dict(tool_context).get("current_cluster_id")
        if not ref:
            return {"status": "no_cluster"}
        with database(self.config.db_path) as conn:
            cluster = conn.execute(
                "SELECT issuer, canonical_domain FROM issuer_clusters WHERE cluster_id = ?",
                (ref,),
            ).fetchone()
            conn.execute(
                """
                UPDATE issuer_clusters
                SET status = 'rejected', notes = COALESCE(notes, '') || ?, updated_at = ?
                WHERE cluster_id = ?
                """,
                (f"\nRejected: {reason}", utc_now(), ref),
            )
            conn.execute(
                """
                INSERT INTO candidate_decisions(
                    run_id, cluster_id, issuer, domain, decision, reason, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.run_id,
                    ref,
                    cluster["issuer"],
                    cluster["canonical_domain"],
                    "reject",
                    reason,
                    utc_now(),
                ),
            )
        self._mark_cluster_reviewed(ref, tool_context)
        self.log(f"Rejected cluster {ref}: {reason}")
        return {"cluster_id": ref, "decision": "reject"}

    def mark_cluster_for_followup(
        self, actions: str, cluster_id: str | None = None, tool_context=None
    ) -> dict[str, Any]:
        """Defer a cluster for later follow-up."""

        ref = cluster_id or self.state_dict(tool_context).get("current_cluster_id")
        if not ref:
            return {"status": "no_cluster"}
        with database(self.config.db_path) as conn:
            cluster = conn.execute(
                "SELECT issuer, canonical_domain FROM issuer_clusters WHERE cluster_id = ?",
                (ref,),
            ).fetchone()
            conn.execute(
                """
                UPDATE issuer_clusters
                SET status = 'needs_more_evidence', notes = COALESCE(notes, '') || ?, updated_at = ?
                WHERE cluster_id = ?
                """,
                (f"\nNeeds more evidence: {actions}", utc_now(), ref),
            )
            conn.execute(
                """
                INSERT INTO candidate_decisions(
                    run_id, cluster_id, issuer, domain, decision, reason, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.run_id,
                    ref,
                    cluster["issuer"],
                    cluster["canonical_domain"],
                    "needs_more_evidence",
                    actions,
                    utc_now(),
                ),
            )
        self._mark_cluster_reviewed(ref, tool_context)
        self.log(f"Deferred cluster {ref} for follow-up: {actions}")
        return {"cluster_id": ref, "decision": "needs_more_evidence"}

    def promote_cluster(
        self,
        promotion_type: str = "promote_as_new_candidate",
        canonical_issuer: str = "",
        cluster_id: str | None = None,
        tool_context=None,
    ) -> dict[str, Any]:
        """Promote a candidate cluster into the exported candidate set."""

        ref = cluster_id or self.state_dict(tool_context).get("current_cluster_id")
        if not ref:
            return {"status": "no_cluster"}
        with database(self.config.db_path) as conn:
            cluster = conn.execute(
                "SELECT * FROM issuer_clusters WHERE cluster_id = ?",
                (ref,),
            ).fetchone()
            domains = json.loads(cluster["domains_json"] or "[]")
            aliases = sorted(domain for domain in domains if domain != cluster["canonical_domain"])
            existing = conn.execute(
                """
                SELECT candidate_id, aliases_json
                FROM candidate_entries
                WHERE status = 'active' AND (issuer = ? OR jwks_uri = ?)
                LIMIT 1
                """,
                (cluster["issuer"], cluster["jwks_uri"]),
            ).fetchone()
            decision = "new_candidate"
            if promotion_type == "attach_as_alternative_domain" or existing:
                candidate_id = existing["candidate_id"] if existing else cluster["canonical_domain"]
                merged_aliases = set(aliases)
                if existing:
                    merged_aliases.update(json.loads(existing["aliases_json"] or "[]"))
                    merged_aliases.add(cluster["canonical_domain"])
                conn.execute(
                    """
                    UPDATE candidate_entries
                    SET aliases_json = ?, last_seen_run_id = ?, review_notes = COALESCE(review_notes, '') || ?
                    WHERE candidate_id = ?
                    """,
                    (
                        json.dumps(sorted(merged_aliases)),
                        self.run_id,
                        f"\nAlternative domains updated from cluster {ref}",
                        candidate_id,
                    ),
                )
                decision = "alternative_domain"
            else:
                candidate_id = canonical_issuer.strip() or cluster["canonical_domain"]
                conn.execute(
                    """
                    INSERT INTO candidate_entries(
                        candidate_id, name, issuer, openid_configuration_url, jwks_uri,
                        primary_domain, aliases_json, status, source,
                        first_seen_run_id, last_seen_run_id, review_notes
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 'discovered', ?, ?, ?)
                    ON CONFLICT(candidate_id) DO UPDATE SET
                        name = excluded.name,
                        issuer = excluded.issuer,
                        openid_configuration_url = excluded.openid_configuration_url,
                        jwks_uri = excluded.jwks_uri,
                        primary_domain = excluded.primary_domain,
                        aliases_json = excluded.aliases_json,
                        status = 'active',
                        source = 'discovered',
                        last_seen_run_id = excluded.last_seen_run_id,
                        review_notes = excluded.review_notes
                    """,
                    (
                        candidate_id,
                        cluster["canonical_domain"],
                        cluster["issuer"],
                        cluster["openid_configuration_url"],
                        cluster["jwks_uri"],
                        cluster["canonical_domain"],
                        json.dumps(aliases),
                        self.run_id,
                        self.run_id,
                        f"Promoted from cluster {ref}",
                    ),
                )
            conn.execute(
                """
                UPDATE issuer_clusters
                SET status = 'promoted', updated_at = ?
                WHERE cluster_id = ?
                """,
                (utc_now(), ref),
            )
            conn.execute(
                """
                INSERT INTO candidate_decisions(
                    run_id, cluster_id, issuer, domain, decision, reason, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.run_id,
                    ref,
                    cluster["issuer"],
                    cluster["canonical_domain"],
                    decision,
                    f"Promotion type: {promotion_type}",
                    utc_now(),
                ),
            )
        self._mark_cluster_reviewed(ref, tool_context)
        self.log(f"Promoted cluster {ref} with decision {decision}.")
        return {"cluster_id": ref, "decision": decision}

    def load_candidates_for_export(self, tool_context=None) -> dict[str, Any]:
        """Summarize the candidates that will be exported."""

        with database(self.config.db_path) as conn:
            rows = conn.execute(
                """
                SELECT candidate_id, issuer, primary_domain, aliases_json
                FROM candidate_entries
                WHERE status = 'active'
                ORDER BY candidate_id
                """
            ).fetchall()
        return {
            "count": len(rows),
            "sample": [dict(row) for row in rows[:10]],
        }

    def write_candidates_yaml(self, tool_context=None) -> dict[str, Any]:
        """Render the canonical candidates file from SQLite."""

        with database(self.config.db_path) as conn:
            exported_count = write_candidates_yaml(conn, self.config.candidates_path)
        summary = {"exported_count": exported_count, "path": str(self.config.candidates_path)}
        self.state_dict(tool_context)["candidates_update_summary"] = summary
        return summary

    def load_run_outcome_summary(self, tool_context=None) -> dict[str, Any]:
        """Load a compact end-of-run summary for reporting."""

        with database(self.config.db_path) as conn:
            promoted = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM candidate_decisions
                WHERE run_id = ? AND decision IN ('new_candidate', 'alternative_domain')
                """,
                (self.run_id,),
            ).fetchone()["count"]
            rejected = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM candidate_decisions
                WHERE run_id = ? AND decision = 'reject'
                """,
                (self.run_id,),
            ).fetchone()["count"]
            followup = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM candidate_decisions
                WHERE run_id = ? AND decision = 'needs_more_evidence'
                """,
                (self.run_id,),
            ).fetchone()["count"]
            valid = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM probe_results
                WHERE run_id = ? AND classification = 'valid_oidc'
                """,
                (self.run_id,),
            ).fetchone()["count"]
            active_candidates = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM candidate_entries
                WHERE status = 'active'
                """
            ).fetchone()["count"]
        return {
            "run_id": self.run_id,
            "valid_oidc_count": valid,
            "promoted_count": promoted,
            "rejected_count": rejected,
            "needs_more_evidence_count": followup,
            "active_candidate_count": active_candidates,
        }

    def append_lessons_learned(
        self, entries: list[str] | None = None, tool_context=None
    ) -> dict[str, Any]:
        """Persist durable lessons for later runs."""

        state = self.state_dict(tool_context)
        outcome = self.load_run_outcome_summary(tool_context)
        lessons = entries or [
            f"Run {self.run_id} found {outcome['valid_oidc_count']} valid OIDC discovery documents.",
            f"Run {self.run_id} promoted {outcome['promoted_count']} candidate clusters.",
        ]
        lessons_path = self.config.lessons_dir / "LESSONS_LEARNED.md"
        with database(self.config.db_path) as conn:
            for lesson in lessons:
                conn.execute(
                    """
                    INSERT INTO lessons_learned(run_id, category, lesson, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (self.run_id, "run_summary", lesson, utc_now()),
                )
        with lessons_path.open("a", encoding="utf-8") as handle:
            if lessons_path.stat().st_size == 0:
                handle.write("# Lessons Learned\n\n")
            handle.write(f"## {self.run_id}\n\n")
            for lesson in lessons:
                handle.write(f"- {lesson}\n")
            handle.write("\n")
        state["lessons_summary"] = {"entries": lessons, "path": str(lessons_path)}
        return state["lessons_summary"]

    def update_strategy_scores(self, tool_context=None) -> dict[str, Any]:
        """Update tactic scorecards based on the current run."""

        with database(self.config.db_path) as conn:
            for tactic in COMMON_TACTICS:
                tactic_id = tactic["tactic_id"]
                used_batches = conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM investigation_batches
                    WHERE run_id = ? AND tactic_id = ?
                    """,
                    (self.run_id, tactic_id),
                ).fetchone()["count"]
                if not used_batches:
                    continue
                successes = conn.execute(
                    """
                    SELECT COUNT(DISTINCT cluster_id) AS count
                    FROM candidate_decisions
                    WHERE run_id = ? AND decision IN ('new_candidate', 'alternative_domain')
                      AND cluster_id IN (
                        SELECT cluster_id
                        FROM issuer_clusters
                        WHERE run_id = ? AND canonical_domain IN (
                          SELECT domain
                          FROM probe_results
                          WHERE run_id = ?
                            AND batch_ref IN (
                              SELECT batch_ref
                              FROM investigation_batches
                              WHERE run_id = ? AND tactic_id = ?
                            )
                        )
                      )
                    """,
                    (self.run_id, self.run_id, self.run_id, self.run_id, tactic_id),
                ).fetchone()["count"]
                false_positives = conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM probe_results
                    WHERE run_id = ? AND classification NOT IN ('valid_oidc')
                      AND batch_ref IN (
                        SELECT batch_ref
                        FROM investigation_batches
                        WHERE run_id = ? AND tactic_id = ?
                      )
                    """,
                    (self.run_id, self.run_id, tactic_id),
                ).fetchone()["count"]
                score = successes - (false_positives / max(1, used_batches))
                conn.execute(
                    """
                    UPDATE strategy_tactics
                    SET historical_runs = historical_runs + 1,
                        successes = successes + ?,
                        false_positives = false_positives + ?,
                        last_used_run_id = ?,
                        last_score = ?
                    WHERE tactic_id = ?
                    """,
                    (successes, false_positives, self.run_id, score, tactic_id),
                )
            rows = conn.execute(
                """
                SELECT tactic_id, successes, false_positives, last_score
                FROM strategy_tactics
                ORDER BY last_score DESC, tactic_id
                """
            ).fetchall()
        return {"scorecard": [dict(row) for row in rows]}

    def write_run_report(self, markdown: str = "", tool_context=None) -> dict[str, Any]:
        """Write the durable run report."""

        state = self.state_dict(tool_context)
        outcome = self.load_run_outcome_summary(tool_context)
        report_path = self.config.reports_dir / f"{self.run_id}.md"
        if not markdown.strip():
            markdown = self._build_report_markdown(state=state, outcome=outcome)
        write_report(report_path, markdown)
        state["run_report"] = {"path": str(report_path)}
        return state["run_report"]

    def close_current_run(self, status: str = "completed", tool_context=None) -> dict[str, Any]:
        """Finalize the durable run record."""

        outcome = self.load_run_outcome_summary(tool_context)
        summary = (
            f"valid_oidc={outcome['valid_oidc_count']}, "
            f"promoted={outcome['promoted_count']}, "
            f"rejected={outcome['rejected_count']}, "
            f"followup={outcome['needs_more_evidence_count']}"
        )
        with database(self.config.db_path) as conn:
            close_run(conn, self.run_id, status, summary)
        return {"run_id": self.run_id, "status": status, "summary": summary}

    async def deterministic_run(self) -> dict[str, Any]:
        """Run the same workflow without ADK when no LLM is configured."""

        self.initialize_run()
        self.load_run_context()
        self.deterministic_plan()
        await self.deterministic_investigation()
        self.deterministic_review()
        return self.finalize_run()

    def deterministic_plan(self) -> dict[str, Any]:
        """Persist the default bounded run plan."""

        self.log("Running deterministic planning stage.")
        return self.record_run_plan()

    async def deterministic_investigation(self) -> None:
        """Run the bounded deterministic investigation loop."""

        self.log("Running deterministic investigation stage.")
        for _ in range(self.config.investigation_iterations):
            tactic = self.load_plan_batch()
            if tactic.get("status") == "no_remaining_tactics":
                self.log("No remaining tactics to investigate.")
                break
            probe_batch = self.execute_investigation_python()
            if probe_batch.get("status") == "no_remaining_tactics":
                self.log("Investigation batch generation reported no remaining tactics.")
                break
            self.record_investigation_output(summary="Deterministic fallback execution.")
            await self.probe_oidc_candidates()
            self.mark_tactic_outcome()

    def deterministic_review(self) -> None:
        """Run the deterministic candidate review loop."""

        self.log("Running deterministic review stage.")
        for _ in range(self.config.review_iterations):
            cluster = self.load_next_candidate_cluster()
            if cluster.get("status") == "no_pending_clusters":
                self.log("No pending clusters left to review.")
                break
            evidence = self.load_cluster_evidence(cluster["cluster_id"])
            known_match = evidence.get("known_match")
            if known_match:
                self.mark_cluster_rejected("Already present in the known set.")
            elif evidence.get("suspicious_domain_relationship"):
                self.mark_cluster_for_followup("Issuer-domain relationship needs more evidence.")
            else:
                self.promote_cluster(
                    promotion_type="promote_as_new_candidate"
                    if len(evidence.get("domains", [])) <= 1
                    else "attach_as_alternative_domain"
                )

    def finalize_run(self) -> dict[str, Any]:
        """Render final artifacts and close the run."""

        self.log("Finalizing run artifacts.")
        self.write_candidates_yaml()
        self.append_lessons_learned()
        self.update_strategy_scores()
        self.write_run_report()
        outcome = self.close_current_run()
        self.log(f"Run finalized with summary: {outcome['summary']}")
        return outcome

    def has_recorded_run_plan(self) -> bool:
        with database(self.config.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM run_plans WHERE run_id = ? LIMIT 1",
                (self.run_id,),
            ).fetchone()
        return row is not None

    def has_investigation_activity(self) -> bool:
        with database(self.config.db_path) as conn:
            batch = conn.execute(
                "SELECT 1 FROM investigation_batches WHERE run_id = ? LIMIT 1",
                (self.run_id,),
            ).fetchone()
            probe = conn.execute(
                "SELECT 1 FROM probe_results WHERE run_id = ? LIMIT 1",
                (self.run_id,),
            ).fetchone()
        return batch is not None or probe is not None

    def has_review_activity(self) -> bool:
        with database(self.config.db_path) as conn:
            decision = conn.execute(
                "SELECT 1 FROM candidate_decisions WHERE run_id = ? LIMIT 1",
                (self.run_id,),
            ).fetchone()
        return decision is not None

    def _default_plan(self) -> dict[str, Any]:
        return {
            "tactics": [
                {
                    "tactic_id": item["tactic_id"],
                    "description": item["description"],
                    "prefixes": item["prefixes"],
                    "max_targets": max(
                        10, self.config.investigation_target_limit // len(COMMON_TACTICS)
                    ),
                }
                for item in COMMON_TACTICS[:2]
            ]
        }

    def _seed_tactics(self, conn) -> None:
        for tactic in COMMON_TACTICS:
            conn.execute(
                """
                INSERT OR IGNORE INTO strategy_tactics(
                    tactic_id, name, description
                )
                VALUES (?, ?, ?)
                """,
                (tactic["tactic_id"], tactic["name"], tactic["description"]),
            )

    def _run_investigation_code(
        self, code: str, payload: dict[str, Any], investigation_dir: Path
    ) -> dict[str, Any]:
        if not code.strip():
            return {"targets": [], "notes": ["No investigator-authored code supplied."]}

        input_path = investigation_dir / "input.json"
        output_path = investigation_dir / "output.json"
        wrapper_path = investigation_dir / "runner.py"
        input_path.write_text(json.dumps({"code": code, "payload": payload}), encoding="utf-8")
        wrapper_path.write_text(
            textwrap.dedent(
                f"""
                import json
                import sys

                SAFE_NAMES = {list(SAFE_INVESTIGATION_BUILTINS)}
                BUILTINS_SOURCE = __builtins__
                if isinstance(BUILTINS_SOURCE, dict):
                    SAFE_BUILTINS = {{name: BUILTINS_SOURCE[name] for name in SAFE_NAMES}}
                else:
                    SAFE_BUILTINS = {{name: getattr(BUILTINS_SOURCE, name) for name in SAFE_NAMES}}

                data = json.load(open(sys.argv[1], encoding="utf-8"))
                payload = data["payload"]
                code = data["code"]
                targets = []
                notes = []

                def emit_target(value):
                    value = str(value).strip().lower()
                    if value:
                        targets.append(value)

                def record_note(value):
                    value = str(value).strip()
                    if value:
                        notes.append(value)

                env = {{
                    "__builtins__": SAFE_BUILTINS,
                    "seed_domains": payload["seed_domains"],
                    "common_prefixes": payload["common_prefixes"],
                    "prior_successes": payload["prior_successes"],
                    "blocked_domains": set(payload["blocked_domains"]),
                    "max_targets": payload["max_targets"],
                    "emit_target": emit_target,
                    "record_note": record_note,
                }}

                try:
                    exec(code, env, {{}})
                    result = {{"targets": targets, "notes": notes}}
                except Exception as exc:
                    result = {{"targets": [], "notes": [f"Investigation code failed: {{type(exc).__name__}}: {{exc}}"]}}

                with open(sys.argv[2], "w", encoding="utf-8") as handle:
                    json.dump(result, handle)
                """
            ),
            encoding="utf-8",
        )

        subprocess.run(
            [sys.executable, "-I", str(wrapper_path), str(input_path), str(output_path)],
            cwd=investigation_dir,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if not output_path.exists():
            return {"targets": [], "notes": ["Investigation subprocess produced no output."]}
        return json.loads(output_path.read_text(encoding="utf-8"))

    def _default_investigation_result(
        self, tactic: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        targets: list[str] = []
        for domain in payload["seed_domains"]:
            targets.append(domain)
            for prefix in tactic.get("prefixes", []):
                targets.append(f"{prefix}.{domain}")
        notes = [
            f"Used built-in fallback for tactic {tactic['tactic_id']}.",
            f"Generated targets from {len(payload['seed_domains'])} seed domains.",
        ]
        if tactic["tactic_id"] == "historical_expansion":
            for success in payload["prior_successes"]:
                for prefix in tactic.get("prefixes", []):
                    targets.append(f"{prefix}.{success}")
        return {"targets": targets, "notes": notes}

    def _filter_candidate_targets(
        self, targets: list[str], blocked_domains: set[str], max_targets: int
    ) -> list[str]:
        filtered: list[str] = []
        seen: set[str] = set()
        for target in targets:
            normalized = normalize_domain(target)
            if normalized in seen or normalized in blocked_domains:
                continue
            if not normalized or "." not in normalized or normalized.count(".") > 5:
                continue
            seen.add(normalized)
            filtered.append(normalized)
            if len(filtered) >= max_targets:
                break
        return filtered

    def _known_domains(self, conn) -> set[str]:
        domains: set[str] = set()
        for row in conn.execute(
            """
            SELECT issuer_hint, openid_configuration_url, jwks_uri, aliases_json
            FROM catalog_entries
            """
        ).fetchall():
            domains.update(self._domains_from_row_like(dict(row)))
        for row in conn.execute(
            """
            SELECT issuer, openid_configuration_url, jwks_uri, primary_domain, aliases_json
            FROM candidate_entries
            WHERE status = 'active'
            """
        ).fetchall():
            domains.update(self._domains_from_row_like(dict(row)))
        return domains

    def _observed_domains(self, conn) -> set[str]:
        return {
            row["domain"]
            for row in conn.execute("SELECT domain FROM domain_state").fetchall()
            if row["domain"]
        }

    def _domains_from_row_like(self, row: dict[str, Any]) -> set[str]:
        domains: set[str] = set()
        for key in (
            "issuer_hint",
            "issuer",
            "openid_configuration_url",
            "jwks_uri",
            "primary_domain",
        ):
            value = row.get(key)
            if isinstance(value, str):
                host = self._host_from_url(value)
                if host:
                    domains.add(host)
        aliases = row.get("aliases_json")
        if isinstance(aliases, str):
            for alias in json.loads(aliases or "[]"):
                host = self._host_from_url(alias)
                if host:
                    domains.add(host)
        return domains

    def _host_from_url(self, value: str | None) -> str:
        if not value:
            return ""
        parsed = urlparse(value if "://" in value else f"https://{value}")
        return parsed.netloc.lower() if parsed.netloc else value.lower().strip("/")

    def _upsert_domain_state(self, conn, run_id: str, tactic_id: str, result: ProbeResult, artifact_ref: str | None) -> None:
        conn.execute(
            """
            INSERT INTO domain_state(
                domain, discovered_by_tactic, first_seen_run_id, last_seen_run_id,
                attempt_count, last_probe_status, last_probe_classification,
                last_openid_configuration_url, last_issuer, last_jwks_uri,
                needs_followup, artifact_ref
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                discovered_by_tactic = excluded.discovered_by_tactic,
                last_seen_run_id = excluded.last_seen_run_id,
                attempt_count = domain_state.attempt_count + 1,
                last_probe_status = excluded.last_probe_status,
                last_probe_classification = excluded.last_probe_classification,
                last_openid_configuration_url = excluded.last_openid_configuration_url,
                last_issuer = excluded.last_issuer,
                last_jwks_uri = excluded.last_jwks_uri,
                needs_followup = excluded.needs_followup,
                artifact_ref = excluded.artifact_ref
            """,
            (
                result.domain,
                tactic_id,
                run_id,
                run_id,
                1,
                result.status,
                result.classification,
                result.openid_configuration_url,
                result.issuer,
                result.jwks_uri,
                0 if result.classification == "valid_oidc" else 1,
                artifact_ref,
            ),
        )

    def _upsert_clusters_from_results(self, conn, results: list[ProbeResult]) -> list[str]:
        grouped: dict[str, list[ProbeResult]] = {}
        for result in results:
            key = hashlib.sha256(
                f"{result.issuer}|{result.jwks_uri or ''}".encode("utf-8")
            ).hexdigest()[:16]
            grouped.setdefault(key, []).append(result)

        cluster_ids: list[str] = []
        for key, group in grouped.items():
            canonical_domain = sorted({item.domain for item in group}, key=lambda value: (value.count("."), value))[0]
            cluster_id = f"{self.run_id}-{key}"
            domains = sorted({item.domain for item in group})
            representative = group[0]
            known_match = self._find_known_match(
                conn=conn,
                issuer=representative.issuer,
                openid_configuration_url=representative.openid_configuration_url,
                jwks_uri=representative.jwks_uri,
                domains=domains,
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO issuer_clusters(
                    cluster_id, run_id, issuer, jwks_uri, openid_configuration_url,
                    canonical_domain, domains_json, known_match_json, status, notes,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cluster_id,
                    self.run_id,
                    representative.issuer,
                    representative.jwks_uri,
                    representative.openid_configuration_url,
                    canonical_domain,
                    json.dumps(domains),
                    json.dumps(known_match.as_dict() if known_match else {}),
                    "pending_review",
                    "",
                    utc_now(),
                    utc_now(),
                ),
            )
            cluster_ids.append(cluster_id)
        return cluster_ids

    def _find_known_match(
        self,
        conn,
        issuer: str | None,
        openid_configuration_url: str,
        jwks_uri: str | None,
        domains: list[str],
    ) -> KnownMatch | None:
        if issuer:
            row = conn.execute(
                """
                SELECT service_id
                FROM catalog_entries
                WHERE issuer_hint = ?
                LIMIT 1
                """,
                (issuer,),
            ).fetchone()
            if row:
                return KnownMatch("official_catalog", "issuer", row["service_id"] or issuer)
        if jwks_uri:
            row = conn.execute(
                """
                SELECT service_id
                FROM catalog_entries
                WHERE jwks_uri = ?
                LIMIT 1
                """,
                (jwks_uri,),
            ).fetchone()
            if row:
                return KnownMatch("official_catalog", "jwks_uri", row["service_id"] or jwks_uri)
        row = conn.execute(
            """
            SELECT candidate_id, issuer, jwks_uri, openid_configuration_url, primary_domain, aliases_json
            FROM candidate_entries
            WHERE status = 'active'
            """
        ).fetchall()
        for candidate in row:
            if issuer and candidate["issuer"] == issuer:
                return KnownMatch("candidate_file", "issuer", candidate["candidate_id"])
            if jwks_uri and candidate["jwks_uri"] == jwks_uri:
                return KnownMatch("candidate_file", "jwks_uri", candidate["candidate_id"])
            if candidate["openid_configuration_url"] == openid_configuration_url:
                return KnownMatch("candidate_file", "openid_configuration_url", candidate["candidate_id"])
            candidate_domains = self._domains_from_row_like(dict(candidate))
            if set(domains) & candidate_domains:
                return KnownMatch("candidate_file", "domain", candidate["candidate_id"])
        for domain in domains:
            row = conn.execute(
                """
                SELECT service_id
                FROM catalog_entries
                WHERE issuer_hint LIKE ? OR openid_configuration_url LIKE ? OR jwks_uri LIKE ?
                LIMIT 1
                """,
                (f"%{domain}%", f"%{domain}%", f"%{domain}%"),
            ).fetchone()
            if row:
                return KnownMatch("official_catalog", "domain", row["service_id"] or domain)
        return None

    def _classification_histogram(self, results: list[ProbeResult]) -> dict[str, int]:
        histogram: dict[str, int] = {}
        for result in results:
            histogram[result.classification] = histogram.get(result.classification, 0) + 1
        return histogram

    def _mark_cluster_reviewed(self, cluster_id: str, tool_context=None) -> None:
        state = self.state_dict(tool_context)
        reviewed = list(state.get("reviewed_clusters", []))
        if cluster_id not in reviewed:
            reviewed.append(cluster_id)
        state["reviewed_clusters"] = reviewed
        self._clear_state_key(state, "current_cluster_id")

    def _build_report_markdown(self, state: MutableMapping[str, Any], outcome: dict[str, Any]) -> str:
        plan = state.get("run_plan", {})
        fallback_reason = state.get("llm_fallback_reason")
        fallback_reasons = state.get("llm_fallback_reasons", [])
        lines = [
            f"# oidc-hunter run {self.run_id}",
            "",
            "## Initialization",
            "",
            "```json",
            json.dumps(state.get("initialization_summary", {}), indent=2, sort_keys=True),
            "```",
            "",
            "## Run Context",
            "",
            "```json",
            json.dumps(state.get("run_brief", {}), indent=2, sort_keys=True),
            "```",
            "",
            "## Plan",
            "",
            "```json",
            json.dumps(plan, indent=2, sort_keys=True),
            "```",
            "",
            "## Probe Summary",
            "",
            "```json",
            json.dumps(state.get("last_probe_summary", {}), indent=2, sort_keys=True),
            "```",
            "",
            "## Candidates Update",
            "",
            "```json",
            json.dumps(state.get("candidates_update_summary", {}), indent=2, sort_keys=True),
            "```",
            "",
            "## Outcome",
            "",
            "```json",
            json.dumps(outcome, indent=2, sort_keys=True),
            "```",
        ]
        if fallback_reason:
            lines.extend(
                [
                    "",
                    "## LLM Fallback",
                    "",
                    "```json",
                    json.dumps(
                        fallback_reasons or [{"stage": "unknown", "reason": str(fallback_reason)}],
                        indent=2,
                        sort_keys=True,
                    ),
                    "```",
                ]
            )
        return "\n".join(lines) + "\n"
