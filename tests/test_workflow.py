import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oidc_hunter.cloudflare import CloudflareSeedBatch
from oidc_hunter.config import AppConfig
from oidc_hunter.db import database
from oidc_hunter.probe import ProbeResult
from oidc_hunter.workflow_tools import WorkflowTools


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


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


if __name__ == "__main__":
    unittest.main()
