import asyncio
from collections.abc import MutableMapping
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oidc_hunter.cloudflare import CloudflareSeedBatch
from oidc_hunter.config import AppConfig
from oidc_hunter.db import database, utc_now
from oidc_hunter.probe import ProbeResult
from oidc_hunter.workflow_tools import WorkflowTools


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


class _DeleteUnsupportedState(MutableMapping[str, object]):
    def __init__(self, initial: dict[str, object]):
        self._values = dict(initial)

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __setitem__(self, key: str, value: object) -> None:
        self._values[key] = value

    def __delitem__(self, key: str) -> None:
        raise AttributeError("__delitem__")

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class _FakeToolContext:
    def __init__(self, state: MutableMapping[str, object]):
        self.state = state


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_deterministic_run_discovers_and_exports_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            config = AppConfig(
                state_dir=state_dir,
                llm_base_url=None,
                llm_model=None,
                llm_api_key=None,
                cloudflare_api_token=None,
                probe_domains=[],
                investigation_iterations=1,
                review_iterations=5,
                investigation_target_limit=10,
            )
            config.ensure_directories()
            run_id = "run-test"
            run_dir = config.artifacts_dir / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            tools = WorkflowTools(config=config, run_id=run_id, run_dir=run_dir)

            with patch(
                "oidc_hunter.workflow_tools.httpx.get",
                return_value=_FakeResponse("services: {}\n"),
            ), patch(
                "oidc_hunter.workflow_tools.fetch_cloudflare_seed_batch",
                return_value=CloudflareSeedBatch(
                    domains=["example.com"],
                    source="test_cloudflare",
                    artifact_ref=None,
                    metadata={"count": 1},
                ),
            ), patch(
                "oidc_hunter.workflow_tools.probe_many_domains",
                new=AsyncMock(
                    return_value=[
                        ProbeResult(
                            domain="auth.example.com",
                            status="ok",
                            classification="valid_oidc",
                            openid_configuration_url="https://auth.example.com/.well-known/openid-configuration",
                            issuer="https://auth.example.com",
                            jwks_uri="https://auth.example.com/jwks.json",
                        )
                    ]
                ),
            ):
                outcome = await tools.deterministic_run()

            self.assertEqual(outcome["status"], "completed")
            self.assertEqual(outcome["summary"], "valid_oidc=1, promoted=1, rejected=0, followup=0")

            candidates_yaml = config.candidates_path.read_text(encoding="utf-8")
            self.assertIn("auth.example.com", candidates_yaml)
            self.assertIn("https://auth.example.com/jwks.json", candidates_yaml)

            with database(config.db_path) as conn:
                candidate = conn.execute(
                    "SELECT candidate_id, issuer, status FROM candidate_entries"
                ).fetchone()
                self.assertEqual(candidate["candidate_id"], "auth.example.com")
                self.assertEqual(candidate["issuer"], "https://auth.example.com")
                self.assertEqual(candidate["status"], "active")
                decision = conn.execute(
                    "SELECT decision FROM candidate_decisions"
                ).fetchone()
                self.assertEqual(decision["decision"], "new_candidate")

    async def test_mark_cluster_rejected_tolerates_state_without_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            config = AppConfig(
                state_dir=state_dir,
                llm_base_url=None,
                llm_model=None,
                llm_api_key=None,
                cloudflare_api_token=None,
                probe_domains=[],
            )
            config.ensure_directories()
            run_id = "run-review-state"
            run_dir = config.artifacts_dir / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            tools = WorkflowTools(config=config, run_id=run_id, run_dir=run_dir)
            tools.ensure_run_started()

            cluster_id = f"{run_id}-cluster-1"
            with database(config.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO issuer_clusters(
                        cluster_id, run_id, issuer, jwks_uri, openid_configuration_url,
                        canonical_domain, domains_json, known_match_json, status, notes,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cluster_id,
                        run_id,
                        "https://auth.example.com",
                        "https://auth.example.com/jwks.json",
                        "https://auth.example.com/.well-known/openid-configuration",
                        "auth.example.com",
                        json.dumps(["auth.example.com"]),
                        "{}",
                        "pending_review",
                        "",
                        utc_now(),
                        utc_now(),
                    ),
                )

            state = _DeleteUnsupportedState(
                {
                    "current_cluster_id": cluster_id,
                    "reviewed_clusters": [],
                }
            )
            result = tools.mark_cluster_rejected(
                "Already present in the known set.",
                tool_context=_FakeToolContext(state),
            )

            self.assertEqual(result["decision"], "reject")
            self.assertEqual(state["current_cluster_id"], None)
            self.assertEqual(state["reviewed_clusters"], [cluster_id])

            with database(config.db_path) as conn:
                cluster = conn.execute(
                    "SELECT status, notes FROM issuer_clusters WHERE cluster_id = ?",
                    (cluster_id,),
                ).fetchone()
                self.assertEqual(cluster["status"], "rejected")
                self.assertIn("Rejected: Already present in the known set.", cluster["notes"])
                decision = conn.execute(
                    "SELECT decision, reason FROM candidate_decisions WHERE cluster_id = ?",
                    (cluster_id,),
                ).fetchone()
                self.assertEqual(decision["decision"], "reject")
                self.assertEqual(decision["reason"], "Already present in the known set.")


if __name__ == "__main__":
    unittest.main()
